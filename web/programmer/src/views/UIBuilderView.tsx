import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Settings, Palette } from "lucide-react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import type { UIElement, UIPage, UISettings, GridArea, MasterElement } from "../api/types";
import * as wsClient from "../api/wsClient";
import { listThemes, getTunnelPrefix } from "../api/restClient";
import { showError } from "../store/toastStore";
import { useProjectStore } from "../store/projectStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { useNavigationStore } from "../store/navigationStore";
import { ElementPalette } from "../components/ui-builder/ElementPalette";
import { Canvas } from "../components/ui-builder/Canvas";
import { CanvasToolbar } from "../components/ui-builder/CanvasToolbar";
import { PropertiesPanel } from "../components/ui-builder/PropertiesPanel";
import { ContextMenu } from "../components/ui-builder/ContextMenu";
import { ThemeStudio } from "../components/ui-builder/ThemeStudio";
import {
  SCREEN_PRESETS,
  ELEMENT_TEMPLATES,
  createDefaultElement,
  addElementToPage,
  removeElementFromPage,
  updateElementInPage,
  moveElementInPage,
  duplicateElementInPage,
  reorderElement,
  promoteToMaster,
  demoteFromMaster,
  updateMasterElement,
  removeMasterElement,
  renameElement,
  validateElementId,
} from "../components/ui-builder/uiBuilderHelpers";
import { showSuccess, showInfo } from "../store/toastStore";

