import { Ban } from "lucide-react";
import type { SkipNote } from "../../store/logStore";

/** "Skipped: <the server's reason>", and when, for the latest fire of a
 *  trigger or start of a macro that did not go ahead, or a step its own
 *  setting held back. Shown on the trigger card and in the macro editor, which
 *  read it from `skipNotices` and the run's step results. `label` goes first
 *  ("Step 2"), where the line is one of a list. */
export function SkipLine({ skip, label, style }: {
  skip: SkipNote;
  label?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: "var(--space-xs)",
        fontSize: 11,
        color: "var(--text-secondary)",
        ...style,
      }}
    >
      <Ban size={11} style={{ flexShrink: 0, alignSelf: "center", color: "#f59e0b" }} />
      {label && <span style={{ fontWeight: 500, flexShrink: 0 }}>{label}:</span>}
      <span>
        <strong style={{ fontWeight: 600 }}>Skipped:</strong> {skip.message}
      </span>
      <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>
        {new Date(skip.at).toLocaleTimeString()}
      </span>
    </div>
  );
}
