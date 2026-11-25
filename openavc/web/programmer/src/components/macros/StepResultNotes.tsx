import { AlertTriangle } from "lucide-react";
import type { StepError, StepSkip } from "../../store/logStore";
import { SkipLine } from "./SkipLine";

const ERROR_STYLE: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  fontSize: 12,
  color: "#ef4444",
  background: "rgba(239,68,68,0.08)",
  borderTop: "1px solid rgba(239,68,68,0.2)",
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
};

const SKIP_STYLE: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  background: "rgba(245,158,11,0.08)",
  borderTop: "1px solid rgba(245,158,11,0.2)",
};

/** Under a step in the macro editor: what it did not do in the run on screen,
 *  its failure in the room's words and its skip with the reason. */
export function StepResultNotes({ stepError, stepSkip }: {
  stepError: StepError | undefined;
  stepSkip: StepSkip | undefined;
}) {
  return (
    <>
      {stepError && (
        <div style={ERROR_STYLE}>
          <AlertTriangle size={12} style={{ flexShrink: 0 }} />
          <span title={stepError.error}>{stepError.message}</span>
        </div>
      )}
      {stepSkip && <SkipLine skip={stepSkip} style={SKIP_STYLE} />}
    </>
  );
}
