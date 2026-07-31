import { useState } from "react";
import { Modal } from "./Modal";

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

  const handleSubmit = () => {
    if (value.trim()) onSubmit(value.trim());
  };

  return (
    <Modal
      onClose={onCancel}
      label={title}
      // Straight into the field with its contents selected, so a suggested
      // name can be replaced by typing.
      initialFocus="input"
      selectOnFocus
      panelStyle={{
        padding: "var(--space-xl)",
        minWidth: 320,
        maxWidth: 480,
      }}
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
    </Modal>
  );
}
