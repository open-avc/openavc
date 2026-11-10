import { useState, useEffect, useCallback, useMemo } from "react";
import { DndContext, DragOverlay } from "@dnd-kit/core";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import type { MasterElement } from "../api/types";
import * as wsClient from "../api/wsClient";
import { useProjectStore } from "../store/projectStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { useNavigationStore } from "../store/navigationStore";
import { usePluginStore } from "../store/pluginStore";
import { Canvas } from "../components/ui-builder/Canvas";
import { CanvasToolbar } from "../components/ui-builder/CanvasToolbar";
import { PropertiesPanel } from "../components/ui-builder/PropertiesPanel";
import { ContextMenu } from "../components/ui-builder/ContextMenu";
import { ThemeStudio } from "../components/ui-builder/ThemeStudio";
import { StylesheetEditor } from "../components/ui-builder/StylesheetEditor";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import {
  SCREEN_PRESETS,
  lockedIdsFor,
  presetForOrientation,
  layoutOrientation,
  resolveHidden,
} from "../components/ui-builder/uiBuilderHelpers";
import { showSuccess } from "../store/toastStore";
import { projectEdit, useEditRun } from "./ui-builder/useEditRun";
import { useBuilderShortcuts } from "./ui-builder/builderShortcuts";
import { useElementEdits } from "./ui-builder/useElementEdits";
import { usePaletteDrop } from "./ui-builder/usePaletteDrop";
import { PaletteDragGhost } from "./ui-builder/PaletteDragGhost";
import { useValidation, ValidationReport } from "./ui-builder/ValidationReport";
import { PanelSettingsDialog } from "./ui-builder/PanelSettingsDialog";
import { SaveFailedBanner } from "./ui-builder/SaveFailedBanner";
import { ToolbarActions } from "./ui-builder/ToolbarActions";
import { BuilderSidebar, type SidebarTab } from "./ui-builder/BuilderSidebar";
import { themeFallbackMode, useBuilderTheme, withoutThemeStyles } from "./ui-builder/builderTheme";

