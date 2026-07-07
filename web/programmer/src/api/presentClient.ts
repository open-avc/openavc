// Client for the Present plugin's room endpoints, mounted by the plugin at
// /api/plugins/present/ext/*. These are only reachable when the plugin is
// enabled for the current project; callers gate the UI on that.
import { request } from "./base";

export interface Room {
  id: string;
  label: string;
  display_key: string;
  display_path: string; // site-relative Display URL (key included)
  code: string;
  output_state: string; // idle | live
  active_presenters: number;
}

export interface RoomInput {
  label: string;
  room_id?: string;
}

const EXT = "/plugins/present/ext";

export function listRooms(): Promise<Room[]> {
  return request<Room[]>(`${EXT}/rooms`);
}

export function createRoom(data: RoomInput): Promise<Room> {
  return request<Room>(`${EXT}/rooms`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateRoom(roomId: string, data: RoomInput): Promise<Room> {
  return request<Room>(`${EXT}/rooms/${encodeURIComponent(roomId)}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteRoom(roomId: string): Promise<{ ok: boolean; room_id: string }> {
  return request(`${EXT}/rooms/${encodeURIComponent(roomId)}`, {
    method: "DELETE",
  });
}

export function regenerateKey(roomId: string): Promise<Room> {
  return request<Room>(`${EXT}/rooms/${encodeURIComponent(roomId)}/regenerate_key`, {
    method: "POST",
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