export function UIBuilderView() {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const save = useProjectStore((s) => s.save);

  // Reactive selectors — only subscribe to values that affect render
  const selectedPageId = useUIBuilderStore((s) => s.selectedPageId);
  const selectedElementId = useUIBuilderStore((s) => s.selectedElementId);
  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);
  const previewMode = useUIBuilderStore((s) => s.previewMode);
  const showGrid = useUIBuilderStore((s) => s.showGrid);
  const zoom = useUIBuilderStore((s) => s.zoom);
  const screenPresetIndex = useUIBuilderStore((s) => s.screenPresetIndex);
  const clipboard = useUIBuilderStore((s) => s.clipboard);

  // Stable action refs — use getState() to avoid re-render subscriptions
  const { selectPage, selectElement, selectMasterElement, pushUndo, undo, redo, touchMutation, setClipboard } = useMemo(() => ({
    selectPage: (...args: any[]) => (useUIBuilderStore.getState().selectPage as any)(...args),
    selectElement: (...args: any[]) => (useUIBuilderStore.getState().selectElement as any)(...args),
    selectMasterElement: (...args: any[]) => (useUIBuilderStore.getState().selectMasterElement as any)(...args),
    pushUndo: (...args: any[]) => (useUIBuilderStore.getState().pushUndo as any)(...args),
    undo: () => useUIBuilderStore.getState().undo(),
    redo: () => useUIBuilderStore.getState().redo(),
    touchMutation: () => useUIBuilderStore.getState().touchMutation(),
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
  const setActiveDragSource = useUIBuilderStore((s) => s.setActiveDragSource);
  const activeDragSource = useUIBuilderStore((s) => s.activeDragSource);

  const [showSettings, setShowSettings] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showPalette, setShowPalette] = useState(true);
  const [showThemeStudio, setShowThemeStudio] = useState(false);
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

  const dragStartPointer = useRef<{ x: number; y: number } | null>(null);
  const dragElementType = useRef<string | null>(null);
  const draggedElement = useRef<UIElement | null>(null);
  const dragCellSize = useRef<{ w: number; h: number }>({ w: 60, h: 50 });
  // Grid offset between pointer and element's top-left at drag start (in grid cells)
  const dragGridOffset = useRef<{ col: number; row: number }>({ col: 0, row: 0 });

  // Auto-select first page if none selected
  useEffect(() => {
    if (!selectedPageId && project?.ui?.pages?.length) {
      selectPage(project.ui.pages[0].id);
    }
  }, [selectedPageId, project, selectPage]);

  // Load theme element defaults when theme_id changes.
  // Buttons (and visually-button-like types: page_nav, camera_preset, keypad)
  // get their colors from CSS variables via .panel-button rules, NOT from
  // element_defaults. Synthesize those colors into themeElementDefaults so
  // the Properties panel can show the real effective color as the swatch
  // placeholder for new elements — otherwise users see blank/#000000 and
  // assume the element is unstyled when it actually renders themed.
  const themeId = project?.ui?.settings?.theme_id;
  useEffect(() => {
    const id = themeId || "dark-default";
    fetch(`${getTunnelPrefix()}/api/themes/${id}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((theme) => {
        const vars = theme?.variables || {};
        const baseDefaults = theme?.element_defaults || {};
        const buttonInherit: Record<string, unknown> = {};
        if (vars.button_bg) buttonInherit.bg_color = vars.button_bg;
        if (vars.button_text) buttonInherit.text_color = vars.button_text;
        if (vars.button_border) buttonInherit.border_color = vars.button_border;
        const synthesized: Record<string, Record<string, unknown>> = { ...baseDefaults };
        for (const t of ["button", "page_nav", "camera_preset", "keypad"]) {
          synthesized[t] = { ...buttonInherit, ...(baseDefaults[t] || {}) };
        }
        // Most labels/text in non-button elements inherit panel_text via CSS;
        // surface label_color so the Properties panel doesn't mis-show black.
        if (vars.panel_text) {
          for (const t of ["label", "slider", "fader", "gauge", "level_meter", "list", "select", "text_input", "group", "clock", "matrix"]) {
            synthesized[t] = { text_color: vars.panel_text, ...(synthesized[t] || baseDefaults[t] || {}) };
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

  // Flush pending save before the tab unloads so the 2 s debounce window
  // can't lose the last edit.
  useEffect(() => {
    const handler = () => useProjectStore.getState().flushSave();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+P toggles preview mode (works in both modes)
      if ((e.ctrlKey || e.metaKey) && e.key === "p") {
        e.preventDefault();
        useUIBuilderStore.getState().setPreviewMode(!previewMode);
        return;
      }
      // Ctrl+E toggles element palette (13.9)
      if ((e.ctrlKey || e.metaKey) && e.key === "e") {
        e.preventDefault();
        setShowPalette((v) => !v);
        return;
      }
      if (previewMode) return;
      const target = e.target as HTMLElement;
      const inInput = target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT";

      // Block Delete/Backspace when typing in inputs, but allow Ctrl shortcuts
      if (inInput && !(e.ctrlKey || e.metaKey)) return;

      // Escape — deselect element
      if (e.key === "Escape") {
        e.preventDefault();
        selectElement(null);
        return;
      }

      // Arrow keys — move all selected elements by 1 grid cell
      if (
        (e.key === "ArrowUp" || e.key === "ArrowDown" ||
         e.key === "ArrowLeft" || e.key === "ArrowRight") &&
        selectedElementIds.length > 0 && currentPage
      ) {
        e.preventDefault();
        const { columns, rows: gridRows } = currentPage.grid;
        // Check all selected elements can move in this direction
        const elementsToMove = selectedElementIds
          .map((eid) => currentPage.elements.find((el) => el.id === eid))
          .filter((el): el is typeof currentPage.elements[0] => !!el);
        if (elementsToMove.length === 0) return;

        const canMove = elementsToMove.every((el) => {
          const { col, row, col_span, row_span } = el.grid_area;
          if (e.key === "ArrowLeft") return col > 1;
          if (e.key === "ArrowRight") return col + col_span <= columns;
          if (e.key === "ArrowUp") return row > 1;
          if (e.key === "ArrowDown") return row + row_span <= gridRows;
          return false;
        });
        if (!canMove) return;

        applyMutation((pages) => {
          let result = pages;
          for (const el of elementsToMove) {
            const { col, row, col_span, row_span } = el.grid_area;
            let newCol = col;
            let newRow = row;
            if (e.key === "ArrowLeft") newCol = col - 1;
            if (e.key === "ArrowRight") newCol = col + 1;
            if (e.key === "ArrowUp") newRow = row - 1;
            if (e.key === "ArrowDown") newRow = row + 1;
            result = moveElementInPage(result, currentPage.id, el.id, {
              col: newCol, row: newRow, col_span, row_span,
            });
          }
          return result;
        }, `Nudge ${e.key.replace("Arrow", "").toLowerCase()}`);
        return;
      }

      if (e.key === "Delete" || e.key === "Backspace") {
        if (selectedElementIds.length > 0 && currentPage) {
          e.preventDefault();
          if (selectedElementIds.length === 1) {
            handleDeleteElement(selectedElementIds[0]);
          } else {
            // Batch delete
            applyMutation((pages) => {
              let result = pages;
              for (const eid of selectedElementIds) {
                result = removeElementFromPage(result, currentPage.id, eid);
              }
              return result;
            }, `Delete ${selectedElementIds.length} elements`);
            selectElement(null);
          }
        }
      }
      if (e.ctrlKey || e.metaKey) {
        if (e.key === "z" && !e.shiftKey) {
          e.preventDefault();
          undo();
        }
        if ((e.key === "z" && e.shiftKey) || e.key === "y") {
          e.preventDefault();
          redo();
        }
        if (e.key === "s" && !e.shiftKey) {
          e.preventDefault();
          const store = useProjectStore.getState();
          store.flushSave();
          // flushSave is a no-op when no debounce timer is pending, so kick a
          // direct save when there are still uncommitted changes.
          if (store.dirty && !store.saving) save();
        }
        if (e.key === "c" && selectedElementId && currentPage) {
          e.preventDefault();
          handleCopyElement(selectedElementId);
        }
        if (e.key === "v" && clipboard && currentPage) {
          e.preventDefault();
          handlePasteElement();
        }
        if (e.key === "d" && selectedElementId && currentPage) {
          e.preventDefault();
          handleDuplicateElement(selectedElementId);
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  });

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

  const preset = SCREEN_PRESETS[screenPresetIndex];
  const screenWidth = preset?.width ?? 1024;
  const screenHeight = preset?.height ?? 600;

  // Property changes debounce undo: push undo only once per editing burst,
  // not on every keystroke. The timer resets on each change; the flag clears
  // 800ms after the last edit, or whenever a structural mutation bookends the burst.
  const propertyUndoPushed = useRef(false);
  const propertyUndoTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // --- Mutation helpers ---
  const applyMutation = useCallback(
    (mutate: (pages: UIPage[]) => UIPage[], description: string) => {
      if (!project) return;
      pushUndo({ pages: project.ui.pages }, description);
      // Structural mutations bookend any in-flight property-edit burst:
      // reset the burst flag so the next typed edit starts a fresh undo entry
      // capturing the post-structural pages.
      propertyUndoPushed.current = false;
      clearTimeout(propertyUndoTimer.current);
      const newPages = mutate(project.ui.pages);
      update({ ui: { ...project.ui, pages: newPages } });
      touchMutation();
    },
    [project, pushUndo, update, touchMutation],
  );

  const handleDeleteElement = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      applyMutation((p) => removeElementFromPage(p, currentPage.id, elementId), "Delete element");
      selectElement(null);
    },
    [currentPage, applyMutation, selectElement],
  );

  const handleDuplicateElement = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      applyMutation((p) => duplicateElementInPage(p, currentPage.id, elementId), "Duplicate element");
    },
    [currentPage, applyMutation],
  );

  const handleCopyElement = useCallback(
    (elementId: string) => {
      if (!currentPage) return;
      const el = currentPage.elements.find((e) => e.id === elementId);
      if (el) setClipboard(JSON.parse(JSON.stringify(el)));
    },
    [currentPage, setClipboard],
  );

  const handlePasteElement = useCallback(() => {
    if (!clipboard || !currentPage) return;
    // Collect IDs from ALL pages to avoid cross-page collisions
    const existingIds = new Set(pages.flatMap((p) => p.elements.map((e) => e.id)));
    let id = clipboard.id;
    let counter = 1;
    while (existingIds.has(id)) {
      id = `${clipboard.type}_paste_${counter++}`;
    }
    const newElement: UIElement = {
      ...JSON.parse(JSON.stringify(clipboard)),
      id,
      grid_area: {
        ...clipboard.grid_area,
        col: Math.max(1, Math.min(clipboard.grid_area.col + 1, currentPage.grid.columns - clipboard.grid_area.col_span + 1)),
        row: Math.max(1, Math.min(clipboard.grid_area.row + 1, currentPage.grid.rows - clipboard.grid_area.row_span + 1)),
      },
    };
    applyMutation((p) => addElementToPage(p, currentPage.id, newElement), "Paste element");
  }, [clipboard, currentPage, pages, applyMutation]);

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
    pushUndo(
      { pages: project.ui.pages, master_elements: project.ui.master_elements || [] },
      "Promote to master",
    );
    propertyUndoPushed.current = false;
    clearTimeout(propertyUndoTimer.current);
    const result = promoteToMaster(
      project.ui.pages,
      project.ui.master_elements || [],
      currentPage.id,
      elementId,
    );
    update({
      ui: {
        ...project.ui,
        pages: result.pages,
        master_elements: result.masterElements,
      },
    });
    touchMutation();
    selectElement(null);
  }, [project, currentPage, pushUndo, update, touchMutation, selectElement]);

  const handleDemoteFromMaster = useCallback((masterElementId: string) => {
    if (!project || !currentPage) return;
    pushUndo(
      { pages: project.ui.pages, master_elements: project.ui.master_elements || [] },
      "Demote from master",
    );
    propertyUndoPushed.current = false;
    clearTimeout(propertyUndoTimer.current);
    const result = demoteFromMaster(
      project.ui.pages,
      project.ui.master_elements || [],
      masterElementId,
      currentPage.id,
    );
    update({
      ui: {
        ...project.ui,
        pages: result.pages,
        master_elements: result.masterElements,
      },
    });
    touchMutation();
    selectMasterElement(null);
  }, [project, currentPage, pushUndo, update, touchMutation, selectMasterElement]);

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
      const snapshot: Parameters<typeof pushUndo>[0] = {};
      if (result.pages !== project.ui.pages) snapshot.pages = project.ui.pages;
      if (result.master_elements !== masters) snapshot.master_elements = masters;
      if (result.macros !== (project.macros || [])) snapshot.macros = project.macros || [];
      if (result.variables !== (project.variables || [])) snapshot.variables = project.variables || [];
      pushUndo(snapshot, `Rename element ${oldId} → ${newId}`);
      propertyUndoPushed.current = false;
      clearTimeout(propertyUndoTimer.current);
      update({
        ui: {
          ...project.ui,
          pages: result.pages,
          master_elements: result.master_elements,
        },
        macros: result.macros,
        variables: result.variables,
      });
      touchMutation();
      // Re-select the renamed element under its new ID so the properties panel
      // doesn't lose focus and refresh into "nothing selected".
      if (selectedElementId === oldId) selectElement(newId);
      if (selectedMasterElementId === oldId) selectMasterElement(newId);
      showSuccess(`Renamed to ${newId}`);
      if (result.scriptsToReview.length > 0) {
        showInfo(
          `Scripts may reference the old ID "ui.${oldId}.*". Search and update manually: ${result.scriptsToReview.join(", ")}`,
        );
      }
    },
    [project, pushUndo, update, touchMutation, selectedElementId, selectedMasterElementId, selectElement, selectMasterElement],
  );

  const handleDeleteMasterElement = useCallback((masterElementId: string) => {
    if (!project) return;
    pushUndo(
      { master_elements: project.ui.master_elements || [] },
      "Delete master element",
    );
    propertyUndoPushed.current = false;
    clearTimeout(propertyUndoTimer.current);
    const newMasters = removeMasterElement(project.ui.master_elements || [], masterElementId);
    update({
      ui: {
        ...project.ui,
        master_elements: newMasters,
      },
    });
    touchMutation();
    selectMasterElement(null);
  }, [project, pushUndo, update, touchMutation, selectMasterElement]);

  // Reset undo burst tracking when selected element changes
  useEffect(() => {
    propertyUndoPushed.current = false;
    clearTimeout(propertyUndoTimer.current);
  }, [selectedElementId, selectedMasterElementId]);

  // --- Theme handlers (also used by ThemeStudio) ---

  const handleThemeChange = useCallback(
    (id: string) => {
      if (!project) return;
      const settings = project.ui.settings;
      pushUndo({ settings }, "Change theme");
      propertyUndoPushed.current = false;
      clearTimeout(propertyUndoTimer.current);
      // Clear accent_color / font_family / theme_overrides when switching
      // themes. These are per-project overrides that were tuned for the
      // PREVIOUS theme — carrying them forward makes the new theme look
      // wrong (e.g. blue accent on a gold-themed Luxury preset).
      update({
        ui: {
          ...project.ui,
          settings: {
            ...settings,
            theme_id: id,
            theme: id.includes("light") || id === "minimal" ? "light" : "dark",
            accent_color: "",
            font_family: "",
            theme_overrides: {},
          },
        },
      });
      touchMutation();
    },
    [project, pushUndo, update, touchMutation],
  );

  // Live theme variable overrides — burst-undo so dragging a color picker
  // produces one undo entry, not 50.
  const handleUpdateThemeOverrides = useCallback(
    (overrides: Record<string, unknown>) => {
      if (!project) return;
      const settings = project.ui.settings;
      if (!propertyUndoPushed.current) {
        pushUndo({ settings }, "Edit theme overrides");
        propertyUndoPushed.current = true;
      }
      update({
        ui: { ...project.ui, settings: { ...settings, theme_overrides: overrides } },
      });
      touchMutation();
      clearTimeout(propertyUndoTimer.current);
      propertyUndoTimer.current = setTimeout(() => {
        propertyUndoPushed.current = false;
      }, 800);
    },
    [project, pushUndo, update, touchMutation],
  );

  const handlePropertyChange = useCallback(
    (elementId: string, patch: Partial<UIElement>) => {
      if (!currentPage || !project) return;

      // Push undo once at the start of an editing burst
      if (!propertyUndoPushed.current) {
        pushUndo({ pages: project.ui.pages }, "Edit element");
        propertyUndoPushed.current = true;
      }

      // Apply the change without pushing another undo
      const newPages = updateElementInPage(
        project.ui.pages,
        currentPage.id,
        elementId,
        patch,
      );
      update({ ui: { ...project.ui, pages: newPages } });
      touchMutation();

      // Reset the flag after a pause (next edit burst will push undo again)
      clearTimeout(propertyUndoTimer.current);
      propertyUndoTimer.current = setTimeout(() => {
        propertyUndoPushed.current = false;
      }, 800);
    },
    [currentPage, project, pushUndo, update, touchMutation],
  );

  const handlePageChange = useCallback(
    (patch: Partial<UIPage>) => {
      if (!currentPage || !project) return;
      if (!propertyUndoPushed.current) {
        pushUndo({ pages: project.ui.pages }, "Edit page properties");
        propertyUndoPushed.current = true;
      }
      const newPages = project.ui.pages.map((p) =>
        p.id === currentPage.id ? { ...p, ...patch } : p,
      );
      update({ ui: { ...project.ui, pages: newPages } });
      touchMutation();
      clearTimeout(propertyUndoTimer.current);
      propertyUndoTimer.current = setTimeout(() => {
        propertyUndoPushed.current = false;
      }, 800);
    },
    [currentPage, project, pushUndo, update, touchMutation],
  );

  const handleMasterElementPropertyChange = useCallback(
    (elementId: string, patch: Partial<MasterElement>) => {
      if (!project) return;

      if (!propertyUndoPushed.current) {
        pushUndo(
          { master_elements: project.ui.master_elements || [] },
          "Edit master element",
        );
        propertyUndoPushed.current = true;
      }

      const newMasters = updateMasterElement(
        project.ui.master_elements || [],
        elementId,
        patch,
      );
      update({ ui: { ...project.ui, master_elements: newMasters } });
      touchMutation();

      clearTimeout(propertyUndoTimer.current);
      propertyUndoTimer.current = setTimeout(() => {
        propertyUndoPushed.current = false;
      }, 800);
    },
    [project, pushUndo, update, touchMutation],
  );

  // --- DnD ---
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 5 },
    }),
  );

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const data = event.active.data.current as
        | { source: string; elementType?: string; elementId?: string; templateId?: string }
        | undefined;
      setActiveDragSource(data?.source || null);
      dragElementType.current = data?.elementType || data?.templateId || null;
      const pointerEvent = event.activatorEvent as PointerEvent;
      dragStartPointer.current = {
        x: pointerEvent.clientX,
        y: pointerEvent.clientY,
      };

      // Capture element for drag overlay preview
      const cp = pages.find((p) => p.id === selectedPageId) || pages[0] || null;
      if (data?.source === "canvas" && data?.elementId && cp) {
        draggedElement.current = cp.elements.find((e) => e.id === data.elementId) || null;
      } else if (data?.source === "palette" && data?.elementType) {
        draggedElement.current = createDefaultElement(data.elementType, 1, 1, new Set());
      } else {
        draggedElement.current = null;
      }

      // Measure canvas cell size for overlay dimensions
      const canvasGrid = document.querySelector("[data-canvas-grid]");
      if (canvasGrid) {
        const rect = canvasGrid.getBoundingClientRect();
        const cols = cp?.grid.columns || 12;
        const rows = cp?.grid.rows || 8;
        dragCellSize.current = { w: rect.width / cols, h: rect.height / rows };

        // Calculate grid offset: how far the pointer is from the element's top-left
        if (data?.source === "canvas" && draggedElement.current) {
          // For canvas drags, compute pointer offset within the element (in grid cells)
          const pointerCol = Math.floor(
            ((pointerEvent.clientX - rect.left) / rect.width) * cols,
          ) + 1;
          const pointerRow = Math.floor(
            ((pointerEvent.clientY - rect.top) / rect.height) * rows,
          ) + 1;
          dragGridOffset.current = {
            col: pointerCol - draggedElement.current.grid_area.col,
            row: pointerRow - draggedElement.current.grid_area.row,
          };
        } else if (draggedElement.current) {
          // For palette/template drags, the DragOverlay is anchored to the
          // palette button's top-left. The pointer offset within the overlay
          // equals the click offset within the palette button. Convert that
          // pixel offset to grid cells so the element lands where the overlay
          // appeared.
          const activeRect = event.active.rect.current.initial;
          const cellW = dragCellSize.current.w;
          const cellH = dragCellSize.current.h;
          dragGridOffset.current = {
            col: activeRect && cellW > 0
              ? Math.floor((pointerEvent.clientX - activeRect.left) / cellW)
              : 0,
            row: activeRect && cellH > 0
              ? Math.floor((pointerEvent.clientY - activeRect.top) / cellH)
              : 0,
          };
        } else {
          dragGridOffset.current = { col: 0, row: 0 };
        }
      }
    },
    [setActiveDragSource, pages, selectedPageId],
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDragSource(null);
      dragElementType.current = null;
      draggedElement.current = null;
      const { active, over, delta } = event;
      if (!over || over.id !== "canvas-drop" || !currentPage || !project)
        return;
      if (!dragStartPointer.current) return;

      const data = active.data.current as
        | { source: string; elementType?: string; elementId?: string; templateId?: string }
        | undefined;
      if (!data) return;

      // Calculate drop grid cell
      const canvasGrid = document.querySelector("[data-canvas-grid]");
      const canvasRect = canvasGrid?.getBoundingClientRect();
      if (!canvasRect) return;

      const pointerX = dragStartPointer.current.x + delta.x;
      const pointerY = dragStartPointer.current.y + delta.y;

      const { columns, rows } = currentPage.grid;
      // Raw grid cell under the pointer
      const rawCol = Math.floor(
        ((pointerX - canvasRect.left) / canvasRect.width) * columns,
      ) + 1;
      const rawRow = Math.floor(
        ((pointerY - canvasRect.top) / canvasRect.height) * rows,
      ) + 1;
      // Adjust for pointer offset within the element (canvas drags maintain
      // the click position; palette drags center the element under the pointer)
      const col = Math.max(1, Math.min(columns, rawCol - dragGridOffset.current.col));
      const row = Math.max(1, Math.min(rows, rawRow - dragGridOffset.current.row));

      if (data.source === "palette" && data.elementType) {
        // Create new element — collect IDs from ALL pages to avoid cross-page collisions
        const existingIds = new Set(
          pages.flatMap((p) => p.elements.map((e) => e.id)),
        );
        const newElement = createDefaultElement(
          data.elementType,
          col,
          row,
          existingIds,
        );
        applyMutation(
          (p) => addElementToPage(p, currentPage.id, newElement),
          `Add ${data.elementType}`,
        );
        selectElement(newElement.id);
      } else if (data.source === "template" && data.templateId) {
        // Create multiple elements from a template
        const template = ELEMENT_TEMPLATES.find((t) => t.id === data.templateId);
        if (template) {
          const existingIds = new Set(
            pages.flatMap((p) => p.elements.map((e) => e.id)),
          );
          let newPages = project.ui.pages;
          let counter = 1;
          for (const tmplEl of template.elements) {
            let id = `${tmplEl.type}_${counter}`;
            while (existingIds.has(id)) {
              counter++;
              id = `${tmplEl.type}_${counter}`;
            }
            existingIds.add(id);
            const newCol = Math.max(1, Math.min(currentPage.grid.columns, tmplEl.grid_area.col + col));
            const newRow = Math.max(1, Math.min(currentPage.grid.rows, tmplEl.grid_area.row + row));
            const newColSpan = Math.min(tmplEl.grid_area.col_span, currentPage.grid.columns - newCol + 1);
            const newRowSpan = Math.min(tmplEl.grid_area.row_span, currentPage.grid.rows - newRow + 1);
            const newEl = {
              ...tmplEl,
              id,
              grid_area: {
                col: newCol,
                row: newRow,
                col_span: Math.max(1, newColSpan),
                row_span: Math.max(1, newRowSpan),
              },
            } as UIElement;
            newPages = addElementToPage(newPages, currentPage.id, newEl);
            counter++;
          }
          pushUndo({ pages: project.ui.pages }, "Insert template");
          propertyUndoPushed.current = false;
          clearTimeout(propertyUndoTimer.current);
          update({ ui: { ...project.ui, pages: newPages } });
          touchMutation();
        }
      } else if (data.source === "canvas" && data.elementId) {
        // Move existing element
        const element = currentPage.elements.find(
          (e) => e.id === data.elementId,
        );
        if (element) {
          const newGridArea: GridArea = {
            col: Math.max(
              1,
              Math.min(columns - element.grid_area.col_span + 1, col),
            ),
            row: Math.max(
              1,
              Math.min(rows - element.grid_area.row_span + 1, row),
            ),
            col_span: element.grid_area.col_span,
            row_span: element.grid_area.row_span,
          };
          applyMutation(
            (p) => moveElementInPage(p, currentPage.id, data.elementId!, newGridArea),
            "Move element",
          );
        }
      }

      dragStartPointer.current = null;
    },
    [currentPage, pages, project, applyMutation, selectElement, setActiveDragSource],
  );

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
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          overflow: "hidden",
        }}
      >
        {/* Save error banner */}
        {error && (
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

        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
          <div style={{ flex: 1 }}>
            <CanvasToolbar pages={pages} selectedPageId={currentPage?.id || null} />
          </div>
          {!previewMode && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", padding: "0 var(--space-md)", borderBottom: "1px solid var(--border-color)", background: "var(--bg-surface)", minHeight: 38 }}>
              <button
                onClick={() => setShowThemeStudio(true)}
                title="Open Theme Studio"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--text-secondary)",
                }}
              >
                <Palette size={16} /> Theme
              </button>
              <button
                onClick={() => setShowSettings(true)}
                title="Panel Settings"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--text-secondary)",
                }}
              >
                <Settings size={16} /> Settings
              </button>
              <button
                onClick={() => setShowShortcuts(true)}
                title="Keyboard Shortcuts"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 28,
                  height: 28,
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
          )}
        </div>

        {/* Main 3-panel layout */}
        <PanelGroup direction="horizontal" style={{ flex: 1 }}>
          {/* Left: Element Palette (Ctrl+E to toggle) */}
          {!previewMode && showPalette && (
            <>
              <Panel defaultSize={15} minSize={10} maxSize={25}>
                <div
                  style={{
                    height: "100%",
                    overflow: "auto",
                    borderRight: "1px solid var(--border-color)",
                    background: "var(--bg-surface)",
                  }}
                >
                  <ElementPalette disabled={previewMode} />
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
                    onOpenThemeStudio={() => setShowThemeStudio(true)}
                    onChange={handlePropertyChange}
                    onRenameElement={handleRenameElement}
                    onPageChange={handlePageChange}
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

      {/* Drag overlay — outlined footprint sized to the drop target, with the element type label. */}
      <DragOverlay dropAnimation={null}>
        {activeDragSource && draggedElement.current ? (
          <div
            style={{
              width: draggedElement.current.grid_area.col_span * dragCellSize.current.w,
              height: draggedElement.current.grid_area.row_span * dragCellSize.current.h,
              opacity: 0.85,
              pointerEvents: "none",
              filter: "drop-shadow(0 4px 16px rgba(0,0,0,0.5))",
              borderRadius: 8,
              outline: "2px solid var(--accent)",
              outlineOffset: -1,
              background: "var(--bg-elevated)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-primary)",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
              textTransform: "capitalize",
            }}
          >
            {draggedElement.current.type.replace(/_/g, " ")}
          </div>
        ) : activeDragSource === "template" ? (
          <div
            style={{
              padding: "6px 14px",
              background: "var(--bg-elevated)",
              border: "1px solid var(--accent)",
              borderRadius: "var(--border-radius)",
              color: "var(--text-primary)",
              fontSize: "var(--font-size-sm)",
              boxShadow: "var(--shadow-md)",
              opacity: 0.9,
              pointerEvents: "none",
            }}
          >
            {dragElementType.current || "Template"}
          </div>
        ) : null}
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
          onDeleteAll={() => {
            if (currentPage) {
              applyMutation((pages) => {
                let result = pages;
                for (const eid of selectedElementIds) {
                  result = removeElementFromPage(result, currentPage.id, eid);
                }
                return result;
              }, `Delete ${selectedElementIds.length} elements`);
              selectElement(null);
            }
          }}
          onDuplicateAll={() => {
            if (currentPage) {
              for (const eid of selectedElementIds) {
                handleDuplicateElement(eid);
              }
            }
          }}
          onCopy={handleCopyElement}
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
          panelWidth={screenWidth}
          panelHeight={screenHeight}
        />
      )}

      {/* Settings dialog */}
      {showSettings && (
        <UISettingsDialog
          settings={project.ui.settings}
          pages={project.ui.pages}
          onUpdate={(settings) => {
            pushUndo({ settings: project.ui.settings }, "Edit project settings");
            propertyUndoPushed.current = false;
            clearTimeout(propertyUndoTimer.current);
            update({ ui: { ...project.ui, settings } });
            touchMutation();
          }}
          onClose={() => setShowSettings(false)}
        />
      )}

      {/* Keyboard shortcuts dialog */}
      {showShortcuts && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
          onClick={() => setShowShortcuts(false)}
        >
          <div
            style={{ background: "var(--bg-elevated)", borderRadius: "var(--border-radius)", padding: "var(--space-xl)", width: 400, boxShadow: "var(--shadow-lg)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>Keyboard Shortcuts</h3>
            {[
              ["Ctrl + Z", "Undo"],
              ["Ctrl + Shift + Z", "Redo"],
              ["Ctrl + C", "Copy element"],
              ["Ctrl + V", "Paste element"],
              ["Ctrl + D", "Duplicate element"],
              ["Delete / Backspace", "Delete selected element(s)"],
              ["Arrow keys", "Move selected element(s)"],
              ["Shift + Click", "Add/remove from selection"],
              ["Escape", "Deselect all"],
            ].map(([key, desc]) => (
              <div key={key} style={{ display: "flex", justifyContent: "space-between", padding: "var(--space-xs) 0", borderBottom: "1px solid var(--border-color)" }}>
                <code style={{ fontSize: "var(--font-size-sm)", background: "var(--bg-hover)", padding: "2px 8px", borderRadius: 4 }}>{key}</code>
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>{desc}</span>
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "var(--space-lg)" }}>
              <button onClick={() => setShowShortcuts(false)} style={{ padding: "var(--space-sm) var(--space-lg)", borderRadius: "var(--border-radius)", background: "var(--bg-hover)", border: "none", cursor: "pointer", color: "var(--text-primary)" }}>
                Close
              </button>
            </div>
          </div>
        </div>
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
  // Stage edits locally — commit to the project only on Save. Prevents
  // accidental mid-edit commits and lets Cancel genuinely discard.
  const [draft, setDraft] = useState<UISettings>(settings);
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings);

  const handleCancel = () => {
    if (dirty && !window.confirm("Discard unsaved settings changes?")) return;
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
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={handleCancel}
    >
      <div
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xl)",
          width: 480,
          maxHeight: "85vh",
          overflow: "auto",
          boxShadow: "var(--shadow-lg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>Panel Settings</h3>

            <div style={fieldStyle}>
              <label style={labelStyle}>Accent Color</label>
              <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
                <input
                  type="color"
                  value={draft.accent_color}
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

            <div style={fieldStyle}>
              <label style={labelStyle}>Orientation</label>
              <select
                value={draft.orientation}
                onChange={(e) => patch({ orientation: e.target.value })}
                style={inputStyle}
              >
                <option value="landscape">Landscape</option>
                <option value="portrait">Portrait</option>
              </select>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                Affects the deployed panel. Canvas preview uses screen presets for sizing.
              </div>
            </div>

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
              <input
                type="number"
                min={0}
                value={draft.idle_timeout_seconds}
                onChange={(e) => patch({ idle_timeout_seconds: parseInt(e.target.value) || 0 })}
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
                  <input
                    type="number"
                    min={50}
                    max={1000}
                    step={50}
                    value={draft.page_transition_duration || 200}
                    onChange={(e) => patch({ page_transition_duration: Number(e.target.value) || 200 })}
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
                <option value="stagger">Stagger (fade up)</option>
              </select>
            </div>
            {draft.element_entry === "stagger" && (
              <div style={fieldStyle}>
                <label style={labelStyle}>Stagger Delay</label>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <input
                    type="number"
                    min={10}
                    max={200}
                    step={10}
                    value={draft.element_stagger_ms || 30}
                    onChange={(e) => patch({ element_stagger_ms: Number(e.target.value) || 30 })}
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
              background: dirty ? "var(--accent)" : "var(--bg-hover)",
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
      </div>
    </div>
  );
}
