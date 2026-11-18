import * as api from "../../api/restClient";
import { onConnect, onMessage } from "../../api/wsClient";
import { parseApiError } from "../../api/errors";
import { syncAfterSetupAction } from "../../store/projectStore";

/** One line of a setup run's step log. */
export interface Step {
  step: string;
  pct: number | null;
  status: string;
}

function isFinal(status: string): boolean {
  return status === "done" || status === "error";
}

function lineOf(msg: Record<string, unknown>): Step {
  return {
    step: String(msg.step ?? ""),
    pct: (msg.pct as number | null) ?? null,
    status: String(msg.status ?? "running"),
  };
}

function failureOf(msg: Record<string, unknown>): string {
  return String(msg.error ?? msg.step ?? "Setup failed");
}

export interface RunListener {
  step: (step: Step) => void;
  done: (msg: Record<string, unknown>) => void;
  failed: (message: string) => void;
}

/**
 * Follow one setup run to its final message: live over the WebSocket, or,
 * each time the socket reconnects, asked of the server, since the final event
 * may have gone out while it was down. Live events are this device's and
 * action's, and once the run's id is known, this run's only. `stop` ends
 * the following; `fail` ends it with a failure of the wizard's own (the
 * request that starts the run was refused).
 */
export function followSetupRun(
  deviceId: string,
  actionId: string,
  runId: () => string | undefined,
  on: RunListener,
): { stop: () => void; fail: (message: string) => void } {
  let finished = false;
  const stop = () => {
    finished = true;
    stopProgress();
    stopReconnect();
  };
  const fail = (message: string) => {
    if (finished) return;
    stop();
    on.failed(message);
  };
  const take = (msg: Record<string, unknown>) => {
    if (finished) return;
    const line = lineOf(msg);
    on.step(line);
    if (!isFinal(line.status)) return;
    // The run may have saved new settings for this device.
    void syncAfterSetupAction(deviceId);
    if (line.status === "error") {
      fail(failureOf(msg));
      return;
    }
    stop();
    on.done(msg);
  };
  // Before the start request answers, the run's id is not known yet.
  const isThisRun = (msg: Record<string, unknown>) => {
    const id = runId();
    return !id || msg.run_id === id;
  };
  const stopProgress = onMessage((msg) => {
    if (msg.type !== "action.progress") return;
    if (msg.device_id !== deviceId || msg.action_id !== actionId) return;
    if (isThisRun(msg)) take(msg);
  });
  const stopReconnect = onConnect(() => {
    const id = runId();
    if (!id || finished) return;
    api.getSetupRun(deviceId, id).then(
      (run) => {
        if (isFinal(run.status)) take({ ...run });
      },
      (e) => fail(parseApiError(e)),
    );
  });
  return { stop, fail };
}
