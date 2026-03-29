type MessageHandler = (msg: Record<string, unknown>) => void;
type LifecycleHandler = () => void;

let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 2000;
const MAX_RECONNECT_DELAY = 15000;
let handlers: MessageHandler[] = [];
let connectHandlers: LifecycleHandler[] = [];
let disconnectHandlers: LifecycleHandler[] = [];

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

export function connect(): void {
  if (socket && socket.readyState <= WebSocket.OPEN) return;

  socket = new WebSocket(getWsUrl());

  socket.onopen = () => {
    console.log("[WS] Connected");
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

  socket.onclose = () => {
    console.log(`[WS] Disconnected, reconnecting in ${reconnectDelay / 1000}s...`);
    socket = null;
    for (const handler of disconnectHandlers) {
      handler();
    }
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
    socket.close();
    socket = null;
  }
}

export function onMessage(handler: MessageHandler): () => void {
  handlers.push(handler);
  return () => {
    handlers = handlers.filter((h) => h !== handler);
  };
}

/** Command types worth queuing during disconnect (stale state updates aren't). */
const QUEUEABLE_TYPES = new Set([
  "command", "macro.execute", "state.set", "ui.press", "ui.release",
  "ui.hold", "ui.toggle_off", "ui.change", "ui.page",
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
