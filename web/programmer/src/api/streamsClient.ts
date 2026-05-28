// Client for the Video Panel plugin's stream endpoints, mounted by the plugin
// at /api/plugins/video_panel/ext/*. These are only reachable when the plugin
// is enabled for the current project; callers gate the UI on that.
import { request, BASE } from "./base";

export interface Stream {
  stream_id: string;
  name: string;
  rtsp_url: string;
  username: string;
  password: string;
  codec_hint: string;
  transcode: string; // auto | always | never
  hardware_accel: string; // auto | none | qsv | nvenc | vaapi | v4l2m2m
  status?: string; // idle | streaming (server-provided)
}

export interface StreamInput {
  name: string;
  stream_id?: string;
  rtsp_url: string;
  username?: string;
  password?: string;
  codec_hint?: string; // auto | h264 | h265 — set from the source probe
  transcode?: string;
  hardware_accel?: string;
}

export interface ProbeResult {
  ok: boolean;
  message?: string;
  codec?: string;
  profile?: string;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  transcode_recommended?: boolean;
  advice?: string;
}

const EXT = "/plugins/video_panel/ext";

export function listStreams(): Promise<Stream[]> {
  return request<Stream[]>(`${EXT}/streams`);
}

export function createStream(data: StreamInput): Promise<Stream> {
  return request<Stream>(`${EXT}/streams`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateStream(streamId: string, data: StreamInput): Promise<Stream> {
  return request<Stream>(`${EXT}/streams/${encodeURIComponent(streamId)}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteStream(streamId: string): Promise<{ ok: boolean; stream_id: string }> {
  return request(`${EXT}/streams/${encodeURIComponent(streamId)}`, {
    method: "DELETE",
  });
}

export function probeStream(data: {
  rtsp_url: string;
  username?: string;
  password?: string;
}): Promise<ProbeResult> {
  return request<ProbeResult>(`${EXT}/streams/probe`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// Snapshot is a binary JPEG, so it loads via <img src>, not request(). The
// timestamp busts the browser cache so each preview grabs a fresh frame.
export function snapshotUrl(streamId: string): string {
  return `${BASE}${EXT}/streams/${encodeURIComponent(streamId)}/snapshot.jpg?t=${Date.now()}`;
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
