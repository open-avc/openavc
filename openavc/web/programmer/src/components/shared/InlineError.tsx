// Inline error row — for action-specific failures that should appear next to
// the button the user clicked. Use a toast for global/async errors instead.
//
// Usage:
//   <InlineError message={uninstallError} onDismiss={() => setUninstallError(null)} />

interface InlineErrorProps {
  message: string | null | undefined;
  onDismiss?: () => void;
  /** Optional override for layout — defaults to a small marginBottom. */
  style?: React.CSSProperties;
}

export function InlineError({ message, onDismiss, style }: InlineErrorProps) {
  if (!message) return null;
  return (
    <div
      role="alert"
      style={{
        padding: "var(--space-sm) var(--space-md)",
        background: "var(--danger-dim, rgba(220,38,38,0.1))",
        border: "1px solid var(--danger, rgba(220,38,38,0.4))",
        borderRadius: "var(--radius, var(--border-radius))",
        color: "var(--danger, var(--color-error))",
        fontSize: "var(--font-size-sm)",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: "var(--space-sm)",
        ...style,
      }}
    >
      <span style={{ whiteSpace: "pre-wrap", flex: 1 }}>{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss error"
          style={{
            background: "transparent",
            border: "none",
            color: "inherit",
            cursor: "pointer",
            fontSize: "var(--font-size-sm)",
            padding: 0,
            lineHeight: 1,
          }}
        >
          ✕
        </button>
      )}
    </div>
  );
}
