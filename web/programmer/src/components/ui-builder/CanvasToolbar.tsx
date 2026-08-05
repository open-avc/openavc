import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import {
  Plus,
  X,
  Grid3x3,
  ZoomIn,
  ZoomOut,
  Play,
  Square,
  Layers,
  PanelRight,
  AlignStartVertical,
  AlignCenterVertical,
  AlignEndVertical,
  AlignStartHorizontal,
  AlignCenterHorizontal,
  AlignEndHorizontal,
  ChevronLeft,
  ChevronRight,
  ChevronDown as ChevronDownIcon,
  FolderOpen,
  FolderPlus,
  Copy,
  Undo2,
  Redo2,
  Check,
  Loader2,
  AlignHorizontalDistributeCenter,
  AlignVerticalDistributeCenter,
  MoveHorizontal,
  MoveVertical,
  Scaling,
  Home,
  Pencil,
  RectangleHorizontal,
  RectangleVertical,
} from "lucide-react";
import type { UIPage, PageGroup, SnapConfig } from "../../api/types";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { PromptDialog } from "../shared/PromptDialog";
import { NumericInput } from "../shared/NumericInput";
import { LAYER } from "../shared/layers";
import {
  SCREEN_PRESETS,
  addPage,
  removePageAndScrubRefs,
  pageSnap,
  renamePage,
  reorderPage,
  duplicatePage,
  alignElements,
  distributeElements,
  matchSizeElements,
  addPageGroup,
  removePageGroup,
  renamePageGroup,
  assignPageToGroup,
  addLayout,
  removeLayout,
  layoutById,
  missingOrientations,
  presetForOrientation,
  layoutOrientation,
  type AlignAction,
  type DistributeAxis,
  type MatchSizeAction,
  type Orientation,
} from "./uiBuilderHelpers";

interface CanvasToolbarProps {
  pages: UIPage[];
  selectedPageId: string | null;
  onValidate?: () => void;
  /** View-owned buttons (Theme, Settings, shortcuts) docked at the right end
   *  of the pages row, so the whole strip is two fixed rows and nothing else. */
  trailing?: React.ReactNode;
}

/** Everything the tab context menu needs to know about where it was opened. */
interface TabMenuState {
  x: number;
  y: number;
  pageId: string;
}

/** A tab that can be the home page. The loader materialises page_type as
 *  "page" on every round-trip, so testing `!page.page_type` alone matches
 *  only never-saved pages — the guard that kept the home affordances from
 *  ever showing on a loaded project. */
const isRegularPage = (p: UIPage) => !p.page_type || p.page_type === "page";

const toolButton: React.CSSProperties = {
  display: "flex",
  padding: 3,
  color: "var(--text-muted)",
  borderRadius: 3,
  background: "transparent",
  border: "none",
  cursor: "pointer",
};

const divider: React.CSSProperties = {
  width: 1,
  height: 20,
  background: "var(--border-color)",
  flexShrink: 0,
};

