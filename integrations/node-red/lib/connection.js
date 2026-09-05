"use strict";

const EventEmitter = require("events");
const http = require("http");
const https = require("https");
const WebSocket = require("ws");

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const REQUEST_TIMEOUT_MS = 10000;
// How long a subscription may go unanswered before the server is taken to be
// older than the event doors (0.33). An OpenAVC that predates them ignores an
// unknown message type in silence, so silence is the only signal there is.
const LEGACY_DETECT_MS = 5000;

// Close codes the server uses on purpose. Anything else is the network.
const CLOSE_REASONS = {
  4001: "the server refused the API key",
  4003: "the server did not recognise the client type",
  1011: "the server's engine has not finished starting",
  1013: "the server has too many connections open",
};

/**
 * One WebSocket to one OpenAVC instance, shared by every node on that server
 * config. It mirrors the state stream, forwards events, routes each reply
 * back to the request that asked for it, and reconnects on its own.
 *
 * The role is derived from the credential, not chosen: an API key connects as
 * `programmer` (what the Programmer can do), none connects as `panel` (what a
 * panel can do). The server decides what each may see and write.
 */
class OpenAVCConnection extends EventEmitter {
  constructor(opts) {
    super();
    this.host = opts.host;
    this.port = Number(opts.port) || 8080;
    this.tls = !!opts.tls;
    this.tlsVerify = !!opts.tlsVerify;
    this.apiKey = opts.apiKey || "";
    // What this connection announces itself as. The server holds up
    // `system.integration.<name>.connected` while the socket is open, so a
    // panel LED or a monitored reading can say whether the flow is alive.
    this.name = String(opts.name || "").trim();
    this.logger = opts.logger || { log() {}, warn() {}, error() {} };
    this.role = this.apiKey ? "programmer" : "panel";

    this.state = {};
    this.status = "disconnected"; // connecting | connected | disconnected
    this._ws = null;
    this._closed = false;
    this._backoff = RECONNECT_MIN_MS;
    this._reconnectTimer = null;

    // owner id -> patterns. The server holds ONE subscription per socket, so
    // every node's patterns are unioned into it and re-sent as a replace.
    this._patterns = new Map();
    this._subscribeTimer = null;
    this._lastUnion = [];
    this._legacyTimer = null;
    // Set once a subscription goes unanswered; cleared on the next open.
    this.legacy = false;

    // request key -> [{resolve, reject, timer}], oldest first
    this._pending = new Map();
  }

  get url() {
    const base = `${this.tls ? "wss" : "ws"}://${this.host}:${this.port}/ws?client=${this.role}`;
    return this.name ? `${base}&name=${encodeURIComponent(this.name)}` : base;
  }

  get baseUrl() {
    return `${this.tls ? "https" : "http"}://${this.host}:${this.port}`;
  }

  headers() {
    return this.apiKey ? { "X-API-Key": this.apiKey } : {};
  }

  // --- lifecycle ---

  connect() {
    this._closed = false;
    this._open();
  }

  close() {
    this._closed = true;
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = null;
    clearTimeout(this._legacyTimer);
    this._legacyTimer = null;
    const ws = this._ws;
    this._ws = null;
    if (ws) {
      ws.removeAllListeners();
      try {
        ws.close();
      } catch (_e) {
        // already gone
      }
    }
    this._rejectAll("connection closed");
    this._setStatus("disconnected");
  }

  _open() {
    if (this._closed || this._ws) return;
    this._setStatus("connecting");
    const ws = new WebSocket(this.url, {
      headers: this.headers(),
      rejectUnauthorized: this.tlsVerify,
    });
    this._ws = ws;
    ws.on("open", () => {
      this._backoff = RECONNECT_MIN_MS;
      this.legacy = false;
      this._setStatus("connected");
      this._sendSubscribe(true);
      this.emit("open");
    });
    ws.on("message", (data) => this._onMessage(data));
    ws.on("close", (code, reason) => this._onClose(ws, code, reason));
    ws.on("error", (err) => {
      // A close follows every error; log here, reconnect there.
      this.logger.warn(`OpenAVC ${this.host}:${this.port}: ${err.message}`);
    });
  }

