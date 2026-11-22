// When the Scripts view asks the server again for what is failing in its
// scripts (`GET /api/scripts/errors`), while it is open.
import { useEffect, useRef } from "react";
import { useLogStore } from "../../store/logStore";
import { useScriptErrorsStore } from "../../store/scriptErrorsStore";
import { latestScriptErrorId } from "./scriptRuntimeErrors";

/** How long a nudge waits for the others one failure brings with it. */
export const REASK_AFTER_MS = 250;

/**
 * Re-ask the record when a script fails while the view is open.
 *
 * Two things say one has:
 * - the server's `script.error` message, sent for a handler, a timer callback
 *   or a control's function that failed, after the failure is recorded. It
 *   arrives whatever the Log view is doing: a paused log keeps every line out
 *   of the log store, and the server's log feed drops its oldest lines under
 *   a flood.
 * - a script's ERROR line in the log, for what the record holds with no
 *   message: an event dropped at the chain limit, a script that failed to load
 *   or whose load was given up on, at a door other than this view's own
 *   reload.
 *
 * One failure usually brings both, and a timer failing every tick brings them
 * every tick, so the nudges in a window become one ask at its end: at most one
 * a window, and always one after the last nudge. What was heard before the
 * view opened asks nothing; the view asks for the record itself on opening.
 */
export function useScriptErrorsRefresh(refresh: () => void): void {
  const heard = useScriptErrorsStore((s) => s.heard);
  const logged = useLogStore((s) => latestScriptErrorId(s.logEntries));

  const latest = useRef(refresh);
  latest.current = refresh;
  const seen = useRef({ heard, logged });
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const last = seen.current;
    if (heard === last.heard && logged === last.logged) return;
    seen.current = { heard, logged };
    if (pending.current !== null) return;
    pending.current = setTimeout(() => {
      pending.current = null;
      latest.current();
    }, REASK_AFTER_MS);
  }, [heard, logged]);

  useEffect(() => () => {
    if (pending.current !== null) clearTimeout(pending.current);
    pending.current = null;
  }, []);
}
