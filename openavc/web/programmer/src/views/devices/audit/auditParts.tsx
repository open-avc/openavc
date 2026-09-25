/** Small pieces the audit wizard's steps share. */

export function ErrorLine({ text }: { text: string }) {
  return (
    <div
      role="alert"
      style={{
        marginBottom: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: "var(--color-error-bg)",
        border: "1px solid var(--color-error)",
        color: "var(--text-primary)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {text}
    </div>
  );
}
