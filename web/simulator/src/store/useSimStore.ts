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

interface WsMessage {
  type: "state" | "error" | "protocol";
  timestamp: number;
  device_id?: string;
  [key: string]: unknown;
}

function connectWs() {
  if (ws && ws.readyState <= 1) return;

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
    setTimeout(connectWs, 2000);
  };

  ws.onerror = () => {
    ws?.close();
  };
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
    };
  }, []);

  // Track connection state changes for stopped overlay
  useEffect(() => {
    const unsub = addConnectionListener((isConnected) => {
      setConnected(isConnected);
      if (!isConnected && everConnected) {
        // Server went away after we were connected — it was stopped
        setStopped(true);
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
