// The save write loop, extracted from projectStore so its async contract is
// unit-testable with injected fakes. The point of the extraction: a retryable
// (non-conflict) failure must be retried WITHIN the awaited promise, so a caller
// that `await save()` sees the final underlying write — not just the first
// failed attempt. The old store scheduled the retry on a detached setTimeout and
// returned, resolving the awaited chain early; it also retried forever on
// persistent failure. This loop awaits each retry and stops after maxRetries.
import type { ProjectConfig } from "../api/types";

/** Fields runSaveWithRetry writes back to the store during a save. */
export interface SaveStatePatch {
  saving?: boolean;
  savePending?: boolean;
  dirty?: boolean;
  etag?: string | null;
  revision?: number | null;
  conflictDetected?: boolean;
  error?: string | null;
}

export interface SaveAttemptDeps {
  /** Latest project to send — re-read each attempt so a retry picks up any
   *  edits the user made during the backoff delay. */
  getProject: () => ProjectConfig | null;
  getEtag: () => string | null;
  saveProject: (project: ProjectConfig, etag?: string) => Promise<{ etag?: string }>;
  /** True for a version conflict (409) — never retried; the user must reload. */
  isConflict: (e: unknown) => boolean;
  conflictMessage: (e: unknown) => string;
  setState: (patch: SaveStatePatch) => void;
  /** Backoff sleep, injected so tests don't wait real seconds. */
  sleep: (ms: number) => Promise<void>;
  maxRetries?: number;
}

export type SaveOutcome = "saved" | "conflict" | "failed" | "noop";

/**
 * Attempt the project write, retrying a transient failure up to `maxRetries`
 * times with linear backoff, AWAITING each retry. The returned promise resolves
 * only once the final write settles — success (`saved`), a version conflict
 * (`conflict`, no retry), or exhausted retries (`failed`) — never after merely
 * the first failed attempt, and never looping past the retry budget.
 */
export async function runSaveWithRetry(
  deps: SaveAttemptDeps,
  startAttempt = 0,
): Promise<SaveOutcome> {
  const maxRetries = deps.maxRetries ?? 2;
  for (let attempt = startAttempt; ; attempt++) {
    const project = deps.getProject();
    if (!project) return "noop";
    const etag = deps.getEtag();
    deps.setState({ saving: true, savePending: false, error: null });
    try {
      const result = await deps.saveProject(project, etag ?? undefined);
      const newEtag = result.etag ?? null;
      const newRevision = newEtag ? parseInt(newEtag.replace(/"/g, ""), 10) || null : null;
      // If the project ref changed during the save, the user kept editing —
      // keep dirty=true so the caller schedules another save.
      const editedDuringSave = deps.getProject() !== project;
      deps.setState({
        saving: false,
        dirty: editedDuringSave,
        etag: newEtag,
        revision: newRevision,
        conflictDetected: false,
      });
      return "saved";
    } catch (e) {
      if (deps.isConflict(e)) {
        deps.setState({ saving: false, conflictDetected: true, error: deps.conflictMessage(e) });
        return "conflict";
      }
      if (attempt < maxRetries) {
        const delay = (attempt + 1) * 1000;
        deps.setState({ error: `Save failed, retrying in ${delay / 1000}s...`, saving: false });
        await deps.sleep(delay);
        continue;
      }
      deps.setState({ error: String(e), saving: false });
      return "failed";
    }
  }
}
