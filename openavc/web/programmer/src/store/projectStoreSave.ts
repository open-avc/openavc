// The save write loop, extracted from projectStore so its async contract is
// unit-testable with injected fakes. A failed save that may be tried again is
// tried WITHIN the awaited promise, so a caller that `await save()` sees the
// final underlying write, not just the first failed attempt, and the loop stops
// after maxRetries.
//
// Which failures are tried again is decided by what the server answered
// (`classify`), never by the attempt count alone: an answer that refuses the
// change, or says another writer moved the project, gets the same answer every
// time. One save is one set of bytes against one revision under one operation
// id, for every try: a save whose answer was lost is sent again as itself, and
// the server, which recognises the id of the save that made its current
// revision, answers what it did rather than a conflict with this tab's own save.
import type { ProjectConfig } from "../api/types";

/** Fields runSaveWithRetry writes back to the store during a save. */
export interface SaveStatePatch {
  saving?: boolean;
  savePending?: boolean;
  dirty?: boolean;
  etag?: string | null;
  revision?: string | null;
  conflictDetected?: boolean;
  /** Why the save failed, set only once it has stopped trying. */
  error?: string | null;
  /** Seconds until a failed save is tried again; null while none waits. */
  saveRetryIn?: number | null;
  /** The id of the save out now, or of the last one whose answer never
   *  came, and the project it sent; null once the server has answered.
   *  A reloaded tab keeps them with its unsaved project, so the next page
   *  can ask whether that save landed (store/projectDraft.ts). */
  saveOperation?: string | null;
  saveSending?: ProjectConfig | null;
}

/**
 * What a failed save's answer says about trying it again:
 * - `conflict`: another writer moved the project. Never tried again; the
 *   conflict banner offers the answers that can work.
 * - `final`: the server refused the change or the request, and would answer
 *   the same again (a refusal, an invalid project, an ended session), or the
 *   failure was in the page. Never tried again.
 * - `not_taken`: the server said nothing was taken in (not applied, still
 *   starting). Tried again, until the same answer comes back twice.
 * - `unknown`: no answer came back, or one that does not say (a gateway's).
 *   The same save is sent again under its operation id.
 */
export type SaveFailureKind = "conflict" | "final" | "not_taken" | "unknown";

export interface SaveAttemptDeps {
  /** The project to send, read once per save: every try sends the same. */
  getProject: () => ProjectConfig | null;
  getEtag: () => string | null;
  /** Moves when the store adopts the server's copy (a reload). A save
   *  waiting to be tried again when it moves is dropped, since the edit it
   *  held was put aside. */
  getGeneration: () => number;
  /** A fresh id naming one save. */
  newOperationId: () => string;
  saveProject: (project: ProjectConfig, etag: string | undefined, operation: string) => Promise<{ etag?: string }>;
  classify: (e: unknown) => SaveFailureKind;
  conflictMessage: (e: unknown) => string;
  /** The sentence a give-up failure shows the user. Injected like
   *  conflictMessage, and for the same reason: unwrapping a thrown value is
   *  the transport's business, and this file stays free of value imports so
   *  the esbuild harness in tests/ can load it on its own. */
  failureMessage: (e: unknown) => string;
  setState: (patch: SaveStatePatch) => void;
  /** Backoff sleep, injected so tests don't wait real seconds. */
  sleep: (ms: number) => Promise<void>;
  maxRetries?: number;
}

export type SaveOutcome = "saved" | "conflict" | "failed" | "noop";

/** One save: these bytes, against this revision, under this id. */
interface SaveRequest {
  project: ProjectConfig;
  etag: string | null;
  generation: number;
  operation: string;
}

/** A failed try's answer, and whether it ends the save. */
interface FailedTry {
  message: string;
  final: boolean;
  /** The answer, when the server said nothing was taken in. */
  notTaken: string | null;
}

function judgeFailure(
  message: string,
  kind: SaveFailureKind,
  previousNotTaken: string | null,
  outOfTries: boolean,
): FailedTry {
  const notTaken = kind === "not_taken" ? message : null;
  // A save the server did not take, answered the same way twice, would be
  // answered so again.
  const repeated = notTaken !== null && notTaken === previousNotTaken;
  return { message, final: kind === "final" || repeated || outOfTries, notTaken };
}

async function trySave(deps: SaveAttemptDeps, request: SaveRequest): Promise<"saved"> {
  deps.setState({
    saving: true, savePending: false, error: null, saveRetryIn: null,
    saveOperation: request.operation, saveSending: request.project,
  });
  const result = await deps.saveProject(request.project, request.etag ?? undefined, request.operation);
  const newEtag = result.etag ?? null;
  // The ETag is an opaque token — kept as it came, compared for equality.
  const newRevision = newEtag ? newEtag.replace(/"/g, "") : null;
  // An edit made while this save was out (or waiting to be tried again) is
  // not in what was sent: keep dirty=true so the caller saves again.
  const editedDuringSave = deps.getProject() !== request.project;
  deps.setState({
    saving: false,
    saveRetryIn: null,
    dirty: editedDuringSave,
    etag: newEtag,
    revision: newRevision,
    conflictDetected: false,
    ...ANSWERED,
  });
  return "saved";
}

/** The save is answered: nothing is left to ask the server about. */
const ANSWERED = { saveOperation: null, saveSending: null } as const;

/**
 * Attempt the project write, trying a failure again up to `maxRetries` times
 * with linear backoff when its answer allows it, AWAITING each try. The
 * returned promise resolves only once the save is settled: saved, a version
 * conflict, failed (an answer that will not change, or the tries spent), or
 * dropped (`noop`: nothing to save, or the project was reloaded during a wait).
 */
export async function runSaveWithRetry(
  deps: SaveAttemptDeps,
  startAttempt = 0,
): Promise<SaveOutcome> {
  const maxRetries = deps.maxRetries ?? 2;
  const project = deps.getProject();
  if (!project) return "noop";
  const request: SaveRequest = {
    project,
    etag: deps.getEtag(),
    generation: deps.getGeneration(),
    operation: deps.newOperationId(),
  };
  let previousNotTaken: string | null = null;
  for (let attempt = startAttempt; ; attempt++) {
    try {
      return await trySave(deps, request);
    } catch (e) {
      const kind = deps.classify(e);
      if (kind === "conflict") {
        deps.setState({
          saving: false, saveRetryIn: null, conflictDetected: true, error: deps.conflictMessage(e),
          ...ANSWERED,
        });
        return "conflict";
      }
      const failed = judgeFailure(deps.failureMessage(e), kind, previousNotTaken, attempt >= maxRetries);
      if (failed.final) {
        // A save that never heard back may still have landed: its id stays.
        const answered = kind === "unknown" ? {} : ANSWERED;
        deps.setState({ error: failed.message, saving: false, saveRetryIn: null, ...answered });
        return "failed";
      }
      previousNotTaken = failed.notTaken;
      const delay = (attempt + 1) * 1000;
      deps.setState({ saving: false, saveRetryIn: delay / 1000 });
      await deps.sleep(delay);
      if (deps.getGeneration() !== request.generation || !deps.getProject()) {
        deps.setState({ saveRetryIn: null });
        return "noop";
      }
    }
  }
}
