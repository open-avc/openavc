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
  // Our own save echoing back while its PUT is still in flight (the PUT's
  // response carries the revision), or our own edit through a dedicated
  // endpoint or a setup wizard this tab started while edits are unsaved: a
  // refetch would drop them, so the caller re-syncs what it changed
  // (syncDeviceConfig, syncAfterSetupAction, syncPendingSettings).
  | "own"
  // Nothing of ours is unsaved: refetch quietly. Also our own edit through a
  // dedicated endpoint when nothing is unsaved and the store is behind it,
  // so a caller that re-syncs nothing (a child's label, a plugin uninstall,
  // a plugin's own action saving its config) leaves no stale revision for
  // the next save to be refused on.
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
  const serverRevision = msg.revision ?? null;
  if (msg.origin_client && msg.origin_client === clientId) {
    const behind = serverRevision != null && serverRevision !== store.revision;
    return !store.dirty && behind ? "refetch" : "own";
  }
  if (!store.dirty) return "refetch";
  if (serverRevision != null && store.revision != null && serverRevision !== store.revision) {
    return "warn-other-session";
  }
  return "warn-external";
}

// Whether a refetch asked for by project.reloaded still has anything to bring
// when its debounce fires: not when the store already holds the revision the
// last notice named, which a caller that re-synced itself (syncDeviceConfig,
// syncAfterSetupAction, syncPendingSettings) has fetched by then.
export function reloadStillNeeded(
  storeRevision: string | null,
  noticeRevision: string | null | undefined,
): boolean {
  return noticeRevision == null || noticeRevision !== storeRevision;
}
