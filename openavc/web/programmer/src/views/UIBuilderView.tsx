import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Settings, Palette, FileCode } from "lucide-react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
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
import type { UIElement, UIPage, UISettings, Placement, MasterElement } from "../api/types";
import * as wsClient from "../api/wsClient";
import { listThemes, getTunnelPrefix } from "../api/restClient";
import { showError } from "../store/toastStore";
import { useProjectStore } from "../store/projectStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";
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
import { Modal, isModalOpen } from "../components/shared/Modal";
import { NumericInput } from "../components/shared/NumericInput";
import {
  SCREEN_PRESETS,
  createDefaultElement,
  addElementToPage,
  removeElementFromPage,
  updateElementInPage,
  moveElementsInPage,
  duplicateElementInPage,
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
  autoPlace,
  defaultElementSize,
  absolutePlacements,
  getPlacement,
  lockedIdsFor,
  pageSnap,
  paletteDragPlacement,
  pointerToPercent,
  resolveDropParent,
  roundPlacement,
  presetForOrientation,
  layoutOrientation,
  resolveHidden,
  withHidden,
  type ValidationIssue,
} from "../components/ui-builder/uiBuilderHelpers";
import { isLightColor } from "../components/ui-builder/colorUtils";
import { showSuccess, showInfo } from "../store/toastStore";

/** Arrow-key travel, in percent, when snapping is off or bypassed. Small
 *  enough to place a control by eye, large enough to see it move. */
