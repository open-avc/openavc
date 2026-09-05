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

const ALL = [serverNode, stateInNode, eventInNode, commandNode, macroNode, setNode, emitNode];

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
      const devices = await helper.request().get("/openavc/srv/devices").expect(200);
      expect(devices.body.map((d) => d.id)).toEqual(["switcher", "projector"]);
      expect(fake.lastRestHeaders["x-api-key"]).toBe("k-123");

      const commands = await helper.request().get("/openavc/srv/devices/switcher/commands").expect(200);
      expect(commands.body.map((c) => c.name)).toEqual(["route"]);
      expect(commands.body[0].params.map((p) => p.name)).toEqual(["input", "output"]);

      const macros = await helper.request().get("/openavc/srv/macros").expect(200);
      expect(macros.body.map((m) => m.id)).toEqual(["system_on", "bad"]);

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
