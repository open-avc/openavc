import { useMemo, useState, useCallback } from "react";
import { Loader2, Check, AlertTriangle, X } from "lucide-react";
import { ElementIcon } from "../../components/ui-builder/ElementIcon";
import { Dialog } from "../../components/shared/Dialog";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import * as api from "../../api/restClient";
import type { DeviceAction } from "../../api/types";
import { isActionVisible } from "./actionVisibility";
import {
  ActionParamFields,
  buildParams,
  hasMissingRequired,
  seedParamValues,
} from "./actionParamFields";
import { hasInvalidParams } from "../../components/shared/paramValidation";
import { SetupActionWizard } from "./SetupActionWizard";

/**
 * Quick Actions strip — driver-declared actions promoted to one-click buttons
 * at the top of the device view. No-param command actions fire on click (with
 * an optional confirm); command actions with params open an input dialog;
 * setup actions (provisioning wizards) open a wizard with live progress. The
 * full "Send Command" list below stays complete — this strip is additive.
 */
export function QuickActions({
  deviceId,
  actions,
  connected,
  liveState,
  onInvoked,
}: {
  deviceId: string;
  actions: DeviceAction[];
  connected: boolean;
  liveState: Record<string, unknown>;
  onInvoked?: () => void;
}) {
  const [dialogAction, setDialogAction] = useState<DeviceAction | null>(null);
  const [confirmAction, setConfirmAction] = useState<DeviceAction | null>(null);
  const [wizardAction, setWizardAction] = useState<DeviceAction | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<
    { id: string; ok: boolean; message: string } | null
  >(null);

  const visible = useMemo(
    () => actions.filter((a) => isActionVisible(a, connected, liveState, deviceId)),
    [actions, connected, liveState, deviceId],
  );

  const invoke = useCallback(
    async (action: DeviceAction, params: Record<string, unknown>) => {
      setRunning(action.id);
      setFeedback(null);
      try {
        await api.invokeDeviceAction(deviceId, action.id, params);
        setFeedback({ id: action.id, ok: true, message: `${action.label} done` });
        onInvoked?.();
      } catch (e) {
        setFeedback({ id: action.id, ok: false, message: String(e) });
      } finally {
        setRunning(null);
        setDialogAction(null);
        setConfirmAction(null);
      }
    },
    [deviceId, onInvoked],
  );

  const handleClick = useCallback(
    (action: DeviceAction) => {
      if (action.kind === "link") {
        // Opens the device's web interface in a new tab — nothing is sent.
        if (action.url) window.open(action.url, "_blank", "noopener,noreferrer");
      } else if (action.kind === "setup") {
        // The wizard owns the whole flow: confirm, params, and live progress.
        setWizardAction(action);
      } else if (Object.keys(action.params).length > 0) {
        setDialogAction(action);
      } else if (action.confirm) {
        setConfirmAction(action);
      } else {
        invoke(action, {});
      }
    },
    [invoke],
  );

  if (visible.length === 0) return null;

  return (
    <div style={{ marginBottom: "var(--space-lg)" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)" }}>
        {visible.map((action) => {
          const isRunning = running === action.id;
          return (
            <button
              key={action.id}
              onClick={() => handleClick(action)}
              disabled={isRunning}
              data-testid={`quick-action-${action.id}`}
              title={action.label}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-sm) var(--space-lg)",
                borderRadius: "var(--border-radius)",
                background: "var(--accent-bg)",
                color: "var(--text-on-accent)",
                fontSize: "var(--font-size-sm)",
                fontWeight: 500,
                border: "none",
                cursor: isRunning ? "default" : "pointer",
                opacity: isRunning ? 0.6 : 1,
              }}
            >
              {isRunning ? (
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
              ) : action.icon ? (
                <ElementIcon name={action.icon} size={14} />
              ) : null}
              {action.label}
            </button>
          );
        })}
      </div>

      {feedback && (
        <div
          style={{
            marginTop: "var(--space-sm)",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            fontSize: "var(--font-size-sm)",
            color: feedback.ok ? "var(--color-success)" : "var(--color-error)",
          }}
        >
          {feedback.ok ? <Check size={14} /> : <AlertTriangle size={14} />}
          <span>{feedback.message}</span>
          <button
            onClick={() => setFeedback(null)}
            title="Dismiss"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              padding: 0,
              display: "flex",
            }}
          >
            <X size={12} />
          </button>
        </div>
      )}

      {confirmAction && (
        <ConfirmDialog
          title={confirmAction.label}
          message={
            typeof confirmAction.confirm === "string"
              ? confirmAction.confirm
              : `Run "${confirmAction.label}"?`
          }
          confirmLabel={running ? "Running..." : "Run"}
          destructive
          onConfirm={() => invoke(confirmAction, {})}
          onCancel={() => setConfirmAction(null)}
        />
      )}

      {dialogAction && (
        <ActionParamDialog
          action={dialogAction}
          deviceId={deviceId}
          running={running === dialogAction.id}
          onCancel={() => setDialogAction(null)}
          onRun={(params) => invoke(dialogAction, params)}
        />
      )}

      {wizardAction && (
        <SetupActionWizard
          deviceId={deviceId}
          action={wizardAction}
          onClose={() => setWizardAction(null)}
          onComplete={() => onInvoked?.()}
        />
      )}
    </div>
  );
}

// --- Param input dialog (command actions) ---

function ActionParamDialog({
  action,
  deviceId,
  running,
  onCancel,
  onRun,
}: {
  action: DeviceAction;
  deviceId: string;
  running: boolean;
  onCancel: () => void;
  onRun: (params: Record<string, unknown>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    seedParamValues(action.params),
  );

  const missingRequired = hasMissingRequired(action.params, values);
  const invalid = hasInvalidParams(action.params, values);
  const blocked = missingRequired || invalid;
  const confirmNote = typeof action.confirm === "string" ? action.confirm : null;

  return (
    <Dialog title={action.label} onClose={onCancel}>
      {confirmNote && (
        <div
          style={{
            marginBottom: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
          }}
        >
          {confirmNote}
        </div>
      )}
      <div style={{ marginBottom: "var(--space-lg)" }}>
        <ActionParamFields
          params={action.params}
          values={values}
          onChange={(name, val) => setValues((v) => ({ ...v, [name]: val }))}
          deviceId={deviceId}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
        <button
          onClick={onCancel}
          style={{
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
          }}
        >
          Cancel
        </button>
        <button
          onClick={() => onRun(buildParams(action.params, values))}
          disabled={running || blocked}
          style={{
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: blocked ? "var(--bg-hover)" : "var(--accent-bg)",
            color: blocked ? "var(--text-muted)" : "var(--text-on-accent)",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            opacity: running ? 0.6 : 1,
          }}
        >
          {running && <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />}
          Run
        </button>
      </div>
    </Dialog>
  );
}
