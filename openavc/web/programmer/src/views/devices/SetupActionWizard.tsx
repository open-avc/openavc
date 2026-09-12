import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Check, AlertTriangle } from "lucide-react";
import { Dialog } from "../../components/shared/Dialog";
import * as api from "../../api/restClient";
import { onMessage } from "../../api/wsClient";
import type { DeviceAction } from "../../api/types";
import {
  ActionParamFields,
  buildParams,
  hasMissingRequired,
  seedParamValues,
} from "./actionParamFields";
import { hasInvalidParams } from "../../components/shared/paramValidation";
import { parseApiError } from "../../api/errors";

type Phase = "input" | "running" | "done" | "error";

interface Step {
  step: string;
  pct: number | null;
  status: string;
}

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
 * Setup-action wizard — runs a driver-declared provisioning action (kind:"setup")
 * that can execute while the device is offline. Collects any input params, fires
 * the action, then streams its `action.progress` WebSocket events as a live step
 * log until the run reports "done" or "error", and shows the terminal event's
 * `result.message` — the handler's own report of what it found. The action's
 * meaning lives in the driver; this dialog only renders what it says.
 *
 * A failed run always arrives as status "error", whether the handler raised or
 * returned success:false (the server decides that, see core/setup_actions.py),
 * so "done" here means it worked.
 */