export function UIBuilderView() {
  const project = useProjectStore((s) => s.project);
  const run = useEditRun();

  // Reactive selectors — only subscribe to values that affect render
  const selectedPageId = useUIBuilderStore((s) => s.selectedPageId);
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
  const { selectPage, selectElement, undo, redo } = useMemo(() => ({
    selectPage: (...args: any[]) => (useUIBuilderStore.getState().selectPage as any)(...args),
    selectElement: (...args: any[]) => (useUIBuilderStore.getState().selectElement as any)(...args),
    undo: () => useUIBuilderStore.getState().undo(),
    redo: () => useUIBuilderStore.getState().redo(),
  }), []);

  // Consume pending focus from navigation store (on mount)
  useEffect(() => {
    const focus = useNavigationStore.getState().consumeFocus();
    if (focus?.type === "element") {
      const pageId = focus.detail?.startsWith("page:") ? focus.detail.slice(5) : undefined;
      if (pageId) selectPage(pageId);
      // Small delay so the page renders before selecting the element
      requestAnimationFrame(() => selectElement(focus.id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const contextMenu = useUIBuilderStore((s) => s.contextMenu);
  const setContextMenu = useUIBuilderStore((s) => s.setContextMenu);
  const panelElements = usePluginStore((s) => s.extensions.panel_elements);

  const [showSettings, setShowSettings] = useState(false);
  const [showPalette, setShowPalette] = useState(true);
  // The left panel's tab outlives the panel, which preview and Ctrl+E take away.
  const [leftTab, setLeftTab] = useState<SidebarTab>("elements");
  const [showThemeStudio, setShowThemeStudio] = useState(false);
  const [showStylesheet, setShowStylesheet] = useState(false);
  const validation = useValidation(project);
  const [confirmResetStyles, setConfirmResetStyles] = useState(false);
  const theme = useBuilderTheme(project?.ui?.settings?.theme_id);
  const themes = theme.themes;

  // Auto-select first page if none selected
  useEffect(() => {
    if (!selectedPageId && project?.ui?.pages?.length) {
      selectPage(project.ui.pages[0].id);
    }
  }, [selectedPageId, project, selectPage]);


  // Listen for server-initiated page navigation (preview mode)
  useEffect(() => {
    if (!previewMode) return;
    const unsub = wsClient.onMessage((msg: Record<string, unknown>) => {
      if (msg.type === "ui.navigate" && msg.page_id) {
        selectPage(String(msg.page_id));
      }
    });
    return unsub;
  }, [previewMode, selectPage]);

  // Autosave is driven by useProjectStore.debouncedSave, called from
  // touchMutation() on every UI Builder mutation. See store/uiBuilderStore.ts.

  // Flush pending save before the tab unloads so the 2 s debounce window
  // can't lose the last edit.
  useEffect(() => {
    const handler = () => useProjectStore.getState().flushSave();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  // Derived data
  const pages = project?.ui?.pages ?? [];
  const currentPage = pages.find((p) => p.id === selectedPageId) || pages[0] || null;
  const selectedElement = currentPage?.elements.find(
    (e) => e.id === selectedElementId,
  ) || null;
  const masterElements = project?.ui?.master_elements || [];
  const selectedMasterElement = selectedMasterElementId
    ? masterElements.find((m) => m.id === selectedMasterElementId) || null
    : null;
  // Lock is a project field now rather than session state, so it comes off the
  // elements themselves and survives a reload.
  const lockedElementIds = lockedIdsFor(currentPage ?? undefined, masterElements);
  // What the arrangement being authored leaves out. Inherited hides included,
  // because that is what the panel would actually draw.
  const hiddenElementIds = currentPage
    ? resolveHidden(currentPage, activeLayoutId)
    : new Set<string>();

  // The screen presets are all landscape. A portrait arrangement gets the same
  // screen turned, because a portrait panel authored on a landscape canvas is
  // not something anyone can design in. vmin is the shorter edge either way, so
  // turning the canvas does not resize a single glyph.
  const preset = SCREEN_PRESETS[screenPresetIndex];
  const { width: screenWidth, height: screenHeight } = presetForOrientation(
    preset,
    layoutOrientation(currentPage ?? undefined, activeLayoutId),
  );

  const edits = useElementEdits({
    project,
    page: currentPage,
    layoutId: activeLayoutId,
    run,
    clipboard,
    selected: { elementId: selectedElementId, masterId: selectedMasterElementId },
  });

  // --- Theme handlers (also used by ThemeStudio) ---

  const handleThemeChange = useCallback(
    (id: string) => {
      if (!project) return;
      const settings = project.ui.settings;
      // settings.theme is the fallback used when theme_id can't be resolved.
      const mode = themeFallbackMode(themes, id);
      // Clear accent_color / font_family / theme_overrides when switching
      // themes. These are per-project overrides that were tuned for the
      // PREVIOUS theme — carrying them forward makes the new theme look
      // wrong (e.g. blue accent on a gold-themed Luxury preset).
      run.commit(projectEdit(project, {
        settings: { ...settings, theme_id: id, theme: mode, accent_color: "", font_family: "", theme_overrides: {} },
      }, "Change theme"));
    },
    [project, themes, run],
  );

  // Live theme variable overrides — burst-undo so dragging a color picker
  // produces one undo entry, not 50.
  const handleUpdateThemeOverrides = useCallback(
    (overrides: Record<string, unknown>) => {
      if (!project) return;
      const settings = project.ui.settings;
      run.join(projectEdit(project, { settings: { ...settings, theme_overrides: overrides } }, "Edit theme overrides"));
    },
    [project, run],
  );

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
                onTheme={() => setShowThemeStudio(true)}
                onStylesheet={() => setShowStylesheet(true)}
                onSettings={() => setShowSettings(true)}
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
                <div
                  style={{
                    height: "100%",
                    overflow: "auto",
                    borderLeft: "1px solid var(--border-color)",
                    background: "var(--bg-surface)",
                  }}
                >
                  <PropertiesPanel
                    element={selectedElement}
                    selectedElementIds={selectedElementIds}
                    masterElement={selectedMasterElement}
                    page={currentPage}
                    project={project}
                    themeDefaults={theme.defaults}
                    themes={themes}
                    onThemeChange={showThemeStudio ? undefined : handleThemeChange}
                    onOpenStylesheet={() => setShowStylesheet(true)}
                    onChange={edits.editControl}
                    onRenameElement={edits.rename}
                    onPageChange={edits.editPage}
                    onReparent={edits.reparent}
                    onMasterElementChange={edits.editMaster}
                    onDemoteMaster={edits.demote}
                    onDeleteMaster={edits.deleteMaster}
                  />
                </div>
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

      {/* Context menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          elementId={contextMenu.elementId}
          isMaster={contextMenu.isMaster}
          multiSelectCount={selectedElementIds.length}
          onClose={() => setContextMenu(null)}
          // Its one-control rows act on the control clicked, its rows for a
          // selection of several on the selection.
          onDuplicate={(id) => edits.duplicate([id])}
          onDelete={(id) => edits.remove([id])}
          onDeleteAll={() => edits.remove(selectedElementIds)}
          onDuplicateAll={() => edits.duplicate(selectedElementIds)}
          onCopy={(ids) => edits.copy(selectedElementIds.length > 1 ? selectedElementIds : ids)}
          onPaste={edits.paste}
          onBringToFront={edits.bringToFront}
          onSendToBack={edits.sendToBack}
          onPromoteToMaster={edits.promote}
          onDemoteFromMaster={edits.demote}
          onDeleteMaster={edits.deleteMaster}
          hasClipboard={!!clipboard}
        />
      )}

      {/* Theme Studio */}
      {project && (
        <ThemeStudio
          open={showThemeStudio}
          onClose={() => setShowThemeStudio(false)}
          themes={themes}
          project={project}
          currentThemeId={project.ui.settings.theme_id || "dark-default"}
          themeOverrides={project.ui.settings.theme_overrides || {}}
          onChangeTheme={handleThemeChange}
          onClearOverrides={() => handleUpdateThemeOverrides({})}
          onRefreshThemes={theme.loadThemes}
          onThemeSaved={theme.refetch}
          onResetElementStyles={() => setConfirmResetStyles(true)}
          // The raw preset, NOT the layout-turned one: a theme preview is about
          // colour and type, and it should not change shape because of which
          // arrangement happened to be selected behind the dialog.
          panelWidth={preset?.width ?? 1024}
          panelHeight={preset?.height ?? 600}
        />
      )}
      {/* Project stylesheet */}
      {project && showStylesheet && (
        <StylesheetEditor
          project={project}
          previewPageId={currentPage?.id ?? null}
          panelWidth={preset?.width ?? 1024}
          panelHeight={preset?.height ?? 600}
          onSave={(css) => {
            run.commit(projectEdit(project, { custom_css: css }, "Edit project stylesheet"));
            showSuccess("Project stylesheet saved");
          }}
          onClose={() => setShowStylesheet(false)}
        />
      )}
      {confirmResetStyles && (
        <ConfirmDialog
          title="Reset element styles to theme defaults?"
          message="This removes per-element style overrides for every property the current theme defines defaults for. Elements will inherit their appearance from the theme. This can be undone with Ctrl+Z."
          confirmLabel="Reset Styles"
          destructive
          onCancel={() => setConfirmResetStyles(false)}
          onConfirm={() => {
            setConfirmResetStyles(false);
            if (!project) return;
            const newPages = withoutThemeStyles(project.ui.pages, theme.defaults);
            run.commit(projectEdit(project, { pages: newPages }, "Reset element styles to theme"));
            showSuccess("Element styles reset to theme defaults");
          }}
        />
      )}

      {validation.issues !== null && (
        <ValidationReport issues={validation.issues} onClose={validation.close} />
      )}

      {/* Settings dialog */}
      {showSettings && (
        <PanelSettingsDialog
          settings={project.ui.settings}
          pages={project.ui.pages}
          onUpdate={(settings) => run.commit(projectEdit(project, { settings }, "Edit project settings"))}
          onClose={() => setShowSettings(false)}
        />
      )}

    </DndContext>
  );
}
