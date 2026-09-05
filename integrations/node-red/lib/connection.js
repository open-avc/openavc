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
// The server sends a ping every 30 s. A socket that has carried nothing for
// three of those is not a quiet room, it is a dead TCP connection nobody
// closed (a rebooted box, a NAT that forgot us): terminate it and reconnect.
const SILENCE_MS = 90000;

// Close codes the server uses on purpose. Anything else is the network.
const CLOSE_REASONS = {
  4001: "the server refused the API key",
  4003: "the server did not recognise the client type",
  1011: "the server's engine has not finished starting",
  1013: "the server has too many connections open",
};

// The server refuses a bad credential at the HTTP upgrade, before there is a
// socket to close, so it arrives as a status code rather than a close code.
const HTTP_REASONS = {
  401: "the server refused the API key",
  403: "the server refused the API key",
};

// A request kind -> the message type the server names in an error frame that
// refuses it.
const KIND_TYPES = {
  command: "command",
  set: "state.set",
  emit: "event.emit",
  macro: "macro.execute",
  cancel: "macro.cancel",
};

/**
 * One WebSocket to one OpenAVC instance, shared by every node on that server
 * config. It mirrors the state stream, forwards events, routes each reply
 * back to the request that asked for it, and reconnects on its own.
 *
 * The role is derived from the credential, not chosen: an API key connects as
 * `programmer` (what the Programmer can do), none connects as `panel` (what a
 * panel can do). The server decides what each may see and write.
 *
 * Reply routing rests on one fact about the server: it reads a client's
 * frames one at a time and answers each before reading the next, so receipts
 * (`command.ack`, `state.set.ack`, `event.emit.ack`, `macro.execute.ack`,
 * `macro.cancel.ack`, and the `error` frame that refuses any of them) arrive
 * in the order the requests were sent. Every request therefore joins one
 * queue, and each receipt settles its head. A macro's receipt only says the
 * run was accepted; its outcome (`macro.completed`, `macro.error`,
 * `macro.cancelled`, `macro.skipped`) arrives whenever the run ends, out of
 * order, so an accepted macro moves to a second table keyed by macro id.
 */
