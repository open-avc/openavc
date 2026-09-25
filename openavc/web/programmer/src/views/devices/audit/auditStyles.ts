import type { CSSProperties } from "react";

/** The wizard's buttons: design tokens only, no global button classes. */
export function buttonStyle(
  variant: "primary" | "muted" | "danger",
  disabled = false,
): CSSProperties {
  const palette = {
    primary: { background: "var(--accent-bg)", color: "var(--text-on-accent)" },
    muted: { background: "var(--bg-hover)", color: "var(--text-primary)" },
    danger: { background: "var(--color-error-bg)", color: "var(--text-primary)" },
  }[variant];
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: "var(--space-xs)",
    padding: "var(--space-sm) var(--space-lg)",
    borderRadius: "var(--border-radius)",
    border: "1px solid var(--border-color)",
    fontSize: "var(--font-size-sm)",
    cursor: disabled ? "default" : "pointer",
    opacity: disabled ? 0.55 : 1,
    ...palette,
  };
}

export const inputStyle: CSSProperties = {
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-input)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};

export const labelStyle: CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  marginBottom: "var(--space-xs)",
};

export const hintStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--text-secondary)",
  marginTop: "var(--space-xs)",
};

export const headingStyle: CSSProperties = {
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
  margin: "0 0 var(--space-md)",
};

export const panelStyle: CSSProperties = {
  padding: "var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
};

/** A spinner that turns without the IDE's global keyframes (see the wizard). */
export const spinStyle: CSSProperties = { animation: "audit-spin 1s linear infinite" };
