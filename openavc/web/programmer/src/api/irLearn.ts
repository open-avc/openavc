// IR learn WebSocket client. Opens a per-session socket to a bridge's
// /api/devices/{bridge}/ir-learn endpoint and streams captured codes back as
// Pronto hex. There is no reusable multi-endpoint WS in the app (wsClient.ts is
// the /ws singleton), so this is a small standalone helper that reuses the same
// subprotocol auth and tunnel-aware URL derivation.

import { getAuthSubprotocols } from "./auth";

export type IrLearnMode = "one_off" | "auto";

export interface IrLearnHandlers {
  onStarted?: (mode: string) => void;
  onCaptured: (pronto: string) => void;
  onError?: (code: string, message: string) => void;
  onStopped?: (reason: string) => void;
}

function learnUrl(bridgeId: string, mode: IrLearnMode): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  // Tunnel-aware base path (matches wsClient.getWsUrl / api base).
  const basePath = window.location.pathname.split("/programmer")[0] || "";
  return (
    `${proto}//${window.location.host}${basePath}` +
    `/api/devices/${encodeURIComponent(bridgeId)}/ir-learn?mode=${mode}`
  );
}

/** One learn session over a dedicated WebSocket. Create, start(), then stop()
 * (asks the server to end the session) or close() (drops the socket). */
export class IrLearnSession {
  private ws: WebSocket | null = null;
  private done = false;

  constructor(
    private bridgeId: string,
    private mode: IrLearnMode,
    private handlers: IrLearnHandlers,
  ) {}

  start(): void {
    const protocols = getAuthSubprotocols();
    const url = learnUrl(this.bridgeId, this.mode);
    this.ws = protocols ? new WebSocket(url, protocols) : new WebSocket(url);

    this.ws.onmessage = (ev) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(ev.data as string);
      } catch {
        return;
      }
      switch (msg.type) {
        case "learn.started":
          this.handlers.onStarted?.(String(msg.mode ?? this.mode));
          break;
        case "learn.captured":
          this.handlers.onCaptured(String(msg.pronto ?? ""));
          break;
        case "learn.error":
          this.handlers.onError?.(
            String(msg.code ?? "error"),
            String(msg.message ?? "Learn failed"),
          );
          break;
        case "learn.stopped":
          this.done = true;
          this.handlers.onStopped?.(String(msg.reason ?? "stopped"));
          break;
        // "learn.heartbeat" — keep-alive, ignore.
      }
    };
    this.ws.onerror = () => {
      if (!this.done) this.handlers.onError?.("ws_error", "Connection error");
    };
    this.ws.onclose = () => {
      // The server sends learn.stopped before closing; if it didn't (abrupt
      // drop), still tell the caller the session ended.
      if (!this.done) {
        this.done = true;
        this.handlers.onStopped?.("disconnected");
      }
    };
  }

  /** Ask the server to end the session (it will reply learn.stopped, then close). */
  stop(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ action: "stop" }));
      } catch {
        // ignore — close() will clean up
      }
    }
  }

  /** Drop the socket immediately (e.g. component unmount). */
  close(): void {
    this.done = true;
    try {
      this.ws?.close();
    } catch {
      // ignore
    }
    this.ws = null;
  }
}
