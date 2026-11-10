import { useState, useEffect, useCallback, useMemo } from "react";
import { Settings, Palette, FileCode } from "lucide-react";
import { DndContext, DragOverlay } from "@dnd-kit/core";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import type { UIElement, UIPage, UISettings, Placement, MasterElement } from "../api/types";
import * as wsClient from "../api/wsClient";
import { listThemes, getTunnelPrefix } from "../api/restClient";
import { commandIssues } from "../api/commandIssues";
import { showError } from "../store/toastStore";
import { useProjectStore } from "../store/projectStore";
import { useUIBuilderStore, type UndoScope } from "../store/uiBuilderStore";
import { useNavigationStore } from "../store/navigationStore";
import { usePluginStore } from "../store/pluginStore";
import { ElementPalette } from "../components/ui-builder/ElementPalette";
import { OutlinePanel } from "../components/ui-builder/OutlinePanel";
import { Canvas } from "../components/ui-builder/Canvas";
import { CanvasToolbar } from "../components/ui-builder/CanvasToolbar";
import { PropertiesPanel } from "../components/ui-builder/PropertiesPanel";
import { ContextMenu } from "../components/ui-builder/ContextMenu";
import { ThemeStudio } from "../components/ui-builder/ThemeStudio";
import { StylesheetEditor } from "../components/ui-builder/StylesheetEditor";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { Modal } from "../components/shared/Modal";
import { NumericInput } from "../components/shared/NumericInput";
import {
  SCREEN_PRESETS,
  createDefaultElement,
  addElementToPage,
  removeElementFromPage,
  updateElementInPage,
  duplicateElementInPage,
  duplicateElementsInPage,
  clipboardForSelection,
  pasteIntoPage,
  topmostSelection,
  reorderElement,
  swapElementsInOrder,
  reparentElement,
  promoteToMaster,
  demoteFromMaster,
  updateMasterElement,
  removeMasterElement,
  renameElement,
  validateElementId,
  validateProject,
  projectCommandActions,
  autoPlace,
  defaultElementSize,
  lockedIdsFor,
  pageSnap,
  resolveDropParent,
  presetForOrientation,
  layoutOrientation,
  resolveHidden,
  withHidden,
  type ValidationIssue,
} from "../components/ui-builder/uiBuilderHelpers";
import { isLightColor } from "../components/ui-builder/colorUtils";
import { showSuccess, showInfo } from "../store/toastStore";
import { useEditRun } from "./ui-builder/useEditRun";
import { nudgedPages, useBuilderShortcuts } from "./ui-builder/builderShortcuts";
import { takenIds, topLevelBoxes } from "./ui-builder/paletteDrop";
import { usePaletteDrop } from "./ui-builder/usePaletteDrop";
import { PaletteDragGhost } from "./ui-builder/PaletteDragGhost";

