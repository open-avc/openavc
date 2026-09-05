// How a reply finds the request that asked for it, and what the connection
// does when the server goes quiet, refuses the credential, or turns a request
// away. All against the fake, which reads frames one at a time like the real
// server.

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const { FakeOpenAVC } = require("./fake-openavc");
const { OpenAVCConnection, LEGACY_DETECT_MS } = require("../lib/connection");

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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

afterEach(async () => {
  for (const c of conns.splice(0)) c.close();
  if (fake) await fake.stop();
  fake = null;
});

describe("receipts", () => {
  beforeEach(async () => {
    fake = await new FakeOpenAVC().start();
  });

  it("settle in the order the requests were sent, whatever their kind", async () => {
    const c = connect();
    await once(c, "open");
    const results = await Promise.all([
      c.sendCommand("switcher", "route", { input: 1 }),
      c.executeMacro("does_not_exist"),
      c.setState("var.status", "x"),
      c.emitEvent("device.x", {}),
      c.sendCommand("projector", "power_on"),
    ]);
    expect(results[0]).toEqual({ success: true });
    expect(results[1].error).toMatch(/No macro named/);
    expect(results[2]).toEqual({ success: true });
    expect(results[3].error).toMatch(/custom\./);
    expect(results[4].error).toMatch(/Could not connect/);
  });

  it("a refusal for one request never lands on the one behind it", async () => {
    // A long macro is accepted first; a refusal for a later request must
    // settle that later request, not the oldest thing of its kind.
    const c = connect();
    await once(c, "open");
    const slow = c.executeMacro("slow");
    const missing = await c.executeMacro("does_not_exist");
    expect(missing.error).toMatch(/No macro named/);
    expect(await slow).toEqual({ success: true });
  });

  it("a rate-limited request is refused at once, not left to time out", async () => {
    await fake.stop();
    fake = await new FakeOpenAVC({ rateLimitAfter: 2 }).start();
    const c = connect();
    await once(c, "open");
    expect(await c.setState("var.status", "one")).toEqual({ success: true });
    const started = Date.now();
    const limited = await c.setState("var.status", "two");
    expect(limited).toEqual({ success: false, error: "Rate limit exceeded" });
    expect(Date.now() - started).toBeLessThan(1000);
    expect(await c.setState("var.status", "three")).toEqual({ success: true });
  });

  it("a request that timed out does not steal the next reply", async () => {
    await fake.stop();
    fake = await new FakeOpenAVC({ stallMs: 300 }).start();
    const c = connect();
    await once(c, "open");
    const first = c._request("command", "switcher:route", { type: "command", device_id: "switcher", command: "route" }, 100);
    const second = c.sendCommand("projector", "power_on");
    await expect(first).rejects.toThrow(/no reply/);
    const result = await second;
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/Could not connect/); // the projector's own answer
  });
});

describe("macro outcomes", () => {
  beforeEach(async () => {
    fake = await new FakeOpenAVC().start();
  });

  it("a start the macro's own guard turns away answers with the reason", async () => {
    const c = connect();
    await once(c, "open");
    const [first, second] = await Promise.all([c.executeMacro("skip_me"), c.executeMacro("skip_me")]);
    expect(first).toEqual({ success: true });
    expect(second).toEqual({ success: false, error: "macro skipped: overlap=skip and an instance is already running" });
  });

  it("cancel answers whether anything was running, and the run answers cancelled", async () => {
    const c = connect();
    await once(c, "open");
    const run = c.executeMacro("slow");
    await fake.waitFor(() => fake.running.get("slow")?.length || null);
    expect(await c.cancelMacro("slow")).toEqual({ success: true, cancelled: true });
    expect(await run).toEqual({ success: false, error: "macro cancelled" });
    expect(await c.cancelMacro("slow")).toEqual({ success: true, cancelled: false });
    const missing = await c.cancelMacro("does_not_exist");
    expect(missing.error).toMatch(/No macro named/);
  });

  it("step errors ride the completion of a run that carried on", async () => {
    const c = connect();
    await once(c, "open");
    const result = await c.executeMacro("flaky");
    expect(result.success).toBe(true);
    expect(result.step_errors).toEqual([
      { step: 1, action: "device.command", device: "projector", command: "power_on", error: "Projector is not connected." },
    ]);
  });
});

describe("a system that refuses the key", () => {
  it("says so once, on the connection's detail and in the log, and keeps trying", async () => {
    fake = await new FakeOpenAVC({ claimed: true }).start();
    const errors = [];
    const c = connect({ apiKey: "wrong", logger: { log() {}, warn() {}, error: (m) => errors.push(m) } });
    await fake.waitFor(() => (c.detail ? true : null), 3000);
    expect(c.detail).toBe("the server refused the API key");
    expect(c.status).toBe("disconnected");
    await sleep(1200); // through at least one retry
    expect(errors).toEqual(["OpenAVC 127.0.0.1:" + fake.port + ": the server refused the API key"]);
    await expect(c.sendCommand("switcher", "route")).rejects.toThrow(/refused the API key/);
  }, 10000);

  it("connects as a panel without a key on the same system", async () => {
    fake = await new FakeOpenAVC({ claimed: true }).start();
    const c = connect();
    await once(c, "snapshot");
    expect(c.status).toBe("connected");
  });
});

describe("a socket that has gone quiet", () => {
  it("is terminated and reopened", async () => {
    fake = await new FakeOpenAVC({ silent: true }).start();
    const warned = [];
    const c = connect({ silenceMs: 300, logger: { log() {}, warn: (m) => warned.push(m), error() {} } });
    await once(c, "open");
    await fake.waitFor(() => (fake.connectionsSeen >= 2 ? true : null), 3000);
    expect(warned.some((w) => /nothing heard for 0s|nothing heard/.test(w))).toBe(true);
  }, 10000);
});

describe("telling an old server from a busy one", () => {
  it("does not convict a server that is answering a slow command ahead of the subscription", async () => {
    fake = await new FakeOpenAVC({ stallMs: LEGACY_DETECT_MS + 1500 }).start();
    const c = connect();
    await once(c, "open");
    const pending = c._request("command", "switcher:route", { type: "command", device_id: "switcher", command: "route" }, 0);
    c.subscribeEvents("a", ["custom.*"]);
    const subscribed = once(c, "subscribed");
    await sleep(LEGACY_DETECT_MS + 500);
    expect(c.legacy).toBe(false);
    await pending;
    await subscribed;
    expect(c.legacy).toBe(false);
  }, 15000);

  it("convicts at once when the server answers something sent after the subscription", async () => {
    fake = await new FakeOpenAVC({ legacy: true }).start();
    const c = connect();
    await once(c, "open");
    c.subscribeEvents("a", ["custom.*"]);
    await sleep(20); // let the coalesced subscribe go out first
    const legacy = once(c, "legacy");
    const started = Date.now();
    await c.sendCommand("switcher", "route");
    await legacy;
    expect(c.legacy).toBe(true);
    expect(Date.now() - started).toBeLessThan(LEGACY_DETECT_MS);
  });
});
