import { CLIENT_ID } from "../api/clientId";

export interface ProjectReloadMessage {
  type: "project.reloaded";
  revision?: string | null;
  // The tab that made the change, when a Programmer request made it. Absent
  // for a persist nobody's tab made (a learned config, a cloud push).
  origin_client?: string | null;
}

export interface ProjectStoreSnapshot {
  saving: boolean;
  dirty: boolean;
  revision: string | null;
}

export type ProjectReloadDecision =
  // Our own save echoing back while its PUT is still in flight, or our own
  // edit through a dedicated endpoint: the caller already re-syncs the store.
  | "own"
  // Nothing of ours is unsaved: refetch quietly.
  | "refetch"
  // We hold unsaved edits and the server moved past our revision.
  | "warn-other-session"
  // We hold unsaved edits and cannot tell who moved the server.
  | "warn-external";

// The one place that decides what a project.reloaded broadcast means to this
// tab. Kept pure so the cases are testable without a WebSocket.
export function decideProjectReload(
  store: ProjectStoreSnapshot,
  msg: ProjectReloadMessage,
  clientId: string = CLIENT_ID,
): ProjectReloadDecision {
  if (store.saving) return "own";
  if (msg.origin_client && msg.origin_client === clientId) return "own";
  if (!store.dirty) return "refetch";
  const serverRevision = msg.revision ?? null;
  if (serverRevision != null && store.revision != null && serverRevision !== store.revision) {
    return "warn-other-session";
  }
  return "warn-external";
}
