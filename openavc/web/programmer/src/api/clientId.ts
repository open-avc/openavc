// A per-tab id sent on every /api request as X-OpenAVC-Client. The server
// echoes it as origin_client on the project.reloaded broadcast that follows
// a persisted edit, which is how this tab tells its own save from an edit
// made in another session (see hooks/projectReload.ts).
export const CLIENT_HEADER = "X-OpenAVC-Client";

// Names one project save: the save sent again after its answer was lost goes
// under the same id, and the server answers it with what the first one did
// instead of a conflict with this tab's own save (store/projectStoreSave.ts).
export const OPERATION_HEADER = "X-OpenAVC-Operation";

/** A fresh random id, for this tab or for one save. */
export function newClientId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }
}

export const CLIENT_ID = newClientId();
