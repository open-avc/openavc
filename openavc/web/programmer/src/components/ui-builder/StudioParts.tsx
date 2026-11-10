/**
 * The pieces the Theme Studio's editor sections share: a section's frame with
 * its title, and the button beside a field that puts back the value the theme
 * was saved with (drawn only while the field differs from it).
 */
import type { CSSProperties, ReactNode } from "react";
import { RotateCcw } from "lucide-react";

const SECTION: CSSProperties = {
  background: "var(--bg-surface)",
  borderRadius: 6,
  border: "1px solid var(--border-color)",
  marginBottom: 10,
};

const SECTION_TITLE: CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  color: "var(--text-secondary)",
  padding: "8px 10px",
  borderBottom: "1px solid var(--border-color)",
};

/** A section of the editor column: its title, then the body the caller lays out. */
export function StudioSection({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div style={SECTION}>
      <div style={SECTION_TITLE}>{title}</div>
      {children}
    </div>
  );
}

/** Put a field back to the value the theme was saved with. */
export function ResetToSaved({ saved, size, onReset }: { saved: unknown; size: number; onReset: () => void }) {
  return (
    <button
      type="button"
      onClick={onReset}
      title={`Reset to saved: ${saved}`}
      style={{
        display: "flex",
        alignItems: "center",
        padding: 2,
        background: "transparent",
        border: "none",
        cursor: "pointer",
        color: "var(--accent)",
        flexShrink: 0,
        opacity: 0.7,
      }}
    >
      <RotateCcw size={size} />
    </button>
  );
}
