import { Modal } from "./Modal";

interface ConfirmDialogProps {
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Optional third button between Cancel and Confirm (e.g. "Keep both"). */
  extraActionLabel?: string;
  onExtraAction?: () => void;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  extraActionLabel,
  onExtraAction,
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      onClose={onCancel}
      role="alertdialog"
      labelledBy="confirm-dialog-title"
      describedBy="confirm-dialog-desc"
      // Focus lands on Confirm, except on a destructive one where it lands on
      // Cancel — so Enter by reflex on a delete confirm can't destroy anything.
      initialFocus={destructive ? "button[data-cancel]" : "button[data-confirm]"}
      panelStyle={{
        padding: "var(--space-xl)",
        minWidth: 320,
        maxWidth: 480,
      }}
    >
      <h3
        id="confirm-dialog-title"
        style={{ marginBottom: "var(--space-md)", fontSize: "var(--font-size-lg)" }}
      >
        {title}
      </h3>
      <div
        id="confirm-dialog-desc"
        style={{ color: "var(--text-secondary)", marginBottom: "var(--space-xl)" }}
      >
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
        {extraActionLabel && onExtraAction && (
          <button
            data-extra
            onClick={onExtraAction}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            {extraActionLabel}
          </button>
        )}
        <button
          data-confirm
          onClick={onConfirm}
          style={{
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: destructive ? "var(--color-error)" : "var(--accent-bg)",
            color: destructive ? "var(--text-on-accent)" : "var(--text-on-accent)",
          }}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