  _onClose(ws, code, reason) {
    if (ws !== this._ws) return; // a socket close() already let go of
    this._ws = null;
    clearTimeout(this._legacyTimer);
    this._legacyTimer = null;
    this._setStatus("disconnected");
    this._rejectAll(`disconnected from OpenAVC (${code})`);
    this.emit("close", code, String(reason || ""));
    if (this._closed) return;
    const why = CLOSE_REASONS[code];
    if (why) this.logger.error(`OpenAVC ${this.host}:${this.port}: ${why} (${code})`);
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this._open();
    }, this._backoff);
    this._backoff = Math.min(this._backoff * 2, RECONNECT_MAX_MS);
  }

  _setStatus(status) {
    if (status === this.status) return;
    this.status = status;
    this.emit("status", status);
  }

  _send(frame) {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return false;
    this._ws.send(JSON.stringify(frame));
    return true;
  }

  // --- inbound ---

  _onMessage(data) {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch (_e) {
      return;
    }
    switch (msg.type) {
      case "ping":
        this._send({ type: "pong" });
        break;
      case "state.snapshot":
        this.state = { ...(msg.state || {}) };
        this.emit("snapshot", this.state);
        break;
      case "state.update":
        for (const [key, value] of Object.entries(msg.changes || {})) {
          this.state[key] = value;
          this.emit("state", key, value, false);
        }
        break;
      case "state.delete":
        for (const key of msg.keys || []) {
          delete this.state[key];
          this.emit("state", key, undefined, true);
        }
        break;
      case "event":
        this.emit("event", msg.event, msg.payload || {}, msg.timestamp);
        break;
      case "event.subscribed":
        clearTimeout(this._legacyTimer);
        this._legacyTimer = null;
        this.emit("subscribed", msg.patterns || []);
        break;
      case "command.ack":
        this._settle(
          `command:${msg.device_id}:${msg.command}`,
          msg.success ? { success: true } : { success: false, error: msg.error || "command failed" }
        );
        break;
      case "state.set.ack":
        this._settle(
          `set:${msg.key}`,
          msg.success ? { success: true } : { success: false, error: msg.error || "state write refused" }
        );
        break;
      case "event.emit.ack":
        this._settle(`emit:${msg.event}`, { success: true });
        break;
      case "macro.execute.ack":
        // A receipt. The outcome arrives as a lifecycle event below.
        break;
      case "macro.completed":
        this._settle(`macro:${msg.macro_id}`, { success: true });
        break;
      case "macro.error":
        this._settle(`macro:${msg.macro_id}`, { success: false, error: msg.error || "macro failed" });
        break;
      case "macro.cancelled":
        this._settle(`macro:${msg.macro_id}`, { success: false, error: "macro cancelled" });
        break;
      case "error":
        this._onError(msg);
        break;
      default:
        break;
    }
  }

  // An error frame names the inbound message type that failed, not which
  // request -- so it settles the OLDEST request of that kind. Requests of one
  // kind are answered in order, which is what makes that correct.
  _onError(msg) {
    const prefix = {
      command: "command:",
      "state.set": "set:",
      "event.emit": "emit:",
      "macro.execute": "macro:",
    }[msg.source_type];
    const result = { success: false, error: msg.message || "OpenAVC refused the request" };
    if (prefix && this._settleOldest(prefix, result)) return;
    this.emit("error-frame", msg);
  }

  // --- requests ---

  _request(key, frame, timeoutMs = REQUEST_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
      if (!this._send(frame)) {
        reject(new Error("not connected to OpenAVC"));
        return;
      }
      const entry = { resolve, reject, timer: null };
      if (timeoutMs > 0) {
        entry.timer = setTimeout(() => {
          this._remove(key, entry);
          reject(new Error(`no reply from OpenAVC within ${timeoutMs / 1000}s`));
        }, timeoutMs);
      }
      const list = this._pending.get(key) || [];
      list.push(entry);
      this._pending.set(key, list);
    });
  }

  _settle(key, result) {
    const list = this._pending.get(key);
    if (!list || !list.length) return false;
    const entry = list.shift();
    if (!list.length) this._pending.delete(key);
    clearTimeout(entry.timer);
    entry.resolve(result);
    return true;
  }

  _settleOldest(prefix, result) {
    for (const key of this._pending.keys()) {
      if (key.startsWith(prefix)) return this._settle(key, result);
    }
    return false;
  }

  _remove(key, entry) {
    const list = this._pending.get(key);
    if (!list) return;
    const idx = list.indexOf(entry);
    if (idx >= 0) list.splice(idx, 1);
    if (!list.length) this._pending.delete(key);
  }

  _rejectAll(reason) {
    for (const list of this._pending.values()) {
      for (const entry of list) {
        clearTimeout(entry.timer);
        entry.reject(new Error(reason));
      }
    }
    this._pending.clear();
  }

  sendCommand(deviceId, command, params) {
    return this._request(`command:${deviceId}:${command}`, {
      type: "command",
      device_id: deviceId,
      command,
      params: params || {},
    });
  }

  // No timeout: a macro with delay steps runs for as long as it runs. The
  // request is rejected if the socket drops before the macro finishes.
  executeMacro(macroId) {
    return this._request(`macro:${macroId}`, { type: "macro.execute", macro_id: macroId }, 0);
  }

  setState(key, value) {
    return this._request(`set:${key}`, { type: "state.set", key, value });
  }

  emitEvent(event, payload) {
    if (this.legacy) {
      return Promise.reject(new Error("this OpenAVC is older than 0.33 and cannot receive events"));
    }
    return this._request(`emit:${event}`, { type: "event.emit", event, payload: payload || {} });
  }

  // --- event subscription ---

  subscribeEvents(ownerId, patterns) {
    this._patterns.set(ownerId, patterns);
    this._scheduleSubscribe();
  }

  unsubscribeEvents(ownerId) {
    this._patterns.delete(ownerId);
    this._scheduleSubscribe();
  }

  eventPatterns() {
    const union = [];
    const seen = new Set();
    for (const patterns of this._patterns.values()) {
      for (const p of patterns) {
        if (!seen.has(p)) {
          seen.add(p);
          union.push(p);
        }
      }
    }
    return union;
  }

  // Coalesce the burst of registrations a deploy produces into one replace.
  _scheduleSubscribe() {
    if (this._subscribeTimer) return;
    this._subscribeTimer = setImmediate(() => {
      this._subscribeTimer = null;
      this._sendSubscribe(false);
    });
  }

  _sendSubscribe(onOpen) {
    const union = this.eventPatterns();
    if (union.length) {
      if (this._send({ type: "event.subscribe", patterns: union })) this._armLegacyTimer();
    } else if (!onOpen && this._lastUnion.length) {
      this._send({ type: "event.unsubscribe" });
    }
    this._lastUnion = union;
  }

  _armLegacyTimer() {
    if (this._legacyTimer || this.legacy) return;
    this._legacyTimer = setTimeout(() => {
      this._legacyTimer = null;
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;
      this.legacy = true;
      this.logger.warn(
        `OpenAVC ${this.host}:${this.port} did not answer the event subscription: ` +
          "it is older than 0.33, so event in and emit event will hear and reach nothing " +
          "until it is updated. State, commands, macros and variables still work."
      );
      this.emit("legacy");
    }, LEGACY_DETECT_MS);
  }

  // --- REST, for the editor's lookups ---

  fetchJson(path) {
    const url = new URL(path, this.baseUrl);
    const mod = this.tls ? https : http;
    return new Promise((resolve, reject) => {
      const req = mod.get(
        url,
        { headers: { ...this.headers(), Accept: "application/json" }, rejectUnauthorized: this.tlsVerify },
        (res) => {
          const chunks = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => {
            const body = Buffer.concat(chunks).toString("utf8");
            if (res.statusCode < 200 || res.statusCode >= 300) {
              reject(new Error(`${res.statusCode} from ${url.pathname}`));
              return;
            }
            try {
              resolve(JSON.parse(body));
            } catch (e) {
              reject(new Error(`${url.pathname} did not return JSON`));
            }
          });
        }
      );
      req.on("error", reject);
    });
  }
}

module.exports = {
  OpenAVCConnection,
  RECONNECT_MIN_MS,
  RECONNECT_MAX_MS,
  REQUEST_TIMEOUT_MS,
  LEGACY_DETECT_MS,
};
