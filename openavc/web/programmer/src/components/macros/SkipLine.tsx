import { Ban } from "lucide-react";
import type { SkipNote } from "../../store/logStore";

/** "Skipped: <the server's reason>", and when, for the latest fire of a
 *  trigger or start of a macro that did not go ahead. Shown on the trigger
 *  card and in the macro editor, which read it from `skipNotices`. */
export function SkipLine({ skip, style }: { skip: SkipNote; style?: React.CSSProperties }) {
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
      <span>
        <strong style={{ fontWeight: 600 }}>Skipped:</strong> {skip.message}
      </span>
      <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>
        {new Date(skip.at).toLocaleTimeString()}
      </span>
    </div>
  );
}
