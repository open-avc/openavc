import { ConfirmDialog } from "./ConfirmDialog";

interface ChangedElsewhereDialogProps {
  /** The server's sentence for the refused save ("This driver was changed by
   *  another session since you opened it."). */
  message: string;
  /** Throw the unsaved edits away and open the copy that is saved now. */
  onReloadTheirs: () => void;
  /** Save the edits again over the copy that is saved now. */
  onSaveMine: () => void;
  /** Close, keeping the unsaved edits in the editor. */
  onKeepEditing: () => void;
}

/**
 * A save refused because someone else saved the same thing first, and the
 * three ways on from it. Shared by every editor that holds a save to the copy
 * it opened (the Driver Builder and the Code view).
 *
 * "Keep editing" is the cancel: Escape and a click outside answer it, and it
 * has the focus, since it is the one choice that loses nothing.
 */
export function ChangedElsewhereDialog({
  message,
  onReloadTheirs,
  onSaveMine,
  onKeepEditing,
}: ChangedElsewhereDialogProps) {
  return (
    <ConfirmDialog
      title="Changed in Another Session"
      message={
        <>
          <p>{message}</p>
          <p style={{ marginTop: "var(--space-md)" }}>
            Reload theirs to see their change, and your unsaved edits are discarded. Save
            mine over it to keep your edits, and their change is replaced.
          </p>
        </>
      }
      cancelLabel="Keep Editing"
      extraActionLabel="Reload Theirs"
      onExtraAction={onReloadTheirs}
      confirmLabel="Save Mine Over It"
      destructive
      onConfirm={onSaveMine}
      onCancel={onKeepEditing}
    />
  );
}
