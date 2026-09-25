import { useCallback, useEffect, useState } from "react";
import { Check, X } from "lucide-react";
import { Modal } from "../../../components/shared/Modal";
import { useAuditStore } from "../../../store/auditStore";
import * as audit from "../../../api/auditClient";
import { BASE } from "../../../api/base";
import { onConnect, onMessage, send } from "../../../api/wsClient";
import { parseApiError } from "../../../api/errors";
import { AUDIT_STEPS, stepFor, type AuditStep } from "./auditHelpers";
import { TargetStep } from "./steps/TargetStep";
import { NetworkCheckStep } from "./steps/NetworkCheckStep";
import { DriverStep } from "./steps/DriverStep";
import { ReportStep } from "./steps/ReportStep";
import { buttonStyle } from "./auditStyles";
import { ErrorLine } from "./auditParts";

/**
 * Device Audit: point OpenAVC at one device, run the network check, and
 * download the report. One audit runs per server; reopening the wizard picks
 * up the running one.
 *
 * The wizard follows the audit over the WebSocket (`audit.subscribe`, sent to
 * this client only) and re-subscribes after a reconnect. Closing the page
 * cancels the audit, so project devices it paused reconnect; the report so
 * far stays in Recent reports.
 */
export function DeviceAuditWizard() {
  const step = useAuditStore((s) => s.step);
  const session = useAuditStore((s) => s.session);
  const sessionId = session?.status === "active" ? session.session_id : null;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Pick up a running audit, if there is one.
  useEffect(() => {
    let alive = true;
    audit
      .getCurrentAudit()
      .then(({ session: current }) => {
        if (!alive || !current) return;
        useAuditStore.getState().setSession(current);
        useAuditStore.getState().setStep(stepFor(current));
      })
      .catch((e) => alive && setError(parseApiError(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  // Follow the running audit.
  useEffect(() => {
    if (!sessionId) return;
    const subscribe = () => send({ type: "audit.subscribe", session_id: sessionId });
    const offMessage = onMessage((msg) => {
      if (typeof msg.type === "string" && msg.type.startsWith("audit.")) {
        useAuditStore.getState().applyMessage(msg);
      }
    });
    const offConnect = onConnect(subscribe);
    subscribe();
    return () => {
      offMessage();
      offConnect();
      send({ type: "audit.unsubscribe" });
    };
  }, [sessionId]);

  // A closed page cancels the audit so paused project devices come back.
  useEffect(() => {
    if (!sessionId) return;
    const onPageHide = () => {
      try {
        fetch(`${BASE}/audit/sessions/${encodeURIComponent(sessionId)}?cancel=true`, {
          method: "DELETE",
          keepalive: true,
        }).catch(() => {});
      } catch {
        /* best effort; the idle timeout covers the rest */
      }
    };
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, [sessionId]);

  const cancel = useCallback(async () => {
    const current = useAuditStore.getState().session;
    if (current && current.status === "active") {
      try {
        await audit.endAudit(current.session_id, true);
      } catch {
        /* already ended */
      }
    }
    useAuditStore.getState().closeWizard();
  }, []);

  const ended = session !== null && session.status !== "active";

  return (
    <Modal
      label="Audit a device"
      closeOnBackdrop={false}
      closeOnEscape={false}
      panelStyle={{
        width: "min(980px, 94vw)",
        height: "min(760px, 92vh)",
        display: "flex",
        overflow: "hidden",
      }}
    >
      <style>{SPIN_CSS}</style>
      <StepRail step={step} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-md)",
            padding: "var(--space-md) var(--space-lg)",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "var(--font-size-lg)", fontWeight: 600 }}>Audit a Device</div>
            {session && (
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
                {session.target.address}
                {session.target.ip && session.target.ip !== session.target.address
                  ? ` (${session.target.ip})`
                  : ""}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={cancel}
            aria-label={sessionId ? "Cancel the audit" : "Close"}
            title={sessionId ? "Cancel the audit" : "Close"}
            style={{ ...buttonStyle("muted"), padding: "var(--space-xs) var(--space-sm)" }}
          >
            <X size={14} /> {sessionId ? "Cancel audit" : "Close"}
          </button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "var(--space-lg)" }}>
          {error && <ErrorLine text={error} />}
          {ended && session && <EndedNotice status={session.status} />}
          {loading ? null : step === "target" ? (
            <TargetStep />
          ) : step === "network" ? (
            <NetworkCheckStep />
          ) : step === "driver" ? (
            <DriverStep />
          ) : (
            <ReportStep />
          )}
        </div>
      </div>
    </Modal>
  );
}

const SPIN_CSS = `@keyframes audit-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`;

function StepRail({ step }: { step: AuditStep }) {
  const current = AUDIT_STEPS.findIndex((s) => s.key === step);
  return (
    <nav
      aria-label="Audit steps"
      style={{
        width: 190,
        flexShrink: 0,
        background: "var(--bg-surface)",
        borderRight: "1px solid var(--border-color)",
        padding: "var(--space-lg) var(--space-md)",
      }}
    >
      <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {AUDIT_STEPS.map((s, i) => {
          const done = i < current;
          const here = i === current;
          return (
            <li
              key={s.key}
              aria-current={here ? "step" : undefined}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "var(--space-sm) 0",
                color: here ? "var(--text-primary)" : "var(--text-secondary)",
                fontWeight: here ? 600 : 400,
                fontSize: "var(--font-size-sm)",
              }}
            >
              <span
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  fontSize: "var(--font-size-xs)",
                  background: here ? "var(--accent-bg)" : "var(--bg-hover)",
                  color: here ? "var(--text-on-accent)" : "var(--text-secondary)",
                }}
              >
                {done ? <Check size={12} /> : i + 1}
              </span>
              {s.label}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

const ENDED_TEXT: Record<string, string> = {
  expired: "This audit ended after 30 minutes without activity. Its report is in Recent reports.",
  shutdown: "This audit ended because OpenAVC stopped. Its report is in Recent reports.",
  cancelled: "This audit was cancelled.",
  finished: "This audit is finished.",
};

function EndedNotice({ status }: { status: string }) {
  return (
    <div
      role="status"
      style={{
        marginBottom: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: "var(--color-info-bg)",
        border: "1px solid var(--color-info)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {ENDED_TEXT[status] ?? "This audit has ended."}{" "}
      <button
        type="button"
        onClick={() => useAuditStore.getState().openWizard()}
        style={{ ...buttonStyle("muted"), display: "inline-flex", marginLeft: "var(--space-sm)" }}
      >
        Start a new audit
      </button>
    </div>
  );
}
