/**
 * The UI Builder's right panel: the Properties panel for what is selected on
 * the page on screen (a control, a master, or else the page itself), bound to
 * the edits of a control or a master and to the theme list.
 *
 * The control selected is looked for among the page on screen's controls and
 * the master among the masters; what is selected is the Builder store's. The
 * panel is not drawn in preview, so the page places it.
 */
import type { MasterElement, ProjectConfig, UIElement, UIPage } from "../../api/types";
import { PropertiesPanel } from "../../components/ui-builder/PropertiesPanel";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import type { ThemeDefaults, ThemeSummary } from "./builderTheme";
import type { ElementEdits } from "./useElementEdits";

export interface BuilderPropertiesProps {
  /** The page on screen. */
  page: UIPage | null;
  masters: MasterElement[];
  project: ProjectConfig;
  theme: { defaults: ThemeDefaults; themes: ThemeSummary[] };
  /** What the theme list chooses with; none while the Theme Studio is open. */
  onThemeChange: ((id: string) => void) | undefined;
  onOpenStylesheet: () => void;
  edits: Pick<ElementEdits, "editControl" | "rename" | "editPage" | "reparent" | "editMaster" | "demote" | "deleteMaster">;
}

/** The control selected, if it is on the page on screen. */
function controlSelected(page: UIPage | null, id: string | null): UIElement | null {
  return page?.elements.find((e) => e.id === id) || null;
}

function masterSelected(masters: MasterElement[], id: string | null): MasterElement | null {
  return id ? masters.find((m) => m.id === id) || null : null;
}

export function BuilderProperties({ page, masters, project, theme, onThemeChange, onOpenStylesheet, edits }: BuilderPropertiesProps) {
  const selectedElementId = useUIBuilderStore((s) => s.selectedElementId);
  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);
  return (
    <div
      style={{
        height: "100%",
        overflow: "auto",
        borderLeft: "1px solid var(--border-color)",
        background: "var(--bg-surface)",
      }}
    >
      <PropertiesPanel
        element={controlSelected(page, selectedElementId)}
        selectedElementIds={selectedElementIds}
        masterElement={masterSelected(masters, selectedMasterElementId)}
        page={page}
        project={project}
        themeDefaults={theme.defaults}
        themes={theme.themes}
        onThemeChange={onThemeChange}
        onOpenStylesheet={onOpenStylesheet}
        onChange={edits.editControl}
        onRenameElement={edits.rename}
        onPageChange={edits.editPage}
        onReparent={edits.reparent}
        onMasterElementChange={edits.editMaster}
        onDemoteMaster={edits.demote}
        onDeleteMaster={edits.deleteMaster}
      />
    </div>
  );
}