export function CanvasToolbar({ pages, selectedPageId, onValidate, trailing }: CanvasToolbarProps) {
  const selectPage = useUIBuilderStore((s) => s.selectPage);
  const previewMode = useUIBuilderStore((s) => s.previewMode);
  const setPreviewMode = useUIBuilderStore((s) => s.setPreviewMode);
  const showGrid = useUIBuilderStore((s) => s.showGrid);
  const toggleGrid = useUIBuilderStore((s) => s.toggleGrid);
  const zoom = useUIBuilderStore((s) => s.zoom);
  const setZoom = useUIBuilderStore((s) => s.setZoom);
  const screenPresetIndex = useUIBuilderStore((s) => s.screenPresetIndex);
  const setScreenPresetIndex = useUIBuilderStore((s) => s.setScreenPresetIndex);
  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  const activeLayoutId = useUIBuilderStore((s) => s.activeLayoutId);
  const selectLayout = useUIBuilderStore((s) => s.selectLayout);
  const pushUndo = useUIBuilderStore((s) => s.pushUndo);
  const touchMutation = useUIBuilderStore((s) => s.touchMutation);

  const ui = useProjectStore((s) => s.project?.ui);
  const update = useProjectStore((s) => s.update);
  const dirty = useProjectStore((s) => s.dirty);
  const saving = useProjectStore((s) => s.saving);
  const savePending = useProjectStore((s) => s.savePending);
  const error = useProjectStore((s) => s.error);
  const conflictDetected = useProjectStore((s) => s.conflictDetected);
  const undo = useUIBuilderStore((s) => s.undo);
  const redo = useUIBuilderStore((s) => s.redo);
  const undoStack = useUIBuilderStore((s) => s.undoStack);
  const redoStack = useUIBuilderStore((s) => s.redoStack);

  const [renamingPageId, setRenamingPageId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [hoveredTabId, setHoveredTabId] = useState<string | null>(null);
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [showSnapMenu, setShowSnapMenu] = useState(false);
  const [tabMenu, setTabMenu] = useState<TabMenuState | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [renamingGroupName, setRenamingGroupName] = useState<string | null>(null);
  const [groupRenameValue, setGroupRenameValue] = useState("");
  const [pendingDeletePageId, setPendingDeletePageId] = useState<string | null>(null);
  const [pendingDeleteGroup, setPendingDeleteGroup] = useState<string | null>(null);
  const [showNewGroupPrompt, setShowNewGroupPrompt] = useState(false);
  const [pendingDeleteLayoutId, setPendingDeleteLayoutId] = useState<string | null>(null);
  const addMenuRef = useRef<HTMLDivElement>(null);
  const snapMenuRef = useRef<HTMLDivElement>(null);
  const tabMenuRef = useRef<HTMLDivElement>(null);
  const gridUndoPushed = useRef(false);
  const gridUndoTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Clear the grid-undo batching timer on unmount (it only resets a ref, so
  // firing late is harmless — but the timer itself must not outlive us).
  useEffect(() => () => clearTimeout(gridUndoTimer.current), []);

  // Close popup menus on click outside
  useEffect(() => {
    if (!showAddMenu && !showSnapMenu && !tabMenu) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (showAddMenu && addMenuRef.current && !addMenuRef.current.contains(t)) {
        setShowAddMenu(false);
      }
      if (showSnapMenu && snapMenuRef.current && !snapMenuRef.current.contains(t)) {
        setShowSnapMenu(false);
      }
      if (tabMenu && tabMenuRef.current && !tabMenuRef.current.contains(t)) {
        setTabMenu(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showAddMenu, showSnapMenu, tabMenu]);

  const pageGroups: PageGroup[] = ui?.page_groups || [];

  // Build organized page list: grouped pages followed by ungrouped pages
  const organizedPages = useMemo(() => {
    if (pageGroups.length === 0) return [{ group: null, pages }] as { group: PageGroup | null; pages: UIPage[] }[];
    const groupedPageIds = new Set(pageGroups.flatMap(g => g.pages));
    const result: { group: PageGroup | null; pages: UIPage[] }[] = [];
    for (const group of pageGroups) {
      const groupPages = group.pages
        .map(id => pages.find(p => p.id === id))
        .filter((p): p is UIPage => !!p);
      if (groupPages.length > 0) result.push({ group, pages: groupPages });
    }
    const ungrouped = pages.filter(p => !groupedPageIds.has(p.id));
    if (ungrouped.length > 0) result.push({ group: null, pages: ungrouped });
    return result;
  }, [pages, pageGroups]);

  const applyPageMutation = useCallback(
    (mutate: (pages: UIPage[]) => UIPage[], description: string) => {
      if (!ui) return;
      pushUndo({ pages: ui.pages }, description);
      const newPages = mutate(ui.pages);
      update({ ui: { ...ui, pages: newPages } });
      touchMutation();
    },
    [ui, pushUndo, update, touchMutation],
  );

  const applyGroupMutation = useCallback(
    (mutate: (groups: PageGroup[]) => PageGroup[], description: string) => {
      if (!ui) return;
      pushUndo({ page_groups: ui.page_groups || [] }, description);
      const newGroups = mutate(ui.page_groups || []);
      update({ ui: { ...ui, page_groups: newGroups } });
      touchMutation();
    },
    [ui, pushUndo, update, touchMutation],
  );

  const handleAddGroup = (name: string) => {
    applyGroupMutation((g) => addPageGroup(g, name), `Add group "${name}"`);
  };

  const handleDeleteGroup = (groupName: string) => {
    applyGroupMutation((g) => removePageGroup(g, groupName), `Delete group "${groupName}"`);
  };

  const handleRenameGroup = (oldName: string, newName: string) => {
    if (!newName.trim() || newName === oldName) return;
    applyGroupMutation((g) => renamePageGroup(g, oldName, newName.trim()), `Rename group`);
  };

  const handleAssignPageToGroup = (pageId: string, groupName: string | null) => {
    applyGroupMutation((g) => assignPageToGroup(g, pageId, groupName), "Assign page to group");
  };

  const handleGroupRenameSubmit = () => {
    if (renamingGroupName && groupRenameValue.trim()) {
      handleRenameGroup(renamingGroupName, groupRenameValue.trim());
    }
    setRenamingGroupName(null);
  };

  const handleAddPage = (pageType: "page" | "overlay" | "sidebar" = "page") => {
    if (!ui) return;
    const newPages = addPage(ui.pages, pageType);
    const newPageId = newPages[newPages.length - 1].id;
    applyPageMutation(() => newPages, `Add ${pageType}`);
    selectPage(newPageId);
    setShowAddMenu(false);
  };

  const handleDuplicatePage = (pageId: string) => {
    if (!ui) return;
    const idx = ui.pages.findIndex((p) => p.id === pageId);
    // Master ids share the ui.<id> namespace with page element ids
    const masterIds = (ui.master_elements || []).map((m) => m.id);
    const newPages = duplicatePage(ui.pages, pageId, masterIds);
    const newPageId = newPages[idx + 1]?.id;
    applyPageMutation(() => newPages, "Duplicate page");
    if (newPageId) selectPage(newPageId);
  };

  const handleDeletePage = (pageId: string) => {
    if (pages.length <= 1) return;
    if (!ui) return;
    setPendingDeletePageId(pageId);
  };

  const confirmDeletePage = () => {
    if (!pendingDeletePageId || !ui) { setPendingDeletePageId(null); return; }
    const pageId = pendingDeletePageId;
    const pageName = pages.find(p => p.id === pageId)?.name || pageId;
    const nextPageId = pages.find((p) => p.id !== pageId)?.id;
    const idleClobbered = ui.settings?.idle_page === pageId;

    // Single references for the scrub inputs — the changed-only snapshot
    // below relies on identity, and a fresh `|| []` per comparison would
    // never match what the scrub returned.
    const masterElements = ui.master_elements || [];
    const projectMacros = useProjectStore.getState().project?.macros || [];
    const result = removePageAndScrubRefs(
      ui.pages,
      pageId,
      masterElements,
      projectMacros,
    );
    const snapshot: Record<string, unknown> = { pages: ui.pages };
    if (idleClobbered) snapshot.settings = ui.settings;
    if (result.masterElements !== masterElements) snapshot.master_elements = masterElements;
    if (result.macros !== projectMacros) snapshot.macros = projectMacros;
    pushUndo(snapshot, `Delete page "${pageName}"`);

    const settings = idleClobbered
      ? { ...ui.settings, idle_page: result.pages[0]?.id || "" }
      : ui.settings;
    update({
      ui: { ...ui, pages: result.pages, master_elements: result.masterElements, settings },
      macros: result.macros,
    });
    touchMutation();
    if (selectedPageId === pageId && nextPageId) {
      selectPage(nextPageId);
    }
    setPendingDeletePageId(null);
  };

  const startRename = (pageId: string, name: string) => {
    setRenamingPageId(pageId);
    setRenameValue(name);
  };

  const handleRenameSubmit = () => {
    if (renamingPageId && renameValue.trim()) {
      applyPageMutation(
        (p) => renamePage(p, renamingPageId, renameValue.trim()),
        "Rename page",
      );
    }
    setRenamingPageId(null);
  };

  const handleAlign = (action: AlignAction) => {
    if (selectedElementIds.length === 0 || !selectedPageId) return;
    applyPageMutation(
      (p) => alignElements(p, selectedPageId!, selectedElementIds, action, activeLayoutId),
      `Align ${action}`,
    );
  };

  const handleDistribute = (axis: DistributeAxis) => {
    if (selectedElementIds.length < 3 || !selectedPageId) return;
    applyPageMutation(
      (p) => distributeElements(p, selectedPageId!, selectedElementIds, axis, activeLayoutId),
      axis === "horizontal" ? "Distribute horizontally" : "Distribute vertically",
    );
  };

  const handleMatchSize = (action: MatchSizeAction) => {
    if (selectedElementIds.length < 2 || !selectedPageId) return;
    applyPageMutation(
      (p) => matchSizeElements(p, selectedPageId!, selectedElementIds, action, activeLayoutId),
      action === "match-width"
        ? "Match width"
        : action === "match-height"
        ? "Match height"
        : "Match size",
    );
  };

  // --- Layout variants ---
  // One set of controls, one or more arrangements of them. The primary is what
  // every unmatched screen falls back to; a variant stores only what moved.

  const currentPage = pages.find((p) => p.id === selectedPageId) ?? null;
  const layouts = currentPage?.layouts ?? [];
  const activeLayout = currentPage ? layoutById(currentPage, activeLayoutId) : undefined;
  const addable = missingOrientations(currentPage ?? undefined);

  const handleAddLayout = (orientation: Orientation) => {
    if (!currentPage) return;
    const before = new Set(currentPage.layouts?.map((l) => l.id) ?? []);
    const grown = addLayout(currentPage, orientation);
    const created = (grown.layouts ?? []).find((l) => !before.has(l.id));
    applyPageMutation(
      (p) => p.map((pg) => (pg.id === currentPage.id ? grown : pg)),
      `Add ${orientation} layout`,
    );
    // Switch straight to it: you added it to author it, and it looks identical
    // to the primary until you move something, so there is nothing to lose.
    if (created) selectLayout(created.id);
  };

  const confirmDeleteLayout = () => {
    if (!currentPage || !pendingDeleteLayoutId) return;
    applyPageMutation(
      (p) =>
        p.map((pg) => (pg.id === currentPage.id ? removeLayout(pg, pendingDeleteLayoutId) : pg)),
      "Delete layout",
    );
    if (activeLayoutId === pendingDeleteLayoutId) selectLayout(null);
    setPendingDeleteLayoutId(null);
  };

  // The presets are all landscape; a portrait arrangement gets the same screen
  // turned, because nobody can design a portrait panel on a landscape canvas.
  const preset = SCREEN_PRESETS[screenPresetIndex];
  const presetSize = presetForOrientation(
    preset,
    layoutOrientation(currentPage ?? undefined, activeLayoutId),
  );

  const snap = currentPage ? pageSnap(currentPage) : null;
  const handleSnapChange = (patch: Partial<SnapConfig>) => {
    if (!currentPage || !ui || !snap) return;
    const updatedPages = ui.pages.map((p) =>
      p.id === currentPage.id ? { ...p, snap: { ...snap, ...patch } } : p,
    );
    if (!gridUndoPushed.current) {
      pushUndo({ pages: ui.pages }, "Edit snap");
      gridUndoPushed.current = true;
    }
    update({ ui: { ...ui, pages: updatedPages } });
    touchMutation();
    clearTimeout(gridUndoTimer.current);
    gridUndoTimer.current = setTimeout(() => {
      gridUndoPushed.current = false;
    }, 800);
  };

  const menuPage = tabMenu ? pages.find((p) => p.id === tabMenu.pageId) ?? null : null;
  const menuPageGroup = menuPage
    ? pageGroups.find((g) => g.pages.includes(menuPage.id))?.name ?? null
    : null;

  const menuItemStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    width: "100%",
    padding: "5px 12px",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-primary)",
    background: "transparent",
    border: "none",
    cursor: "pointer",
    textAlign: "left",
    whiteSpace: "nowrap",
  };
  const menuHover = {
    onMouseEnter: (e: React.MouseEvent) =>
      ((e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"),
    onMouseLeave: (e: React.MouseEvent) =>
      ((e.currentTarget as HTMLElement).style.background = "transparent"),
  };

  const renderTab = (page: UIPage) => {
    const isActive = selectedPageId === page.id;
    return (
      <div
        key={page.id}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 3,
          padding: "2px 8px",
          borderRadius: "var(--border-radius)",
          background: isActive ? "var(--accent-dim)" : "transparent",
          cursor: "pointer",
          fontSize: "var(--font-size-sm)",
          whiteSpace: "nowrap",
          border: isActive ? "1px solid var(--accent)" : "1px solid transparent",
          flexShrink: 0,
        }}
        onClick={() => selectPage(page.id)}
        onDoubleClick={() => startRename(page.id, page.name)}
        onMouseEnter={() => setHoveredTabId(page.id)}
        onMouseLeave={() => setHoveredTabId((h) => (h === page.id ? null : h))}
        onContextMenu={(e) => {
          e.preventDefault();
          setTabMenu({ x: e.clientX, y: e.clientY, pageId: page.id });
        }}
        title={`${page.name}${isRegularPage(page) ? "" : ` (${page.page_type})`} — ${page.elements.length} element${page.elements.length !== 1 ? "s" : ""}. Double-click to rename, right-click for more.`}
      >
        {/* Page type icon */}
        {!page.page_type && (
          <Square size={10} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
        )}
        {page.page_type === "overlay" && (
          <Layers size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
        )}
        {page.page_type === "sidebar" && (
          <PanelRight size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
        )}
        {renamingPageId === page.id ? (
          <input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onBlur={handleRenameSubmit}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRenameSubmit();
              if (e.key === "Escape") setRenamingPageId(null);
            }}
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 80,
              padding: "0 4px",
              fontSize: "var(--font-size-sm)",
              background: "var(--bg-base)",
              border: "1px solid var(--accent)",
              borderRadius: 3,
              color: "var(--text-primary)",
            }}
          />
        ) : (
          page.name
        )}
        {/* Home page indicator */}
        {pages[0]?.id === page.id && isRegularPage(page) && (
          <span title="Home page (shown first on startup)"><Home size={10} style={{ color: "var(--accent)" }} /></span>
        )}
        {/* Quick actions appear on the HOVERED tab only — every page is also
            one right-click away. Hover, not active: showing them on the active
            tab resizes OTHER tabs when the selection moves, which shifts the
            strip mid-double-click and lands the second click on a button. The
            e.detail guard keeps a double-click meaning rename even when it
            ends on one of these. */}
        {hoveredTabId === page.id && !previewMode && renamingPageId !== page.id && (
          <>
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (e.detail > 1) return;
                startRename(page.id, page.name);
              }}
              style={{ display: "flex", padding: 1, color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer" }}
              title="Rename page"
            >
              <Pencil size={10} />
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (e.detail > 1) return;
                handleDuplicatePage(page.id);
              }}
              style={{ display: "flex", padding: 1, color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer" }}
              title="Duplicate page"
            >
              <Copy size={10} />
            </button>
            {pages.length > 1 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (e.detail > 1) return;
                  handleDeletePage(page.id);
                }}
                style={{ display: "flex", padding: 1, color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer" }}
                title="Delete page"
              >
                <X size={11} />
              </button>
            )}
          </>
        )}
      </div>
    );
  };

  return (
    <div
      style={{
        borderBottom: "1px solid var(--border-color)",
        background: "var(--bg-surface)",
        flexShrink: 0,
      }}
    >
      {/* ---- Row 1: pages ---- */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "2px var(--space-md) 0",
          minHeight: 30,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            flex: 1,
            minWidth: 0,
            overflow: "auto",
          }}
        >
          {organizedPages.map(({ group, pages: groupPages }) => {
            const isCollapsed = group ? collapsedGroups.has(group.name) : false;
            return (
              <div key={group?.name || "_ungrouped"} style={{ display: "flex", alignItems: "center", gap: 2 }}>
                {/* Group header */}
                {group && (
                  <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
                    <button
                      onClick={() => {
                        setCollapsedGroups(prev => {
                          const next = new Set(prev);
                          if (next.has(group.name)) next.delete(group.name);
                          else next.add(group.name);
                          return next;
                        });
                      }}
                      onDoubleClick={(e) => {
                        e.stopPropagation();
                        setRenamingGroupName(group.name);
                        setGroupRenameValue(group.name);
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 2,
                        padding: "2px 6px",
                        fontSize: 10,
                        fontWeight: 600,
                        textTransform: "uppercase",
                        letterSpacing: "0.5px",
                        color: "var(--text-muted)",
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        whiteSpace: "nowrap",
                      }}
                      title={isCollapsed ? "Expand group (double-click to rename)" : "Collapse group (double-click to rename)"}
                    >
                      <FolderOpen size={10} style={{ color: "var(--text-muted)" }} />
                      {renamingGroupName === group.name ? (
                        <input
                          autoFocus
                          value={groupRenameValue}
                          onChange={(e) => setGroupRenameValue(e.target.value)}
                          onBlur={handleGroupRenameSubmit}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleGroupRenameSubmit();
                            if (e.key === "Escape") setRenamingGroupName(null);
                          }}
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            width: 70,
                            padding: "0 3px",
                            fontSize: 10,
                            fontWeight: 600,
                            textTransform: "uppercase",
                            background: "var(--bg-base)",
                            border: "1px solid var(--accent)",
                            borderRadius: 3,
                            color: "var(--text-primary)",
                          }}
                        />
                      ) : (
                        group.name
                      )}
                      <ChevronDownIcon size={10} style={{ transform: isCollapsed ? "rotate(-90deg)" : "none", transition: "transform 0.15s" }} />
                    </button>
                    {!previewMode && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setPendingDeleteGroup(group.name);
                        }}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          padding: 2,
                          color: "var(--text-muted)",
                          background: "none",
                          border: "none",
                          cursor: "pointer",
                        }}
                        title="Delete group"
                      >
                        <X size={12} />
                      </button>
                    )}
                  </div>
                )}
                {/* Group pages (collapsible) */}
                {!isCollapsed && groupPages.map(renderTab)}
                {/* Group separator */}
                {group && (
                  <div style={{ width: 1, height: 16, background: "var(--border-color)", margin: "0 2px" }} />
                )}
              </div>
            );
          })}
        </div>
        {/* + Add button — OUTSIDE the overflow:auto container so the dropdown isn't clipped */}
        {!previewMode && (
          <div ref={addMenuRef} style={{ position: "relative", flexShrink: 0 }}>
            <button
              onClick={() => setShowAddMenu(!showAddMenu)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 2,
                padding: "3px 10px",
                borderRadius: "var(--border-radius)",
                color: "var(--text-secondary)",
                fontSize: "var(--font-size-sm)",
                background: "var(--bg-hover)",
                cursor: "pointer",
              }}
              title="Add page, overlay, or sidebar"
            >
              <Plus size={14} />
              <span style={{ fontSize: 11 }}>Add</span>
              <ChevronDownIcon size={10} />
            </button>
            {showAddMenu && (
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  right: 0,
                  marginTop: 4,
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-color)",
                  borderRadius: 6,
                  boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                  zIndex: 100,
                  minWidth: 130,
                  overflow: "hidden",
                }}
              >
                {(
                  [
                    { type: "page" as const, label: "Page", icon: null },
                    { type: "overlay" as const, label: "Overlay", icon: <Layers size={12} /> },
                    { type: "sidebar" as const, label: "Sidebar", icon: <PanelRight size={12} /> },
                  ] as const
                ).map((item) => (
                  <button
                    key={item.type}
                    onClick={() => handleAddPage(item.type)}
                    style={menuItemStyle}
                    {...menuHover}
                  >
                    {item.icon}
                    {item.label}
                  </button>
                ))}
                <div style={{ borderTop: "1px solid var(--border-color)", margin: "4px 0" }} />
                <button
                  onClick={() => {
                    setShowNewGroupPrompt(true);
                    setShowAddMenu(false);
                  }}
                  style={menuItemStyle}
                  {...menuHover}
                >
                  <FolderPlus size={12} />
                  New Group
                </button>
              </div>
            )}
          </div>
        )}
        {trailing}
      </div>

      {/* ---- Row 2: tools. Fixed height, never wraps — every group is either
           compact or always-present-but-disabled, so nothing jumps around. ---- */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "2px var(--space-md) 4px",
          minHeight: 32,
          overflow: "hidden",
          whiteSpace: "nowrap",
        }}
      >
        {!previewMode && currentPage && (
          <>
            {/* Layout variants, icon-only. The panel picks an arrangement by
                the shape of the glass at runtime; this picks which one you are
                authoring. */}
            <div style={{ display: "flex", alignItems: "center", gap: 2, flexShrink: 0 }}>
              {layouts.map((layout) => {
                const isActive = layout.id === (activeLayout?.id ?? null);
                const Icon = layout.orientation === "portrait" ? RectangleVertical : RectangleHorizontal;
                const label = layout.orientation === "portrait" ? "Portrait" : "Landscape";
                return (
                  <button
                    key={layout.id}
                    onClick={() => selectLayout(layout.primary ? null : layout.id)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      padding: "3px 5px",
                      borderRadius: 3,
                      border: "1px solid",
                      borderColor: isActive ? "var(--accent)" : "var(--border-color)",
                      background: isActive ? "var(--accent-dim)" : "transparent",
                      color: isActive ? "var(--accent)" : "var(--text-muted)",
                      cursor: "pointer",
                    }}
                    title={
                      layout.primary
                        ? `${label} is the primary arrangement. Every screen that has no layout of its own falls back to it.`
                        : `${label} arrangement. It stores only what you move here; everything else follows the primary.`
                    }
                  >
                    <Icon size={13} />
                  </button>
                );
              })}
              {addable.map((orientation) => (
                <button
                  key={`add-${orientation}`}
                  onClick={() => handleAddLayout(orientation)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    padding: "3px 4px",
                    borderRadius: 3,
                    border: "1px dashed var(--border-color)",
                    background: "transparent",
                    color: "var(--text-muted)",
                    cursor: "pointer",
                  }}
                  title={`Add a ${orientation} arrangement of these same controls. It starts identical to the primary and stores only what you move.`}
                >
                  <Plus size={9} />
                  {orientation === "portrait" ? <RectangleVertical size={13} /> : <RectangleHorizontal size={13} />}
                </button>
              ))}
              {activeLayout && !activeLayout.primary && (
                <button
                  onClick={() => setPendingDeleteLayoutId(activeLayout.id)}
                  style={{ ...toolButton, padding: "2px 3px" }}
                  title="Delete this arrangement. The controls and the primary arrangement are untouched."
                >
                  <X size={11} />
                </button>
              )}
            </div>

            {/* Screen preset (the turned size for a portrait arrangement lives
                in the tooltip — no separate readout). */}
            <select
              value={screenPresetIndex}
              onChange={(e) => setScreenPresetIndex(Number(e.target.value))}
              title={`Canvas: ${presetSize.width}x${presetSize.height}`}
              style={{
                padding: "2px 4px",
                fontSize: "var(--font-size-sm)",
                background: "var(--bg-base)",
                border: "1px solid var(--border-color)",
                borderRadius: 3,
                color: "var(--text-secondary)",
                flexShrink: 0,
              }}
            >
              {SCREEN_PRESETS.map((p, i) => (
                <option key={i} value={i}>
                  {p.label}
                </option>
              ))}
            </select>

            {/* Grid toggle + snap summary. The numbers moved into a popover —
                they are set once per page, not per drag. */}
            <div style={{ display: "flex", alignItems: "center", gap: 2, flexShrink: 0 }}>
              <button
                onClick={toggleGrid}
                style={{
                  display: "flex",
                  alignItems: "center",
                  padding: "2px 6px",
                  borderRadius: 3,
                  background: showGrid ? "var(--accent-dim)" : "transparent",
                  color: showGrid ? "var(--accent)" : "var(--text-muted)",
                  border: "none",
                  cursor: "pointer",
                }}
                title="Toggle grid"
              >
                <Grid3x3 size={14} />
              </button>
              {snap && (
                <div ref={snapMenuRef} style={{ position: "relative" }}>
                  <button
                    onClick={() => setShowSnapMenu((v) => !v)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 3,
                      padding: "2px 6px",
                      borderRadius: 3,
                      fontSize: 11,
                      background: "transparent",
                      border: "1px solid var(--border-color)",
                      color: snap.enabled ? "var(--text-secondary)" : "var(--text-muted)",
                      cursor: "pointer",
                    }}
                    title="Snap while dragging. Hold Alt to ignore it for one move."
                  >
                    Snap {snap.enabled ? `${Math.round(100 / snap.x)}×${Math.round(100 / snap.y)}` : "off"}
                    <ChevronDownIcon size={10} />
                  </button>
                  {showSnapMenu && (
                    <div
                      style={{
                        position: "absolute",
                        top: "100%",
                        left: 0,
                        marginTop: 4,
                        background: "var(--bg-surface)",
                        border: "1px solid var(--border-color)",
                        borderRadius: 6,
                        boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                        zIndex: 100,
                        padding: "8px 10px",
                        display: "flex",
                        flexDirection: "column",
                        gap: 6,
                      }}
                    >
                      <label
                        style={{ fontSize: 11, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 5, whiteSpace: "nowrap" }}
                        title="Snap while dragging. Hold Alt to ignore it for one move."
                      >
                        <input
                          type="checkbox"
                          checked={snap.enabled}
                          onChange={(e) => handleSnapChange({ enabled: e.target.checked })}
                        />
                        Snap while dragging
                      </label>
                      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <label style={{ fontSize: 10, color: "var(--text-muted)" }}>Cols</label>
                        <NumericInput
                          integer
                          min={1}
                          max={48}
                          value={Math.round(100 / snap.x)}
                          onCommit={(v) => {
                            if (v !== undefined) handleSnapChange({ x: 100 / v });
                          }}
                          style={{
                            width: 42, padding: "1px 3px", fontSize: 11, textAlign: "center",
                            background: "var(--bg-primary)", border: "1px solid var(--border-color)",
                            borderRadius: 3, color: "var(--text-primary)",
                          }}
                          title="Snap increment across. Changing it never moves an element."
                        />
                        <label style={{ fontSize: 10, color: "var(--text-muted)" }}>Rows</label>
                        <NumericInput
                          integer
                          min={1}
                          max={48}
                          value={Math.round(100 / snap.y)}
                          onCommit={(v) => {
                            if (v !== undefined) handleSnapChange({ y: 100 / v });
                          }}
                          style={{
                            width: 42, padding: "1px 3px", fontSize: 11, textAlign: "center",
                            background: "var(--bg-primary)", border: "1px solid var(--border-color)",
                            borderRadius: 3, color: "var(--text-primary)",
                          }}
                          title="Snap increment down. Changing it never moves an element."
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div style={divider} />

            {/* Arrange tools — always present, lit when the selection can use
                them. A toolbar that reshuffles itself every time you click is
                a toolbar you can never learn. */}
            <div style={{ display: "flex", alignItems: "center", gap: 1, flexShrink: 0 }}>
              {(
                [
                  { action: "align-left" as AlignAction, icon: <AlignStartVertical size={13} />, title: "Align left" },
                  { action: "align-center" as AlignAction, icon: <AlignCenterVertical size={13} />, title: "Align center" },
                  { action: "align-right" as AlignAction, icon: <AlignEndVertical size={13} />, title: "Align right" },
                  { action: "align-top" as AlignAction, icon: <AlignStartHorizontal size={13} />, title: "Align top" },
                  { action: "align-middle" as AlignAction, icon: <AlignCenterHorizontal size={13} />, title: "Align middle" },
                  { action: "align-bottom" as AlignAction, icon: <AlignEndHorizontal size={13} />, title: "Align bottom" },
                ] as const
              ).map((item) => {
                const enabled = selectedElementIds.length > 0;
                return (
                  <button
                    key={item.action}
                    onClick={() => handleAlign(item.action)}
                    disabled={!enabled}
                    style={{ ...toolButton, opacity: enabled ? 1 : 0.3, cursor: enabled ? "pointer" : "default" }}
                    title={enabled ? item.title : `${item.title} (select an element first)`}
                    onMouseEnter={(e) => { if (enabled) e.currentTarget.style.background = "var(--bg-hover)"; }}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    {item.icon}
                  </button>
                );
              })}
              <div style={{ width: 1, height: 14, background: "var(--border-color)", margin: "0 2px" }} />
              {/* Match size: needs a second element to match against, and the
                  first one selected is the one being matched TO. */}
              {(
                [
                  {
                    action: "match-width" as MatchSizeAction,
                    icon: <MoveHorizontal size={13} />,
                    title: "Match width to the first element selected",
                  },
                  {
                    action: "match-height" as MatchSizeAction,
                    icon: <MoveVertical size={13} />,
                    title: "Match height to the first element selected",
                  },
                  {
                    action: "match-both" as MatchSizeAction,
                    icon: <Scaling size={13} />,
                    title: "Match width and height to the first element selected",
                  },
                ] as const
              ).map((item) => {
                const enabled = selectedElementIds.length >= 2;
                return (
                  <button
                    key={item.action}
                    onClick={() => handleMatchSize(item.action)}
                    disabled={!enabled}
                    style={{ ...toolButton, opacity: enabled ? 1 : 0.3, cursor: enabled ? "pointer" : "default" }}
                    title={enabled ? item.title : `${item.title} (select two or more)`}
                    onMouseEnter={(e) => { if (enabled) e.currentTarget.style.background = "var(--bg-hover)"; }}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    {item.icon}
                  </button>
                );
              })}
              {/* Distribute needs a middle to move, so it wants three. */}
              {(
                [
                  { axis: "horizontal" as DistributeAxis, icon: <AlignHorizontalDistributeCenter size={13} />, title: "Distribute horizontally (equal gaps between elements)" },
                  { axis: "vertical" as DistributeAxis, icon: <AlignVerticalDistributeCenter size={13} />, title: "Distribute vertically (equal gaps between elements)" },
                ] as const
              ).map((item) => {
                const enabled = selectedElementIds.length >= 3;
                return (
                  <button
                    key={item.axis}
                    onClick={() => handleDistribute(item.axis)}
                    disabled={!enabled}
                    style={{ ...toolButton, opacity: enabled ? 1 : 0.3, cursor: enabled ? "pointer" : "default" }}
                    title={enabled ? item.title : `${item.title} (select three or more)`}
                    onMouseEnter={(e) => { if (enabled) e.currentTarget.style.background = "var(--bg-hover)"; }}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    {item.icon}
                  </button>
                );
              })}
            </div>

            <div style={divider} />

            {/* Undo/Redo */}
            <div style={{ display: "flex", alignItems: "center", gap: 2, flexShrink: 0 }}>
              <button
                onClick={undo}
                disabled={undoStack.length === 0}
                style={{
                  ...toolButton,
                  color: undoStack.length > 0 ? "var(--text-secondary)" : "var(--text-muted)",
                  opacity: undoStack.length > 0 ? 1 : 0.3,
                  cursor: undoStack.length > 0 ? "pointer" : "default",
                }}
                title={
                  undoStack.length > 0
                    ? `Undo ${undoStack[undoStack.length - 1].description} (Ctrl+Z) — ${undoStack.length} step${undoStack.length > 1 ? "s" : ""}`
                    : "Undo (Ctrl+Z)"
                }
              >
                <Undo2 size={14} />
              </button>
              <button
                onClick={redo}
                disabled={redoStack.length === 0}
                style={{
                  ...toolButton,
                  color: redoStack.length > 0 ? "var(--text-secondary)" : "var(--text-muted)",
                  opacity: redoStack.length > 0 ? 1 : 0.3,
                  cursor: redoStack.length > 0 ? "pointer" : "default",
                }}
                title={
                  redoStack.length > 0
                    ? `Redo ${redoStack[redoStack.length - 1].description} (Ctrl+Y) — ${redoStack.length} step${redoStack.length > 1 ? "s" : ""}`
                    : "Redo (Ctrl+Y)"
                }
              >
                <Redo2 size={14} />
              </button>
            </div>
          </>
        )}

        {/* Spacer: the selection count floats here, so its coming and going
            never shifts the blocks on either side. */}
        <div style={{ flex: 1, minWidth: 8, textAlign: "center" }}>
          {!previewMode && selectedElementIds.length > 1 && (
            <span style={{ fontSize: 11, color: "var(--accent)", fontWeight: 500 }}>
              {selectedElementIds.length} selected
            </span>
          )}
        </div>

        {/* Save state + manual Save button */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, flexShrink: 0 }}>
          {error && !conflictDetected ? (
            <span style={{ color: "var(--color-error, #d33)", fontWeight: 500 }} title={error}>
              Save failed
            </span>
          ) : saving ? (
            <span style={{ display: "flex", alignItems: "center", gap: 3, color: "var(--text-secondary)" }}>
              <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
              Saving...
            </span>
          ) : savePending ? (
            <span style={{ color: "var(--text-secondary)", fontStyle: "italic" }} title="Will save shortly">
              Pending...
            </span>
          ) : dirty ? (
            <span style={{ color: "var(--color-warning)", fontWeight: 500 }}>Unsaved</span>
          ) : (
            <span style={{ display: "flex", alignItems: "center", gap: 3, color: "var(--text-secondary)" }}>
              <Check size={12} />
              Saved
            </span>
          )}
          <button
            onClick={() => {
              const store = useProjectStore.getState();
              store.flushSave();
              if (store.dirty && !store.saving) store.save();
            }}
            disabled={!dirty && !error}
            title="Save now (Ctrl+S)"
            style={{
              padding: "2px 8px",
              fontSize: 11,
              fontWeight: 600,
              borderRadius: 3,
              border: "1px solid var(--border-color)",
              background: dirty || error ? "var(--accent-bg)" : "var(--bg-hover)",
              color: dirty || error ? "#fff" : "var(--text-muted)",
              cursor: dirty || error ? "pointer" : "default",
              opacity: dirty || error ? 1 : 0.5,
            }}
          >
            Save
          </button>
        </div>

        {/* Validate project */}
        {!previewMode && onValidate && (
          <button
            onClick={onValidate}
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "3px 10px", borderRadius: "var(--border-radius)",
              background: "transparent", border: "1px solid var(--border-color)",
              color: "var(--text-secondary)", fontSize: "var(--font-size-sm)",
              cursor: "pointer", flexShrink: 0,
            }}
            title="Validate project for broken references"
            onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          >
            <Check size={12} /> Validate
          </button>
        )}

        {/* Zoom */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
          <button
            onClick={() => setZoom(zoom - 0.1)}
            disabled={zoom <= 0.25}
            style={{
              display: "flex",
              padding: 2,
              color: zoom <= 0.25 ? "var(--border-color)" : "var(--text-muted)",
              cursor: zoom <= 0.25 ? "default" : "pointer",
            }}
          >
            <ZoomOut size={14} />
          </button>
          <button
            onClick={() => setZoom(1)}
            title="Reset to 100%"
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              minWidth: 36,
              textAlign: "center",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              padding: "1px 0",
            }}
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            onClick={() => setZoom(zoom + 0.1)}
            disabled={zoom >= 2}
            style={{
              display: "flex",
              padding: 2,
              color: zoom >= 2 ? "var(--border-color)" : "var(--text-muted)",
              cursor: zoom >= 2 ? "default" : "pointer",
            }}
          >
            <ZoomIn size={14} />
          </button>
        </div>

        {/* Preview toggle */}
        <button
          onClick={() => setPreviewMode(!previewMode)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "3px 10px",
            borderRadius: "var(--border-radius)",
            background: previewMode ? "var(--color-success)" : "var(--accent-bg)",
            color: "#fff",
            fontSize: "var(--font-size-sm)",
            fontWeight: 500,
            flexShrink: 0,
          }}
          title={previewMode ? "Exit preview" : "Preview panel"}
        >
          {previewMode ? (
            <>
              <Square size={12} /> Stop
            </>
          ) : (
            <>
              <Play size={12} /> Preview
            </>
          )}
        </button>
      </div>

      {/* Tab context menu — every per-page action that used to crowd the tab. */}
      {tabMenu && menuPage && (
        <div
          ref={tabMenuRef}
          style={{
            position: "fixed",
            left: tabMenu.x,
            top: tabMenu.y,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-color)",
            borderRadius: 6,
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            zIndex: LAYER.popover,
            minWidth: 170,
            overflow: "hidden",
            padding: "4px 0",
          }}
        >
          <button
            style={menuItemStyle}
            {...menuHover}
            onClick={() => {
              startRename(menuPage.id, menuPage.name);
              setTabMenu(null);
            }}
          >
            <Pencil size={12} /> Rename
          </button>
          <button
            style={menuItemStyle}
            {...menuHover}
            onClick={() => {
              handleDuplicatePage(menuPage.id);
              setTabMenu(null);
            }}
          >
            <Copy size={12} /> Duplicate
          </button>
          {pages[0]?.id !== menuPage.id && isRegularPage(menuPage) && (
            <button
              style={menuItemStyle}
              {...menuHover}
              onClick={() => {
                applyPageMutation((p) => {
                  const idx = p.findIndex((pg) => pg.id === menuPage.id);
                  if (idx <= 0) return p;
                  const result = [...p];
                  const [moved] = result.splice(idx, 1);
                  result.unshift(moved);
                  return result;
                }, "Set as home page");
                setTabMenu(null);
              }}
            >
              <Home size={12} /> Set as home page
            </button>
          )}
          <button
            style={menuItemStyle}
            {...menuHover}
            onClick={() => {
              applyPageMutation((p) => reorderPage(p, menuPage.id, "left"), "Move page left");
              setTabMenu(null);
            }}
          >
            <ChevronLeft size={12} /> Move left
          </button>
          <button
            style={menuItemStyle}
            {...menuHover}
            onClick={() => {
              applyPageMutation((p) => reorderPage(p, menuPage.id, "right"), "Move page right");
              setTabMenu(null);
            }}
          >
            <ChevronRight size={12} /> Move right
          </button>
          {pageGroups.length > 0 && (
            <>
              <div style={{ borderTop: "1px solid var(--border-color)", margin: "4px 0" }} />
              <div style={{ padding: "2px 12px", fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                Group
              </div>
              <button
                style={menuItemStyle}
                {...menuHover}
                onClick={() => {
                  handleAssignPageToGroup(menuPage.id, null);
                  setTabMenu(null);
                }}
              >
                {menuPageGroup === null ? <Check size={12} /> : <span style={{ width: 12 }} />}
                No group
              </button>
              {pageGroups.map((g) => (
                <button
                  key={g.name}
                  style={menuItemStyle}
                  {...menuHover}
                  onClick={() => {
                    handleAssignPageToGroup(menuPage.id, g.name);
                    setTabMenu(null);
                  }}
                >
                  {menuPageGroup === g.name ? <Check size={12} /> : <span style={{ width: 12 }} />}
                  {g.name}
                </button>
              ))}
            </>
          )}
          {pages.length > 1 && (
            <>
              <div style={{ borderTop: "1px solid var(--border-color)", margin: "4px 0" }} />
              <button
                style={{ ...menuItemStyle, color: "var(--color-error)" }}
                {...menuHover}
                onClick={() => {
                  handleDeletePage(menuPage.id);
                  setTabMenu(null);
                }}
              >
                <X size={12} /> Delete
              </button>
            </>
          )}
        </div>
      )}

      {pendingDeleteLayoutId && currentPage && (
        <ConfirmDialog
          title="Delete Layout"
          message={`Delete the ${
            layoutById(currentPage, pendingDeleteLayoutId)?.orientation ?? ""
          } arrangement of "${currentPage.name}"? The controls stay, and the primary arrangement is untouched. Only the positions you set here are lost.`}
          confirmLabel="Delete"
          destructive
          onConfirm={confirmDeleteLayout}
          onCancel={() => setPendingDeleteLayoutId(null)}
        />
      )}

      {pendingDeletePageId && (
        <ConfirmDialog
          title="Delete Page"
          message={`Delete page "${pages.find(p => p.id === pendingDeletePageId)?.name || pendingDeletePageId}"?`}
          confirmLabel="Delete"
          destructive
          onConfirm={confirmDeletePage}
          onCancel={() => setPendingDeletePageId(null)}
        />
      )}

      {pendingDeleteGroup && (
        <ConfirmDialog
          title="Delete Group"
          message={`Delete group "${pendingDeleteGroup}"? Pages will become ungrouped.`}
          confirmLabel="Delete"
          destructive
          onConfirm={() => {
            handleDeleteGroup(pendingDeleteGroup);
            setPendingDeleteGroup(null);
          }}
          onCancel={() => setPendingDeleteGroup(null)}
        />
      )}

      {showNewGroupPrompt && (
        <PromptDialog
          title="New Group"
          placeholder="Group name"
          submitLabel="Create"
          onSubmit={(name) => {
            handleAddGroup(name);
            setShowNewGroupPrompt(false);
          }}
          onCancel={() => setShowNewGroupPrompt(false)}
        />
      )}
    </div>
  );
}
