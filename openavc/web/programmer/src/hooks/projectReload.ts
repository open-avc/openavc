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
  /** The last save met a newer revision; its own banner says so. */
  conflictDetected?: boolean;
}

/** What acting on a notice can do, injected so it is testable without a
 *  WebSocket (hooks/useWebSocket.ts gives the real ones). */
export interface ProjectReloadEffects {
  getStore: () => ProjectStoreSnapshot;
  /** Settles once the save under way, and any it chains, is done. */
  whenSaved: () => Promise<void>;
  refetch: (revision: string | null | undefined) => void;
  warn: (message: string) => void;
}

export type ProjectReloadDecision =
  // Our own save echoing back while its PUT is still in flight (the PUT's
  // response carries the revision), or our own edit through a dedicated
  // endpoint or a setup wizard this tab started while edits are unsaved: a
  // refetch would drop them, so the caller re-syncs what it changed
  // (syncDeviceConfig, syncAfterSetupAction).
  | "own"
  // Another writer's change (another tab, a learned setting, a cloud push)
  // while our project save is in flight: decided again once the save is done.
  // If the save then lands, the server is already past the revision it
  // answers with; if it conflicts, its own banner says so.
  | "after-save"
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
  const fromThisTab = Boolean(msg.origin_client) && msg.origin_client === clientId;
  if (store.saving) return fromThisTab ? "own" : "after-save";
  const serverRevision = msg.revision ?? null;
  if (fromThisTab) {
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
// syncAfterSetupAction) has fetched by then.
export function reloadStillNeeded(
  storeRevision: string | null,
  noticeRevision: string | null | undefined,
): boolean {
  return noticeRevision == null || noticeRevision !== storeRevision;
}

/**
 * Act on a project.reloaded notice: nothing for this tab's own change, a
 * quiet refetch, or a warning while edits are unsaved. Another writer's
 * change during this tab's save is acted on once the save is done (and not
 * at all when the save conflicted: its banner already says the project
 * moved). Resolves when the notice has been acted on.
 */
export async function actOnProjectReload(
  notice: ProjectReloadMessage,
  effects: ProjectReloadEffects,
  clientId: string = CLIENT_ID,
): Promise<void> {
  const decision = decideProjectReload(effects.getStore(), notice, clientId);
  if (decision === "own") return;
  if (decision === "after-save") {
    await effects.whenSaved();
    if (!effects.getStore().conflictDetected) await actOnProjectReload(notice, effects, clientId);
  } else if (decision === "refetch") {
    effects.refetch(notice.revision);
  } else if (decision === "warn-other-session") {
    effects.warn("Project modified by another session. Save may trigger a conflict. Consider reloading.");
  } else {
    effects.warn("Project modified externally. Your unsaved changes may conflict");
  }
}
