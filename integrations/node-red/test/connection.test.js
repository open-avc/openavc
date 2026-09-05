import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createRequire } from "module";

// The package is CommonJS (Node-RED loads nodes with require); the tests are
// ESM because vitest is.
const require = createRequire(import.meta.url);
const { FakeOpenAVC } = require("./fake-openavc");
const { OpenAVCConnection } = require("../lib/connection");
const { globToRegExp, compile, parsePatterns } = require("../lib/glob");

let fake;
const conns = [];

function connect(opts = {}) {
  const c = new OpenAVCConnection({ host: "127.0.0.1", port: fake.port, ...opts });
  conns.push(c);
  c.connect();
  return c;
}

function once(emitter, event) {
  return new Promise((resolve) => emitter.once(event, (...args) => resolve(args)));
}

beforeEach(async () => {
  fake = await new FakeOpenAVC().start();
});

afterEach(async () => {
  for (const c of conns.splice(0)) c.close();
  await fake.stop();
});

describe("globs", () => {
  it("match the way the bus does: * crosses dots, ? is one character", () => {
    expect(globToRegExp("var.*").test("var.a.b")).toBe(true);
    expect(globToRegExp("device.*.online").test("device.proj.online")).toBe(true);
    expect(globToRegExp("device.*.online").test("device.proj.power")).toBe(false);
    expect(globToRegExp("custom.a?").test("custom.ab")).toBe(true);
    expect(compile([])("anything")).toBe(false);
  });

  it("parse a config field the way people type it", () => {
    expect(parsePatterns(" var.*, ,device.x ,var.*")).toEqual(["var.*", "device.x"]);
    expect(parsePatterns(["a", "a", "b"])).toEqual(["a", "b"]);
    expect(parsePatterns(undefined)).toEqual([]);
  });
});

describe("role and credential", () => {
  it("connects as a panel with no key, and sends no credential header", async () => {
    const c = connect();
    await once(c, "open");
    const [server] = fake.connections;
    expect(c.role).toBe("panel");
    expect(server.url).toContain("client=panel");
    expect(server.headers["x-api-key"]).toBeUndefined();
  });

  it("connects as the programmer with a key, carried in X-API-Key", async () => {
    const c = connect({ apiKey: "secret-key" });
    await once(c, "open");
    const [server] = fake.connections;
    expect(c.role).toBe("programmer");
    expect(server.url).toContain("client=programmer");
    expect(server.headers["x-api-key"]).toBe("secret-key");
  });

  it("carries the key on the editor's REST lookups too", async () => {
    const c = connect({ apiKey: "secret-key" });
    await once(c, "open");
    const data = await c.fetchJson("/api/devices");
    expect(data.devices.map((d) => d.id)).toEqual(["switcher", "projector"]);
    expect(fake.lastRestHeaders["x-api-key"]).toBe("secret-key");
  });
});

describe("the state mirror", () => {
  it("holds the snapshot and follows updates and deletes", async () => {
    const c = connect();
    await once(c, "snapshot");
    expect(c.state["var.status"]).toBe("idle");

    const changed = once(c, "state");
    fake.setState("var.status", "switching");
    expect(await changed).toEqual(["var.status", "switching", false]);
    expect(c.state["var.status"]).toBe("switching");

    const deleted = once(c, "state");
    fake.deleteState("var.status");
    expect(await deleted).toEqual(["var.status", undefined, true]);
    expect("var.status" in c.state).toBe(false);
  });

  it("answers the server's heartbeat", async () => {
    const c = connect();
    await once(c, "open");
    fake.ping();
    await fake.waitFor(() => fake.framesOfType("pong").length);
  });
});

