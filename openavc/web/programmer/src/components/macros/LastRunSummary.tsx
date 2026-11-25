import { CheckCircle, Clock, XCircle } from "lucide-react";
import type { MacroConfig } from "../../api/types";
import type { MacroLastRun } from "../../store/logStore";
import { SkipLine } from "./SkipLine";
import { macroNamer, resultStepLabel } from "./stepResults";

/** How the last run ended, in the words the summary's heading uses. */
function lastRunWords(lastRun: MacroLastRun, isSuccess: boolean): string {
  if (isSuccess) return "Completed";
  if (lastRun.status === "cancelled") return "Cancelled";
  return lastRun.status === "error" ? "Failed" : "Completed with errors";
}

const ERROR_LINE_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "2px 0",
  color: "#ef4444",
  fontSize: 11,
};

/** The macro editor's note under its steps on how the last run went: how it
 *  ended, how long it took, each step that failed and each that was skipped,
 *  named by the step that holds it (`stepResults.ts`). */
export function LastRunSummary({ lastRun, allMacros }: {
  lastRun: MacroLastRun;
  allMacros: MacroConfig[];
}) {
  const durationSec = (lastRun.duration / 1000).toFixed(1);
  const time = new Date(lastRun.completedAt).toLocaleTimeString();
  const hasErrors = lastRun.stepErrors.length > 0;
  const isSuccess = lastRun.status === "completed" && !hasErrors;
  const nameOf = macroNamer(allMacros);

  return (
    <div
      style={{
        marginTop: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        border: `1px solid ${isSuccess ? "rgba(16,185,129,0.3)" : "rgba(239,68,68,0.3)"}`,
        background: isSuccess ? "rgba(16,185,129,0.06)" : "rgba(239,68,68,0.06)",
        fontSize: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: hasErrors ? "var(--space-xs)" : 0 }}>
        {isSuccess ? (
          <CheckCircle size={14} style={{ color: "#10b981" }} />
        ) : (
          <XCircle size={14} style={{ color: "#ef4444" }} />
        )}
        <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
          Last run: {lastRunWords(lastRun, isSuccess)}
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          <Clock size={11} style={{ verticalAlign: "middle", marginRight: 2 }} />
          {durationSec}s at {time}
        </span>
      </div>
      {lastRun.stepErrors.map((err, i) => (
        <div key={i} style={ERROR_LINE_STYLE}>
          <XCircle size={11} style={{ flexShrink: 0 }} />
          <span style={{ fontWeight: 500 }}>{resultStepLabel(err, lastRun.macroId, nameOf)}:</span>
          <span title={err.error}>{err.message}</span>
          {err.device && <span style={{ color: "var(--text-muted)" }}>({err.device})</span>}
        </div>
      ))}
      {lastRun.stepSkips.map((skip, i) => (
        <SkipLine
          key={i}
          skip={skip}
          label={resultStepLabel(skip, lastRun.macroId, nameOf)}
          style={{ padding: "2px 0" }}
        />
      ))}
    </div>
  );
}
