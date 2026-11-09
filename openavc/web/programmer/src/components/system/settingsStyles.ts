/** The Settings screen's card and field styles: one copy for the page and for
 *  every card drawn inside it (its own sections, the host network card, the
 *  network choice fields), so a row from any of them reads as part of the
 *  same card. */
import type { CSSProperties } from "react";

export const cardStyle: CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-lg)",
  marginBottom: "var(--space-xl)",
};

export const sectionTitle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  fontWeight: 600,
  marginBottom: "var(--space-md)",
};

export const subCardTitle: CSSProperties = {
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
  color: "var(--text-primary)",
  margin: 0,
  marginBottom: "var(--space-xs)",
};

export const subCardDescription: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-lg)",
  lineHeight: 1.5,
};

export const fieldRow: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "200px 1fr",
  gap: "var(--space-sm) var(--space-lg)",
  alignItems: "center",
  marginBottom: "var(--space-md)",
};

export const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
};

export const helpText: CSSProperties = {
  fontSize: 12,
  color: "var(--text-muted)",
  gridColumn: "2",
  marginTop: -4,
  marginBottom: "var(--space-xs)",
};

export const inputStyle: CSSProperties = {
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  background: "var(--bg-input, var(--bg-elevated))",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
};

export const selectStyle: CSSProperties = {
  ...inputStyle,
  cursor: "pointer",
};

export const toggleRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "var(--space-sm) 0",
  marginBottom: "var(--space-sm)",
};

export const radioRow: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-md)",
  padding: "var(--space-sm) 0",
  cursor: "pointer",
};

export const radioInput: CSSProperties = {
  marginTop: 3,
  flexShrink: 0,
  accentColor: "var(--accent)",
};

export const btnStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-sm) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  fontWeight: 500,
  cursor: "pointer",
  transition: "all var(--transition-fast)",
  background: "var(--accent-bg)",
  color: "var(--text-on-accent)",
  border: "1px solid var(--accent-bg)",
};

export const warningBox: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  padding: "var(--space-md)",
  background: "rgba(255, 152, 0, 0.08)",
  border: "1px solid rgba(255, 152, 0, 0.3)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-primary)",
  marginBottom: "var(--space-md)",
};
