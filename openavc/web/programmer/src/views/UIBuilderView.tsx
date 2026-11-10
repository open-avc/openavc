import { useState } from "react";
import { DndContext } from "@dnd-kit/core";
import { useProjectStore } from "../store/projectStore";
import { useFlushBeforeUnload } from "../store/saveState";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { usePluginStore } from "../store/pluginStore";
import { CanvasToolbar } from "../components/ui-builder/CanvasToolbar";
import { useEditRun } from "./ui-builder/useEditRun";
import { useBuilderShortcuts } from "./ui-builder/builderShortcuts";
import { useElementEdits } from "./ui-builder/useElementEdits";
import { usePaletteDrop } from "./ui-builder/usePaletteDrop";
import { PaletteDragOverlay } from "./ui-builder/PaletteDragGhost";
import { useValidation } from "./ui-builder/ValidationReport";
import { BuilderDialogs, themeListChange, useBuilderDialogs } from "./ui-builder/BuilderDialogs";
import { BuilderContextMenu } from "./ui-builder/BuilderContextMenu";
import { ToolbarActions } from "./ui-builder/ToolbarActions";
import { BuilderSidebar, type SidebarTab } from "./ui-builder/BuilderSidebar";
import { BuilderProperties } from "./ui-builder/BuilderProperties";
import { useBuilderTheme } from "./ui-builder/builderTheme";
import { usePanelEdits } from "./ui-builder/panelEdits";
import { useShownPage } from "./ui-builder/useShownPage";
import { BuilderCanvas } from "./ui-builder/BuilderCanvas";
import { BuilderNote } from "./ui-builder/BuilderNote";
import { BuilderFrame, frameParts } from "./ui-builder/BuilderFrame";

export function UIBuilderView() {
  const project = useProjectStore((s) => s.project);
  const run = useEditRun();
  const { pages, page: currentPage, masters: masterElements, locked: lockedElementIds, hidden: hiddenElementIds } =
    useShownPage(project);

  // Reactive selectors — only subscribe to values that affect render
  const selectedElementId = useUIBuilderStore((s) => s.selectedElementId);
  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);
  const previewMode = useUIBuilderStore((s) => s.previewMode);
  const activeLayoutId = useUIBuilderStore((s) => s.activeLayoutId);
  const clipboard = useUIBuilderStore((s) => s.clipboard);

  const panelElements = usePluginStore((s) => s.extensions.panel_elements);

  const [showPalette, setShowPalette] = useState(true);
  // The left panel's tab outlives the panel, which preview and Ctrl+E take away.
  const [leftTab, setLeftTab] = useState<SidebarTab>("elements");
  const dialogs = useBuilderDialogs();
  const validation = useValidation(project);
  const theme = useBuilderTheme(project?.ui?.settings?.theme_id);

  useFlushBeforeUnload();

  const edits = useElementEdits({
    project,
    page: currentPage,
    layoutId: activeLayoutId,
    run,
    clipboard,
    selected: { elementId: selectedElementId, masterId: selectedMasterElementId },
  });

  const panelEdits = usePanelEdits(project, run, theme);

  const palette = usePaletteDrop({
    page: currentPage,
    layoutId: activeLayoutId,
    panelElements,
    onAdd: edits.add,
  });

  useBuilderShortcuts({
    previewMode,
    page: currentPage,
    activeLayoutId,
    masters: masterElements,
    selectedIds: selectedElementIds,
    selectedMasterId: selectedMasterElementId,
    locked: lockedElementIds,
    hasClipboard: !!clipboard,
    togglePalette: () => setShowPalette((v) => !v),
    edits,
    run,
  });

  if (!project) return <BuilderNote>Loading project...</BuilderNote>;
  const parts = frameParts(previewMode, showPalette);

  return (
    <DndContext {...palette.dndProps}>
      <BuilderFrame
        parts={parts}
        toolbar={
          // Two fixed rows; the dialogs' buttons dock at the end of the pages row.
          <CanvasToolbar
            pages={pages}
            selectedPageId={currentPage?.id || null}
            onValidate={validation.run}
            trailing={
              parts.dialogButtons ? (
                <ToolbarActions
                  onTheme={() => dialogs.show("studio")}
                  onStylesheet={() => dialogs.show("stylesheet")}
                  onSettings={() => dialogs.show("settings")}
                />
              ) : undefined
            }
          />
        }
        left={
          <BuilderSidebar
            tab={leftTab}
            onTab={setLeftTab}
            page={currentPage}
            masters={masterElements}
            locked={lockedElementIds}
            hidden={hiddenElementIds}
            onAdd={palette.add}
            edits={edits}
          />
        }
        canvas={<BuilderCanvas page={currentPage} masters={project.ui?.master_elements} themeVariables={theme.variables} />}
        right={
          <BuilderProperties
            page={currentPage}
            masters={masterElements}
            project={project}
            theme={theme}
            onThemeChange={themeListChange(dialogs, panelEdits.chooseTheme)}
            onOpenStylesheet={() => dialogs.show("stylesheet")}
            edits={edits}
          />
        }
      />

      <PaletteDragOverlay ghost={palette.ghost} />

      <BuilderContextMenu edits={edits} />

      <BuilderDialogs
        dialogs={dialogs}
        project={project}
        page={currentPage}
        theme={theme}
        edits={panelEdits}
        validation={validation}
      />
    </DndContext>
  );
}
