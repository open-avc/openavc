import { useEffect, useRef } from "react";

interface ConfirmDialogProps {
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<Element | null>(null);

  useEffect(() => {
    previousFocusRef.current = document.activeElement;
    requestAnimationFrame(() => {
      const selector = destructive ? "button[data-cancel]" : "button[data-confirm]";
      const btn = dialogRef.current?.querySelector<HTMLElement>(selector);
      btn?.focus();
    });
    return () => {
      (previousFocusRef.current as HTMLElement)?.focus?.();
    };
  }, [destructive]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onCancel(); return; }
      if (e.key === "Tab" && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault(); first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onCancel]);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10000,
      }}
      onClick={onCancel}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-desc"
        tabIndex={-1}
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xl)",
          minWidth: 320,
          maxWidth: 480,
          boxShadow: "var(--shadow-lg)",
          outline: "none",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="confirm-dialog-title" style={{ marginBottom: "var(--space-md)", fontSize: "var(--font-size-lg)" }}>
          {title}
        </h3>
        <div id="confirm-dialog-desc" style={{ color: "var(--text-secondary)", marginBottom: "var(--space-xl)" }}>
          {message}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
          <button
            data-cancel
            onClick={onCancel}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            {cancelLabel}
          </button>
          <button
            data-confirm
            onClick={onConfirm}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: destructive ? "var(--color-error)" : "var(--accent-bg)",
              color: destructive ? "#fff" : "var(--text-on-accent)",
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