export function UIBuilderView() {
  const project = useProjectStore((s) => s.project);
  const save = useProjectStore((s) => s.save);
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
  const { selectPage, selectElement, selectMasterElement, undo, redo, setClipboard } = useMemo(() => ({
    selectPage: (...args: any[]) => (useUIBuilderStore.getState().selectPage as any)(...args),
    selectElement: (...args: any[]) => (useUIBuilderStore.getState().selectElement as any)(...args),
    selectMasterElement: (...args: any[]) => (useUIBuilderStore.getState().selectMasterElement as any)(...args),
    undo: () => useUIBuilderStore.getState().undo(),
    redo: () => useUIBuilderStore.getState().redo(),
    setClipboard: (...args: any[]) => (useUIBuilderStore.getState().setClipboard as any)(...args),
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
  const collapsedOutlineIds = useUIBuilderStore((s) => s.collapsedOutlineIds);
  const toggleOutlineCollapse = useUIBuilderStore((s) => s.toggleOutlineCollapse);
  const panelElements = usePluginStore((s) => s.extensions.panel_elements);

  const [showSettings, setShowSettings] = useState(false);
  const openGlobalShortcuts = useCallback(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "/", ctrlKey: true, bubbles: true }));
  }, []);
  const [showPalette, setShowPalette] = useState(true);
  const [leftTab, setLeftTab] = useState<"elements" | "outline">("elements");
  const [showThemeStudio, setShowThemeStudio] = useState(false);
  const [showStylesheet, setShowStylesheet] = useState(false);
  const [validationIssues, setValidationIssues] = useState<ValidationIssue[] | null>(null);
  const [confirmResetStyles, setConfirmResetStyles] = useState(false);
  // Validate asks the platform one question it cannot answer itself: whether
  // each chosen command has the parameters its driver requires. Everything
  // else is derived from the project in hand, so a request that fails still
  // leaves a full report -- it just cannot speak to that one thing.
  const runValidation = useCallback(async () => {
    if (!project) return;
    const found = await commandIssues(projectCommandActions(project));
    setValidationIssues(validateProject(project, found));
  }, [project]);
  const [themeElementDefaults, setThemeElementDefaults] = useState<Record<string, Record<string, unknown>>>({});
  const [themeVariables, setThemeVariables] = useState<Record<string, unknown>>({});
  const [themes, setThemes] = useState<{ id: string; name: string; version: string; author: string; description: string; preview_colors: string[]; variables: Record<string, unknown>; source: string }[]>([]);
  // Bumped by the Theme Studio after Save Changes to force the canvas iframe
  // to re-fetch the theme from the server (same theme_id but changed file).
  const [themeFetchKey, setThemeFetchKey] = useState(0);

  // Load themes list
  const loadThemes = useCallback(() => {
    listThemes().then(setThemes).catch(() => showError("Failed to load themes"));
  }, []);
  useEffect(() => { loadThemes(); }, [loadThemes]);

  // Auto-select first page if none selected
  useEffect(() => {
    if (!selectedPageId && project?.ui?.pages?.length) {
      selectPage(project.ui.pages[0].id);
    }
  }, [selectedPageId, project, selectPage]);

  // Load the full set of effective defaults for every element type so the
  // Properties panel always shows the real value the element renders with.
  // Sources: theme element_defaults (per-type), theme variables (CSS-derived),
  // and CSS baseline defaults. Every themed property must be present here —
  // if it's missing, the Properties panel shows blank instead of the real value.
  const themeId = project?.ui?.settings?.theme_id;
  useEffect(() => {
    const id = themeId || "dark-default";
    fetch(`${getTunnelPrefix()}/api/themes/${id}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((theme) => {
        const vars = theme?.variables || {};
        const baseDefaults = theme?.element_defaults || {};

        // Start with the full element_defaults from the theme — these already
        // have bg_color, text_color, border_color, border_radius, border_width,
        // box_shadow, etc. for every element type the theme author defined.
        const synthesized: Record<string, Record<string, unknown>> = {};
        for (const [type, defaults] of Object.entries(baseDefaults)) {
          synthesized[type] = { ...(defaults as Record<string, unknown>) };
        }

        // Button types get their base colors from button_* CSS variables,
        // not element_defaults. Overlay these so the Properties panel shows them.
        const buttonTypes = ["button", "page_nav", "camera_preset", "keypad"];
        for (const t of buttonTypes) {
          if (!synthesized[t]) synthesized[t] = {};
          if (vars.button_bg && !synthesized[t].bg_color) synthesized[t].bg_color = vars.button_bg;
          if (vars.button_text && !synthesized[t].text_color) synthesized[t].text_color = vars.button_text;
          if (vars.button_border && !synthesized[t].border_color) synthesized[t].border_color = vars.button_border;
        }

        // Interactive elements get accent_color and track_color from CSS
        // variables (--el-accent from vars.accent, --el-surface from vars.surface).
        // These aren't in element_defaults — they're set at render time.
        const interactiveTypes = ["slider", "fader", "select", "text_input", "page_nav", "keypad"];
        for (const t of interactiveTypes) {
          if (!synthesized[t]) synthesized[t] = {};
          if (vars.accent) synthesized[t].accent_color = vars.accent;
          if (vars.surface) synthesized[t].track_color = vars.surface;
        }

        // Non-button elements inherit panel_text via CSS. Fill in any type
        // that doesn't already have text_color from element_defaults.
        if (vars.panel_text) {
          for (const t of ["label", "slider", "fader", "gauge", "level_meter", "list", "select", "text_input", "group", "clock", "matrix", "status_led"]) {
            if (!synthesized[t]) synthesized[t] = {};
            if (!synthesized[t].text_color) synthesized[t].text_color = vars.panel_text;
          }
        }

        // CSS baseline defaults that every element inherits if nothing else is
        // set. NOT font_size: that used to be a hard-coded 1rem here, which was
        // the Builder's private idea of the panel's text default and drifted
        // from the stylesheet's. The panel canvas now reports what it actually
        // drew (uiBuilderStore.textDefaultsRem) and the Style panel shows that.
        const cssBaseline: Record<string, unknown> = {};
        if (vars.border_radius) cssBaseline.border_radius = vars.border_radius;
        if (vars.font_family) cssBaseline.font_family = vars.font_family;
        for (const t of Object.keys(synthesized)) {
          for (const [k, v] of Object.entries(cssBaseline)) {
            if (synthesized[t][k] === undefined) synthesized[t][k] = v;
          }
        }
        setThemeElementDefaults(synthesized);
        setThemeVariables(vars);
      })
      .catch(() => { setThemeElementDefaults({}); setThemeVariables({}); });
  }, [themeId, themeFetchKey]);

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
  const error = useProjectStore((s) => s.error);
  // A version conflict already has its own banner, above the whole app, saying
  // the same sentence with the only two answers that can work. This one would
  // repeat it and offer Retry beside them, which can only conflict again.
  const conflictDetected = useProjectStore((s) => s.conflictDetected);

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
  // A page that hands the whole screen to markup the author wrote. It needs a
  // file to be one: a page that says custom and names none still draws its
  // elements, so the palette must stay open for it.
  const currentPageIsCustom =
    currentPage?.render_mode === "custom" && !!currentPage?.custom_file;
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

  // --- Mutation helpers ---
  // An edit of the pages. `coalesce` makes it a run edit, so a held arrow
  // (one nudge per keypress) is one undo entry rather than fifty.
  const applyMutation = useCallback(
    (mutate: (pages: UIPage[]) => UIPage[], description: string, coalesce = false) => {
      if (!project) return;
      const edit = {
        before: { pages: project.ui.pages },
        description,
        patch: { ui: { ...project.ui, pages: mutate(project.ui.pages) } },
      };
      if (coalesce) run.join(edit);
      else run.commit(edit);
    },
    [project, run],
  );

  const handleDeleteElement = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      applyMutation((p) => removeElementFromPage(p, currentPage.id, elementId), "Delete element");
      selectElement(null);
    },
    [currentPage, applyMutation, selectElement],
  );

  const handleDeleteSelection = useCallback(() => {
    if (!currentPage) return;
    applyMutation((pages) => {
      let result = pages;
      for (const eid of selectedElementIds) {
        result = removeElementFromPage(result, currentPage.id, eid);
      }
      return result;
    }, `Delete ${selectedElementIds.length} elements`);
    selectElement(null);
  }, [currentPage, selectedElementIds, applyMutation, selectElement]);

  const handleDuplicateElement = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      const masterIds = masterElements.map((m) => m.id);
      applyMutation((p) => duplicateElementInPage(p, currentPage.id, elementId, masterIds, activeLayoutId), "Duplicate element");
    },
    [currentPage, masterElements, applyMutation, activeLayoutId],
  );

  // Duplicating a selection copies each container's contents with it, so a
  // selection holding a container and something inside it produces one copy of
  // that child, not two. The count in the label is what actually gets copied.
  const handleDuplicateSelection = useCallback(() => {
    if (!currentPage) return;
    const masterIds = masterElements.map((m) => m.id);
    const roots = topmostSelection(currentPage, selectedElementIds);
    if (roots.length === 0) return;
    applyMutation(
      (p) => duplicateElementsInPage(p, currentPage.id, selectedElementIds, masterIds, activeLayoutId),
      roots.length === 1 ? "Duplicate element" : `Duplicate ${roots.length} elements`,
    );
  }, [currentPage, masterElements, selectedElementIds, applyMutation, activeLayoutId]);

  // Hiding is per-arrangement: it writes to the layout being authored and leaves
  // every other one showing the control.
  const handleToggleHidden = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      const hidden = resolveHidden(currentPage, activeLayoutId).has(elementId);
      applyMutation(
        (p) =>
          p.map((pg) =>
            pg.id === currentPage.id
              ? withHidden(pg, elementId, !hidden, activeLayoutId)
              : pg,
          ),
        hidden ? "Show in this layout" : "Hide in this layout",
      );
    },
    [currentPage, activeLayoutId, applyMutation],
  );

  const handleCopyElement = useCallback(
    (elementIds: string[]) => {
      if (!currentPage) return;
      const payload = clipboardForSelection(currentPage, elementIds, activeLayoutId);
      if (payload.elements.length === 0) return;
      setClipboard(payload);
    },
    [currentPage, activeLayoutId, setClipboard],
  );

  const handlePasteElement = useCallback(() => {
    if (!clipboard || clipboard.elements.length === 0 || !currentPage) return;
    const masterIds = masterElements.map((m) => m.id);
    const count = clipboard.elements.length;
    applyMutation(
      (p) => pasteIntoPage(p, currentPage.id, clipboard, masterIds, activeLayoutId),
      `Paste ${count === 1 ? "element" : `${count} elements`}`,
    );
  }, [clipboard, currentPage, masterElements, applyMutation, activeLayoutId]);

  const handleBringToFront = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      applyMutation((p) =>
        reorderElement(p, currentPage.id, elementId, "front"),
        "Bring to front",
      );
    },
    [currentPage, applyMutation],
  );

  const handleSendToBack = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      applyMutation((p) =>
        reorderElement(p, currentPage.id, elementId, "back"),
        "Send to back",
      );
    },
    [currentPage, applyMutation],
  );

  // --- Master element mutation handlers ---

  const handlePromoteToMaster = useCallback((elementId: string) => {
    if (!project || !currentPage) return;
    const result = promoteToMaster(
      project.ui.pages,
      project.ui.master_elements || [],
      currentPage.id,
      elementId,
    );
    run.commit({
      before: { pages: project.ui.pages, master_elements: project.ui.master_elements || [] },
      description: "Promote to master",
      patch: { ui: { ...project.ui, pages: result.pages, master_elements: result.masterElements } },
    });
    selectElement(null);
  }, [project, currentPage, run, selectElement]);

  const handleDemoteFromMaster = useCallback((masterElementId: string) => {
    if (!project || !currentPage) return;
    const result = demoteFromMaster(
      project.ui.pages,
      project.ui.master_elements || [],
      masterElementId,
      currentPage.id,
    );
    run.commit({
      before: { pages: project.ui.pages, master_elements: project.ui.master_elements || [] },
      description: "Demote from master",
      patch: { ui: { ...project.ui, pages: result.pages, master_elements: result.masterElements } },
    });
    selectMasterElement(null);
  }, [project, currentPage, run, selectMasterElement]);

  const handleRenameElement = useCallback(
    (oldId: string, newId: string) => {
      if (!project) return;
      const masters = project.ui.master_elements || [];
      const err = validateElementId(newId, oldId, project.ui.pages, masters);
      if (err) {
        showError(err);
        return;
      }
      if (newId === oldId) return;
      const result = renameElement(
        project.ui.pages,
        masters,
        project.macros || [],
        project.variables || [],
        project.scripts || [],
        oldId,
        newId,
      );
      // Snapshot only the scopes that actually changed (skip ones the helper
      // didn't touch — e.g. variables stay reference-equal if no var.source_key
      // matched). Keeps the undo entry small and the rollback honest.
      const snapshot: UndoScope = {};
      if (result.pages !== project.ui.pages) snapshot.pages = project.ui.pages;
      if (result.master_elements !== masters) snapshot.master_elements = masters;
      if (result.macros !== (project.macros || [])) snapshot.macros = project.macros || [];
      if (result.variables !== (project.variables || [])) snapshot.variables = project.variables || [];
      run.commit({
        before: snapshot,
        description: `Rename element ${oldId} → ${newId}`,
        patch: {
          ui: { ...project.ui, pages: result.pages, master_elements: result.master_elements },
          macros: result.macros,
          variables: result.variables,
        },
      });
      // Re-select the renamed element under its new ID so the properties panel
      // doesn't lose focus and refresh into "nothing selected".
      if (selectedElementId === oldId) selectElement(newId);
      if (selectedMasterElementId === oldId) selectMasterElement(newId);
      showSuccess(`Renamed to ${newId}`);
      if ((project.scripts || []).length > 0) {
        showInfo(
          `If any scripts reference "ui.${oldId}.*", update them manually.`,
        );
      }
    },
    [project, run, selectedElementId, selectedMasterElementId, selectElement, selectMasterElement],
  );

  const handleDeleteMasterElement = useCallback((masterElementId: string) => {
    if (!project) return;
    run.commit({
      before: { master_elements: project.ui.master_elements || [] },
      description: "Delete master element",
      patch: {
        ui: {
          ...project.ui,
          master_elements: removeMasterElement(project.ui.master_elements || [], masterElementId),
        },
      },
    });
    selectMasterElement(null);
  }, [project, run, selectMasterElement]);

  // --- Theme handlers (also used by ThemeStudio) ---

  const handleThemeChange = useCallback(
    (id: string) => {
      if (!project) return;
      const settings = project.ui.settings;
      // Derive the light/dark fallback (settings.theme) from the theme's actual
      // panel background luminance, not its id. settings.theme is only the
      // fallback used when theme_id can't be resolved; a light theme whose id
      // lacks "light" must still fall back to a light treatment. Fall back to the
      // id heuristic only when the background can't be read.
      const bg = themes.find((t) => t.id === id)?.variables?.panel_bg;
      const light = typeof bg === "string" ? isLightColor(bg) : null;
      const mode = light === null
        ? (id.includes("light") || id === "minimal" ? "light" : "dark")
        : (light ? "light" : "dark");
      // Clear accent_color / font_family / theme_overrides when switching
      // themes. These are per-project overrides that were tuned for the
      // PREVIOUS theme — carrying them forward makes the new theme look
      // wrong (e.g. blue accent on a gold-themed Luxury preset).
      run.commit({
        before: { settings },
        description: "Change theme",
        patch: {
          ui: {
            ...project.ui,
            settings: {
              ...settings,
              theme_id: id,
              theme: mode,
              accent_color: "",
              font_family: "",
              theme_overrides: {},
            },
          },
        },
      });
    },
    [project, themes, run],
  );

  // Live theme variable overrides — burst-undo so dragging a color picker
  // produces one undo entry, not 50.
  const handleUpdateThemeOverrides = useCallback(
    (overrides: Record<string, unknown>) => {
      if (!project) return;
      const settings = project.ui.settings;
      run.join({
        before: { settings },
        description: "Edit theme overrides",
        patch: { ui: { ...project.ui, settings: { ...settings, theme_overrides: overrides } } },
      });
    },
    [project, run],
  );

  const handlePropertyChange = useCallback(
    (elementId: string, patch: Partial<UIElement>) => {
      if (!currentPage || !project) return;
      run.join({
        before: { pages: project.ui.pages },
        description: "Edit element",
        patch: {
          ui: { ...project.ui, pages: updateElementInPage(project.ui.pages, currentPage.id, elementId, patch) },
        },
      });
    },
    [currentPage, project, run],
  );

  const handlePageChange = useCallback(
    (patch: Partial<UIPage>) => {
      if (!currentPage || !project) return;
      const newPages = project.ui.pages.map((p) =>
        p.id === currentPage.id ? { ...p, ...patch } : p,
      );
      run.join({
        before: { pages: project.ui.pages },
        description: "Edit page properties",
        patch: { ui: { ...project.ui, pages: newPages } },
      });
    },
    [currentPage, project, run],
  );

  // Moving a control into a container (or back out) is a geometry change as
  // much as a structural one: its percentages are of whatever holds it, so the
  // helper converts through page space and the element stays exactly where it
  // is drawn.
  const handleReparentElement = useCallback(
    (elementId: string, newParentId: string | null) => {
      if (!currentPage) return;
      applyMutation(
        (p) => reparentElement(p, currentPage.id, elementId, newParentId),
        newParentId ? "Move into container" : "Move out of container",
      );
      selectElement(elementId);
    },
    [currentPage, applyMutation, selectElement],
  );

  const handleMasterElementPropertyChange = useCallback(
    (elementId: string, patch: Partial<MasterElement>) => {
      if (!project) return;
      const masters = project.ui.master_elements || [];
      run.join({
        before: { master_elements: masters },
        description: "Edit master element",
        patch: { ui: { ...project.ui, master_elements: updateMasterElement(masters, elementId, patch) } },
      });
    },
    [project, run],
  );

  // A control new from the palette, dropped at `placement` (page terms): a
  // drop inside a container becomes a child of it, re-expressed relative to
  // that container, which is what the author just showed they meant by
  // dropping it there. Measured against the arrangement on screen: a
  // container can sit somewhere else entirely in another layout, and the
  // pointer was over THIS one.
  const handlePaletteDrop = useCallback(
    (type: string, placement: Placement) => {
      if (!currentPage || !project) return;
      const newElement = createDefaultElement(type, takenIds(pages, masterElements));
      const { parentId, relative } = resolveDropParent(currentPage, placement, activeLayoutId);
      if (parentId) newElement.parent = parentId;
      applyMutation(
        (p) => addElementToPage(p, currentPage.id, newElement, relative),
        `Add ${type}`,
      );
      selectElement(newElement.id);
    },
    [currentPage, pages, masterElements, project, applyMutation, selectElement, activeLayoutId],
  );

  const palette = usePaletteDrop({
    page: currentPage,
    layoutId: activeLayoutId,
    panelElements,
    onDrop: handlePaletteDrop,
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
    moveMaster: (id, placements) =>
      handleMasterElementPropertyChange(id, { placements } as Partial<MasterElement>),
    nudge: (ids, offset, description) => {
      if (!currentPage) return;
      applyMutation((pages) => nudgedPages(pages, currentPage.id, { ids, ...offset }, activeLayoutId), description, true);
    },
    deleteMaster: handleDeleteMasterElement,
    deleteOne: handleDeleteElement,
    deleteSelection: handleDeleteSelection,
    undo: () => { run.endRun(); undo(); },
    redo: () => { run.endRun(); redo(); },
    copy: () => handleCopyElement(selectedElementIds),
    paste: handlePasteElement,
    duplicateOne: handleDuplicateElement,
    duplicateSelection: handleDuplicateSelection,
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
        {/* Save error banner */}
        {error && !conflictDetected && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "6px 16px",
              background: "rgba(244,67,54,0.15)",
              color: "var(--color-error)",
              fontSize: "var(--font-size-sm)",
              borderBottom: "1px solid var(--color-error)",
              flexShrink: 0,
            }}
          >
            <span style={{ flex: 1 }}>Save failed: {error}</span>
            <button
              onClick={() => save()}
              style={{
                padding: "2px 10px",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 3,
                border: "1px solid var(--color-error)",
                background: "transparent",
                color: "var(--color-error)",
                cursor: "pointer",
              }}
            >
              Retry
            </button>
            <button
              onClick={() => useProjectStore.setState({ error: null })}
              style={{
                padding: "2px 10px",
                fontSize: 11,
                borderRadius: 3,
                border: "1px solid var(--border-color)",
                background: "transparent",
                color: "var(--text-secondary)",
                cursor: "pointer",
              }}
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Toolbar — two fixed rows; Theme/Settings dock at the end of the pages row */}
        <CanvasToolbar
          pages={pages}
          selectedPageId={currentPage?.id || null}
          onValidate={runValidation}
          trailing={
            !previewMode ? (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexShrink: 0 }}>
                <button
                  onClick={() => setShowThemeStudio(true)}
                  title="Open Theme Studio"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-xs)",
                    padding: "3px var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-secondary)",
                  }}
                >
                  <Palette size={14} /> Theme
                </button>
                <button
                  onClick={() => setShowStylesheet(true)}
                  title="Edit the project stylesheet (custom CSS)"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-xs)",
                    padding: "3px var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-secondary)",
                  }}
                >
                  <FileCode size={14} /> Stylesheet
                </button>
                <button
                  onClick={() => setShowSettings(true)}
                  title="Panel Settings"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-xs)",
                    padding: "3px var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-secondary)",
                  }}
                >
                  <Settings size={14} /> Settings
                </button>
                <button
                  onClick={openGlobalShortcuts}
                  title="Keyboard Shortcuts"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 24,
                    height: 24,
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                    fontWeight: 700,
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-secondary)",
                  }}
                >
                  ?
                </button>
              </div>
            ) : undefined
          }
        />

        {/* Main 3-panel layout */}
        <PanelGroup direction="horizontal" style={{ flex: 1 }}>
          {/* Left: Element Palette / Outline (Ctrl+E to toggle) */}
          {!previewMode && showPalette && (
            <>
              <Panel defaultSize={15} minSize={10} maxSize={25}>
                <div
                  style={{
                    height: "100%",
                    display: "flex",
                    flexDirection: "column",
                    borderRight: "1px solid var(--border-color)",
                    background: "var(--bg-surface)",
                  }}
                >
                  {/* Tab bar */}
                  <div style={{ display: "flex", borderBottom: "1px solid var(--border-color)", flexShrink: 0 }}>
                    {(["elements", "outline"] as const).map((tab) => (
                      <button
                        key={tab}
                        onClick={() => setLeftTab(tab)}
                        style={{
                          flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 500,
                          background: "transparent", border: "none", cursor: "pointer",
                          color: leftTab === tab ? "var(--accent)" : "var(--text-muted)",
                          borderBottom: leftTab === tab ? "2px solid var(--accent)" : "2px solid transparent",
                        }}
                      >
                        {tab === "elements" ? "Elements" : "Outline"}
                      </button>
                    ))}
                  </div>
                  {/* Tab content */}
                  <div style={{ flex: 1, overflow: "auto" }}>
                    {leftTab === "elements" ? (
                      <ElementPalette
                        disabled={previewMode || currentPageIsCustom}
                        disabledNote={
                          !previewMode && currentPageIsCustom
                            ? "This page shows a page you wrote, so controls placed here are not drawn."
                            : undefined
                        }
                        onAdd={(type) => {
                          if (!currentPage || !project) return;
                          const newElement = createDefaultElement(type, takenIds(pages, masterElements));
                          // Click-to-add has no pointer to land on, so it takes
                          // the auto-placement rule: the first free snap cell,
                          // scanning row-major — which is exactly where the old
                          // grid would have put it — or the page centre plus a
                          // cascade when snap is off.
                          const placement = autoPlace(
                            topLevelBoxes(currentPage, activeLayoutId),
                            defaultElementSize(type, panelElements),
                            pageSnap(currentPage),
                            currentPage.elements.length,
                          );
                          applyMutation(
                            (p) => addElementToPage(p, currentPage.id, newElement, placement),
                            `Add ${type}`,
                          );
                          selectElement(newElement.id);
                        }}
                      />
                    ) : (
                      <OutlinePanel
                        page={currentPage ?? null}
                        masterElements={masterElements}
                        selectedElementIds={selectedElementIds}
                        selectedMasterElementId={selectedMasterElementId}
                        lockedElementIds={lockedElementIds}
                        hiddenElementIds={hiddenElementIds}
                        collapsedIds={collapsedOutlineIds}
                        onToggleCollapse={toggleOutlineCollapse}
                        onToggleHidden={handleToggleHidden}
                        onReparent={handleReparentElement}
                        onSelectElement={(id, shift) => {
                          if (shift) {
                            useUIBuilderStore.getState().toggleSelectElement(id);
                          } else {
                            selectElement(id);
                          }
                        }}
                        onSelectMasterElement={selectMasterElement}
                        onMoveOrder={(elementId, neighborId) => {
                          if (!currentPage) return;
                          applyMutation(
                            (p) => swapElementsInOrder(p, currentPage.id, elementId, neighborId),
                            "Reorder element",
                          );
                        }}
                        onToggleLock={(id) => useUIBuilderStore.getState().toggleLock(id)}
                      />
                    )}
                  </div>
                </div>
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
                themeVariables={themeVariables}
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
                    themeDefaults={themeElementDefaults}
                    themes={themes}
                    onThemeChange={showThemeStudio ? undefined : handleThemeChange}
                    onOpenStylesheet={() => setShowStylesheet(true)}
                    onChange={handlePropertyChange}
                    onRenameElement={handleRenameElement}
                    onPageChange={handlePageChange}
                    onReparent={handleReparentElement}
                    onMasterElementChange={handleMasterElementPropertyChange}
                    onDemoteMaster={handleDemoteFromMaster}
                    onDeleteMaster={handleDeleteMasterElement}
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
          onDuplicate={handleDuplicateElement}
          onDelete={handleDeleteElement}
          onDeleteAll={handleDeleteSelection}
          onDuplicateAll={handleDuplicateSelection}
          onCopy={(ids) => handleCopyElement(selectedElementIds.length > 1 ? selectedElementIds : ids)}
          onPaste={handlePasteElement}
          onBringToFront={handleBringToFront}
          onSendToBack={handleSendToBack}
          onPromoteToMaster={handlePromoteToMaster}
          onDemoteFromMaster={handleDemoteFromMaster}
          onDeleteMaster={handleDeleteMasterElement}
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
          onRefreshThemes={loadThemes}
          onThemeSaved={() => setThemeFetchKey((k) => k + 1)}
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
            run.commit({
              before: { custom_css: project.ui.custom_css ?? "" },
              description: "Edit project stylesheet",
              patch: { ui: { ...project.ui, custom_css: css } },
            });
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
            const newPages = project.ui.pages.map((page) => ({
              ...page,
              elements: page.elements.map((el) => {
                const keysToReset = new Set(
                  Object.keys(themeElementDefaults[el.type] || {}),
                );
                if (keysToReset.size === 0) return el;
                const cleaned: Record<string, unknown> = {};
                for (const [k, v] of Object.entries(el.style || {})) {
                  if (!keysToReset.has(k)) cleaned[k] = v;
                }
                return { ...el, style: cleaned };
              }),
            }));
            run.commit({
              before: { pages: project.ui.pages },
              description: "Reset element styles to theme",
              patch: { ui: { ...project.ui, pages: newPages } },
            });
            showSuccess("Element styles reset to theme defaults");
          }}
        />
      )}

      {/* Validation results */}
      {validationIssues !== null && (
        <Modal
          onClose={() => setValidationIssues(null)}
          label="Validation Results"
          overlayStyle={{ background: "rgba(0,0,0,0.5)" }}
          panelStyle={{
            borderRadius: 8,
            border: "1px solid var(--border-color)",
            padding: 20, minWidth: 400, maxWidth: 600, maxHeight: "70vh",
            display: "flex", flexDirection: "column", gap: 12,
          }}
        >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>
                Project Validation {validationIssues.length === 0 ? "(No Issues)" : `(${validationIssues.length} issue${validationIssues.length === 1 ? "" : "s"})`}
              </h3>
              <button onClick={() => setValidationIssues(null)} style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 16 }}>&times;</button>
            </div>
            {validationIssues.length === 0 ? (
              <div style={{ color: "var(--color-success)", fontSize: 13, padding: "12px 0" }}>
                All bindings, pages, devices, and macros are valid.
              </div>
            ) : (
              <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
                {validationIssues.map((issue, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex", gap: 8, padding: "6px 8px", borderRadius: 4, fontSize: 12,
                      background: issue.severity === "error" ? "rgba(244,67,54,0.08)" : "rgba(255,152,0,0.08)",
                      border: `1px solid ${issue.severity === "error" ? "rgba(244,67,54,0.2)" : "rgba(255,152,0,0.2)"}`,
                      cursor: issue.pageId ? "pointer" : "default",
                    }}
                    onClick={() => {
                      if (issue.pageId) {
                        selectPage(issue.pageId);
                        if (issue.elementId) selectElement(issue.elementId);
                        setValidationIssues(null);
                      }
                    }}
                  >
                    <span style={{ color: issue.severity === "error" ? "var(--color-error)" : "#ff9800", fontWeight: 600, flexShrink: 0 }}>
                      {issue.severity === "error" ? "ERR" : "WARN"}
                    </span>
                    <div>
                      <div style={{ fontWeight: 500 }}>{issue.message}</div>
                      <div style={{ color: "var(--text-muted)", fontSize: 11 }}>{issue.location}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
        </Modal>
      )}

      {/* Settings dialog */}
      {showSettings && (
        <UISettingsDialog
          settings={project.ui.settings}
          pages={project.ui.pages}
          onUpdate={(settings) => run.commit({
            before: { settings: project.ui.settings },
            description: "Edit project settings",
            patch: { ui: { ...project.ui, settings } },
          })}
          onClose={() => setShowSettings(false)}
        />
      )}

    </DndContext>
  );
}

function UISettingsDialog({
  settings,
  pages,
  onUpdate,
  onClose,
}: {
  settings: UISettings;
  pages: UIPage[];
  onUpdate: (s: UISettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<UISettings>(settings);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings);

  const handleCancel = () => {
    if (dirty) { setShowDiscardConfirm(true); return; }
    onClose();
  };

  const handleSave = () => {
    if (dirty) onUpdate(draft);
    onClose();
  };

  const patch = (p: Partial<UISettings>) => setDraft((d) => ({ ...d, ...p }));

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };
  const fieldStyle: React.CSSProperties = { marginBottom: "var(--space-lg)" };
  const inputStyle: React.CSSProperties = { width: "100%" };

  return (
    <Modal
      onClose={handleCancel}
      label="Panel Settings"
      panelStyle={{
        padding: "var(--space-xl)",
        width: 480,
        maxHeight: "85vh",
        overflow: "auto",
      }}
    >
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>Panel Settings</h3>

            <div style={fieldStyle}>
              <label style={labelStyle}>Accent Color</label>
              <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
                <input
                  type="color"
                  value={draft.accent_color || "#2196F3"}
                  onChange={(e) => patch({ accent_color: e.target.value })}
                  style={{ width: 40, height: 32, padding: 0, border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)", cursor: "pointer" }}
                />
                <input
                  value={draft.accent_color}
                  onChange={(e) => patch({ accent_color: e.target.value })}
                  placeholder="#2196F3"
                  style={{ flex: 1 }}
                />
              </div>
            </div>

            <div style={fieldStyle}>
              <label style={labelStyle}>Font Family</label>
              <select
                value={draft.font_family}
                onChange={(e) => patch({ font_family: e.target.value })}
                style={inputStyle}
              >
                <option value="Inter, system-ui, sans-serif">Inter (Default)</option>
                <option value="system-ui, sans-serif">System UI</option>
                <option value="'Roboto', sans-serif">Roboto</option>
                <option value="'Segoe UI', sans-serif">Segoe UI</option>
                <option value="monospace">Monospace</option>
              </select>
            </div>

            {/* Orientation used to be one setting for the whole project, which
                is what stopped a project serving a landscape wall panel and a
                portrait phone at the same time. It belongs to a layout now:
                each page can carry a portrait arrangement of the same controls,
                and the panel picks by the viewport it finds itself on. */}

            <div style={fieldStyle}>
              <label style={labelStyle}>Lock Code (PIN)</label>
              <input
                value={draft.lock_code}
                onChange={(e) => patch({ lock_code: e.target.value.replace(/[^0-9]/g, "").slice(0, 6) })}
                placeholder="Leave empty for no lock"
                style={inputStyle}
              />
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                4-6 digit PIN to lock the touch panel. Leave empty to disable.
              </div>
            </div>

            <div style={fieldStyle}>
              <label style={labelStyle}>Idle Timeout (seconds)</label>
              <NumericInput
                integer
                min={0}
                owner="panel-settings/idle_timeout_seconds"
                value={draft.idle_timeout_seconds}
                onCommit={(v) => {
                  if (v !== undefined) patch({ idle_timeout_seconds: v });
                }}
                style={inputStyle}
              />
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                Return to idle page after this many seconds of inactivity. 0 = disabled.
              </div>
            </div>

            <div style={fieldStyle}>
              <label style={labelStyle}>Idle Page</label>
              <select
                value={draft.idle_page}
                onChange={(e) => patch({ idle_page: e.target.value })}
                style={inputStyle}
              >
                {pages.map(p => (
                  <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
                ))}
              </select>
            </div>

            <div style={fieldStyle}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={draft.show_error_messages !== false}
                  onChange={(e) => patch({ show_error_messages: e.target.checked })}
                />
                Show a message when a control fails
              </label>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                A press that does not reach its device puts the reason across the
                panel for about five seconds. Turn this off in a space that shows
                its own status.
              </div>
            </div>

            <div style={fieldStyle}>
              <label style={labelStyle}>Page Transition</label>
              <select
                value={draft.page_transition || "none"}
                onChange={(e) => patch({ page_transition: e.target.value })}
                style={inputStyle}
              >
                <option value="none">None (instant)</option>
                <option value="fade">Fade</option>
                <option value="slide-left">Slide Left</option>
                <option value="slide-right">Slide Right</option>
                <option value="slide-up">Slide Up</option>
                <option value="scale">Scale</option>
              </select>
            </div>
            {draft.page_transition && draft.page_transition !== "none" && (
              <div style={fieldStyle}>
                <label style={labelStyle}>Transition Duration</label>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <NumericInput
                    integer
                    min={50}
                    max={1000}
                    step={50}
                    owner="panel-settings/page_transition_duration"
                    value={draft.page_transition_duration || 200}
                    onCommit={(v) => {
                      if (v !== undefined) patch({ page_transition_duration: v });
                    }}
                    style={{ ...inputStyle, width: 80 }}
                  />
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>ms</span>
                </div>
              </div>
            )}

            <div style={fieldStyle}>
              <label style={labelStyle}>Element Entry Animation</label>
              <select
                value={draft.element_entry || "none"}
                onChange={(e) => patch({ element_entry: e.target.value })}
                style={inputStyle}
              >
                <option value="none">None (instant)</option>
                <option value="fade">Fade In</option>
                <option value="fade-up">Fade Up</option>
                <option value="scale">Scale In</option>
                <option value="stagger">Stagger</option>
              </select>
            </div>
            {draft.element_entry === "stagger" && (
              <div style={fieldStyle}>
                <label style={labelStyle}>Stagger Style</label>
                <select
                  value={draft.element_stagger_style || "fade-up"}
                  onChange={(e) => patch({ element_stagger_style: e.target.value })}
                  style={inputStyle}
                >
                  <option value="fade">Fade</option>
                  <option value="fade-up">Fade Up</option>
                  <option value="scale">Scale</option>
                </select>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  How each element animates in as the stagger sweeps across the page.
                </div>
              </div>
            )}
            {draft.element_entry === "stagger" && (
              <div style={fieldStyle}>
                <label style={labelStyle}>Stagger Delay</label>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <NumericInput
                    integer
                    min={10}
                    max={200}
                    step={10}
                    owner="panel-settings/element_stagger_ms"
                    value={draft.element_stagger_ms || 30}
                    onCommit={(v) => {
                      if (v !== undefined) patch({ element_stagger_ms: v });
                    }}
                    style={{ ...inputStyle, width: 80 }}
                  />
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>ms per element</span>
                </div>
              </div>
            )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)", marginTop: "var(--space-lg)" }}>
          <button
            onClick={handleCancel}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              border: "none",
              cursor: "pointer",
              color: "var(--text-primary)",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!dirty}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: dirty ? "var(--accent-bg)" : "var(--bg-hover)",
              border: "none",
              cursor: dirty ? "pointer" : "default",
              opacity: dirty ? 1 : 0.5,
              color: dirty ? "#fff" : "var(--text-muted)",
              fontWeight: 600,
            }}
          >
            Save
          </button>
        </div>

      {showDiscardConfirm && (
        <ConfirmDialog
          title="Discard Changes"
          message="Discard unsaved settings changes?"
          confirmLabel="Discard"
          destructive
          onConfirm={onClose}
          onCancel={() => setShowDiscardConfirm(false)}
        />
      )}
    </Modal>
  );
}
