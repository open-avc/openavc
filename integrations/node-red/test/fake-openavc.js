"use strict";

// A stand-in OpenAVC that speaks the real WebSocket protocol and the four REST
// reads the editor makes. Enough to drive every node end to end without a
// Python process, and to make the server misbehave on purpose (drop clients,
// refuse a command, fail a macro, stall, rate-limit, demand a key).
//
// Like the real server it reads each connection's frames ONE AT A TIME and
// answers each before reading the next, which is the fact the connection's
// reply routing rests on.

const http = require("http");
const { WebSocketServer } = require("ws");
const { compile } = require("../lib/glob");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class FakeOpenAVC {
  constructor(opts = {}) {
    // legacy: behave like an OpenAVC that predates the event doors -- every
    // event.* frame is ignored in silence, which is what an unknown message
    // type gets.
    this.legacy = !!opts.legacy;
    // claimed: a password-protected system. REST answers 401 without the
    // key, and a programmer socket without it is refused at the upgrade.
    this.claimed = !!opts.claimed;
    this.apiKey = opts.apiKey || "k-123";
    // stallMs: every device command takes this long, holding the loop.
    this.stallMs = opts.stallMs || 0;
    // rateLimitAfter: the Nth frame on a connection gets the limiter's
    // error frame (no source_type) and is dropped, like the real one.
    this.rateLimitAfter = opts.rateLimitAfter || 0;
    // silent: send the snapshot and then nothing at all, not even pings.
    this.silent = !!opts.silent;
    this.state = { "var.request_source": "", "var.status": "idle", "device.switcher.online": true, "device.switcher.name": "Main Switcher", "device.switcher.connected": true, "device.projector.name": "Projector", "device.projector.connected": false, ...(opts.state || {}) };
    this.devices = opts.devices || [
      { id: "switcher", name: "Main Switcher", driver: "acme_switcher", connected: true },
      { id: "projector", name: "Projector", driver: "acme_projector", connected: false },
    ];
    // Command specs in the API's shape: params is a dict keyed by name.
    this.commands = opts.commands || {
      switcher: { route: { label: "Route", help: "Route an input to an output", params: { input: { type: "integer", required: true }, output: { type: "integer", required: true } } } },
      projector: { power_on: { label: "Power On", params: {} }, power_off: {} },
    };
    this.macros = opts.macros || [
      { id: "system_on", name: "System On" },
      { id: "bad", name: "Always fails" },
      { id: "skip_me", name: "Skips when running", overlap: "skip", ms: 200 },
      { id: "flaky", name: "One step fails" },
      { id: "slow", name: "Slow", ms: 400 },
    ];
    this.running = new Map(); // macro id -> [{timer, finish}]
    this.connections = []; // {ws, url, headers, role, patterns, name, frames}
    this.received = []; // every inbound frame, in order
    this.port = 0;
  }

  async start() {
    this.server = http.createServer((req, res) => this._rest(req, res));
    this.wss = new WebSocketServer({
      server: this.server,
      verifyClient: (info, cb) => {
        const url = new URL(info.req.url, "http://x");
        const key = info.req.headers["x-api-key"];
        if (this.claimed && url.searchParams.get("client") === "programmer" && key !== this.apiKey) {
          cb(false, 403, "Forbidden");
          return;
        }
        cb(true);
      },
    });
    this.wss.on("connection", (ws, req) => this._onConnection(ws, req));
    await new Promise((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    this.port = this.server.address().port;
    return this;
  }

  async stop() {
    for (const list of this.running.values()) for (const r of list) clearTimeout(r.timer);
    this.running.clear();
    for (const c of this.connections) c.ws.terminate();
    this.connections = [];
    await new Promise((resolve) => this.wss.close(() => resolve()));
    await new Promise((resolve) => this.server.close(() => resolve()));
  }

  // --- helpers a test drives the server with ---

  setState(key, value) {
    this.state[key] = value;
    this._broadcast({ type: "state.update", changes: { [key]: value } });
  }

  deleteState(key) {
    delete this.state[key];
    this._broadcast({ type: "state.delete", keys: [key] });
  }

  emitEvent(event, payload = {}) {
    for (const c of this.connections) {
      if (c.patterns.length && compile(c.patterns)(event)) {
        this._send(c.ws, { type: "event", event, payload, timestamp: Date.now() / 1000 });
      }
    }
  }

  ping() {
    this._broadcast({ type: "ping" });
  }

  dropClients(code = 1012) {
    for (const c of this.connections) c.ws.close(code, "restart");
  }

  framesOfType(type) {
    return this.received.filter((f) => f.type === type);
  }

  waitFor(predicate, timeoutMs = 2000) {
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const tick = () => {
        const hit = predicate();
        if (hit) return resolve(hit);
        if (Date.now() - started > timeoutMs) return reject(new Error("timed out waiting"));
        setTimeout(tick, 10);
      };
      tick();
    });
  }

  waitForConnections(n, timeoutMs) {
    return this.waitFor(() => (this.connections.length >= n ? this.connections : null), timeoutMs);
  }

  // --- the protocol ---

  _onConnection(ws, req) {
    const url = new URL(req.url, "http://x");
    const role = url.searchParams.get("client") || "panel";
    const conn = { ws, url: req.url, headers: req.headers, role, patterns: [], name: url.searchParams.get("name") || "", frames: 0, queue: Promise.resolve() };
    const events = url.searchParams.get("events");
    if (events) conn.patterns = events.split(",").map((s) => s.trim()).filter(Boolean);
    this.connections.push(conn);
    this.connectionsSeen = (this.connectionsSeen || 0) + 1;
    ws.on("close", () => {
      this.connections = this.connections.filter((c) => c !== conn);
    });
    ws.on("message", (data) => {
      const frame = JSON.parse(data);
      this.received.push(frame);
      // One at a time, in order, like the real server's message loop.
      conn.queue = conn.queue.then(() => this._onFrame(conn, frame)).catch(() => {});
    });
    this._send(ws, { type: "state.snapshot", state: { ...this.state } });
    if (!this.silent) this._send(ws, { type: "ui.definition", ui: { pages: [] } });
  }

  async _onFrame(conn, frame) {
    const ws = conn.ws;
    if (this.silent) return;
    conn.frames += 1;
    if (this.rateLimitAfter && conn.frames === this.rateLimitAfter) {
      this._send(ws, { type: "error", message: "Rate limit exceeded" });
      return;
    }
    if (this.legacy && String(frame.type).startsWith("event.")) return;
    switch (frame.type) {
      case "pong":
        break;
      case "event.subscribe":
        conn.patterns = frame.patterns || [];
        this._send(ws, { type: "event.subscribed", patterns: conn.patterns });
        break;
      case "event.unsubscribe":
        conn.patterns = [];
        this._send(ws, { type: "event.subscribed", patterns: [] });
        break;
      case "event.emit":
        if (!String(frame.event || "").startsWith("custom.")) {
          this._send(ws, {
            type: "error",
            source_type: "event.emit",
            message: "An event emitted over the API must be named 'custom.<name>'",
          });
          break;
        }
        this._send(ws, { type: "event.emit.ack", event: frame.event });
        this.emitEvent(frame.event, frame.payload || {});
        break;
      case "command":
        if (this.stallMs) await sleep(this.stallMs);
        if (frame.device_id === "projector") {
          this._send(ws, {
            type: "command.ack",
            device_id: frame.device_id,
            command: frame.command,
            success: false,
            error: "Could not connect to 'Projector'. Check that the device is powered on.",
          });
        } else {
          this._send(ws, { type: "command.ack", device_id: frame.device_id, command: frame.command, success: true });
        }
        break;
      case "state.set":
        if (conn.role === "panel" && !/^(var|plugin)\./.test(frame.key)) {
          this._send(ws, {
            type: "state.set.ack",
            key: frame.key,
            success: false,
            error: "Panel clients can only set keys under: var.*, plugin.*",
          });
          break;
        }
        this.state[frame.key] = frame.value;
        this._send(ws, { type: "state.set.ack", key: frame.key, success: true });
        this._broadcast({ type: "state.update", changes: { [frame.key]: frame.value } });
        break;
      case "macro.execute": {
        const id = frame.macro_id;
        const macro = this.macros.find((m) => m.id === id);
        if (!macro) {
          this._send(ws, { type: "error", source_type: "macro.execute", message: `No macro named '${id}'.` });
          break;
        }
        this._send(ws, { type: "macro.execute.ack", macro_id: id });
        this._runMacro(macro);
        break;
      }
      case "macro.cancel": {
        const id = frame.macro_id;
        if (!this.macros.some((m) => m.id === id)) {
          this._send(ws, { type: "error", source_type: "macro.cancel", message: `No macro named '${id}'.` });
          break;
        }
        const runs = this.running.get(id) || [];
        this._send(ws, { type: "macro.cancel.ack", macro_id: id, cancelled: runs.length > 0 });
        for (const r of runs.splice(0)) {
          clearTimeout(r.timer);
          this._broadcast({ type: "macro.cancelled", macro_id: id, name: id });
        }
        break;
      }
      default:
        break;
    }
  }

  _runMacro(macro) {
    const id = macro.id;
    const runs = this.running.get(id) || [];
    if (macro.overlap === "skip" && runs.length) {
      this._broadcast({ type: "macro.skipped", macro_id: id, name: macro.name, reason: "overlap=skip and an instance is already running" });
      return;
    }
    this._broadcast({ type: "macro.started", macro_id: id, name: macro.name, total_steps: 1 });
    const run = {};
    run.timer = setTimeout(() => {
      const list = this.running.get(id) || [];
      list.splice(list.indexOf(run), 1);
      if (id === "bad") {
        this._broadcast({ type: "macro.error", macro_id: id, name: macro.name, error: "step 1/1 failed" });
        return;
      }
      if (id === "flaky") {
        this._broadcast({ type: "macro.step_error", macro_id: id, call_chain: [id], step_index: 0, total_steps: 2, action: "device.command", device: "projector", group: null, command: "power_on", error: "Device 'projector' is not connected", message: "Projector is not connected." });
      }
      this._broadcast({ type: "macro.completed", macro_id: id, name: macro.name });
    }, macro.ms || 20);
    runs.push(run);
    this.running.set(id, runs);
  }

  _send(ws, frame) {
    if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(frame));
  }

  _broadcast(frame) {
    for (const c of this.connections) this._send(c.ws, frame);
  }

  // --- the REST reads the editor makes ---

  _rest(req, res) {
    const url = new URL(req.url, "http://x");
    const json = (code, body) => {
      res.writeHead(code, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body));
    };
    this.lastRestHeaders = req.headers;
    if (this.claimed && req.headers["x-api-key"] !== this.apiKey) return json(401, { detail: "Not authenticated" });
    if (url.pathname === "/api/devices") return json(200, { devices: this.devices });
    const m = url.pathname.match(/^\/api\/devices\/([^/]+)$/);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const dev = this.devices.find((d) => d.id === id);
      if (!dev) return json(404, { detail: "not found" });
      return json(200, { ...dev, commands: this.commands[id] || {} });
    }
    if (url.pathname === "/api/project") return json(200, { macros: this.macros });
    if (url.pathname === "/api/state") return json(200, { state: this.state });
    return json(404, { detail: "not found" });
  }
}

module.exports = { FakeOpenAVC };
