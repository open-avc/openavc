/**
 * The canvas's right-click menu as the UI Builder draws it: there while the
 * Builder store holds one, its rows bound to the edits of a control or a
 * master (useElementEdits.ts).
 *
 * A row for one control acts on the control clicked, which a right-click does
 * not select. The rows for a selection of several, which the menu shows while
 * more than one control is selected (ContextMenu.tsx), act on the selection,
 * and so does Copy then, whatever the menu hands it.
 */
import { ContextMenu } from "../../components/ui-builder/ContextMenu";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import type { ElementEdits } from "./useElementEdits";

type MenuEdits = Pick<
  ElementEdits,
  "remove" | "duplicate" | "copy" | "paste" | "bringToFront" | "sendToBack" | "promote" | "demote" | "deleteMaster"
>;

export function BuilderContextMenu({ edits }: { edits: MenuEdits }) {
  const menu = useUIBuilderStore((s) => s.contextMenu);
  const setContextMenu = useUIBuilderStore((s) => s.setContextMenu);
  const selected = useUIBuilderStore((s) => s.selectedElementIds);
  const hasClipboard = useUIBuilderStore((s) => !!s.clipboard);
  if (!menu) return null;
  const several = selected.length > 1;
  return (
    <ContextMenu
      x={menu.x}
      y={menu.y}
      elementId={menu.elementId}
      isMaster={menu.isMaster}
      multiSelectCount={selected.length}
      onClose={() => setContextMenu(null)}
      onDuplicate={(id) => edits.duplicate([id])}
      onDelete={(id) => edits.remove([id])}
      onDeleteAll={() => edits.remove(selected)}
      onDuplicateAll={() => edits.duplicate(selected)}
      onCopy={(ids) => edits.copy(several ? selected : ids)}
      onPaste={edits.paste}
      onBringToFront={edits.bringToFront}
      onSendToBack={edits.sendToBack}
      onPromoteToMaster={edits.promote}
      onDemoteFromMaster={edits.demote}
      onDeleteMaster={edits.deleteMaster}
      hasClipboard={hasClipboard}
    />
  );
}
