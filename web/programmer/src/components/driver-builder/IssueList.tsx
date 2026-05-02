import { AlertCircle, AlertTriangle } from "lucide-react";
import type { ValidationIssue } from "./validateDriver";

interface IssueListProps {
  issues: ValidationIssue[];
  /** Compact mode shows fewer pixels — used inside section headers. */
  compact?: boolean;
}

/**
 * Render validation issues as a stacked list of rows. Errors and warnings
 * use distinct colors and icons. Each row is keyed by its message + anchor
 * so React doesn't reorder unrelated rows when one resolves.
 */
export function IssueList({ issues, compact = false }: IssueListProps) {
  if (issues.length === 0) return null;
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        marginBottom: compact ? 0 : "var(--space-md)",
      }}
    >
      {issues.map((issue, i) => (
        <IssueRow key={`${issue.field}-${issue.command}-${issue.param}-${i}`} issue={issue} compact={compact} />
      ))}
    </div>
  );
}

function IssueRow({ issue, compact }: { issue: ValidationIssue; compact: boolean }) {
  const isError = issue.severity === "error";
  const Icon = isError ? AlertCircle : AlertTriangle;
  const tone = isError
    ? {
        background: "rgba(220, 53, 69, 0.08)",
        color: "var(--color-error)",
        border: "1px solid rgba(220, 53, 69, 0.4)",
      }
    : {
        background: "rgba(255, 152, 0, 0.10)",
        color: "var(--color-warning, #d97706)",
        border: "1px solid rgba(255, 152, 0, 0.4)",
      };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 6,
        padding: compact ? "2px 6px" : "var(--space-xs) var(--space-sm)",
        borderRadius: "var(--border-radius)",
        fontSize: compact ? 11 : "var(--font-size-sm)",
        ...tone,
      }}
    >
      <Icon size={compact ? 11 : 14} style={{ flexShrink: 0, marginTop: 2 }} />
      <span style={{ lineHeight: 1.4 }}>{issue.message}</span>
    </div>
  );
}
