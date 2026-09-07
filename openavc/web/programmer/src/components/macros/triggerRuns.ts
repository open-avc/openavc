import { useEffect } from "react";
import * as api from "../../api/restClient";
import { useConnectionStore } from "../../store/connectionStore";
import { useLogStore } from "../../store/logStore";
import type { TriggerRun } from "../../store/logStore";

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
            firedAt: t.last_fired ?? undefined,
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
