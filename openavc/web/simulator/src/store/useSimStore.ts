/**
 * Simulator store — manages devices, WebSocket connection, and protocol log.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { DeviceInfo, LogEntry } from "./api";
import { fetchDevices } from "./api";

// ── WebSocket singleton ──

let ws: WebSocket | null = null;
let wsListeners: Array<(msg: WsMessage) => void> = [];
let connectionListeners: Array<(connected: boolean) => void> = [];
let everConnected = false;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let consumerCount = 0;

interface WsMessage {
  type: "state" | "error" | "protocol";
  timestamp: number;
  device_id?: string;
  [key: string]: unknown;
}

function connectWs() {
  if (ws && ws.readyState <= 1) return;

  // Cancel any pending reconnect so timers can't stack (e.g. under StrictMode
  // double-invoke, or if connectWs is called while a reconnect is queued).
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${window.location.host}/ws`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    everConnected = true;
    for (const l of connectionListeners) l(true);
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data) as WsMessage;
      for (const listener of wsListeners) {
        listener(msg);
      }
    } catch { /* ignore parse errors */ }
  };

  ws.onclose = () => {
    for (const l of connectionListeners) l(false);
    reconnectTimer = setTimeout(connectWs, 2000);
  };

  ws.onerror = () => {
    ws?.close();
  };
}

// Tear down the shared socket when the last consumer unmounts. Cancels the
// pending reconnect and closes the socket without triggering another reconnect,
// and clears everConnected so a fresh mount starts from a clean slate.
function teardownWs() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
  everConnected = false;
}

function addWsListener(fn: (msg: WsMessage) => void) {
  wsListeners.push(fn);
  return () => {
    wsListeners = wsListeners.filter((l) => l !== fn);
  };
}

function addConnectionListener(fn: (connected: boolean) => void) {
  connectionListeners.push(fn);
  return () => {
    connectionListeners = connectionListeners.filter((l) => l !== fn);
  };
}

function isWsConnected(): boolean {
  return ws?.readyState === WebSocket.OPEN;
}

// ── Hook ──

const MAX_LOG = 500;

export function useSimStore() {
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const [stopped, setStopped] = useState(false);
  const logRef = useRef(log);
  logRef.current = log;

  // Connect WebSocket and poll devices on mount
  useEffect(() => {
    consumerCount++;
    connectWs();

    // Poll connection status
    const interval = setInterval(() => {
      setConnected(isWsConnected());
    }, 1000);

    // Initial device load
    fetchDevices()
      .then(setDevices)
      .catch(() => {});

    // Periodic refresh (in case we miss WS updates)
    const refresh = setInterval(() => {
      if (isWsConnected()) {
        fetchDevices()
          .then(setDevices)
          .catch(() => {});
      }
    }, 5000);

    return () => {
      clearInterval(interval);
      clearInterval(refresh);
      // The socket is a module-level singleton shared by all consumers; only
      // tear it down (and cancel the reconnect timer) once the last one leaves.
      consumerCount--;
      if (consumerCount === 0) {
        teardownWs();
      }
    };
  }, []);

  // Track connection state changes for stopped overlay
  useEffect(() => {
    const unsub = addConnectionListener((isConnected) => {
      setConnected(isConnected);
      if (!isConnected && everConnected) {
        // The WS dropped after we were connected. That alone doesn't mean the
        // simulator stopped — a transient blip drops the socket too. The sim UI
        // is served by the sim process itself, so confirm the server is really
        // gone (HTTP unreachable) before showing the "stopped" overlay; a
        // successful fetch means it was just a transient drop that will reconnect.
        fetchDevices()
          .then((d) => setDevices(d))
          .catch(() => setStopped(true));
      } else if (isConnected) {
        // Server is back — refresh everything
        setStopped(false);
        fetchDevices()
          .then(setDevices)
          .catch(() => {});
      }
    });
    return unsub;
  }, []);

  // Listen to WebSocket messages
  useEffect(() => {
    const unsub = addWsListener((msg) => {
      if (msg.type === "state" && msg.device_id) {
        setDevices((prev) =>
          prev.map((d) => {
            if (d.device_id !== msg.device_id) return d;
            return {
              ...d,
              state: { ...d.state, [msg.key as string]: msg.value },
            };
          })
        );
      } else if (msg.type === "error" && msg.device_id) {
        setDevices((prev) =>
          prev.map((d) => {
            if (d.device_id !== msg.device_id) return d;
            const errors = new Set(d.active_errors);
            if (msg.active) errors.add(msg.mode as string);
            else errors.delete(msg.mode as string);
            return { ...d, active_errors: Array.from(errors) };
          })
        );
      } else if (msg.type === "protocol") {
        const entry: LogEntry = {
          timestamp: msg.timestamp as number,
          device_id: msg.device_id || "",
          direction: msg.direction as "in" | "out",
          data: msg.data as string,
          data_text: msg.data_text as string,
          client_id: msg.client_id as string || "",
        };
        setLog((prev) => {
          const next = [...prev, entry];
          return next.length > MAX_LOG ? next.slice(-MAX_LOG) : next;
        });
      }
    });

    return unsub;
  }, []);

  const clearLog = useCallback(() => setLog([]), []);

  return { devices, log, connected, stopped, clearLog };
}
