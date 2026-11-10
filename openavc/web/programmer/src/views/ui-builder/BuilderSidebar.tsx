/**
 * The UI Builder's left panel: the Elements tab (the palette) and the Outline
 * tab (the page's controls and the masters as a tree), one shown at a time.
 *
 * Which tab is shown is the page's to hold: the frame does not draw the panel
 * in preview or while Ctrl+E has it closed (`frameParts`, BuilderFrame.tsx), and
 * the tab must be the same when it comes back. What is selected, collapsed and
 * locked is the Builder store's.
 */
import type { MasterElement, UIPage } from "../../api/types";
import { ElementPalette } from "../../components/ui-builder/ElementPalette";
import { OutlinePanel } from "../../components/ui-builder/OutlinePanel";
import { isCustomPage } from "../../components/ui-builder/customPage";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import type { ElementEdits } from "./useElementEdits";

export type SidebarTab = "elements" | "outline";

const TABS: { tab: SidebarTab; label: string }[] = [
  { tab: "elements", label: "Elements" },
  { tab: "outline", label: "Outline" },
];

export interface BuilderSidebarProps {
  tab: SidebarTab;
  onTab: (tab: SidebarTab) => void;
  /** The page on screen. */
  page: UIPage | null;
  masters: MasterElement[];
  locked: Set<string>;
  /** What the arrangement being authored leaves out. */
  hidden: Set<string>;
  /** Clicking a palette entry adds one where it fits. */
  onAdd: (type: string) => void;
  edits: Pick<ElementEdits, "toggleHidden" | "reparent" | "swapOrder">;
}

const store = () => useUIBuilderStore.getState();

/** A row clicked selects it; with shift it joins the selection, or leaves it. */
function selectRow(id: string, shift?: boolean) {
  if (shift) store().toggleSelectElement(id);
  else store().selectElement(id);
}
const selectMaster = (id: string) => store().selectMasterElement(id);
const toggleLock = (id: string) => store().toggleLock(id);

export function BuilderSidebar({ tab, onTab, page, masters, locked, hidden, onAdd, edits }: BuilderSidebarProps) {
  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);
  const collapsedOutlineIds = useUIBuilderStore((s) => s.collapsedOutlineIds);
  const toggleOutlineCollapse = useUIBuilderStore((s) => s.toggleOutlineCollapse);
  // Controls placed on a page the author wrote are not drawn.
  const custom = !!page && isCustomPage(page);

  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid var(--border-color)",
        background: "var(--bg-surface)",
      }}
    >
      <div style={{ display: "flex", borderBottom: "1px solid var(--border-color)", flexShrink: 0 }}>
        {TABS.map(({ tab: t, label }) => (
          <button
            key={t}
            onClick={() => onTab(t)}
            style={{
              flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 500,
              background: "transparent", border: "none", cursor: "pointer",
              color: tab === t ? "var(--accent)" : "var(--text-muted)",
              borderBottom: tab === t ? "2px solid var(--accent)" : "2px solid transparent",
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflow: "auto" }}>
        {tab === "elements" ? (
          <ElementPalette
            disabled={custom}
            disabledNote={custom ? "This page shows a page you wrote, so controls placed here are not drawn." : undefined}
            onAdd={onAdd}
          />
        ) : (
          <OutlinePanel
            page={page}
            masterElements={masters}
            selectedElementIds={selectedElementIds}
            selectedMasterElementId={selectedMasterElementId}
            lockedElementIds={locked}
            hiddenElementIds={hidden}
            collapsedIds={collapsedOutlineIds}
            onToggleCollapse={toggleOutlineCollapse}
            onToggleHidden={edits.toggleHidden}
            onReparent={edits.reparent}
            onSelectElement={selectRow}
            onSelectMasterElement={selectMaster}
            onMoveOrder={edits.swapOrder}
            onToggleLock={toggleLock}
          />
        )}
      </div>
    </div>
  );
}
