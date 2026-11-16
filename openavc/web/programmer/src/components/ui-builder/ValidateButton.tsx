/**
 * The toolbar's Validate, carrying how many issues the project holds as it
 * stands, so an issue is seen without anybody thinking to press it. The count
 * is Validate's own (views/ui-builder/projectIssues.ts): the report the button
 * opens lists exactly that many.
 */
import { Check } from "lucide-react";

interface ValidateButtonProps {
  onValidate: () => void;
  /** Issues Validate would list now; null until first worked out. */
  issueCount: number | null;
}

/** What the badge says, "" for no badge. */
export function issueBadge(count: number | null): string {
  if (!count) return "";
  return count > 99 ? "99+" : String(count);
}

export function ValidateButton({ onValidate, issueCount }: ValidateButtonProps) {
  const badge = issueBadge(issueCount);
  return (
    <button
      onClick={onValidate}
      style={{
        display: "flex", alignItems: "center", gap: 4,
        padding: "3px 10px", borderRadius: "var(--border-radius)",
        background: "transparent", border: "1px solid var(--border-color)",
        color: "var(--text-secondary)", fontSize: "var(--font-size-sm)",
        cursor: "pointer", flexShrink: 0,
      }}
      title="Validate project for broken references"
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      <Check size={12} /> Validate
      {badge && (
        <span
          data-testid="validate-issue-count"
          aria-label={`${issueCount} issue${issueCount === 1 ? "" : "s"}`}
          style={{
            marginLeft: 2, minWidth: 16, padding: "0 5px", borderRadius: 8,
            background: "#ff9800", color: "#000", fontSize: 10, fontWeight: 700,
            lineHeight: "16px", textAlign: "center",
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}
