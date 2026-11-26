import { useEffect } from "react";
import * as api from "../../api/restClient";
import { useConnectionStore } from "../../store/connectionStore";
import { useLogStore } from "../../store/logStore";
import type { SkipNote, TriggerRun } from "../../store/logStore";

/** Does this outcome mean the trigger is not doing its job?
 *
 *  THE rule, asked by every surface that marks a trigger, so the dashboard and
 *  the trigger card cannot disagree about one trigger.
 *
 *  `cancelled` and `skipped` are ordinary operation — a cancel_group preemption
 *  is the feature somebody set the group up for, and an overlap guard refusing
 *  a second run is the guard working. Marking those red would cry wolf on
 *  exactly the triggers that are behaving. */
export function isFailedRun(outcome: TriggerRun["outcome"] | undefined): boolean {
  return outcome === "failed" || outcome === "error";
}

/** The line a trigger's last-run record says when its last fire did not go
 *  ahead and the server said why (its macro refused it), dated by the
 *  record. None for any other run, or a skip from a server that gave no
 *  reason: the run marker says that one. */
export function runSkip(run: TriggerRun | undefined): SkipNote | null {
  if (run?.outcome !== "skipped" || !run.reason) return null;
  return { ...run.reason, at: (run.endedAt ?? Date.now() / 1000) * 1000 };
}

/** A trigger's last run from its `trigger.completed` message: how the fire
 *  ended, and for one its macro refused, the server's reason. */
export function completedRun(msg: Record<string, unknown>): TriggerRun {
  return {
    outcome: msg.outcome as TriggerRun["outcome"],
    error: msg.error as string | undefined,
    reason: msg.code ? { code: String(msg.code), message: String(msg.message ?? "") } : undefined,
    endedAt: Date.now() / 1000,
  };
}

/** What a trigger card says of its last fire: the run marker's outcome, and
 *  the skip line, which is a fire of the trigger's own this session that did
 *  not go ahead (newer than the record, which a fire ends), else the record's
 *  own reason (`runSkip`), which then stands in for the marker. */
export function cardRun(
  run: TriggerRun | undefined, sessionSkip: SkipNote | undefined,
): { outcome: TriggerRun["outcome"] | null; skip: SkipNote | null } {
  const recordSkip = runSkip(run);
  return { outcome: run && !recordSkip ? run.outcome : null, skip: sessionSkip ?? recordSkip };
}

/** Seed the store with how each trigger's last fire actually ended.
 *
 * The project file says what a trigger is FOR; only the server knows how it
 * went. `trigger.completed` keeps that current while the IDE is open, but a
 * trigger that failed at 6am and an IDE opened at 9 never share a session, so
 * the record has to be read back rather than accumulated.
 *
 * Re-read on every (re)connect, not just on mount: a socket that dropped for
 * ten minutes missed whatever fired in them, and the events do not replay.
 *
 * A failed request keeps what is already there. A dropped connection says
 * nothing about the triggers, and clearing the marks would read as "fixed".
 */
export function useTriggerRuns(): void {
  const connected = useConnectionStore((s) => s.connected);

  useEffect(() => {
    if (!connected) return;
    let live = true;
    api
      .listTriggers()
      .then(({ triggers }) => {
        if (!live) return;
        const runs: Record<string, TriggerRun> = {};
        for (const t of triggers) {
          if (!t.last_outcome) continue;
          runs[t.id] = {
            outcome: t.last_outcome,
            error: t.last_error ?? undefined,
            reason: t.last_reason ?? undefined,
            // An older server keeps no time of its own on the record.
            endedAt: t.last_run_at ?? t.last_fired ?? undefined,
          };
        }
        useLogStore.getState().setTriggerRuns(runs);
      })
      .catch(() => {
        /* keep what we have */
      });
    return () => {
      live = false;
    };
  }, [connected]);
}
