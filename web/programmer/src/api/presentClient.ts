// Client for the Present plugin's display + routing endpoints, mounted by the
// plugin at /api/plugins/present/ext/*. These are only reachable when the
// plugin is enabled for the current project; callers gate the UI on that.
import { request } from "./base";

export interface PresentDisplay {
  id: string;
  label: string;
  kind: string; // "browser" (Display page) | "stream" (decoder pulls RTSP/SRT)
  display_key: string;
  display_path: string; // site-relative Display URL (key included)
  source: string; // routing assignment: "auto" or a pinned presenter
  showing: string; // who it is actually showing ("" = the connect card)
  output_state: string; // idle | live
  // Browser displays only ("" = not shown from this server):
  local_output?: string; // id of this server's video output showing it
  // Present only when local_output is set:
  local_state?: string; // starting | running | waiting_for_output | waiting_for_signin | error | unsupported | stopped
  local_output_name?: string; // human name of that output ("" when unknown)
  local_output_connected?: boolean;
  // Stream displays only:
  stream_path?: string; // sidecar path with the stream key baked in (out/<id>-<key>)
  rtsp_port?: number;
  srt_port?: number;
  encoder_state?: string; // stopped | starting | idle | live | error
}

export interface HostOutput {
  id: string; // stable output identity (Windows device path / connector name)
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  primary: boolean;
  in_use_by: string; // display id already claiming this output ("" = free)
}

export interface HostOutputs {
  supported: boolean; // false: this host can't show a local display (reason says why)
  reason: string;
  outputs: HostOutput[];
}

export interface PresentPresenter {
  name: string; // path-safe ingest name (the routable value)
  label: string; // the display name the guest typed
  since: number;
}

export interface PresentSourceOption {
  value: string; // "auto" or a presenter name
  label: string;
}

export interface PresentStatus {
  running: boolean;
  mediamtx_version: string;
  space_name: string;
  code: string; // the join code shown on every connect card
  join_url: string; // what guests type, exactly as the connect cards show it
  presenters: PresentPresenter[];
  active_presenters: number;
  sources: PresentSourceOption[];
  display_ids: string[];
}

export interface DisplayInput {
  label: string;
  display_id?: string;
  kind?: string; // "browser" | "stream"
  local_output?: string; // "" clears; omit to keep the current assignment
}

const EXT = "/plugins/present/ext";

export function getStatus(): Promise<PresentStatus> {
  return request<PresentStatus>(`${EXT}/status`);
}

export function listDisplays(): Promise<PresentDisplay[]> {
  return request<PresentDisplay[]>(`${EXT}/displays`);
}

export function getOutputs(): Promise<HostOutputs> {
  return request<HostOutputs>(`${EXT}/outputs`);
}

export function createDisplay(data: DisplayInput): Promise<PresentDisplay> {
  return request<PresentDisplay>(`${EXT}/displays`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateDisplay(displayId: string, data: DisplayInput): Promise<PresentDisplay> {
  return request<PresentDisplay>(`${EXT}/displays/${encodeURIComponent(displayId)}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteDisplay(displayId: string): Promise<{ ok: boolean; display_id: string }> {
  return request(`${EXT}/displays/${encodeURIComponent(displayId)}`, {
    method: "DELETE",
  });
}

export function regenerateKey(displayId: string): Promise<PresentDisplay> {
  return request<PresentDisplay>(`${EXT}/displays/${encodeURIComponent(displayId)}/regenerate_key`, {
    method: "POST",
  });
}

export function regenerateStreamKey(displayId: string): Promise<PresentDisplay> {
  return request<PresentDisplay>(`${EXT}/displays/${encodeURIComponent(displayId)}/regenerate_stream_key`, {
    method: "POST",
  });
}

export function routeDisplay(displayId: string, source: string): Promise<PresentDisplay> {
  return request<PresentDisplay>(`${EXT}/displays/${encodeURIComponent(displayId)}/route`, {
    method: "POST",
    body: JSON.stringify({ source }),
  });
}

// request() throws "API <status>: <body>"; body is usually {"detail": "..."}.
// Pull out the human-readable part for toasts.
export function errorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  const match = raw.match(/API \d+: ([\s\S]*)/);
  if (!match) return raw;
  try {
    const parsed = JSON.parse(match[1]);
    if (parsed?.detail) {
      return typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    }
  } catch {
    /* body wasn't JSON; fall through */
  }
  return match[1];
}