class OpenAVCConnection extends EventEmitter {
  constructor(opts) {
    super();
    this.host = opts.host;
    this.port = Number(opts.port) || 8080;
    this.tls = !!opts.tls;
    this.tlsVerify = !!opts.tlsVerify;
    // Extra TLS options (ca, cert, key, servername, rejectUnauthorized) from a
    // Node-RED tls-config node; applied over tlsVerify.
    this.tlsOptions = opts.tlsOptions || null;
    this.apiKey = opts.apiKey || "";
    // What this connection announces itself as. The server holds up
    // `system.integration.<name>.connected` while the socket is open, so a
    // panel LED or a monitored reading can say whether the flow is alive.
    this.name = String(opts.name || "").trim();
    this.logger = opts.logger || { log() {}, warn() {}, error() {} };
    this.silenceMs = opts.silenceMs || SILENCE_MS;
    this.role = this.apiKey ? "programmer" : "panel";

    this.state = {};
    this.status = "disconnected"; // connecting | connected | disconnected
    // Why the last connect attempt failed, in words a person can act on.
    this.detail = "";
    this._ws = null;
    this._closed = false;
    this._backoff = RECONNECT_MIN_MS;
    this._reconnectTimer = null;
    this._watchdog = null;
    this._lastFrameAt = 0;
    this._lastLogged = "";

    // owner id -> patterns. The server holds ONE subscription per socket, so
    // every node's patterns are unioned into it and re-sent as a replace.
    this._patterns = new Map();
    this._subscribeTimer = null;
    this._lastUnion = [];
    this._legacyTimer = null;
    this._subscribeSeq = 0;
    this._subscribeAnswered = true;
    // Set once a subscription goes unanswered; cleared on the next open.
    this.legacy = false;

    // Requests awaiting their receipt, oldest first. A request that timed
    // out stays here as a tombstone until its reply arrives, so a late reply
    // to it cannot be mistaken for the reply to the request behind it.
    this._receipts = [];
    // macro id -> accepted macro requests awaiting their outcome, oldest first.
    this._outcomes = new Map();
    this._seq = 0;
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

  _tlsOpts() {
    return { rejectUnauthorized: this.tlsVerify, ...(this.tlsOptions || {}) };
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
    this._stopWatchdog();
    const ws = this._ws;
    this._ws = null;
    if (ws) {
      ws.removeAllListeners();
      // Closing a socket still mid-handshake makes `ws` emit an error on the
      // next tick; with nobody listening that is an uncaught exception in
      // the middle of a deploy.
      ws.on("error", () => {});
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
    const ws = new WebSocket(this.url, { headers: this.headers(), ...this._tlsOpts() });
    this._ws = ws;
    ws.on("open", () => {
      this._backoff = RECONNECT_MIN_MS;
      this.legacy = false;
      this.detail = "";
      this._lastLogged = "";
      this._lastFrameAt = Date.now();
      this._startWatchdog(ws);
      this._setStatus("connected");
      this._sendSubscribe(true);
      this.emit("open");
    });
    ws.on("message", (data) => {
      this._lastFrameAt = Date.now();
      this._onMessage(data);
    });
    ws.on("close", (code, reason) => this._onClose(ws, code, reason));
    ws.on("error", (err) => {
      // A close follows every error; remember why here, report there.
      const status = /Unexpected server response: (\d+)/.exec(err.message || "");
      this.detail = status ? HTTP_REASONS[status[1]] || `HTTP ${status[1]} from the server` : err.message;
    });
  }

  _onClose(ws, code, reason) {
    if (ws !== this._ws) return; // a socket close() already let go of
    this._ws = null;
    clearTimeout(this._legacyTimer);
    this._legacyTimer = null;
    this._stopWatchdog();
    const why = CLOSE_REASONS[code] || this.detail || "";
    this.detail = why;
    this._setStatus("disconnected");
    this._rejectAll(`disconnected from OpenAVC${why ? ` (${why})` : ` (${code})`}`);
    this.emit("close", code, String(reason || ""));
    if (this._closed) return;
    // Say why once, not once per retry: a wrong key is refused every attempt.
    if (why && why !== this._lastLogged) {
      this._lastLogged = why;
      this.logger.error(`OpenAVC ${this.host}:${this.port}: ${why}`);
    }
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this._open();
    }, this._backoff);
    this._backoff = Math.min(this._backoff * 2, RECONNECT_MAX_MS);
  }

  _startWatchdog(ws) {
    this._stopWatchdog();
    const interval = Math.max(1000, Math.floor(this.silenceMs / 3));
    this._watchdog = setInterval(() => {
      if (ws !== this._ws) return;
      if (Date.now() - this._lastFrameAt < this.silenceMs) return;
      this.detail = `nothing heard for ${Math.round(this.silenceMs / 1000)}s`;
      this.logger.warn(`OpenAVC ${this.host}:${this.port}: ${this.detail}, reconnecting`);
      ws.terminate(); // the close event does the rest
    }, interval);
    if (this._watchdog.unref) this._watchdog.unref();
  }

  _stopWatchdog() {
    clearInterval(this._watchdog);
    this._watchdog = null;
  }

