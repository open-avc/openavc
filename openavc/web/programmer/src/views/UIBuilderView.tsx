import { useState, useEffect, useMemo } from "react";
import { DndContext, DragOverlay } from "@dnd-kit/core";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import type { MasterElement } from "../api/types";
import { useProjectStore } from "../store/projectStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { usePluginStore } from "../store/pluginStore";
import { Canvas } from "../components/ui-builder/Canvas";
import { CanvasToolbar } from "../components/ui-builder/CanvasToolbar";
import { SCREEN_PRESETS } from "../components/ui-builder/uiBuilderHelpers";
import { canvasScreen } from "../components/ui-builder/canvasScreen";
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
  const showGrid = useUIBuilderStore((s) => s.showGrid);
  const zoom = useUIBuilderStore((s) => s.zoom);
  const screenPresetIndex = useUIBuilderStore((s) => s.screenPresetIndex);
  const clipboard = useUIBuilderStore((s) => s.clipboard);

  // Stable action refs — use getState() to avoid re-render subscriptions
  const { selectElement, undo, redo } = useMemo(() => ({
    selectElement: (...args: any[]) => (useUIBuilderStore.getState().selectElement as any)(...args),
    undo: () => useUIBuilderStore.getState().undo(),
    redo: () => useUIBuilderStore.getState().redo(),
  }), []);

  const panelElements = usePluginStore((s) => s.extensions.panel_elements);

  const [showPalette, setShowPalette] = useState(true);
  // The left panel's tab outlives the panel, which preview and Ctrl+E take away.
  const [leftTab, setLeftTab] = useState<SidebarTab>("elements");
  const dialogs = useBuilderDialogs();
  const validation = useValidation(project);
  const theme = useBuilderTheme(project?.ui?.settings?.theme_id);

  // Autosave is driven by useProjectStore.debouncedSave, called from
  // touchMutation() on every UI Builder mutation. See store/uiBuilderStore.ts.

  // Flush pending save before the tab unloads so the 2 s debounce window
  // can't lose the last edit.
  useEffect(() => {
    const handler = () => useProjectStore.getState().flushSave();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  const preset = SCREEN_PRESETS[screenPresetIndex];
  const { width: screenWidth, height: screenHeight } = canvasScreen(screenPresetIndex, currentPage, activeLayoutId);

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
    togglePreview: () => useUIBuilderStore.getState().setPreviewMode(!previewMode),
    togglePalette: () => setShowPalette((v) => !v),
    deselect: () => selectElement(null),
    moveMaster: (id, placements) => edits.editMaster(id, { placements } as Partial<MasterElement>),
    nudge: edits.nudge,
    deleteMaster: edits.deleteMaster,
    remove: edits.remove,
    undo: () => { run.endRun(); undo(); },
    redo: () => { run.endRun(); redo(); },
    copy: edits.copy,
    paste: edits.paste,
    duplicate: edits.duplicate,
  });

  if (!project) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--text-muted)",
        }}
      >
        Loading project...
      </div>
    );
  }

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
            {currentPage ? (
              <Canvas
                page={currentPage}
                previewMode={previewMode}
                showGrid={showGrid}
                zoom={zoom}
                screenWidth={screenWidth}
                screenHeight={screenHeight}
                masterElements={project?.ui?.master_elements}
                themeVariables={theme.variables}
              />
            ) : (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                  color: "var(--text-muted)",
                }}
              >
                No pages yet. Click + to add a page.
              </div>
            )}
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
        preset={preset}
        theme={theme}
        edits={panelEdits}
        validation={validation}
      />
    </DndContext>
  );
}
