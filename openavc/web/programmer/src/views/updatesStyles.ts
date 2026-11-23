import type { CSSProperties } from "react";

/** The card and the heading every section of the System Updates page is drawn with. */
export const cardStyle: CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-lg)",
};

export const sectionTitle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  fontWeight: 600,
  marginBottom: "var(--space-md)",
};

const btnStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-sm) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  fontWeight: 500,
  cursor: "pointer",
  transition: "all var(--transition-fast)",
};

/** The page's main action button and its quieter one. */
export const primaryBtn: CSSProperties = {
  ...btnStyle,
  background: "var(--accent-bg)",
  color: "#fff",
  border: "1px solid var(--accent)",
};

export const secondaryBtn: CSSProperties = {
  ...btnStyle,
  background: "transparent",
  color: "var(--text-primary)",
  border: "1px solid var(--border-color)",
};
