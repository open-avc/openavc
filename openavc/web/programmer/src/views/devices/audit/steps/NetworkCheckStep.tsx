import { useState } from "react";
import {
  AlertTriangle,
  Check,
  Circle,
  Download,
  HelpCircle,
  Loader2,
  Minus,
} from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import { useAuditStore } from "../../../../store/auditStore";
import { EvidenceList } from "../../../discoveryEvidence";
import { ACTIVITY_LABELS, ACTIVITY_ORDER, checkStarted, verdictDrivers } from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { buttonStyle, headingStyle, panelStyle, spinStyle } from "../auditStyles";

/** Step 2: the network check as it runs, then the verdict. */
export function NetworkCheckStep() {
  const session = useAuditStore((s) => s.session);
  const [showWhy, setShowWhy] = useState(false);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);

  const check = session?.check ?? null;
  if (!session || !check) return null;
  const activities = new Map(check.activities.map((a) => [a.key, a]));
  const result = check.result;
  const finished = check.status === "done" || check.status === "failed";

  const download = async () => {
    setError("");
    setDownloading(true);
    try {
      await audit.downloadAuditReport(session.session_id);
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <h2 style={headingStyle}>Network check</h2>
      <ol style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Network check progress">
        {ACTIVITY_ORDER.map((key) => {
          const act = activities.get(key);
          const status = act?.status ?? "pending";
          return (
            <li
              key={key}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--space-sm)",
                padding: "var(--space-xs) 0",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <span style={{ width: 16, flexShrink: 0, paddingTop: 2, display: "flex" }}>
                <ActivityIcon status={status} />
              </span>
              <span style={{ width: 230, flexShrink: 0, fontWeight: 500 }}>
                {ACTIVITY_LABELS[key]}
              </span>
              <span style={{ color: "var(--text-secondary)", minWidth: 0, overflowWrap: "anywhere" }}>
                {act?.message ?? ""}
              </span>
            </li>
          );
        })}
      </ol>

      {check.status === "failed" && <ErrorLine text={check.error} />}

      {result && (
        <div style={{ ...panelStyle, marginTop: "var(--space-lg)" }}>
          <div style={{ fontSize: "var(--font-size-md)", fontWeight: 600 }}>
            {result.verdict.sentence}
          </div>
          <DriverList result={result} />
          <button
            type="button"
            onClick={() => setShowWhy(!showWhy)}
            aria-expanded={showWhy}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              marginTop: "var(--space-sm)",
              cursor: "pointer",
              color: "var(--text-secondary)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontSize: "var(--font-size-xs)",
            }}
          >
            <HelpCircle size={12} /> {showWhy ? "Hide evidence" : "Why?"}
          </button>
          {showWhy && <EvidenceList evidence={result.evidence} />}
        </div>
      )}

      {error && (
        <div style={{ marginTop: "var(--space-md)" }}>
          <ErrorLine text={error} />
        </div>
      )}

      <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: "var(--space-sm)" }}>
        <button
          type="button"
          onClick={() => void download()}
          disabled={!checkStarted(session) || downloading}
          style={buttonStyle("muted", !checkStarted(session) || downloading)}
        >
          {downloading ? <Loader2 size={14} style={spinStyle} /> : <Download size={14} />}
          {finished ? "Download report" : "Download report so far"}
        </button>
        <button
          type="button"
          onClick={() => useAuditStore.getState().setStep("driver")}
          disabled={!finished}
          style={buttonStyle("primary", !finished)}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

function DriverList({ result }: { result: audit.AuditCheckResult }) {
  const listed = verdictDrivers(result.verdict.explanation.drivers, result.verdict.drivers);
  if (listed.length === 0) return null;
  return (
    <div style={{ marginTop: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
      <div style={{ color: "var(--text-secondary)" }}>
        {listed.length === 1 ? "A signal points at this driver:" : "Signals point at these drivers:"}
      </div>
      <ul style={{ margin: "var(--space-xs) 0 0", paddingLeft: "var(--space-lg)" }}>
        {listed.map((d) => (
          <li key={d.id}>
            {d.name} <span style={{ color: "var(--text-secondary)" }}>({d.id})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ActivityIcon({ status }: { status: string }) {
  if (status === "running") {
    return <Loader2 size={14} style={{ ...spinStyle, color: "var(--accent)" }} aria-label="Running" />;
  }
  if (status === "done") return <Check size={14} style={{ color: "var(--color-success)" }} aria-label="Done" />;
  if (status === "skipped") return <Minus size={14} style={{ color: "var(--text-muted)" }} aria-label="Skipped" />;
  if (status === "failed") {
    return <AlertTriangle size={14} style={{ color: "var(--color-error)" }} aria-label="Failed" />;
  }
  return <Circle size={14} style={{ color: "var(--text-muted)" }} aria-label="Waiting" />;
}
