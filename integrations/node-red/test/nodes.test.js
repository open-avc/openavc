// Every node loaded into a real Node-RED runtime (node-red-node-test-helper)
// against the fake OpenAVC, driven the way a flow drives it.

import { describe, it, expect, beforeAll, beforeEach, afterEach } from "vitest";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const helper = require("node-red-node-test-helper");
const { FakeOpenAVC } = require("./fake-openavc");

const serverNode = require("../nodes/openavc-server");
const stateInNode = require("../nodes/openavc-state-in");
const eventInNode = require("../nodes/openavc-event-in");
const commandNode = require("../nodes/openavc-command");
const macroNode = require("../nodes/openavc-macro");
const setNode = require("../nodes/openavc-set");
const emitNode = require("../nodes/openavc-emit");
const stateGetNode = require("../nodes/openavc-state-get");
const { NEEDS_KEY } = require("../lib/admin");

const ALL = [serverNode, stateInNode, eventInNode, commandNode, macroNode, setNode, emitNode, stateGetNode];

let fake;

beforeAll(() => {
  helper.init(require.resolve("node-red"));
});

beforeEach(async () => {
  fake = await new FakeOpenAVC().start();
});

afterEach(async () => {
  await helper.unload();
  await fake.stop();
});

function server(extra = {}) {
  return { id: "srv", type: "openavc-server", name: "Room", host: "127.0.0.1", port: fake.port, tls: false, ...extra };
}

function nextInput(node) {
  return new Promise((resolve) => node.once("input", resolve));
}

async function loadFlow(flow, credentials) {
  await helper.load(ALL, flow, credentials);
  await fake.waitForConnections(1);
}

describe("openavc-server", () => {
  it("opens one connection per server config, as a panel without a key", async () => {
    await loadFlow([server()]);
    const srv = helper.getNode("srv");
    expect(srv.connection.role).toBe("panel");
    expect(fake.connections).toHaveLength(1);
  });

  it("uses the stored credential as the programmer", async () => {
    await loadFlow([server()], { srv: { apiKey: "k-123" } });
    expect(helper.getNode("srv").connection.role).toBe("programmer");
    expect(fake.connections[0].headers["x-api-key"]).toBe("k-123");
  });

  it("announces the configured name", async () => {
    await loadFlow([server({ clientName: "lobby-logic" })]);
    expect(fake.connections[0].name).toBe("lobby-logic");
  });

  it("closes the connection when the flow is undeployed", async () => {
    await loadFlow([server()]);
    await helper.unload();
    await fake.waitFor(() => (fake.connections.length === 0 ? true : null));
  });
});