export function SetupActionWizard({
  deviceId,
  action,
  onClose,
  onComplete,
}: {
  deviceId: string;
  action: DeviceAction;
  onClose: () => void;
  onComplete?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("input");
  const [values, setValues] = useState<Record<string, string>>(() =>
    seedParamValues(action.params),
  );
  const [steps, setSteps] = useState<Step[]>([]);
  const [summary, setSummary] = useState<string>("");
  const [error, setError] = useState<string>("");
  const unsubRef = useRef<null | (() => void)>(null);

  // Stop listening if the wizard unmounts mid-run.
  useEffect(() => () => unsubRef.current?.(), []);

  const hasParams = Object.keys(action.params).length > 0;
  const blocked =
    hasMissingRequired(action.params, values) ||
    hasInvalidParams(action.params, values);
  const confirmNote = typeof action.confirm === "string" ? action.confirm : null;

  const startRun = useCallback(async () => {
    setPhase("running");
    setSteps([]);
    setSummary("");
    setError("");
    // Subscribe BEFORE the POST so an early progress event can't be missed.
    // One run per device+action is guaranteed server-side, so that pair is a
    // sufficient filter.
    unsubRef.current = onMessage((msg) => {
      if (msg.type !== "action.progress") return;
      if (msg.device_id !== deviceId || msg.action_id !== action.id) return;
      const status = String(msg.status ?? "running");
      setSteps((prev) => [
        ...prev,
        { step: String(msg.step ?? ""), pct: (msg.pct as number | null) ?? null, status },
      ]);
      if (status === "done") {
        setSummary(resultMessage(msg.result));
        setPhase("done");
        unsubRef.current?.();
      } else if (status === "error") {
        setError(String(msg.error ?? msg.step ?? "Setup failed"));
        setPhase("error");
        unsubRef.current?.();
      }
    });
    try {
      await api.invokeDeviceAction(
        deviceId,
        action.id,
        buildParams(action.params, values),
      );
    } catch (e) {
      unsubRef.current?.();
      setError(parseApiError(e));
      setPhase("error");
    }
  }, [deviceId, action, values]);

  const close = useCallback(() => {
    unsubRef.current?.();
    onComplete?.();
    onClose();
  }, [onClose, onComplete]);

  const btnStyle = (variant: "primary" | "muted"): React.CSSProperties => ({
    padding: "var(--space-sm) var(--space-lg)",
    borderRadius: "var(--border-radius)",
    background: variant === "primary" ? "var(--accent-bg)" : "var(--bg-hover)",
    color: variant === "primary" ? "var(--text-on-accent)" : "var(--text-primary)",
    display: "flex",
    alignItems: "center",
    gap: "var(--space-xs)",
  });

  return (
    <Dialog
      title={action.label}
      onClose={phase === "running" ? () => {} : close}
    >
      {phase === "input" ? (
        <>
          <div
            style={{
              marginBottom: "var(--space-lg)",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
            }}
          >
            {confirmNote ??
              "This changes the device configuration to bring it online. It runs while the device is offline and reconnects when finished."}
          </div>
          {hasParams && (
            <div style={{ marginBottom: "var(--space-lg)" }}>
              <ActionParamFields
                params={action.params}
                values={values}
                onChange={(name, val) => setValues((v) => ({ ...v, [name]: val }))}
                deviceId={deviceId}
              />
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
            <button onClick={close} style={btnStyle("muted")}>
              Cancel
            </button>
            <button
              onClick={startRun}
              disabled={blocked}
              style={{
                ...btnStyle(blocked ? "muted" : "primary"),
                color: blocked ? "var(--text-muted)" : "var(--text-on-accent)",
              }}
            >
              Start
            </button>
          </div>
        </>
      ) : (
        <>
          <StepLog steps={steps} phase={phase} error={error} />
          {phase === "done" && summary && (
            // Below the log, not inside it: the log scrolls, and this is the
            // one line the operator has to actually read.
            <div
              style={{
                marginTop: "var(--space-md)",
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--space-xs)",
                padding: "var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--color-success-bg)",
                border: "1px solid var(--color-success)",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-primary)",
                lineHeight: 1.5,
              }}
            >
              <Check
                size={14}
                style={{ color: "var(--color-success)", flexShrink: 0, marginTop: 3 }}
              />
              <span>{summary}</span>
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "var(--space-lg)" }}>
            <button
              onClick={close}
              disabled={phase === "running"}
              style={{
                ...btnStyle(phase === "error" ? "muted" : "primary"),
                opacity: phase === "running" ? 0.6 : 1,
                cursor: phase === "running" ? "default" : "pointer",
              }}
            >
              {phase === "running" && (
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
              )}
              {phase === "running" ? "Running…" : "Close"}
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function StepLog({
  steps,
  phase,
  error,
}: {
  steps: Step[];
  phase: Phase;
  error: string;
}) {
  return (
    <div
      style={{
        background: "var(--bg-base)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-md)",
        maxHeight: 240,
        overflowY: "auto",
      }}
    >
      {steps.length === 0 && phase === "running" && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
          <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
          Starting…
        </div>
      )}
      {steps.map((s, i) => {
        const isLast = i === steps.length - 1;
        const icon =
          s.status === "done" ? (
            <Check size={14} style={{ color: "var(--color-success)" }} />
          ) : s.status === "error" ? (
            <AlertTriangle size={14} style={{ color: "var(--color-error)" }} />
          ) : isLast && phase === "running" ? (
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite", color: "var(--accent)" }} />
          ) : (
            <Check size={14} style={{ color: "var(--text-muted)" }} />
          );
        return (
          <div
            key={i}
            style={{
              // flex-start, not center: a failure step carries the driver's
              // whole sentence and wraps to several lines.
              display: "flex",
              alignItems: "flex-start",
              gap: "var(--space-xs)",
              fontSize: "var(--font-size-sm)",
              lineHeight: 1.5,
              padding: "2px 0",
              color: s.status === "error" ? "var(--color-error)" : "var(--text-primary)",
            }}
          >
            <span style={{ flexShrink: 0, width: 16, display: "flex", paddingTop: 3 }}>
              {icon}
            </span>
            <span style={{ flex: 1 }}>{s.step}</span>
            {s.pct != null && (
              <span style={{ color: "var(--text-muted)", fontSize: 11, flexShrink: 0 }}>
                {s.pct}%
              </span>
            )}
          </div>
        );
      })}
      {phase === "error" && error && !steps.some((s) => s.status === "error") && (
        <div
          style={{
            marginTop: "var(--space-xs)",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            color: "var(--color-error)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          <AlertTriangle size={14} />
          {error}
        </div>
      )}
    </div>
  );
}
