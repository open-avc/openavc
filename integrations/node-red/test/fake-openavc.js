"use strict";

// A stand-in OpenAVC that speaks the real WebSocket protocol and the four REST
// reads the editor makes. Enough to drive every node end to end without a
// Python process, and to make the server misbehave on purpose (drop clients,
// refuse a command, fail a macro).

const http = require("http");
const { WebSocketServer } = require("ws");
const { compile } = require("../lib/glob");

class FakeOpenAVC {
  constructor(opts = {}) {
    this.state = { "var.request_source": "", "var.status": "idle", "device.switcher.online": true, ...(opts.state || {}) };
    this.devices = opts.devices || [
      { id: "switcher", name: "Main Switcher", driver: "acme_switcher", connected: true },
      { id: "projector", name: "Projector", driver: "acme_projector", connected: false },
    ];
    this.commands = opts.commands || {
      switcher: { route: { description: "Route an input to an output", params: [{ name: "input" }, { name: "output" }] } },
      projector: { power_on: { description: "Power on" }, power_off: {} },
    };
    this.macros = opts.macros || [{ id: "system_on", name: "System On" }, { id: "bad", name: "Always fails" }];
    this.connections = []; // {ws, url, headers, role, patterns}
    this.received = []; // every inbound frame, in order
    this.port = 0;
  }

  async start() {
    this.server = http.createServer((req, res) => this._rest(req, res));
    this.wss = new WebSocketServer({ server: this.server });
    this.wss.on("connection", (ws, req) => this._onConnection(ws, req));
    await new Promise((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    this.port = this.server.address().port;
    return this;
  }

  async stop() {
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
    const conn = { ws, url: req.url, headers: req.headers, role, patterns: [] };
    const events = url.searchParams.get("events");
    if (events) conn.patterns = events.split(",").map((s) => s.trim()).filter(Boolean);
    this.connections.push(conn);
    ws.on("close", () => {
      this.connections = this.connections.filter((c) => c !== conn);
    });
    ws.on("message", (data) => this._onFrame(conn, JSON.parse(data)));
    this._send(ws, { type: "state.snapshot", state: { ...this.state } });
    this._send(ws, { type: "ui.definition", ui: { pages: [] } });
  }

  _onFrame(conn, frame) {
    this.received.push(frame);
    const ws = conn.ws;
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
        if (!this.macros.some((m) => m.id === id)) {
          this._send(ws, { type: "error", source_type: "macro.execute", message: `No macro named '${id}'.` });
          break;
        }
        this._send(ws, { type: "macro.execute.ack", macro_id: id });
        setTimeout(() => {
          if (id === "bad") {
            this._broadcast({ type: "macro.error", macro_id: id, name: "Always fails", error: "step 1/1 failed" });
          } else {
            this._broadcast({ type: "macro.completed", macro_id: id, name: id });
          }
        }, 20);
        break;
      }
      default:
        break;
    }
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
