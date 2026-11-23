import { useCallback, useEffect, useRef, useState } from "react";
import { followSetupRun, type Step } from "./followSetupRun";
import { forgetOpenSetupRun, rememberOpenSetupRun } from "./openSetupRun";

export type Phase = "input" | "running" | "done" | "error";

/**
 * The sentence the handler wrote for the operator, out of the terminal event's
 * `result` object. Drivers put their whole finding here — the model they
 * reached, the firmware, the endpoint counts the operator is meant to
 * sanity-check — so it is shown in full rather than summarised. A result with
 * no message (or no result at all) is normal; the step log stands on its own.
 */
function resultMessage(result: unknown): string {
  if (!result || typeof result !== "object") return "";
  const message = (result as Record<string, unknown>).message;
  return typeof message === "string" ? message.trim() : "";
}

/**
 * One setup wizard's run: its phase, its step log and how it ended, followed
 * by `followSetupRun`. While it runs the tab keeps it (`openSetupRun.ts`), so
 * a wizard opened again on it after a sign-in or a reload (`runId`) asks the
 * server how it stands and says how it ended.
 */
export function useSetupRun(deviceId: string, actionId: string, runId?: string) {
  const [phase, setPhase] = useState<Phase>(runId ? "running" : "input");
  const [steps, setSteps] = useState<Step[]>([]);
  const [summary, setSummary] = useState("");
  const [error, setError] = useState("");
  const unsubRef = useRef<null | (() => void)>(null);
  const runIdRef = useRef<string | undefined>(runId);
  const endedRef = useRef(false);

  // Stop listening if the wizard unmounts mid-run.
  useEffect(() => () => unsubRef.current?.(), []);

  const follow = useCallback(() => {
    endedRef.current = false;
    const ended = () => {
      endedRef.current = true;
      forgetOpenSetupRun(runIdRef.current ?? "");
    };
    const run = followSetupRun(deviceId, actionId, () => runIdRef.current, {
      step: (step) => setSteps((prev) => [...prev, step]),
      done: (msg) => {
        ended();
        setSummary(resultMessage(msg.result));
        setPhase("done");
      },
      failed: (message) => {
        ended();
        setError(message);
        setPhase("error");
      },
    });
    unsubRef.current = run.stop;
    return run;
  }, [deviceId, actionId]);

  // Opened on a run that started before this page: ask how it stands now.
  useEffect(() => {
    if (runId) follow().check();
  }, [runId, follow]);

  /** A run begins: followed before its start request goes out, so an early
   *  progress event cannot be missed. */
  const begin = useCallback(() => {
    setPhase("running");
    setSteps([]);
    setSummary("");
    setError("");
    runIdRef.current = undefined;
    return follow();
  }, [follow]);

  /** The start request answered with the run's id: kept in the tab, unless
   *  the run's end came before its id did. */
  const started = useCallback((id: string | undefined) => {
    runIdRef.current = id;
    if (id && !endedRef.current) rememberOpenSetupRun({ deviceId, actionId, runId: id });
  }, [deviceId, actionId]);

  /** The wizard closes: stop following, and stop keeping the run. */
  const stop = useCallback(() => {
    unsubRef.current?.();
    forgetOpenSetupRun(runIdRef.current ?? "");
  }, []);

  return { phase, steps, setSteps, summary, error, begin, started, stop };
}
