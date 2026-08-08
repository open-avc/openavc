import { AUTH_REQUIRED_EVENT, clearSession, getAuthSubprotocols } from "./auth";

type MessageHandler = (msg: Record<string, unknown>) => void;
type LifecycleHandler = () => void;

let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 2000;
const MAX_RECONNECT_DELAY = 15000;
let handlers: MessageHandler[] = [];
let connectHandlers: LifecycleHandler[] = [];
let disconnectHandlers: LifecycleHandler[] = [];
/** True once any WS attempt has reached the OPEN state. Used to distinguish
 *  "server rejected us" (probably auth) from "connection dropped mid-session". */
let everConnected = false;

/** Consecutive pre-open 1006 closes. A 1006 before we ever open is ambiguous
 *  (proxy-level 401 vs. a server that isn't up yet); we retry with backoff and
 *  only conclude an auth failure once it persists, so a slow-starting server
 *  doesn't wipe valid credentials. */
let preOpenFailures = 0;
const MAX_PREOPEN_RETRIES = 3;

/** Queue for messages sent while disconnected (commands only, capped). */
const MAX_SEND_QUEUE = 50;
let sendQueue: Record<string, unknown>[] = [];

function getWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  // Derive WS path relative to page so tunneled remote access works.
  // /tunnel/{id}/programmer/ → /tunnel/{id}/ws
  // /programmer/ → /ws
  const pathParts = window.location.pathname.split("/programmer");
  const basePath = pathParts[0] || "";
  return `${proto}//${window.location.host}${basePath}/ws?client=programmer`;
}

/** Clear cached credentials and ask the App to show the login screen. */
function requestLogin(code: number): void {
  console.warn(`[WS] Connection rejected (code ${code}); requesting login`);
  clearSession();
  window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
}

export function connect(): void {
  if (socket && socket.readyState <= WebSocket.OPEN) return;

  // Pass the session token as a Sec-WebSocket-Protocol subprotocol so the
  // server can authenticate the upgrade request — browsers can't attach
  // Authorization headers to WebSockets.
  const protocols = getAuthSubprotocols();
  socket = protocols
    ? new WebSocket(getWsUrl(), protocols)
    : new WebSocket(getWsUrl());

  socket.onopen = () => {
    console.log("[WS] Connected");
    everConnected = true;
    preOpenFailures = 0;
    reconnectDelay = 2000; // Reset backoff on successful connect
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    // Flush queued messages
    for (const queued of sendQueue) {
      try {
        socket!.send(JSON.stringify(queued));
      } catch {
        break;
      }
    }
    sendQueue = [];
    for (const handler of connectHandlers) {
      handler();
    }
  };

  socket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      // Respond to server heartbeat
      if (msg.type === "ping") {
        send({ type: "pong" });
        return;
      }
      for (const handler of handlers) {
        handler(msg);
      }
    } catch {
      console.warn("[WS] Failed to parse message", event.data);
    }
  };

  socket.onclose = (ev) => {
    socket = null;
    for (const handler of disconnectHandlers) {
      handler();
    }
    // Auth rejection: the server returns 4001 after accepting the upgrade, or
    // (behind a proxy that authenticates at the HTTP layer) rejects the upgrade
    // with 401, which the browser surfaces as 1006 (abnormal close, no message).
    // 4001 is unambiguous — bounce to login immediately. A pre-open 1006 is NOT:
    // it's equally the symptom of a server still starting up or a transient
    // blip, so retry with backoff and only treat persistent pre-open 1006s as an
    // auth failure (otherwise a slow-starting server wipes valid credentials).
    if (ev.code === 4001) {
      requestLogin(ev.code);
      return;
    }
    if (ev.code === 1006 && everConnected === false) {
      preOpenFailures += 1;
      if (preOpenFailures >= MAX_PREOPEN_RETRIES) {
        requestLogin(ev.code);
        return;
      }
    }
    console.log(`[WS] Disconnected (code ${ev.code}), reconnecting in ${reconnectDelay / 1000}s...`);
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT_DELAY);
  };

  socket.onerror = () => {
    socket?.close();
  };
}

/** Register a handler called each time the WebSocket opens (including reconnects). */
export function onConnect(handler: LifecycleHandler): () => void {
  connectHandlers.push(handler);
  return () => {
    connectHandlers = connectHandlers.filter((h) => h !== handler);
  };
}

/** Register a handler called each time the WebSocket closes. */
export function onDisconnect(handler: LifecycleHandler): () => void {
  disconnectHandlers.push(handler);
  return () => {
    disconnectHandlers = disconnectHandlers.filter((h) => h !== handler);
  };
}

export function disconnect(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (socket) {
    // Detach handlers BEFORE closing so the imminent (async) onclose can't
    // reschedule a reconnect and resurrect the connection we just tore down —
    // or, on a logout-then-relogin, null out a freshly-created socket.
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    socket.close();
    socket = null;
  }
  // Reset per-session state so a later connect() starts fresh: a genuine auth
  // failure on the next first-open is detectable again (everConnected /
  // preOpenFailures), backoff is restored, and stale queued commands from this
  // session aren't replayed onto AV hardware (sendQueue).
  everConnected = false;
  preOpenFailures = 0;
  reconnectDelay = 2000;
  sendQueue = [];
}

export function onMessage(handler: MessageHandler): () => void {
  handlers.push(handler);
  return () => {
    handlers = handlers.filter((h) => h !== handler);
  };
}

/** Command types worth queuing during disconnect (stale state updates aren't). */
const QUEUEABLE_TYPES = new Set([
  "command", "macro.execute", "state.set",
]);

export function send(msg: Record<string, unknown>): void {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(msg));
  } else if (typeof msg.type === "string" && QUEUEABLE_TYPES.has(msg.type)) {
    // Queue command-type messages for retry on reconnect
    if (sendQueue.length >= MAX_SEND_QUEUE) {
      sendQueue.shift(); // Drop oldest
    }
    sendQueue.push(msg);
  }
}
