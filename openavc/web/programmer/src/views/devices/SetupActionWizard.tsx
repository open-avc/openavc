import { useCallback, useEffect, useState } from "react";
import { Loader2, Check, AlertTriangle } from "lucide-react";
import { Dialog } from "../../components/shared/Dialog";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { CopyButton } from "../../components/shared/CopyButton";
import type { DeviceAction } from "../../api/types";
import type { SetupStage } from "../../api/setupClient";
import { ActionParamFields, buildParams, hasMissingRequired, seedParamValues } from "./actionParamFields";
import { hasInvalidParams } from "../../components/shared/paramValidation";
import { useSetupRun } from "./useSetupRun";

const stageLabel: Record<SetupStage, string> = {
  not_requested: "Not requested", reported_complete: "Reported complete", failed: "Failed", unknown: "Not confirmed",
};
const buttonStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-lg)", borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)", display: "inline-flex", gap: "var(--space-xs)", alignItems: "center",
};

/** A prepared run is retained until close. Recovery reads it, never pairs again. */
export function SetupActionWizard({ deviceId, action, onClose, onComplete }: {
  deviceId: string; action: DeviceAction; onClose: () => void; onComplete?: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => seedParamValues(action.params));
  const [confirmClose, setConfirmClose] = useState(false);
  const run = useSetupRun(deviceId, action.id);
  const result = run.status?.result;
  const terminal = run.status?.status === "terminal";
  const input = !run.status && !run.pending;
  const blocked = hasMissingRequired(action.params, values) || hasInvalidParams(action.params, values);

  useEffect(() => { if (run.status?.run_id) setValues({}); }, [run.status?.run_id]);

  const close = useCallback(() => {
    run.dispose();
    setValues({});
    onComplete?.();
    onClose();
  }, [run.dispose, onComplete, onClose]);
  const requestClose = () => {
    if (run.needsRetention || run.pending) setConfirmClose(true);
    else close();
  };

  return <>
    <Dialog title={action.label} onClose={requestClose}>
      {input ? <>
        <p style={{ marginBottom: "var(--space-lg)", color: "var(--text-secondary)" }}>
          {typeof action.confirm === "string" ? action.confirm :
            "This runs the device's setup action. It may change settings or reconnect the device."}
        </p>
        <ActionParamFields params={action.params} values={values}
          onChange={(name, value) => setValues((old) => ({ ...old, [name]: value }))} deviceId={deviceId} />
      </> : <>
        {!terminal && !run.unavailable && <p role="status" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
          {run.status?.status === "prepared" ? "Ready to start this attempt." : "Setup in progress"}
        </p>}
        {run.steps.length > 0 && <div aria-label="Setup progress" style={{
          background: "var(--bg-base)", border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)", padding: "var(--space-md)", maxHeight: 220, overflowY: "auto",
        }}>
          {run.steps.map((step) => <div key={step.sequence} style={{ display: "flex", gap: "var(--space-sm)", marginBottom: 4 }}>
            <span style={{ flex: 1 }}>{step.detail}</span>
            {step.percent != null && <span>{step.percent}%</span>}
          </div>)}
        </div>}
        {result && <div style={{ marginTop: "var(--space-md)" }}>
          <p role="status" style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
            {result.outcome === "succeeded" ? <Check size={16} color="var(--color-success)" /> :
              <AlertTriangle size={16} color="var(--color-warning)" />}
            <span><strong>{{ succeeded: "Completed", failed: "Failed", degraded: "Partly completed", unverified: "Outcome not confirmed" }[result.outcome]}</strong><br />{result.summary}</span>
          </p>
          {result.findings.length > 0 && <ul>{result.findings.map((finding, i) =>
            <li key={`${finding.code}-${i}`}>{finding.detail || finding.code}
              {finding.fields?.length ? <dl style={{ fontSize: "var(--font-size-sm)" }}>
                {finding.fields.map((field, j) => <div key={`${field.label}-${j}`}>
                  <dt>{field.label}</dt><dd>{field.value === null ? "Not reported" :
                    typeof field.value === "boolean" ? (field.value ? "Yes" : "No") : String(field.value)}</dd>
                </div>)}
              </dl> : null}
            </li>)}</ul>}
          <dl style={{ fontSize: "var(--font-size-sm)" }}>
            <dt>Save settings</dt><dd>{stageLabel[result.config_save]}</dd>
            <dt>Reconnect</dt><dd>{stageLabel[result.reconnect]}</dd>
          </dl>
        </div>}
        {!run.unavailable && run.status?.secret.state === "available" && <section aria-label="Unsaved credentials" style={{ marginTop: "var(--space-lg)" }}>
          <p>Generated credentials are available for this attempt. Save them before closing. A server restart or expiry removes this copy.</p>
          {run.secret ? <>
            {Object.entries(run.secret.values).map(([key, value]) => <label key={key} style={{ display: "block", marginBottom: 8 }}>
              {key}<span style={{ display: "flex", gap: 8 }}>
                <input aria-label={key} value={value} readOnly autoComplete="off" spellCheck={false} style={{ minWidth: 0, flex: 1 }} />
                <CopyButton value={value} title={`Copy ${key}`} />
              </span>
            </label>)}
            <button style={buttonStyle} disabled={run.secretBusy} onClick={() => void run.acknowledge()}>I've saved these credentials</button>
          </> : <button style={buttonStyle} disabled={run.secretBusy} onClick={() => void run.reveal()}>Reveal credentials</button>}
        </section>}
        {run.status?.secret.state === "reported_saved" && <p>Generated credentials were saved with the device settings.</p>}
        {run.status?.secret.state === "acknowledged" && <p>The retained credential copy has been cleared.</p>}
      </>}
      {run.error && <p role="alert" style={{ color: "var(--color-error)" }}>{run.error}</p>}
      <div style={{ display: "flex", justifyContent: "flex-end", flexWrap: "wrap", gap: "var(--space-sm)", marginTop: "var(--space-lg)" }}>
        {run.status && !run.unavailable && <button style={buttonStyle} onClick={() => void run.refresh()}>Check status</button>}
        {run.status?.status === "prepared" && !run.unavailable && <button style={buttonStyle}
          disabled={run.pending} onClick={() => void run.retryPrepared()}>Start this attempt</button>}
        <button style={buttonStyle} onClick={requestClose}>{input ? "Cancel" : "Close"}</button>
        {input && <button disabled={blocked} style={{ ...buttonStyle, background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
          onClick={() => { void run.start(buildParams(action.params, values)); }}>Start</button>}
      </div>
    </Dialog>
    {confirmClose && <ConfirmDialog title="Close this setup attempt?" destructive
      message="Closing loses access to this attempt's result and any unsaved credentials. It does not stop work already sent to the device or revoke its credentials."
      confirmLabel="Close and discard access" cancelLabel="Keep open" onConfirm={close} onCancel={() => setConfirmClose(false)} />}
  </>;
}
