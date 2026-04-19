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
  Home,
} from "lucide-react";
import type { UIPage, PageGroup } from "../../api/types";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import {
  SCREEN_PRESETS,
  addPage,
  removePage,
  renamePage,
  reorderPage,
  duplicatePage,
  alignElement,
  addPageGroup,
  removePageGroup,
  renamePageGroup,
  assignPageToGroup,
  type AlignAction,
} from "./uiBuilderHelpers";

interface CanvasToolbarProps {
  pages: UIPage[];
  selectedPageId: string | null;
}

export function CanvasToolbar({ pages, selectedPageId }: CanvasToolbarProps) {
  const selectPage = useUIBuilderStore((s) => s.selectPage);
  const previewMode = useUIBuilderStore((s) => s.previewMode);
  const setPreviewMode = useUIBuilderStore((s) => s.setPreviewMode);
  const showGrid = useUIBuilderStore((s) => s.showGrid);
  const toggleGrid = useUIBuilderStore((s) => s.toggleGrid);
  const zoom = useUIBuilderStore((s) => s.zoom);
  const setZoom = useUIBuilderStore((s) => s.setZoom);
  const screenPresetIndex = useUIBuilderStore((s) => s.screenPresetIndex);
  const setScreenPresetIndex = useUIBuilderStore((s) => s.setScreenPresetIndex);
  const selectedElementId = useUIBuilderStore((s) => s.selectedElementId);
  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  const pushUndo = useUIBuilderStore((s) => s.pushUndo);
  const touchMutation = useUIBuilderStore((s) => s.touchMutation);

  const project = useProjectStore((s) => s.project);
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
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [renamingGroupName, setRenamingGroupName] = useState<string | null>(null);
  const [groupRenameValue, setGroupRenameValue] = useState("");
  const addMenuRef = useRef<HTMLDivElement>(null);

  // Close add menu on click outside
  useEffect(() => {
    if (!showAddMenu) return;
    const handler = (e: MouseEvent) => {
      if (addMenuRef.current && !addMenuRef.current.contains(e.target as Node)) {
        setShowAddMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showAddMenu]);

  const pageGroups: PageGroup[] = project?.ui?.page_groups || [];

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
      if (!project) return;
      pushUndo({ pages: project.ui.pages }, description);
      const newPages = mutate(project.ui.pages);
      update({ ui: { ...project.ui, pages: newPages } });
      touchMutation();
    },
    [project, pushUndo, update, touchMutation],
  );

  const applyGroupMutation = useCallback(
    (mutate: (groups: PageGroup[]) => PageGroup[], description: string) => {
      if (!project) return;
      pushUndo({ page_groups: project.ui.page_groups || [] }, description);
      const newGroups = mutate(project.ui.page_groups || []);
      update({ ui: { ...project.ui, page_groups: newGroups } });
      touchMutation();
    },
    [project, pushUndo, update, touchMutation],
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
    if (!project) return;
    const newPages = addPage(project.ui.pages, pageType);
    const newPageId = newPages[newPages.length - 1].id;
    applyPageMutation(() => newPages, `Add ${pageType}`);
    selectPage(newPageId);
    setShowAddMenu(false);
  };

  const handleDuplicatePage = (pageId: string) => {
    if (!project) return;
    const newPages = duplicatePage(project.ui.pages, pageId);
    const newPageId = newPages[newPages.length - 1]?.id;
    applyPageMutation(() => newPages, "Duplicate page");
    if (newPageId) selectPage(newPageId);
  };

  const handleDeletePage = (pageId: string) => {
    if (pages.length <= 1) return;
    if (!project) return;
    const pageName = pages.find(p => p.id === pageId)?.name || pageId;
    if (!window.confirm(`Delete page "${pageName}"?`)) return;
    // Find the page to switch to BEFORE mutating, so we can select it after
    const nextPageId = pages.find((p) => p.id !== pageId)?.id;
    const idleClobbered = project.ui.settings?.idle_page === pageId;
    // Snapshot pages always; include settings only when idle_page collateral fires
    pushUndo(
      idleClobbered
        ? { pages: project.ui.pages, settings: project.ui.settings }
        : { pages: project.ui.pages },
      `Delete page "${pageName}"`,
    );
    const newPages = removePage(project.ui.pages, pageId);
    const settings = idleClobbered
      ? { ...project.ui.settings, idle_page: newPages[0]?.id || "" }
      : project.ui.settings;
    update({ ui: { ...project.ui, pages: newPages, settings } });
    touchMutation();
    if (selectedPageId === pageId && nextPageId) {
      selectPage(nextPageId);
    }
  };

  const handleDoubleClick = (pageId: string, name: string) => {
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
    if (!selectedElementId || !selectedPageId || !project) return;
    const page = project.ui.pages.find((p) => p.id === selectedPageId);
    if (!page) return;
    applyPageMutation(
      (p) => alignElement(p, selectedPageId!, selectedElementId!, action, page.grid),
      `Align ${action}`,
    );
  };

  const handleDistribute = (direction: "horizontal" | "vertical") => {
    if (selectedElementIds.length < 3 || !selectedPageId || !project) return;
    const page = project.ui.pages.find((p) => p.id === selectedPageId);
    if (!page) return;
    const elements = selectedElementIds
      .map((eid) => page.elements.find((el) => el.id === eid))
      .filter((el): el is typeof page.elements[0] => !!el);
    if (elements.length < 3) return;

    if (direction === "horizontal") {
      const sorted = [...elements].sort((a, b) => a.grid_area.col - b.grid_area.col);
      const first = sorted[0].grid_area.col;
      const last = sorted[sorted.length - 1].grid_area.col;
      const step = (last - first) / (sorted.length - 1);
      applyPageMutation((p) => {
        let result = p;
        sorted.forEach((el, i) => {
          if (i === 0 || i === sorted.length - 1) return;
          const newCol = Math.round(first + step * i);
          result = result.map((pg) =>
            pg.id === selectedPageId
              ? { ...pg, elements: pg.elements.map((e) => e.id === el.id ? { ...e, grid_area: { ...e.grid_area, col: newCol } } : e) }
              : pg
          );
        });
        return result;
      }, "Distribute horizontally");
    } else {
      const sorted = [...elements].sort((a, b) => a.grid_area.row - b.grid_area.row);
      const first = sorted[0].grid_area.row;
      const last = sorted[sorted.length - 1].grid_area.row;
      const step = (last - first) / (sorted.length - 1);
      applyPageMutation((p) => {
        let result = p;
        sorted.forEach((el, i) => {
          if (i === 0 || i === sorted.length - 1) return;
          const newRow = Math.round(first + step * i);
          result = result.map((pg) =>
            pg.id === selectedPageId
              ? { ...pg, elements: pg.elements.map((e) => e.id === el.id ? { ...e, grid_area: { ...e.grid_area, row: newRow } } : e) }
              : pg
          );
        });
        return result;
      }, "Distribute vertically");
    }
  };

  const preset = SCREEN_PRESETS[screenPresetIndex];

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        padding: "var(--space-xs) var(--space-md)",
        borderBottom: "1px solid var(--border-color)",
        background: "var(--bg-surface)",
        flexShrink: 0,
        minHeight: 38,
        flexWrap: "wrap",
      }}
    >
      {/* Page tabs (with optional group headers) */}
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
                    <FolderOpen size={10} style={{ opacity: 0.5 }} />
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
                        if (window.confirm(`Delete group "${group.name}"? Pages will become ungrouped.`)) {
                          handleDeleteGroup(group.name);
                        }
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        padding: 1,
                        opacity: 0.25,
                        color: "var(--text-secondary)",
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                      }}
                      title="Delete group"
                    >
                      <X size={10} />
                    </button>
                  )}
                </div>
              )}
              {/* Group pages (collapsible) */}
              {!isCollapsed && groupPages.map((page) => (
                <div
                  key={page.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 2,
                    padding: "2px 8px",
                    borderRadius: "var(--border-radius)",
                    background:
                      selectedPageId === page.id
                        ? "var(--accent-dim)"
                        : "transparent",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                    whiteSpace: "nowrap",
                    border:
                      selectedPageId === page.id
                        ? "1px solid var(--accent)"
                        : "1px solid transparent",
                  }}
                  onClick={() => selectPage(page.id)}
                  onDoubleClick={() => handleDoubleClick(page.id, page.name)}
                  title={`${page.name}${page.page_type ? ` (${page.page_type})` : ""} — ${page.elements.length} element${page.elements.length !== 1 ? "s" : ""}, ${page.grid.columns}×${page.grid.rows} grid`}
                >
                  {/* Page type icon */}
                  {!page.page_type && (
                    <Square size={10} style={{ opacity: 0.3, flexShrink: 0 }} />
                  )}
                  {page.page_type === "overlay" && (
                    <Layers size={11} style={{ opacity: 0.5, flexShrink: 0 }} />
                  )}
                  {page.page_type === "sidebar" && (
                    <PanelRight size={11} style={{ opacity: 0.5, flexShrink: 0 }} />
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
                  {pages[0]?.id === page.id && !page.page_type && (
                    <span title="Home page (shown first on startup)"><Home size={10} style={{ color: "var(--accent)", opacity: 0.6 }} /></span>
                  )}
                  {pages.length > 1 && !previewMode && (
                    <>
                      {pages[0]?.id !== page.id && !page.page_type && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            applyPageMutation((p) => {
                              const idx = p.findIndex((pg) => pg.id === page.id);
                              if (idx <= 0) return p;
                              const result = [...p];
                              const [moved] = result.splice(idx, 1);
                              result.unshift(moved);
                              return result;
                            }, "Set as home page");
                          }}
                          style={{ display: "flex", padding: 0, opacity: 0.3, color: "var(--text-secondary)", background: "none", border: "none", cursor: "pointer" }}
                          title="Set as home page (move to first position)"
                        >
                          <Home size={11} />
                        </button>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          applyPageMutation((p) => reorderPage(p, page.id, "left"), "Move page left");
                        }}
                        style={{ display: "flex", padding: 0, opacity: 0.3, color: "var(--text-secondary)" }}
                        title="Move page left"
                      >
                        <ChevronLeft size={12} />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          applyPageMutation((p) => reorderPage(p, page.id, "right"), "Move page right");
                        }}
                        style={{ display: "flex", padding: 0, opacity: 0.3, color: "var(--text-secondary)" }}
                        title="Move page right"
                      >
                        <ChevronRight size={12} />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDuplicatePage(page.id);
                        }}
                        style={{ display: "flex", padding: 0, opacity: 0.3, color: "var(--text-secondary)" }}
                        title="Duplicate page"
                      >
                        <Copy size={11} />
                      </button>
                      {pageGroups.length > 0 && (
                        <select
                          value={(() => {
                            for (const g of pageGroups) {
                              if (g.pages.includes(page.id)) return g.name;
                            }
                            return "";
                          })()}
                          onChange={(e) => {
                            e.stopPropagation();
                            handleAssignPageToGroup(page.id, e.target.value || null);
                          }}
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            fontSize: 9,
                            width: 18,
                            opacity: 0.35,
                            padding: 0,
                            background: "transparent",
                            border: "none",
                            color: "var(--text-secondary)",
                            cursor: "pointer",
                          }}
                          title="Assign to group"
                        >
                          <option value="">--</option>
                          {pageGroups.map(g => (
                            <option key={g.name} value={g.name}>{g.name}</option>
                          ))}
                        </select>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeletePage(page.id);
                        }}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          padding: 0,
                          marginLeft: 2,
                          opacity: 0.3,
                          color: "var(--text-secondary)",
                        }}
                        title="Delete page"
                      >
                        <X size={12} />
                      </button>
                    </>
                  )}
                </div>
              ))}
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
              padding: "4px 10px",
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
                left: 0,
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
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    width: "100%",
                    padding: "6px 12px",
                    fontSize: "var(--font-size-sm)",
                    color: "var(--text-primary)",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background =
                      "var(--bg-hover)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background =
                      "transparent")
                  }
                >
                  {item.icon}
                  {item.label}
                </button>
              ))}
              <div style={{ borderTop: "1px solid var(--border-color)", margin: "4px 0" }} />
              <button
                onClick={() => {
                  const name = window.prompt("Group name:");
                  if (name?.trim()) {
                    handleAddGroup(name.trim());
                  }
                  setShowAddMenu(false);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  width: "100%",
                  padding: "6px 12px",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-primary)",
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background =
                    "var(--bg-hover)")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background =
                    "transparent")
                }
              >
                <FolderPlus size={12} />
                New Group
              </button>
            </div>
          )}
        </div>
      )}

      {/* Breadcrumb (12.4) */}
      {(() => {
        const currentPage = pages.find((p) => p.id === selectedPageId);
        if (!currentPage) return null;
        const group = pageGroups.find((g) => g.pages.includes(currentPage.id));
        const typeLabel = currentPage.page_type ? ` (${currentPage.page_type})` : "";
        return (
          <span style={{ fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 180 }}>
            {group ? <><span style={{ opacity: 0.6 }}>{group.name}</span> <ChevronRight size={10} style={{ verticalAlign: "middle", opacity: 0.4 }} /> </> : null}
            <span style={{ color: "var(--text-secondary)" }}>{currentPage.name}</span>
            {typeLabel && <span style={{ opacity: 0.5 }}>{typeLabel}</span>}
          </span>
        );
      })()}

      {/* Divider */}
      <div
        style={{
          width: 1,
          height: 20,
          background: "var(--border-color)",
        }}
      />

      {/* Screen preset */}
      {!previewMode && (
        <select
          value={screenPresetIndex}
          onChange={(e) => setScreenPresetIndex(Number(e.target.value))}
          style={{
            padding: "2px 4px",
            fontSize: "var(--font-size-sm)",
            background: "var(--bg-base)",
            border: "1px solid var(--border-color)",
            borderRadius: 3,
            color: "var(--text-secondary)",
          }}
        >
          {SCREEN_PRESETS.map((p, i) => (
            <option key={i} value={i}>
              {p.label}
            </option>
          ))}
        </select>
      )}

      {/* Grid toggle */}
      {!previewMode && (
        <button
          onClick={toggleGrid}
          style={{
            display: "flex",
            alignItems: "center",
            padding: "2px 6px",
            borderRadius: 3,
            background: showGrid ? "var(--accent-dim)" : "transparent",
            color: showGrid ? "var(--accent)" : "var(--text-muted)",
          }}
          title="Toggle grid"
        >
          <Grid3x3 size={14} />
        </button>
      )}

      {/* Grid configuration quick-access (11.2) */}
      {!previewMode && showGrid && (() => {
        const currentPage = pages.find((p) => p.id === selectedPageId);
        if (!currentPage || !project) return null;
        const handleGridChange = (patch: Record<string, number>) => {
          const updatedPages = project.ui.pages.map((p) =>
            p.id === currentPage.id
              ? {
                  ...p,
                  grid: { ...p.grid, ...("columns" in patch || "rows" in patch ? patch : {}) },
                  ...("grid_gap" in patch ? { grid_gap: patch.grid_gap } : {}),
                }
              : p
          );
          pushUndo({ pages: project.ui.pages }, "Edit grid");
          update({ ui: { ...project.ui, pages: updatedPages } });
          touchMutation();
        };
        return (
          <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <label style={{ fontSize: 10, color: "var(--text-muted)" }}>Cols</label>
            <input
              type="number" min={1} max={24}
              value={currentPage.grid.columns}
              onChange={(e) => handleGridChange({ columns: Math.max(1, parseInt(e.target.value) || 1) })}
              style={{ width: 36, padding: "1px 3px", fontSize: 11, textAlign: "center", background: "var(--bg-primary)", border: "1px solid var(--border-color)", borderRadius: 3, color: "var(--text-primary)" }}
            />
            <label style={{ fontSize: 10, color: "var(--text-muted)" }}>Rows</label>
            <input
              type="number" min={1} max={24}
              value={currentPage.grid.rows}
              onChange={(e) => handleGridChange({ rows: Math.max(1, parseInt(e.target.value) || 1) })}
              style={{ width: 36, padding: "1px 3px", fontSize: 11, textAlign: "center", background: "var(--bg-primary)", border: "1px solid var(--border-color)", borderRadius: 3, color: "var(--text-primary)" }}
            />
            <label style={{ fontSize: 10, color: "var(--text-muted)" }}>Gap</label>
            <input
              type="number" min={0} max={32}
              value={currentPage.grid_gap ?? 8}
              onChange={(e) => handleGridChange({ grid_gap: Math.max(0, parseInt(e.target.value) || 0) })}
              style={{ width: 32, padding: "1px 3px", fontSize: 11, textAlign: "center", background: "var(--bg-primary)", border: "1px solid var(--border-color)", borderRadius: 3, color: "var(--text-primary)" }}
            />
          </div>
        );
      })()}

      {/* Selection count indicator */}
      {!previewMode && selectedElementIds.length > 1 && (
        <span style={{ fontSize: 11, color: "var(--accent)", fontWeight: 500 }}>
          {selectedElementIds.length} selected
        </span>
      )}

      {/* Alignment tools (visible when element selected) */}
      {!previewMode && selectedElementIds.length > 0 && (
        <>
          <div style={{ width: 1, height: 20, background: "var(--border-color)" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 1 }}>
            {(
              [
                { action: "align-left" as AlignAction, icon: <AlignStartVertical size={13} />, title: "Align left" },
                { action: "align-center" as AlignAction, icon: <AlignCenterVertical size={13} />, title: "Align center" },
                { action: "align-right" as AlignAction, icon: <AlignEndVertical size={13} />, title: "Align right" },
                { action: "align-top" as AlignAction, icon: <AlignStartHorizontal size={13} />, title: "Align top" },
                { action: "align-middle" as AlignAction, icon: <AlignCenterHorizontal size={13} />, title: "Align middle" },
                { action: "align-bottom" as AlignAction, icon: <AlignEndHorizontal size={13} />, title: "Align bottom" },
              ] as const
            ).map((item) => (
              <button
                key={item.action}
                onClick={() => handleAlign(item.action)}
                style={{
                  display: "flex",
                  padding: 3,
                  color: "var(--text-muted)",
                  borderRadius: 3,
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                }}
                title={item.title}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                {item.icon}
              </button>
            ))}
            {/* Distribute buttons (11.3, visible with 3+ selected) */}
            {selectedElementIds.length >= 3 && (
              <>
                <div style={{ width: 1, height: 14, background: "var(--border-color)", margin: "0 2px" }} />
                <button
                  onClick={() => handleDistribute("horizontal")}
                  style={{ display: "flex", padding: 3, color: "var(--text-muted)", borderRadius: 3, background: "transparent", border: "none", cursor: "pointer" }}
                  title="Distribute horizontally (even spacing)"
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <AlignHorizontalDistributeCenter size={13} />
                </button>
                <button
                  onClick={() => handleDistribute("vertical")}
                  style={{ display: "flex", padding: 3, color: "var(--text-muted)", borderRadius: 3, background: "transparent", border: "none", cursor: "pointer" }}
                  title="Distribute vertically (even spacing)"
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <AlignVerticalDistributeCenter size={13} />
                </button>
              </>
            )}
          </div>
        </>
      )}

      {/* Undo/Redo */}
      {!previewMode && (
        <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
          <button
            onClick={undo}
            disabled={undoStack.length === 0}
            style={{
              display: "flex",
              padding: 3,
              color: undoStack.length > 0 ? "var(--text-secondary)" : "var(--text-muted)",
              opacity: undoStack.length > 0 ? 1 : 0.3,
              borderRadius: 3,
              background: "transparent",
              border: "none",
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
              display: "flex",
              padding: 3,
              color: redoStack.length > 0 ? "var(--text-secondary)" : "var(--text-muted)",
              opacity: redoStack.length > 0 ? 1 : 0.3,
              borderRadius: 3,
              background: "transparent",
              border: "none",
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
      )}

      {/* Save state + manual Save button */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
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
            background: dirty || error ? "var(--accent)" : "var(--bg-hover)",
            color: dirty || error ? "#fff" : "var(--text-muted)",
            cursor: dirty || error ? "pointer" : "default",
            opacity: dirty || error ? 1 : 0.5,
          }}
        >
          Save
        </button>
      </div>

      {/* Zoom */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <button
          onClick={() => setZoom(zoom - 0.1)}
          style={{
            display: "flex",
            padding: 2,
            color: "var(--text-muted)",
          }}
        >
          <ZoomOut size={14} />
        </button>
        <span
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            minWidth: 36,
            textAlign: "center",
          }}
        >
          {Math.round(zoom * 100)}%
        </span>
        <button
          onClick={() => setZoom(zoom + 0.1)}
          style={{
            display: "flex",
            padding: 2,
            color: "var(--text-muted)",
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
          background: previewMode ? "var(--color-success)" : "var(--accent)",
          color: "#fff",
          fontSize: "var(--font-size-sm)",
          fontWeight: 500,
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

      {/* Screen size info */}
      {preset && (
        <span
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
          }}
        >
          {preset.width}x{preset.height}
        </span>
      )}
    </div>
  );
}
