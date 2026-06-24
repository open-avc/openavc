"use strict";
// Loads the real Programmer WebSocket client (wsClient.ts, bundled on the fly
// with the esbuild already in web/programmer/node_modules — auth.ts comes along
// in the bundle) and drives it against a fake WebSocket + window so the
// disconnect/reconnect/auth lifecycle can be exercised deterministically without
// a browser. Mirrors driver_builder_store_harness.cjs (buildSync bundle), but
// re-executes the bundle per scenario so each starts from pristine module state
// (everConnected / sendQueue / preOpenFailures are module-level singletons).
// The Python wrapper skips when the Node toolchain or esbuild is absent rather
// than failing the Python-only CI gate.
const path = require("path");

const wsClientPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [wsClientPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;

// The client logs via console.* — keep stdout pure JSON for the Python wrapper
// by routing all console output to stderr (still visible when debugging).
const toStderr = (...args) => process.stderr.write(args.join(" ") + "\n");
global.console = { log: toStderr, warn: toStderr, error: toStderr, info: toStderr };

// --- Fakes injected as globals the bundle references at runtime ---

class FakeWebSocket {
  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    this.readyState = FakeWebSocket.CONNECTING;
    this.onopen = null;
    this.onclose = null;
    this.onmessage = null;
    this.onerror = null;
    this.sent = [];
    this.closeCalls = 0;
    FakeWebSocket.instances.push(this);
  }
  send(data) {
    this.sent.push(data);
  }
  close() {
    this.closeCalls += 1;
    this.readyState = FakeWebSocket.CLOSED;
  }
  // Test driver: simulate the browser firing the lifecycle events.
  fireOpen() {
    this.readyState = FakeWebSocket.OPEN;
    if (this.onopen) this.onopen();
  }
  fireClose(closeCode) {
    this.readyState = FakeWebSocket.CLOSED;
    if (this.onclose) this.onclose({ code: closeCode });
  }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;

// Each scenario re-loads the module against a fresh set of fakes.
function freshEnv() {
  FakeWebSocket.instances = [];
  const scheduled = []; // { id, fn, delay, cleared }
  const authEvents = [];

  global.WebSocket = FakeWebSocket;
  global.setTimeout = (fn, delay) => {
    const id = scheduled.length + 1;
    scheduled.push({ id, fn, delay, cleared: false });
    return id;
  };
  global.clearTimeout = (id) => {
    const t = scheduled.find((s) => s.id === id);
    if (t) t.cleared = true;
  };
  global.window = {
    location: { protocol: "http:", pathname: "/programmer/", host: "localhost:8080" },
    dispatchEvent: (ev) => {
      authEvents.push(ev.type);
      return true;
    },
  };
  global.CustomEvent = class {
    constructor(type) {
      this.type = type;
    }
  };
  // No stored credentials -> connect() opens without a subprotocol.
  global.sessionStorage = {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  };

  const moduleObj = { exports: {} };
  const fn = new Function(
    "exports", "require", "module", "__filename", "__dirname", code,
  );
  fn(moduleObj.exports, require, moduleObj, wsClientPath, path.dirname(wsClientPath));

  const pendingReconnects = () => scheduled.filter((s) => !s.cleared);
  return { ws: moduleObj.exports, scheduled, pendingReconnects, authEvents };
}

const results = {};

// Upper bound on simulated reconnect attempts (the client's retry budget is 3).
const MAX_TRIES = 5;

// --- H-116: an intentional disconnect() must not resurrect the socket ---
{
  const { ws, pendingReconnects, authEvents } = freshEnv();
  ws.connect();
  const sock = FakeWebSocket.instances[0];
  sock.fireOpen();
  ws.disconnect();
  // The browser fires onclose from socket.close() afterwards (normal 1000):
  sock.fireClose(1000);
  results.h116_disconnect_does_not_resurrect = {
    pass: pendingReconnects().length === 0 && authEvents.length === 0,
    detail: {
      reconnectsScheduled: pendingReconnects().length,
      authEvents: authEvents.length,
    },
  };
}

// --- M-166: sendQueue is cleared on disconnect (no stale replay onto hardware) ---
{
  const { ws } = freshEnv();
  // Queue a command while disconnected (no socket yet).
  ws.send({ type: "command", device: "proj1", action: "power_on" });
  ws.disconnect();
  ws.connect();
  const sock = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  sock.fireOpen(); // would flush any queued commands here
  results.m166_stale_command_not_replayed = {
    pass: sock.sent.length === 0,
    detail: { sent: sock.sent },
  };
}

// --- M-166: everConnected is reset on disconnect, re-enabling auth detection ---
// After a session that opened, a later connect() that keeps getting pre-open
// 1006s must still be able to conclude an auth failure. If everConnected is
// never reset it stays true forever and the 1006 path can never fire.
{
  const { ws, pendingReconnects, authEvents } = freshEnv();
  ws.connect();
  FakeWebSocket.instances[0].fireOpen(); // everConnected = true
  ws.disconnect(); // must reset everConnected = false
  ws.connect();
  for (let i = 0; i < MAX_TRIES; i++) {
    const sock = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    sock.fireClose(1006);
    if (authEvents.length > 0) break;
    const next = pendingReconnects().pop();
    if (next) next.fn(); // simulate the scheduled reconnect firing
  }
  results.m166_everconnected_reset_reenables_auth = {
    pass: authEvents.length === 1,
    detail: { authEvents: authEvents.length, attempts: FakeWebSocket.instances.length },
  };
}

// --- M-167: a transient pre-open 1006 retries with backoff, does NOT log out ---
{
  const { ws, pendingReconnects, authEvents } = freshEnv();
  ws.connect();
  FakeWebSocket.instances[0].fireClose(1006); // server still starting up
  results.m167_transient_1006_retries_not_logout = {
    pass: authEvents.length === 0 && pendingReconnects().length >= 1,
    detail: {
      authEvents: authEvents.length,
      reconnectsScheduled: pendingReconnects().length,
    },
  };
}

// --- M-167: a persistent pre-open 1006 logs out, but only after the retry budget ---
{
  const { ws, pendingReconnects, authEvents } = freshEnv();
  ws.connect();
  for (let i = 0; i < MAX_TRIES; i++) {
    const sock = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    sock.fireClose(1006);
    if (authEvents.length > 0) break;
    const next = pendingReconnects().pop();
    if (next) next.fn();
  }
  results.m167_persistent_1006_logs_out_after_threshold = {
    // Pre-fix: logs out on the very first attempt (1 instance). Post-fix: only
    // after MAX_PREOPEN_RETRIES connection attempts.
    pass: authEvents.length === 1 && FakeWebSocket.instances.length === 3,
    detail: { authEvents: authEvents.length, attempts: FakeWebSocket.instances.length },
  };
}

process.stdout.write(JSON.stringify(results));
