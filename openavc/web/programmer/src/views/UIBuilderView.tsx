import { useState } from "react";
import { DndContext, DragOverlay } from "@dnd-kit/core";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import { useProjectStore } from "../store/projectStore";
import { useFlushBeforeUnload } from "../store/saveState";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { usePluginStore } from "../store/pluginStore";
import { CanvasToolbar } from "../components/ui-builder/CanvasToolbar";
import { useEditRun } from "./ui-builder/useEditRun";
import { useBuilderShortcuts } from "./ui-builder/builderShortcuts";
import { useElementEdits } from "./ui-builder/useElementEdits";
import { usePaletteDrop } from "./ui-builder/usePaletteDrop";
import { PaletteDragGhost } from "./ui-builder/PaletteDragGhost";
import { useValidation } from "./ui-builder/ValidationReport";
import { BuilderDialogs, themeListChange, useBuilderDialogs } from "./ui-builder/BuilderDialogs";
import { BuilderContextMenu } from "./ui-builder/BuilderContextMenu";
import { SaveFailedBanner } from "./ui-builder/SaveFailedBanner";
import { ToolbarActions } from "./ui-builder/ToolbarActions";
import { BuilderSidebar, type SidebarTab } from "./ui-builder/BuilderSidebar";
import { BuilderProperties } from "./ui-builder/BuilderProperties";
import { useBuilderTheme } from "./ui-builder/builderTheme";
import { usePanelEdits } from "./ui-builder/panelEdits";
import { useShownPage } from "./ui-builder/useShownPage";
import { BuilderCanvas } from "./ui-builder/BuilderCanvas";
import { BuilderNote } from "./ui-builder/BuilderNote";

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

  return (
    <DndContext {...palette.dndProps}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          overflow: "hidden",
        }}
      >
        <SaveFailedBanner />

        {/* Toolbar — two fixed rows; Theme/Settings dock at the end of the pages row */}
        <CanvasToolbar
          pages={pages}
          selectedPageId={currentPage?.id || null}
          onValidate={validation.run}
          trailing={
            !previewMode ? (
              <ToolbarActions
                onTheme={() => dialogs.show("studio")}
                onStylesheet={() => dialogs.show("stylesheet")}
                onSettings={() => dialogs.show("settings")}
              />
            ) : undefined
          }
        />

        {/* Main 3-panel layout */}
        <PanelGroup direction="horizontal" style={{ flex: 1 }}>
          {/* Left: Element Palette / Outline (Ctrl+E to toggle) */}
          {!previewMode && showPalette && (
            <>
              <Panel defaultSize={15} minSize={10} maxSize={25}>
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
              </Panel>
              <PanelResizeHandle
                style={{
                  width: 4,
                  background: "var(--border-color)",
                  cursor: "col-resize",
                }}
              />
            </>
          )}

          {/* Center: Canvas */}
          <Panel defaultSize={previewMode ? 100 : 60}>
            <BuilderCanvas page={currentPage} masters={project.ui?.master_elements} themeVariables={theme.variables} />
          </Panel>

          {/* Right: Properties Panel */}
          {!previewMode && (
            <>
              <PanelResizeHandle
                style={{
                  width: 4,
                  background: "var(--border-color)",
                  cursor: "col-resize",
                }}
              />
              <Panel defaultSize={25} minSize={15} maxSize={40}>
                <BuilderProperties
                  page={currentPage}
                  masters={masterElements}
                  project={project}
                  theme={theme}
                  onThemeChange={themeListChange(dialogs, panelEdits.chooseTheme)}
                  onOpenStylesheet={() => dialogs.show("stylesheet")}
                  edits={edits}
                />
              </Panel>
            </>
          )}
        </PanelGroup>
      </div>

      {/* Drag overlay — outlined footprint sized to the drop target, with the
          element type label. Only shown while the pointer is OFF the canvas;
          over it, the canvas draws the snapped footprint in its place. */}
      <DragOverlay dropAnimation={null}>
        {palette.ghost ? <PaletteDragGhost {...palette.ghost} /> : null}
      </DragOverlay>

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
