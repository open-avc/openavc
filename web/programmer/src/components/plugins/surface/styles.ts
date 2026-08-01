/**
 * Inline styles spelled in more than one editor here.
 *
 * The panels are styled inline like the rest of the IDE, so this is not a
 * stylesheet — just the handful two or more of them share, which is what keeps
 * a field in the dial panel looking like the same field in the zone editor.
 */


export const fieldInputStyle: React.CSSProperties = {
  width: "100%", padding: "4px 6px",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)", color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};

export const panelLabelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  fontWeight: 600,
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

export const panelHintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  width: 56,
  flexShrink: 0,
};

export const dialTestBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 28,
  height: 24,
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: 14,
  cursor: "pointer",
};

export const pageMenuConfirmStyle: React.CSSProperties = {
  padding: "2px 8px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  cursor: "pointer",
  fontSize: 12,
};
