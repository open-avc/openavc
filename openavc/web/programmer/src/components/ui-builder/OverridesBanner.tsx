/**
 * The banner over the Theme Studio's sections while the project has theme
 * overrides of its own: how many, which, and a button that asks the Builder to
 * clear them. The Studio edits the theme as saved, without the project's
 * overrides, and the panel draws the theme with them on top, so while there
 * are any the two can differ; the banner is what says so. It draws nothing
 * while there are none.
 */
import { AlertTriangle } from "lucide-react";

const AMBER = "#ffa726";

export function OverridesBanner({ overrides, onClear }: { overrides: Record<string, unknown>; onClear: () => void }) {
  const keys = Object.keys(overrides);
  if (keys.length === 0) return null;
  return (
    <div
      style={{
        padding: "8px 12px",
        background: "rgba(255,167,38,0.12)",
        borderBottom: "1px solid rgba(255,167,38,0.4)",
        color: AMBER,
        fontSize: 11,
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <AlertTriangle size={14} style={{ flexShrink: 0 }} />
      <div style={{ flex: 1 }}>
        <strong>{keys.length} project override{keys.length === 1 ? "" : "s"}</strong>
        {" "}applied on top of this theme:{" "}
        <code style={{ fontSize: 10 }}>{keys.join(", ")}</code>
        <div style={{ color: "var(--text-muted)", marginTop: 2 }}>
          The panel may not match what the editor shows until these are cleared or saved into the theme.
        </div>
      </div>
      <button
        onClick={onClear}
        style={{
          padding: "4px 10px",
          background: "transparent",
          border: `1px solid ${AMBER}`,
          borderRadius: 4,
          color: AMBER,
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 600,
          flexShrink: 0,
        }}
      >
        Clear overrides
      </button>
    </div>
  );
}