  _setStatus(status) {
    if (status === this.status) return;
    this.status = status;
    this.emit("status", status, this.detail);
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
        this._subscribeAnswered = true;
        clearTimeout(this._legacyTimer);
        this._legacyTimer = null;
        this.emit("subscribed", msg.patterns || []);
        break;
      case "command.ack":
        this._receipt("command", `${msg.device_id}:${msg.command}`, msg.success ? { success: true } : { success: false, error: msg.error || "command failed" });
        break;
      case "state.set.ack":
        this._receipt("set", msg.key, msg.success ? { success: true } : { success: false, error: msg.error || "state write refused" });
        break;
      case "event.emit.ack":
        this._receipt("emit", msg.event, { success: true });
        break;
      case "macro.execute.ack": {
        // Accepted, not finished: the outcome arrives as a lifecycle frame.
        const entry = this._receipt("macro", msg.macro_id, null);
        if (entry && !entry.done) {
          const list = this._outcomes.get(msg.macro_id) || [];
          list.push(entry);
          this._outcomes.set(msg.macro_id, list);
        }
        break;
      }
      case "macro.cancel.ack":
        this._receipt("cancel", msg.macro_id, { success: true, cancelled: !!msg.cancelled });
        break;
      case "macro.step_error":
        this._noteStepError(msg);
        break;
      case "macro.completed":
        this._outcome(msg.macro_id, { success: true });
        break;
      case "macro.error":
        this._outcome(msg.macro_id, { success: false, error: msg.error || "macro failed" });
        break;
      case "macro.cancelled":
        this._outcome(msg.macro_id, { success: false, error: "macro cancelled" });
        break;
      case "macro.skipped":
        // A skip is the macro's guard turning away the LATEST ask while an
        // earlier run is still going (overlap) or has just gone (cooldown),
        // so it belongs to the newest accepted request, not the oldest.
        this._outcome(msg.macro_id, { success: false, error: `macro skipped: ${msg.reason || "its own overlap or cooldown setting"}` }, true);
        break;
      case "error":
        this._onError(msg);
        break;
      default:
        break;
    }
  }

  // An error frame that refuses a request names the message type it refused
  // and nothing else; the server answers in order, so it is the oldest
  // request still awaiting its receipt. One with no source_type is the rate
  // limiter, which fires before the frame is even parsed -- same request.
  // Anything that does not fit the head (a failure the server reports
  // outside the request order) is handed to the server node to log.
  _onError(msg) {
    const head = this._receipts[0];
    const fits = head && (!msg.source_type || KIND_TYPES[head.kind] === msg.source_type);
    if (fits) {
      this._receipts.shift();
      this._settle(head, { success: false, error: msg.message || "OpenAVC refused the request" });
      return;
    }
    this.emit("error-frame", msg);
  }

  // --- requests ---

  _request(kind, key, frame, timeoutMs = REQUEST_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
      if (!this._send(frame)) {
        reject(new Error(`not connected to OpenAVC${this.detail ? ` (${this.detail})` : ""}`));
        return;
      }
      const entry = { kind, key, seq: ++this._seq, resolve, reject, timer: null, done: false, stepErrors: [] };
      if (timeoutMs > 0) {
        entry.timer = setTimeout(() => {
          // Left in place as a tombstone: the reply still arrives in order.
          this._fail(entry, new Error(`no reply from OpenAVC within ${timeoutMs / 1000}s`));
        }, timeoutMs);
      }
      this._receipts.push(entry);
    });
  }

  // Take the head of the receipt queue as the reply to `kind`/`key`, settling
  // it with `result` (or, for null, handing it back for a second stage). A
  // head that does not match is a reply for something this queue no longer
  // holds (never, given an in-order server; guarded anyway): the frame is
  // dropped and the head left for its own reply.
  _receipt(kind, key, result) {
    const head = this._receipts[0];
    if (!head || head.kind !== kind || head.key !== key) {
      this.logger.warn(`OpenAVC ${this.host}:${this.port}: unexpected ${kind} receipt for ${key}`);
      return null;
    }
    this._receipts.shift();
    if (result !== null) this._settle(head, result);
    if (!this._subscribeAnswered && head.seq > this._subscribeSeq) this._declareLegacy();
    return head;
  }

  _outcome(macroId, result, newest = false) {
    const list = this._outcomes.get(macroId);
    if (!list || !list.length) return;
    const entry = newest ? list.pop() : list.shift();
    if (!list.length) this._outcomes.delete(macroId);
    if (entry.stepErrors.length) result = { ...result, step_errors: entry.stepErrors };
    this._settle(entry, result);
  }

  _noteStepError(msg) {
    const list = this._outcomes.get(msg.macro_id);
    if (!list || !list.length) return;
    list[0].stepErrors.push({
      step: (msg.step_index ?? -1) + 1,
      action: msg.action,
      device: msg.device || msg.group || "",
      command: msg.command || "",
      error: msg.message || msg.error || "step failed",
    });
  }

  _settle(entry, result) {
    if (entry.done) return;
    entry.done = true;
    clearTimeout(entry.timer);
    entry.resolve(result);
  }

  _fail(entry, err) {
    if (entry.done) return;
    entry.done = true;
    clearTimeout(entry.timer);
    entry.reject(err);
  }

  _rejectAll(reason) {
    for (const entry of this._receipts) this._fail(entry, new Error(reason));
    this._receipts = [];
    for (const list of this._outcomes.values()) {
      for (const entry of list) this._fail(entry, new Error(reason));
    }
    this._outcomes.clear();
  }

  sendCommand(deviceId, command, params) {
    return this._request("command", `${deviceId}:${command}`, {
      type: "command",
      device_id: deviceId,
      command,
      params: params || {},
    });
  }

  // No timeout on the outcome: a macro with delay steps runs for as long as
  // it runs. The request is rejected if the socket drops before it finishes.
  executeMacro(macroId) {
    return this._request("macro", macroId, { type: "macro.execute", macro_id: macroId }, 0);
  }

  cancelMacro(macroId) {
    return this._request("cancel", macroId, { type: "macro.cancel", macro_id: macroId });
  }

  setState(key, value) {
    return this._request("set", key, { type: "state.set", key, value });
  }

  emitEvent(event, payload) {
    if (this.legacy) {
      return Promise.reject(new Error("this OpenAVC is older than 0.33 and cannot receive events"));
    }
    return this._request("emit", event, { type: "event.emit", event, payload: payload || {} });
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
      if (this._send({ type: "event.subscribe", patterns: union })) {
        this._subscribeSeq = ++this._seq;
        this._subscribeAnswered = false;
        this._armLegacyTimer();
      }
    } else if (!onOpen && this._lastUnion.length) {
      this._send({ type: "event.unsubscribe" });
    }
    this._lastUnion = union;
  }

  // Silence is the only signal an old server gives, and a busy new one is
  // silent too: it answers frames one at a time, so a subscription sent
  // behind a slow device command waits its turn. The timer therefore only
  // convicts when nothing older than the subscription is still waiting;
  // a receipt for something sent AFTER it convicts at once (the server read
  // past the subscription and said nothing).
  _armLegacyTimer() {
    if (this._legacyTimer || this.legacy) return;
    this._legacyTimer = setTimeout(() => {
      this._legacyTimer = null;
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN || this._subscribeAnswered) return;
      const stalled = this._receipts.some((e) => e.seq < this._subscribeSeq);
      if (stalled) {
        this._armLegacyTimer();
        return;
      }
      this._declareLegacy();
    }, LEGACY_DETECT_MS);
  }

  _declareLegacy() {
    if (this.legacy) return;
    clearTimeout(this._legacyTimer);
    this._legacyTimer = null;
    this.legacy = true;
    this.logger.warn(
      `OpenAVC ${this.host}:${this.port} did not answer the event subscription: ` +
        "it is older than 0.33, so event in and emit event will hear and reach nothing " +
        "until it is updated. State, commands, macros and variables still work."
    );
    this.emit("legacy");
  }

  // --- REST, for the editor's lookups ---

  fetchJson(path) {
    const url = new URL(path, this.baseUrl);
    const mod = this.tls ? https : http;
    return new Promise((resolve, reject) => {
      const req = mod.get(
        url,
        { headers: { ...this.headers(), Accept: "application/json" }, ...this._tlsOpts() },
        (res) => {
          const chunks = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => {
            const body = Buffer.concat(chunks).toString("utf8");
            if (res.statusCode < 200 || res.statusCode >= 300) {
              const err = new Error(`${res.statusCode} from ${url.pathname}`);
              err.statusCode = res.statusCode;
              reject(err);
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
  SILENCE_MS,
};