describe("event subscription", () => {
  it("sends the union of every owner's patterns as one replace", async () => {
    const c = connect();
    await once(c, "open");
    c.subscribeEvents("a", ["custom.*"]);
    c.subscribeEvents("b", ["device.disconnected.*", "custom.*"]);
    const [reply] = await once(c, "subscribed");
    expect(reply).toEqual(["custom.*", "device.disconnected.*"]);
    expect(fake.framesOfType("event.subscribe")).toHaveLength(1);

    c.unsubscribeEvents("b");
    const [after] = await once(c, "subscribed");
    expect(after).toEqual(["custom.*"]);
  });

  it("delivers a matching event and nothing else", async () => {
    const c = connect();
    await once(c, "open");
    c.subscribeEvents("a", ["custom.*"]);
    await once(c, "subscribed");
    const got = once(c, "event");
    fake.emitEvent("device.connected.x", {});
    fake.emitEvent("custom.ready", { room: "201" });
    const [event, payload, ts] = await got;
    expect(event).toBe("custom.ready");
    expect(payload).toEqual({ room: "201" });
    expect(typeof ts).toBe("number");
  });

  it("resubscribes after a reconnect, so a server restart loses nothing", async () => {
    const c = connect();
    await once(c, "open");
    c.subscribeEvents("a", ["custom.*"]);
    await once(c, "subscribed");

    const reopened = once(c, "open");
    fake.dropClients();
    await once(c, "close");
    await reopened;
    await fake.waitFor(() => fake.framesOfType("event.subscribe").length >= 2);
    expect(fake.connections[0].patterns).toEqual(["custom.*"]);
  });
});

describe("requests", () => {
  it("resolves a command on its ack, success or not", async () => {
    const c = connect();
    await once(c, "open");
    expect(await c.sendCommand("switcher", "route", { input: 2, output: 1 })).toEqual({ success: true });
    const failed = await c.sendCommand("projector", "power_on");
    expect(failed.success).toBe(false);
    expect(failed.error).toMatch(/Could not connect/);
    const sent = fake.framesOfType("command")[0];
    expect(sent).toEqual({ type: "command", device_id: "switcher", command: "route", params: { input: 2, output: 1 } });
  });

  it("answers concurrent commands to the same device in order", async () => {
    const c = connect();
    await once(c, "open");
    const results = await Promise.all([
      c.sendCommand("switcher", "route", { input: 1 }),
      c.sendCommand("switcher", "route", { input: 2 }),
    ]);
    expect(results).toEqual([{ success: true }, { success: true }]);
  });

  it("resolves a macro when it finishes, not when it is accepted", async () => {
    const c = connect();
    await once(c, "open");
    expect(await c.executeMacro("system_on")).toEqual({ success: true });
    const failed = await c.executeMacro("bad");
    expect(failed).toEqual({ success: false, error: "step 1/1 failed" });
  });

  it("turns an error frame into the oldest request of that kind", async () => {
    const c = connect();
    await once(c, "open");
    const result = await c.executeMacro("does_not_exist");
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/No macro named/);
  });

  it("writes state and emits events, and hears the refusal", async () => {
    const c = connect();
    await once(c, "open");
    expect(await c.setState("var.status", "ready")).toEqual({ success: true });
    expect(fake.state["var.status"]).toBe("ready");
    const refused = await c.setState("device.switcher.online", false);
    expect(refused.success).toBe(false);

    expect(await c.emitEvent("custom.x", { n: 1 })).toEqual({ success: true });
    const bad = await c.emitEvent("device.x", {});
    expect(bad.success).toBe(false);
    expect(bad.error).toMatch(/custom\./);
  });

  it("rejects what is in flight when the socket drops, rather than hanging", async () => {
    const c = connect();
    await once(c, "open");
    fake.wss.removeAllListeners("connection"); // nothing answers any more
    fake.wss.on("connection", (ws) => {
      fake.connections.push({ ws, url: "", headers: {}, role: "panel", patterns: [] });
    });
    // The already-open socket still gets frames; make the reply never come by
    // dropping the connection mid-request.
    const pending = c.sendCommand("switcher", "route");
    fake.dropClients();
    await expect(pending).rejects.toThrow(/disconnected/);
  });

  it("refuses a request while disconnected instead of queueing it silently", async () => {
    const c = new OpenAVCConnection({ host: "127.0.0.1", port: fake.port });
    conns.push(c);
    await expect(c.sendCommand("switcher", "route")).rejects.toThrow(/not connected/);
  });

  it("close() stays closed: no reconnect", async () => {
    const c = connect();
    await once(c, "open");
    c.close();
    expect(c.status).toBe("disconnected");
    await new Promise((r) => setTimeout(r, 50));
    expect(fake.connections).toHaveLength(0);
  });
});
