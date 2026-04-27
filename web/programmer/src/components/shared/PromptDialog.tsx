import { useEffect, useRef, useState } from "react";

interface PromptDialogProps {
  title: string;
  message?: string;
  placeholder?: string;
  defaultValue?: string;
  submitLabel?: string;
  cancelLabel?: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

export function PromptDialog({
  title,
  message,
  placeholder,
  defaultValue = "",
  submitLabel = "OK",
  cancelLabel = "Cancel",
  onSubmit,
  onCancel,
}: PromptDialogProps) {
  const [value, setValue] = useState(defaultValue);
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const previousFocusRef = useRef<Element | null>(null);

  useEffect(() => {
    previousFocusRef.current = document.activeElement;
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
    return () => {
      (previousFocusRef.current as HTMLElement)?.focus?.();
    };
  }, []);

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

  const handleSubmit = () => {
    if (value.trim()) onSubmit(value.trim());
  };

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
        role="dialog"
        aria-modal="true"
        aria-label={title}
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
        <h3 style={{ marginBottom: "var(--space-md)", fontSize: "var(--font-size-lg)" }}>
          {title}
        </h3>
        {message && (
          <div style={{ color: "var(--text-secondary)", marginBottom: "var(--space-md)" }}>
            {message}
          </div>
        )}
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
          placeholder={placeholder}
          style={{
            width: "100%",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-input)",
            color: "var(--text-primary)",
            fontSize: "var(--font-size-md)",
            marginBottom: "var(--space-xl)",
            boxSizing: "border-box",
          }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
          <button
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
            onClick={handleSubmit}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              opacity: value.trim() ? 1 : 0.5,
            }}
            disabled={!value.trim()}
          >
            {submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