describe("openavc-state-in", () => {
  it("sends a message for a matching change, with the key as the topic", async () => {
    await loadFlow([
      server(),
      { id: "in", type: "openavc-state-in", server: "srv", patterns: "var.*", replay: false, wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    fake.setState("device.switcher.online", false); // not matched
    fake.setState("var.request_source", "hdmi1");
    const msg = await got;
    expect(msg.topic).toBe("var.request_source");
    expect(msg.payload).toBe("hdmi1");
    expect(msg.replay).toBeUndefined();
  });

  it("replays current values on connect when asked", async () => {
    const received = [];
    await helper.load(ALL, [
      server(),
      { id: "in", type: "openavc-state-in", server: "srv", patterns: "var.status", replay: true, wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    helper.getNode("h").on("input", (m) => received.push(m));
    await fake.waitFor(() => received.length || null);
    expect(received[0]).toMatchObject({ topic: "var.status", payload: "idle", replay: true });
  });

  it("marks a deleted key", async () => {
    await loadFlow([
      server(),
      { id: "in", type: "openavc-state-in", server: "srv", patterns: "var.*", replay: false, wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    fake.deleteState("var.status");
    expect(await got).toMatchObject({ topic: "var.status", payload: null, deleted: true });
  });
});

describe("openavc-event-in", () => {
  it("subscribes for its patterns and forwards a matching event", async () => {
    await loadFlow([
      server(),
      { id: "ev", type: "openavc-event-in", server: "srv", patterns: "custom.*", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    await fake.waitFor(() => (fake.connections[0].patterns.length ? true : null));
    const got = nextInput(helper.getNode("h"));
    fake.emitEvent("custom.presentation_started", { source: "laptop" });
    const msg = await got;
    expect(msg.topic).toBe("custom.presentation_started");
    expect(msg.payload).toEqual({ source: "laptop" });
    expect(typeof msg.timestamp).toBe("number");
  });

  it("only its own patterns reach it, even though the socket carries the union", async () => {
    await loadFlow([
      server(),
      { id: "a", type: "openavc-event-in", server: "srv", patterns: "custom.*", wires: [["ha"]] },
      { id: "b", type: "openavc-event-in", server: "srv", patterns: "device.*", wires: [["hb"]] },
      { id: "ha", type: "helper" },
      { id: "hb", type: "helper" },
    ]);
    await fake.waitFor(() => (fake.connections[0].patterns.length === 2 ? true : null));
    const seenA = [];
    helper.getNode("ha").on("input", (m) => seenA.push(m.topic));
    const gotB = nextInput(helper.getNode("hb"));
    fake.emitEvent("device.disconnected.projector", {});
    expect((await gotB).topic).toBe("device.disconnected.projector");
    expect(seenA).toEqual([]);
  });
});

describe("openavc-command", () => {
  it("sends the configured command and reports the ack on payload", async () => {
    await loadFlow([
      server(),
      { id: "cmd", type: "openavc-command", server: "srv", device: "switcher", command: "route", params: '{"input": 2, "output": 1}', paramsType: "json", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("cmd").receive({ payload: "go" });
    expect((await got).payload).toEqual({ success: true });
    expect(fake.framesOfType("command")[0]).toMatchObject({ device_id: "switcher", command: "route", params: { input: 2, output: 1 } });
  });

  it("lets the message override device, command and params", async () => {
    await loadFlow([
      server(),
      { id: "cmd", type: "openavc-command", server: "srv", device: "", command: "", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("cmd").receive({ device: "switcher", command: "route", params: { input: 3 } });
    await got;
    expect(fake.framesOfType("command")[0]).toMatchObject({ device_id: "switcher", command: "route", params: { input: 3 } });
  });

  it("puts a refused command on the payload AND raises it for a catch node", async () => {
    await loadFlow([
      server(),
      { id: "cmd", type: "openavc-command", server: "srv", device: "projector", command: "power_on", wires: [["h"]] },
      { id: "c", type: "catch", scope: null, uncaught: false, wires: [["hc"]] },
      { id: "h", type: "helper" },
      { id: "hc", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    const caught = nextInput(helper.getNode("hc"));
    helper.getNode("cmd").receive({});
    const msg = await got;
    expect(msg.payload.success).toBe(false);
    expect(msg.payload.error).toMatch(/Could not connect/);
    expect((await caught).error.message).toMatch(/Could not connect/);
  });

  it("refuses to send with no device named anywhere", async () => {
    await loadFlow([
      server(),
      { id: "cmd", type: "openavc-command", server: "srv", device: "", command: "route", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("cmd").receive({});
    expect((await got).payload.error).toMatch(/device and a command are required/);
    expect(fake.framesOfType("command")).toHaveLength(0);
  });
});

describe("openavc-macro", () => {
  it("runs the macro and answers when it has finished", async () => {
    await loadFlow([
      server(),
      { id: "m", type: "openavc-macro", server: "srv", macro: "system_on", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("m").receive({});
    expect((await got).payload).toEqual({ success: true });
  });

  it("reports a macro that failed", async () => {
    await loadFlow([
      server(),
      { id: "m", type: "openavc-macro", server: "srv", macro: "", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("m").receive({ macro: "bad" });
    expect((await got).payload).toEqual({ success: false, error: "step 1/1 failed" });
  });
});

describe("openavc-set", () => {
  it("writes msg.payload to the configured key", async () => {
    await loadFlow([
      server(),
      { id: "s", type: "openavc-set", server: "srv", key: "var.status", value: "payload", valueType: "msg", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("s").receive({ payload: "switching" });
    expect((await got).payload).toEqual({ success: true });
    expect(fake.state["var.status"]).toBe("switching");
  });

  it("writes a fixed value, and refuses a nested one before sending", async () => {
    await loadFlow([
      server(),
      { id: "s", type: "openavc-set", server: "srv", key: "var.count", value: "3", valueType: "num", wires: [["h"]] },
      { id: "s2", type: "openavc-set", server: "srv", key: "var.count", value: "payload", valueType: "msg", wires: [["h2"]] },
      { id: "h", type: "helper" },
      { id: "h2", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("s").receive({});
    await got;
    expect(fake.state["var.count"]).toBe(3);

    const got2 = nextInput(helper.getNode("h2"));
    helper.getNode("s2").receive({ payload: { nested: true } });
    expect((await got2).payload.error).toMatch(/string, a number, a boolean or null/);
    expect(fake.framesOfType("state.set")).toHaveLength(1);
  });

  it("hears the server refuse a namespace a panel may not write", async () => {
    await loadFlow([
      server(),
      { id: "s", type: "openavc-set", server: "srv", key: "device.switcher.online", value: "payload", valueType: "msg", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("s").receive({ payload: false });
    expect((await got).payload.error).toMatch(/Panel clients can only set/);
  });
});

describe("openavc-emit", () => {
  it("adds the custom. prefix and wraps a scalar payload", async () => {
    await loadFlow([
      server(),
      { id: "e", type: "openavc-emit", server: "srv", event: "occupancy_changed", payload: "payload", payloadType: "msg", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("e").receive({ payload: 12 });
    expect((await got).payload).toEqual({ success: true });
    expect(fake.framesOfType("event.emit")[0]).toEqual({ type: "event.emit", event: "custom.occupancy_changed", payload: { value: 12 } });
  });

  it("passes an object payload through as it is", async () => {
    await loadFlow([
      server(),
      { id: "e", type: "openavc-emit", server: "srv", event: "custom.x", payload: '{"occupied": true}', payloadType: "json", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("e").receive({});
    await got;
    expect(fake.framesOfType("event.emit")[0].payload).toEqual({ occupied: true });
  });
});

describe("the editor's lookups", () => {
  it("are answered through the deployed server node, with its credential", async () => {
    await helper.startServer();
    try {
      await loadFlow([server()], { srv: { apiKey: "k-123" } });
      // Devices come from the live state mirror (every device publishes its
      // name there), so no REST call and no credential is needed for them.
      const devices = await helper.request().get("/openavc/srv/devices").expect(200);
      expect(devices.body).toEqual([
        { id: "projector", name: "Projector", driver: "", connected: false },
        { id: "switcher", name: "Main Switcher", driver: "", connected: true },
      ]);

      const commands = await helper.request().get("/openavc/srv/devices/switcher/commands").expect(200);
      expect(fake.lastRestHeaders["x-api-key"]).toBe("k-123");
      expect(commands.body.map((c) => c.name)).toEqual(["route"]);
      expect(commands.body[0].description).toBe("Route");
      expect(commands.body[0].params.map((p) => p.name)).toEqual(["input", "output"]);
      expect(commands.body[0].params[0]).toMatchObject({ name: "input", type: "integer", required: true });

      const macros = await helper.request().get("/openavc/srv/macros").expect(200);
      expect(macros.body.map((m) => m.id)).toEqual(["system_on", "bad", "skip_me", "flaky", "slow"]);

      const keys = await helper.request().get("/openavc/srv/state-keys").expect(200);
      expect(keys.body).toContain("var.status");

      const missing = await helper.request().get("/openavc/nope/devices").expect(404);
      expect(missing.body.error).toMatch(/Deploy the OpenAVC server node first/);
    } finally {
      await helper.stopServer();
    }
  });
});

describe("a node with no server", () => {
  it("logs an error naming the problem and sends nothing", async () => {
    await helper.load(ALL, [
      { id: "cmd", type: "openavc-command", server: "", device: "x", command: "y", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const logged = helper.log().args.map((a) => JSON.stringify(a[0]));
    expect(logged.some((line) => /No OpenAVC server is configured/.test(line))).toBe(true);
    const seen = [];
    helper.getNode("h").on("input", (m) => seen.push(m));
    helper.getNode("cmd").receive({});
    await new Promise((r) => setTimeout(r, 30));
    expect(seen).toEqual([]);
  });
});

describe("the editor's lookups on a password-protected system", () => {
  it("still list devices and state keys without a key, and say a key is needed for the rest", async () => {
    await fake.stop();
    fake = await new FakeOpenAVC({ claimed: true }).start();
    await helper.startServer();
    try {
      await loadFlow([server()]);
      const devices = await helper.request().get("/openavc/srv/devices").expect(200);
      expect(devices.body.map((d) => d.id)).toEqual(["projector", "switcher"]);
      const keys = await helper.request().get("/openavc/srv/state-keys").expect(200);
      expect(keys.body).toContain("var.status");
      const macros = await helper.request().get("/openavc/srv/macros").expect(403);
      expect(macros.body.error).toBe(NEEDS_KEY);
      expect(NEEDS_KEY).toMatch(/Settings › Access/);
      const commands = await helper.request().get("/openavc/srv/devices/switcher/commands").expect(403);
      expect(commands.body.error).toBe(NEEDS_KEY);
    } finally {
      await helper.stopServer();
    }
  });
});

describe("openavc-state-get", () => {
  const flow = (extra) => [
    server(),
    { id: "g", type: "openavc-state-get", server: "srv", key: "var.status", output: "payload", ...extra, wires: [["h"]] },
    { id: "h", type: "helper" },
  ];

  it("answers with what the key holds now, on demand", async () => {
    await loadFlow(flow());
    await fake.waitFor(() => (helper.getNode("srv").connection.status === "connected" ? true : null));
    const got = nextInput(helper.getNode("h"));
    helper.getNode("g").receive({ payload: "asked" });
    const msg = await got;
    expect(msg.topic).toBe("var.status");
    expect(msg.payload).toBe("idle");
  });

  it("takes the key from the message, puts the value where asked, and gives null for a key nobody holds", async () => {
    await loadFlow(flow({ key: "", output: "current" }));
    await fake.waitFor(() => (helper.getNode("srv").connection.status === "connected" ? true : null));
    const got = nextInput(helper.getNode("h"));
    helper.getNode("g").receive({ key: "device.switcher.online", payload: "kept" });
    const msg = await got;
    expect(msg.current).toBe(true);
    expect(msg.payload).toBe("kept");

    const got2 = nextInput(helper.getNode("h"));
    helper.getNode("g").receive({ key: "var.nothing_here" });
    expect((await got2).current).toBeNull();
  });

  it("answers a pattern with every matching key", async () => {
    await loadFlow(flow({ key: "device.switcher.*" }));
    await fake.waitFor(() => (helper.getNode("srv").connection.status === "connected" ? true : null));
    const got = nextInput(helper.getNode("h"));
    helper.getNode("g").receive({});
    expect((await got).payload).toEqual({ "device.switcher.connected": true, "device.switcher.name": "Main Switcher", "device.switcher.online": true });
  });

  it("raises rather than answering from a stale copy while disconnected", async () => {
    await loadFlow([
      ...flow(),
      { id: "c", type: "catch", scope: null, uncaught: false, wires: [["hc"]] },
      { id: "hc", type: "helper" },
    ]);
    const conn = helper.getNode("srv").connection;
    await fake.waitFor(() => (conn.status === "connected" ? true : null));
    fake.dropClients();
    await fake.waitFor(() => (conn.status !== "connected" ? true : null));
    const caught = nextInput(helper.getNode("hc"));
    helper.getNode("g").receive({});
    expect((await caught).error.message).toMatch(/not connected/);
  });
});

describe("openavc-macro, cancelling", () => {
  it("cancels a running macro from the node's action or from msg.action", async () => {
    await loadFlow([
      server(),
      { id: "m_run", type: "openavc-macro", server: "srv", macro: "slow", action: "run", wires: [["h"]] },
      { id: "m_stop", type: "openavc-macro", server: "srv", macro: "slow", action: "cancel", wires: [["hs"]] },
      { id: "m_any", type: "openavc-macro", server: "srv", macro: "", action: "run", wires: [["ha"]] },
      { id: "h", type: "helper" },
      { id: "hs", type: "helper" },
      { id: "ha", type: "helper" },
    ]);
    const finished = nextInput(helper.getNode("h"));
    helper.getNode("m_run").receive({});
    await fake.waitFor(() => fake.running.get("slow")?.length || null);
    const stopped = nextInput(helper.getNode("hs"));
    helper.getNode("m_stop").receive({});
    expect((await stopped).payload).toEqual({ success: true, cancelled: true });
    expect((await finished).payload).toEqual({ success: false, error: "macro cancelled" });

    const nothing = nextInput(helper.getNode("ha"));
    helper.getNode("m_any").receive({ macro: "slow", action: "cancel" });
    expect((await nothing).payload).toEqual({ success: true, cancelled: false });
  });

  it("reports a start the macro's own guard turned away, and step errors on a run that finished", async () => {
    await loadFlow([
      server(),
      { id: "m", type: "openavc-macro", server: "srv", macro: "", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const seen = [];
    helper.getNode("h").on("input", (m) => seen.push(m.payload));
    helper.getNode("m").receive({ macro: "skip_me" });
    helper.getNode("m").receive({ macro: "skip_me" });
    await fake.waitFor(() => (seen.length === 2 ? true : null));
    expect(seen).toEqual([
      { success: false, error: "macro skipped: overlap=skip and an instance is already running" },
      { success: true },
    ]);

    const got = nextInput(helper.getNode("h"));
    helper.getNode("m").receive({ macro: "flaky" });
    const msg = await got;
    expect(msg.payload.success).toBe(true);
    expect(msg.payload.step_errors).toEqual([
      { step: 1, action: "device.command", device: "projector", command: "power_on", error: "Projector is not connected." },
    ]);
  });
});

describe("openavc-set, with nothing to write", () => {
  it("refuses a message without the property rather than writing null", async () => {
    await loadFlow([
      server(),
      { id: "s", type: "openavc-set", server: "srv", key: "var.status", value: "payload", valueType: "msg", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("s").receive({ topic: "no payload here" });
    expect((await got).payload.error).toMatch(/msg\.payload is not on the message/);
    expect(fake.framesOfType("state.set")).toHaveLength(0);
  });

  it("writes a fixed empty string, which is how a request variable is cleared", async () => {
    await loadFlow([
      server(),
      { id: "s", type: "openavc-set", server: "srv", key: "var.request_source", value: "", valueType: "str", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    fake.state["var.request_source"] = "laptop";
    const got = nextInput(helper.getNode("h"));
    helper.getNode("s").receive({});
    expect((await got).payload).toEqual({ success: true });
    expect(fake.state["var.request_source"]).toBe("");
  });

  it("writes a value computed by JSONata", async () => {
    await loadFlow([
      server(),
      { id: "s", type: "openavc-set", server: "srv", key: "var.status", value: '"Showing " & payload', valueType: "jsonata", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const got = nextInput(helper.getNode("h"));
    helper.getNode("s").receive({ payload: "HDMI 1" });
    expect((await got).payload).toEqual({ success: true });
    expect(fake.state["var.status"]).toBe("Showing HDMI 1");
  });
});

describe("openavc-event-in, on a connection with no key", () => {
  it("says which patterns it cannot hear instead of sitting silent", async () => {
    await loadFlow([
      server(),
      { id: "ev", type: "openavc-event-in", server: "srv", patterns: "plugin.*, custom.*", wires: [["h"]] },
      { id: "h", type: "helper" },
    ]);
    const logged = helper.log().args.map((a) => a[0]);
    const warning = logged.find((l) => l && l.level === 30 && /plugin\.\*/.test(l.msg));
    expect(warning).toBeTruthy();
    expect(warning.msg).toMatch(/Without an API key/);
    const ev = helper.getNode("ev");
    await fake.waitFor(() => (ev.status.lastCall && /needs an API key for plugin/.test(ev.status.lastCall.args[0].text) ? true : null));
  });

  it("does not warn when the connection has a key", async () => {
    await loadFlow([
      server(),
      { id: "ev", type: "openavc-event-in", server: "srv", patterns: "plugin.*", wires: [["h"]] },
      { id: "h", type: "helper" },
    ], { srv: { apiKey: "k-123" } });
    const logged = helper.log().args.map((a) => a[0]);
    expect(logged.some((l) => l && /Without an API key/.test(l.msg))).toBe(false);
  });
});

describe("openavc-server, key refused", () => {
  it("shows the reason on every node's status", async () => {
    await fake.stop();
    fake = await new FakeOpenAVC({ claimed: true }).start();
    await helper.load(ALL, [
      server(),
      { id: "s", type: "openavc-set", server: "srv", key: "var.status", wires: [[]] },
    ], { srv: { apiKey: "wrong" } });
    const s = helper.getNode("s");
    await fake.waitFor(() => (s.status.lastCall && /refused the API key/.test(s.status.lastCall.args[0].text) ? true : null), 3000);
    expect(s.status.lastCall.args[0]).toMatchObject({ fill: "red", text: "disconnected: the server refused the API key" });
  });
});

describe("openavc-server, with a TLS configuration", () => {
  it("takes its certificate options, verify setting included", async () => {
    const tlsNode = require("@node-red/nodes/core/network/05-tls.js");
    await helper.load([tlsNode, serverNode], [
      { id: "t", type: "tls-config", name: "company CA", verifyservercert: true, servername: "avc.example" },
      server({ tls: true, tlsVerify: false, tlsConfig: "t", port: 1 }),
    ]);
    const conn = helper.getNode("srv").connection;
    expect(conn.tls).toBe(true);
    expect(conn.tlsOptions).toMatchObject({ rejectUnauthorized: true, servername: "avc.example" });
    expect(conn._tlsOpts().rejectUnauthorized).toBe(true);
  });
});
