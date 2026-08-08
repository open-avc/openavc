import type { ReactNode } from "react";
import { Modal } from "./Modal";

interface DialogProps {
  title: string;
  onClose: () => void;
  /**
   * Off by default here, unlike the rest of the IDE's dialogs: most Dialogs
   * hold a form, and losing half-typed input to a stray click outside is a
   * worse outcome than having to reach for Escape or Cancel. A Dialog that
   * only shows something can turn it on.
   */
  closeOnBackdrop?: boolean;
  children: ReactNode;
}

/** A titled dialog panel. The overlay, Escape and focus handling are Modal's. */
export function Dialog({ title, onClose, closeOnBackdrop = false, children }: DialogProps) {
  return (
    <Modal
      onClose={onClose}
      label={title}
      closeOnBackdrop={closeOnBackdrop}
      panelStyle={{
        padding: "var(--space-xl)",
        minWidth: 380,
        maxWidth: 500,
      }}
    >
      <h3 style={{ marginBottom: "var(--space-lg)", fontSize: "var(--font-size-lg)" }}>
        {title}
      </h3>
      {children}
    </Modal>
  );
}