const FINE_NUDGE = 0.25;

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
  const activeLayoutId = useUIBuilderStore((s) => s.activeLayoutId);
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
  /** The box a palette drop will land at, in percent. */
  const dragElementSize = useRef<{ w: number; h: number } | null>(null);
  /** The same box in screen pixels, for the ghost. */
  const dragGhostSize = useRef<{ w: number; h: number }>({ w: 120, h: 60 });
  // Pixel offset of the pointer within the palette button at drag start. The
  // drag overlay is anchored to the button origin, but a palette button is a
  // fixed size while the ghost footprint is sized to the landing box — so for
  // palette drags the overlay is re-centred under the pointer using this.
  const dragGrabPx = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  /** Alt state, tracked because a drop only learns about it at drag start. */
  const altHeld = useRef(false);

  useEffect(() => {
    const track = (e: KeyboardEvent) => { altHeld.current = e.altKey; };
    window.addEventListener("keydown", track);
    window.addEventListener("keyup", track);
    return () => {
      window.removeEventListener("keydown", track);
      window.removeEventListener("keyup", track);
    };
  }, []);

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

        // CSS baseline defaults that every element inherits if nothing else is set
        // The stylesheet's base size, in the rem every stored measurement now
        // uses — 1rem IS 14px at the reference, so this is the same baseline it
        // always was. The Style panel converts it back to px for its
        // placeholder; a bare 14 here would advertise a 196px default.
        const cssBaseline: Record<string, unknown> = { font_size: 1 };
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
      // A dialog is up: the canvas is behind a backdrop, so these keys are
      // meant for the dialog. Before this guard, Escape on a dialog also
      // deselected the element underneath, and Delete reached the canvas and
      // removed it.
      if (isModalOpen()) return;
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

      // Arrow keys — nudge the selection. One snap increment per press, ten
      // with shift; holding Alt suspends snapping and nudges by a hair, the
      // same bypass a drag gets.
      if (
        (e.key === "ArrowUp" || e.key === "ArrowDown" ||
         e.key === "ArrowLeft" || e.key === "ArrowRight") && currentPage
      ) {
        const snap = pageSnap(currentPage);
        const stepX = e.altKey || !snap.enabled ? FINE_NUDGE : snap.x;
        const stepY = e.altKey || !snap.enabled ? FINE_NUDGE : snap.y;
        const mult = e.shiftKey ? 10 : 1;
        const dx =
          e.key === "ArrowLeft" ? -stepX * mult : e.key === "ArrowRight" ? stepX * mult : 0;
        const dy =
          e.key === "ArrowUp" ? -stepY * mult : e.key === "ArrowDown" ? stepY * mult : 0;

        // Nudge master element
        if (selectedMasterElementId && masterElements.length > 0) {
          if (lockedElementIds.has(selectedMasterElementId)) return;
          const mel = masterElements.find((m) => m.id === selectedMasterElementId);
          if (!mel) return;
          e.preventDefault();
          const current =
            mel.placements?.landscape ??
            mel.placements?.portrait ??
            Object.values(mel.placements ?? {})[0];
          if (!current) return;
          handleMasterElementPropertyChange(selectedMasterElementId, {
            placements: {
              ...mel.placements,
              landscape: roundPlacement({ ...current, x: current.x + dx, y: current.y + dy }),
            },
          } as Partial<MasterElement>);
          return;
        }

        // Nudge page elements. Nothing clamps: free positioning warns rather
        // than prevents, and the out-of-bounds badge is what says so.
        if (selectedElementIds.length > 0) {
          if (selectedElementIds.some(eid => lockedElementIds.has(eid))) return;
          e.preventDefault();
          const movable = selectedElementIds.filter((eid) =>
            currentPage.elements.some((el) => el.id === eid),
          );
          if (movable.length === 0) return;

          applyMutation((pages) => {
            const target = pages.find((p) => p.id === currentPage.id);
            if (!target) return pages;
            const moved: Record<string, Placement> = {};
            for (const eid of movable) {
              const box = getPlacement(target, eid, activeLayoutId);
              moved[eid] = roundPlacement({ ...box, x: box.x + dx, y: box.y + dy });
            }
            return moveElementsInPage(pages, currentPage.id, moved, activeLayoutId);
          }, `Nudge ${e.key.replace("Arrow", "").toLowerCase()}`, true);
          return;
        }
      }

      if (e.key === "Delete" || e.key === "Backspace") {
        if (selectedMasterElementId) {
          // A locked master is protected from accidental keyboard deletion,
          // matching the nudge guard and the page-element delete guard below.
          if (lockedElementIds.has(selectedMasterElementId)) return;
          e.preventDefault();
          handleDeleteMasterElement(selectedMasterElementId);
          return;
        }
        if (selectedElementIds.length > 0 && currentPage) {
          if (selectedElementIds.some(eid => lockedElementIds.has(eid))) return;
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
      if ((e.ctrlKey || e.metaKey) && !inInput) {
        if (e.key === "z" && !e.shiftKey) {
          e.preventDefault();
          // End any in-flight property-edit burst so the next edit captures a
          // fresh undo snapshot (a property-edit undo keeps the selection, so
          // the selection-change reset effect doesn't fire on its own).
          propertyUndoPushed.current = false;
          clearTimeout(propertyUndoTimer.current);
          undo();
        }
        if ((e.key === "z" && e.shiftKey) || e.key === "y") {
          e.preventDefault();
          propertyUndoPushed.current = false;
          clearTimeout(propertyUndoTimer.current);
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
        if (e.key === "c" && selectedElementIds.length > 0 && currentPage) {
          e.preventDefault();
          handleCopyElement(selectedElementIds);
        }
        if (e.key === "v" && clipboard && currentPage) {
          e.preventDefault();
          handlePasteElement();
        }
        if (e.key === "d" && selectedElementIds.length > 0 && currentPage) {
          e.preventDefault();
          if (selectedElementIds.length === 1) {
            handleDuplicateElement(selectedElementIds[0]);
          } else {
            const masterIds = masterElements.map((m) => m.id);
            applyMutation((pages) => {
              let result = pages;
              for (const eid of selectedElementIds) {
                result = duplicateElementInPage(result, currentPage.id, eid, masterIds, activeLayoutId);
              }
              return result;
            }, `Duplicate ${selectedElementIds.length} elements`);
          }
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  });

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

  // Property changes debounce undo: push undo only once per editing burst,
  // not on every keystroke. The timer resets on each change; the flag clears
  // 800ms after the last edit, or whenever a structural mutation bookends the burst.
  const propertyUndoPushed = useRef(false);
  const propertyUndoTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // --- Mutation helpers ---
  // When `coalesce` is set the mutation joins the current property-edit burst:
  // undo is pushed once and re-armed on an 800ms idle timer, so a held arrow
  // (one nudge per keypress) collapses into a single undoable gesture instead
  // of flooding the 50-entry undo stack. Structural mutations leave it false,
  // which bookends any in-flight burst and starts a fresh entry.
  const applyMutation = useCallback(
    (mutate: (pages: UIPage[]) => UIPage[], description: string, coalesce = false) => {
      if (!project) return;
      if (coalesce) {
        if (!propertyUndoPushed.current) {
          pushUndo({ pages: project.ui.pages }, description);
          propertyUndoPushed.current = true;
        }
      } else {
        pushUndo({ pages: project.ui.pages }, description);
        propertyUndoPushed.current = false;
      }
      clearTimeout(propertyUndoTimer.current);
      const newPages = mutate(project.ui.pages);
      update({ ui: { ...project.ui, pages: newPages } });
      touchMutation();
      if (coalesce) {
        propertyUndoTimer.current = setTimeout(() => {
          propertyUndoPushed.current = false;
        }, 800);
      }
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
      const masterIds = masterElements.map((m) => m.id);
      applyMutation((p) => duplicateElementInPage(p, currentPage.id, elementId, masterIds, activeLayoutId), "Duplicate element");
    },
    [currentPage, masterElements, applyMutation, activeLayoutId],
  );

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
      const els = elementIds
        .map((eid) => currentPage.elements.find((e) => e.id === eid))
        .filter((e): e is UIElement => !!e);
      if (els.length === 0) return;
      // Capture the box the eye sees -- page space, not the stored value. A
      // container child stores percentages OF ITS CONTAINER, and paste ejects
      // to page level, so the raw number would reinterpret half-a-container as
      // half-a-page.
      const absolute = absolutePlacements(currentPage, activeLayoutId);
      const placements: Record<string, Placement> = {};
      for (const el of els) {
        placements[el.id] = absolute[el.id] ?? getPlacement(currentPage, el.id, activeLayoutId);
      }
      setClipboard({ elements: JSON.parse(JSON.stringify(els)), placements });
    },
    [currentPage, activeLayoutId, setClipboard],
  );

  const handlePasteElement = useCallback(() => {
    if (!clipboard || clipboard.elements.length === 0 || !currentPage) return;
    // Page element ids and master ids share the ui.<id> runtime namespace, so a
    // pasted element must avoid colliding with either.
    const existingIds = new Set([
      ...pages.flatMap((p) => p.elements.map((e) => e.id)),
      ...masterElements.map((m) => m.id),
    ]);
    const snap = pageSnap(currentPage);
    const occupied = currentPage.elements.map((el) => getPlacement(currentPage, el.id, activeLayoutId));
    const pasted: { element: UIElement; placement: Placement }[] = [];
    for (const src of clipboard.elements) {
      let id = src.id;
      if (existingIds.has(id)) {
        let counter = 1;
        id = `${src.type}_${counter}`;
        while (existingIds.has(id)) {
          counter++;
          id = `${src.type}_${counter}`;
        }
      }
      existingIds.add(id);
      // Paste has no pointer, so it takes the auto-placement rule: the first
      // free snap cell, or the page centre plus a cascade with snap off.
      const size = clipboard.placements[src.id] ?? { w: 25, h: 12.5 };
      const placement = autoPlace(occupied, size, snap, pasted.length);
      occupied.push(placement);
      pasted.push({ element: { ...JSON.parse(JSON.stringify(src)), id, parent: null }, placement });
    }
    applyMutation((p) => {
      let result = p;
      for (const { element, placement } of pasted) {
        result = addElementToPage(result, currentPage.id, element, placement);
      }
      return result;
    }, `Paste ${pasted.length === 1 ? "element" : `${pasted.length} elements`}`);
  }, [clipboard, currentPage, pages, masterElements, applyMutation, activeLayoutId]);

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
      if ((project.scripts || []).length > 0) {
        showInfo(
          `If any scripts reference "ui.${oldId}.*", update them manually.`,
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
      update({
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
      });
      touchMutation();
    },
    [project, themes, pushUndo, update, touchMutation],
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

  // The pointer's whereabouts during a palette drag, so a mid-drag Alt press
  // can recompute the preview without waiting for the pointer to twitch.
  const lastPalettePointer = useRef<{ x: number; y: number } | null>(null);

  // Snap the palette drag live and publish it for the canvas to draw. This is
  // the SAME math the drop commits, so the footprint and guides never lie.
  const updatePalettePreview = useCallback(() => {
    const store = useUIBuilderStore.getState();
    const pointer = lastPalettePointer.current;
    const size = dragElementSize.current;
    const type = dragElementType.current;
    if (!pointer || !size || !type || !currentPage) {
      if (store.paletteDragPreview) store.setPaletteDragPreview(null);
      return;
    }
    const rect = document.querySelector("[data-canvas-grid]")?.getBoundingClientRect();
    const over =
      rect &&
      pointer.x >= rect.left && pointer.x <= rect.right &&
      pointer.y >= rect.top && pointer.y <= rect.bottom;
    if (!over) {
      if (store.paletteDragPreview) store.setPaletteDragPreview(null);
      return;
    }
    const centre = {
      x: pointerToPercent(pointer.x, rect.left, rect.width),
      y: pointerToPercent(pointer.y, rect.top, rect.height),
    };
    const siblings = currentPage.elements
      .filter((el) => !el.parent)
      .map((el) => getPlacement(currentPage, el.id, activeLayoutId));
    const outcome = paletteDragPlacement(centre, size, {
      snap: pageSnap(currentPage),
      bypass: altHeld.current,
      others: siblings,
    });
    const { parentId } = resolveDropParent(currentPage, outcome.placement, activeLayoutId);
    store.setPaletteDragPreview({
      placement: outcome.placement,
      guidesX: outcome.guidesX,
      guidesY: outcome.guidesY,
      adoptInto: parentId,
      label: type,
    });
  }, [currentPage, activeLayoutId]);

  // The preview follows the REAL pointer, from a native listener — never
  // dnd-kit's delta. The delta folds in scroll compensation, so when the
  // palette list was scrolled (its auto-scroll crawling back up during the
  // drag), start + delta drifted from the cursor by exactly the scroll — and
  // the footprint and the landing drifted with it.
  const palettePointerCleanup = useRef<(() => void) | null>(null);

  const trackPalettePointer = useCallback(() => {
    palettePointerCleanup.current?.();
    const onMove = (ev: PointerEvent) => {
      lastPalettePointer.current = { x: ev.clientX, y: ev.clientY };
      updatePalettePreview();
    };
    window.addEventListener("pointermove", onMove);
    palettePointerCleanup.current = () => {
      window.removeEventListener("pointermove", onMove);
      palettePointerCleanup.current = null;
    };
  }, [updatePalettePreview]);

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const data = event.active.data.current as
        | { source: string; elementType?: string; elementId?: string }
        | undefined;
      setActiveDragSource(data?.source || null);
      dragElementType.current = data?.elementType || null;
      const pointerEvent = event.activatorEvent as PointerEvent;
      dragStartPointer.current = {
        x: pointerEvent.clientX,
        y: pointerEvent.clientY,
      };

      // Only the palette drags through dnd-kit now — moving an element that is
      // already on the canvas is a pointer gesture Canvas owns, so it can snap
      // live and commit once on pointer-up.
      if (data?.source === "palette" && data?.elementType) {
        draggedElement.current = createDefaultElement(data.elementType, new Set());
        dragElementSize.current = defaultElementSize(data.elementType, panelElements);
        lastPalettePointer.current = {
          x: pointerEvent.clientX,
          y: pointerEvent.clientY,
        };
        trackPalettePointer();
      } else {
        draggedElement.current = null;
        dragElementSize.current = null;
      }

      const canvasGrid = document.querySelector("[data-canvas-grid]");
      if (canvasGrid && draggedElement.current && dragElementSize.current) {
        const rect = canvasGrid.getBoundingClientRect();
        const size = dragElementSize.current;
        // The ghost is drawn at the size the element will actually land at,
        // which is a percentage of the canvas — zoom included, since the rect
        // is already the scaled one.
        dragGhostSize.current = {
          w: (size.w / 100) * rect.width,
          h: (size.h / 100) * rect.height,
        };
        // Centre the element under the pointer, and re-centre the DragOverlay
        // to match so the ghost and the landing spot agree. Measured from the
        // palette item's real DOM rect: dnd-kit's own measurement
        // (active.rect.current.initial) is still null at drag start, so the
        // old read always fell back to 0,0 and the ghost rode off-cursor by
        // the grab offset.
        const itemRect = (pointerEvent.target as Element | null)
          ?.closest('[aria-roledescription="draggable"]')
          ?.getBoundingClientRect();
        dragGrabPx.current = {
          x: itemRect ? pointerEvent.clientX - itemRect.left : 0,
          y: itemRect ? pointerEvent.clientY - itemRect.top : 0,
        };
      }
    },
    [setActiveDragSource, panelElements, trackPalettePointer],
  );

  // Alt toggled mid-drag changes the answer without the pointer moving. The
  // window-level tracker above updates altHeld first (it registered earlier),
  // so recomputing here sees the new state.
  useEffect(() => {
    if (activeDragSource !== "palette") return;
    const onModifier = (e: KeyboardEvent) => {
      if (e.key === "Alt") updatePalettePreview();
    };
    window.addEventListener("keydown", onModifier);
    window.addEventListener("keyup", onModifier);
    return () => {
      window.removeEventListener("keydown", onModifier);
      window.removeEventListener("keyup", onModifier);
    };
  }, [activeDragSource, updatePalettePreview]);

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      palettePointerCleanup.current?.();
      const preview = useUIBuilderStore.getState().paletteDragPreview;
      useUIBuilderStore.getState().setPaletteDragPreview(null);
      setActiveDragSource(null);
      dragElementType.current = null;
      draggedElement.current = null;
      const releasePointer = lastPalettePointer.current;
      lastPalettePointer.current = null;
      const { active, over } = event;
      if (!over || over.id !== "canvas-drop" || !currentPage || !project)
        return;
      if (!dragStartPointer.current) return;

      const data = active.data.current as
        | { source: string; elementType?: string; elementId?: string }
        | undefined;
      if (!data) return;

      const canvasGrid = document.querySelector("[data-canvas-grid]");
      const canvasRect = canvasGrid?.getBoundingClientRect();
      if (!canvasRect) return;
      if (data.source !== "palette" || !data.elementType) return;

      // Create new element — collect IDs from ALL pages AND master_elements
      // (shared ui.<id> namespace) to avoid collisions.
      const existingIds = new Set([
        ...pages.flatMap((p) => p.elements.map((e) => e.id)),
        ...masterElements.map((m) => m.id),
      ]);
      const newElement = createDefaultElement(data.elementType, existingIds);
      const size = defaultElementSize(data.elementType, panelElements);

      // The element lands exactly where the live preview showed it — same box,
      // same snap, same Alt bypass. The recompute below only covers a drop the
      // preview never saw (nothing moved after entering the canvas).
      let placement: Placement;
      if (preview) {
        placement = preview.placement;
      } else {
        // Same native-tracked pointer the preview uses — dnd-kit's delta is
        // scroll-compensated and drifts when the palette list scrolls.
        const pointerX = (releasePointer ?? dragStartPointer.current).x;
        const pointerY = (releasePointer ?? dragStartPointer.current).y;
        const centre = {
          x: pointerToPercent(pointerX, canvasRect.left, canvasRect.width),
          y: pointerToPercent(pointerY, canvasRect.top, canvasRect.height),
        };
        const siblings = currentPage.elements
          .filter((el) => !el.parent)
          .map((el) => getPlacement(currentPage, el.id, activeLayoutId));
        const bypass = !!(event.activatorEvent as PointerEvent | undefined)?.altKey || altHeld.current;
        placement = paletteDragPlacement(centre, size, {
          snap: pageSnap(currentPage),
          bypass,
          others: siblings,
        }).placement;
      }

      // A drop inside a container becomes a child of it, with coordinates
      // re-expressed relative to that container — which is what the author
      // just showed you they meant by dropping it there. Measured against the
      // arrangement on screen: a container can sit somewhere else entirely in
      // another layout, and the pointer was over THIS one.
      const { parentId, relative } = resolveDropParent(currentPage, placement, activeLayoutId);
      if (parentId) newElement.parent = parentId;

      applyMutation(
        (p) => addElementToPage(p, currentPage.id, newElement, relative),
        `Add ${data.elementType}`,
      );
      selectElement(newElement.id);

      dragStartPointer.current = null;
    },
    [currentPage, pages, masterElements, project, applyMutation, selectElement, setActiveDragSource, panelElements, activeLayoutId],
  );

  const handleDragCancel = useCallback(() => {
    palettePointerCleanup.current?.();
    useUIBuilderStore.getState().setPaletteDragPreview(null);
    setActiveDragSource(null);
    dragElementType.current = null;
    draggedElement.current = null;
    dragStartPointer.current = null;
    lastPalettePointer.current = null;
  }, [setActiveDragSource]);

  // A drag in flight when the view unmounts would leave the tracker attached.
  useEffect(() => () => palettePointerCleanup.current?.(), []);

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
      // Pointer-based: the drop is accepted exactly when the pointer is over
      // the canvas, which is also when the snapped preview is showing — so
      // what you see mid-drag and what a release does can never disagree.
      collisionDetection={pointerWithin}
      // No auto-scroll: a palette drag is headed for the canvas, and the list
      // crawling along under the drag both looks broken and feeds scroll
      // compensation into dnd-kit's coordinates.
      autoScroll={false}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
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

        {/* Toolbar — two fixed rows; Theme/Settings dock at the end of the pages row */}
        <CanvasToolbar
          pages={pages}
          selectedPageId={currentPage?.id || null}
          onValidate={() => setValidationIssues(validateProject(project))}
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
                          // Page + master ids share the ui.<id> namespace.
                          const existingIds = new Set([
                            ...pages.flatMap((p) => p.elements.map((e) => e.id)),
                            ...masterElements.map((m) => m.id),
                          ]);
                          const newElement = createDefaultElement(type, existingIds);
                          // Click-to-add has no pointer to land on, so it takes
                          // the auto-placement rule: the first free snap cell,
                          // scanning row-major — which is exactly where the old
                          // grid would have put it — or the page centre plus a
                          // cascade when snap is off.
                          const placement = autoPlace(
                            currentPage.elements
                              .filter((el) => !el.parent)
                              .map((el) => getPlacement(currentPage, el.id, activeLayoutId)),
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
        {activeDragSource && draggedElement.current ? (
          <PaletteDragGhost
            label={draggedElement.current.type}
            width={dragGhostSize.current.w}
            height={dragGhostSize.current.h}
            grabX={dragGrabPx.current.x}
            grabY={dragGrabPx.current.y}
          />
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
              const masterIds = masterElements.map((m) => m.id);
              applyMutation((pages) => {
                let result = pages;
                for (const eid of selectedElementIds) {
                  result = duplicateElementInPage(result, currentPage.id, eid, masterIds, activeLayoutId);
                }
                return result;
              }, `Duplicate ${selectedElementIds.length} elements`);
            }
          }}
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
            pushUndo({ custom_css: project.ui.custom_css ?? "" }, "Edit project stylesheet");
            propertyUndoPushed.current = false;
            clearTimeout(propertyUndoTimer.current);
            update({ ui: { ...project.ui, custom_css: css } });
            touchMutation();
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
            pushUndo({ pages: project.ui.pages }, "Reset element styles to theme");
            propertyUndoPushed.current = false;
            clearTimeout(propertyUndoTimer.current);
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
            update({ ui: { ...project.ui, pages: newPages } });
            touchMutation();
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

/**
 * The cross-panel ghost for a palette drag. It hides itself the moment the
 * snapped canvas footprint takes over, and it subscribes to the store itself
 * so the per-pointer-move re-render stays contained here instead of re-running
 * the whole view.
 */
function PaletteDragGhost({
  label,
  width,
  height,
  grabX,
  grabY,
}: {
  label: string;
  width: number;
  height: number;
  grabX: number;
  grabY: number;
}) {
  const overCanvas = useUIBuilderStore((s) => !!s.paletteDragPreview);
  if (overCanvas) return null;
  // dnd-kit anchors the overlay to the palette button's origin, so shift the
  // ghost until its centre sits under the pointer — matching where the centred
  // drop actually lands.
  return (
    <div
      style={{
        width,
        height,
        transform: `translate(${grabX - width / 2}px, ${grabY - height / 2}px)`,
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
      {label.replace(/_/g, " ")}
    </div>
  );
}
