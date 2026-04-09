import { useRef, useCallback, useMemo } from "react";
import { useDroppable } from "@dnd-kit/core";
import type { UIPage, UIElement, MasterElement, GridArea } from "../../api/types";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { CanvasElement } from "./CanvasElement";
import { RenderElement } from "./ElementRenderers/renderElement";
import { moveElementInPage } from "./uiBuilderHelpers";

/** Check if two grid areas overlap. */
function areasOverlap(a: GridArea, b: GridArea): boolean {
  const aRight = a.col + a.col_span;
  const aBottom = a.row + a.row_span;
  const bRight = b.col + b.col_span;
  const bBottom = b.row + b.row_span;
  return a.col < bRight && aRight > b.col && a.row < bBottom && aBottom > b.row;
}

/** Return set of element IDs that overlap with at least one other element.
 *  Group elements are excluded — they are containers and overlap with their children by design. */
function findOverlappingIds(elements: UIElement[]): Set<string> {
  const ids = new Set<string>();
  for (let i = 0; i < elements.length; i++) {
    if (elements[i].type === "group") continue;
    for (let j = i + 1; j < elements.length; j++) {
      if (elements[j].type === "group") continue;
      if (areasOverlap(elements[i].grid_area, elements[j].grid_area)) {
        ids.add(elements[i].id);
        ids.add(elements[j].id);
      }
    }
  }
  return ids;
}

interface CanvasProps {
  page: UIPage;
  previewMode: boolean;
  showGrid: boolean;
  zoom: number;
  screenWidth: number;
  screenHeight: number;
  masterElements?: MasterElement[];
  themeElementDefaults?: Record<string, Record<string, unknown>>;
}

export function Canvas({
  page,
  previewMode,
  showGrid,
  zoom,
  screenWidth,
  masterElements,
  screenHeight,
  themeElementDefaults,
}: CanvasProps) {
  const gridRef = useRef<HTMLDivElement>(null);
  const { setNodeRef } = useDroppable({ id: "canvas-drop" });

  const selectedElementId = useUIBuilderStore((s) => s.selectedElementId);
  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);
  const selectElement = useUIBuilderStore((s) => s.selectElement);
  const toggleSelectElement = useUIBuilderStore((s) => s.toggleSelectElement);
  const selectMasterElement = useUIBuilderStore((s) => s.selectMasterElement);
  const pushUndo = useUIBuilderStore((s) => s.pushUndo);
  const touchMutation = useUIBuilderStore((s) => s.touchMutation);
  const setContextMenu = useUIBuilderStore((s) => s.setContextMenu);

  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);

  const liveState = useConnectionStore((s) => s.liveState);

  // Detect overlapping elements (only in edit mode)
  const overlappingIds = useMemo(
    () => (previewMode ? new Set<string>() : findOverlappingIds(page.elements)),
    [page.elements, previewMode],
  );

  const handleCanvasClick = useCallback(() => {
    if (!previewMode) {
      selectElement(null);
      selectMasterElement(null);
    }
  }, [previewMode, selectElement, selectMasterElement]);

  const handleCommitResize = useCallback(
    (elementId: string, gridArea: GridArea) => {
      if (!project) return;
      pushUndo(project.ui.pages);
      const newPages = moveElementInPage(
        project.ui.pages,
        page.id,
        elementId,
        gridArea,
      );
      update({ ui: { ...project.ui, pages: newPages } });
      touchMutation();
    },
    [project, page.id, pushUndo, update, touchMutation],
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent, elementId: string) => {
      setContextMenu({ x: e.clientX, y: e.clientY, elementId });
    },
    [setContextMenu],
  );

  const combinedRef = useCallback(
    (el: HTMLDivElement | null) => {
      gridRef.current = el;
      setNodeRef(el);
    },
    [setNodeRef],
  );

  // Overlay/sidebar pages use their configured dimensions
  const pageType = page.page_type || "page";
  const isOverlay = pageType === "overlay" || pageType === "sidebar";
  const overlayWidth = isOverlay ? (page.overlay?.width ?? (pageType === "sidebar" ? 320 : 400)) : screenWidth;
  const overlayHeight = isOverlay
    ? (pageType === "sidebar" ? screenHeight : (page.overlay?.height ?? 300))
    : screenHeight;

  return (
    <div
      style={{
        flex: 1,
        overflow: "auto",
        background: "var(--bg-base)",
        padding: "var(--space-lg)",
      }}
    >
      <div
        style={{
          width: overlayWidth,
          height: overlayHeight,
          transform: `scale(${zoom})`,
          transformOrigin: "top center",
          flexShrink: 0,
          margin: "auto",
        }}
      >
        <div
          ref={combinedRef}
          data-canvas-grid=""
          onClick={handleCanvasClick}
          onContextMenu={(e) => {
            if (!previewMode) e.preventDefault();
          }}
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${page.grid.columns}, 1fr)`,
            gridTemplateRows: `repeat(${page.grid.rows}, 1fr)`,
            gap: `${page.grid_gap ?? 8}px`,
            width: "100%",
            height: "100%",
            background: page.background?.color || "#1a1a2e",
            borderRadius: isOverlay ? "12px" : "8px",
            padding: "8px",
            position: "relative",
            boxShadow: isOverlay
              ? "0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.1)"
              : "0 4px 24px rgba(0,0,0,0.5)",
          }}
        >
          {/* Page background layers */}
          {page.background?.image && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                zIndex: 0,
                pointerEvents: "none",
                backgroundImage: `url("/api/projects/default/assets/${(page.background.image || "").replace("assets://", "")}")`,
                backgroundSize: page.background.image_size || "cover",
                backgroundPosition: page.background.image_position || "center",
                backgroundRepeat: "no-repeat",
                opacity: page.background.image_opacity ?? 1,
                borderRadius: "inherit",
              }}
            />
          )}
          {page.background?.gradient?.from && page.background?.gradient?.to && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                zIndex: 1,
                pointerEvents: "none",
                background: `linear-gradient(${page.background.gradient.angle ?? 180}deg, ${page.background.gradient.from}, ${page.background.gradient.to})`,
                borderRadius: "inherit",
              }}
            />
          )}

          {/* Grid overlay */}
          {showGrid && !previewMode && (
            <GridOverlay columns={page.grid.columns} rows={page.grid.rows} gap={page.grid_gap ?? 8} />
          )}
          {/* Grid dimension label */}
          {showGrid && !previewMode && (
            <div
              style={{
                position: "absolute",
                top: 2,
                left: 10,
                fontSize: 10,
                color: "rgba(255,255,255,0.3)",
                pointerEvents: "none",
                zIndex: 2,
                fontFamily: "monospace",
              }}
            >
              {page.grid.columns} &times; {page.grid.rows}
            </div>
          )}

          {/* Master elements (persistent, rendered below page elements) */}
          {(masterElements || [])
            .filter((m) => m.pages === "*" || (Array.isArray(m.pages) && m.pages.includes(page.id)))
            .map((el) => {
              const isMasterSelected = selectedMasterElementId === el.id;
              return (
                <div
                  key={`master-${el.id}`}
                  onClick={(e) => {
                    if (!previewMode) {
                      e.stopPropagation();
                      selectMasterElement(el.id);
                    }
                  }}
                  onContextMenu={(e) => {
                    if (!previewMode) {
                      e.preventDefault();
                      e.stopPropagation();
                      setContextMenu({ x: e.clientX, y: e.clientY, elementId: el.id, isMaster: true });
                    }
                  }}
                  style={{
                    gridColumn: `${el.grid_area.col} / span ${el.grid_area.col_span}`,
                    gridRow: `${el.grid_area.row} / span ${el.grid_area.row_span}`,
                    opacity: previewMode ? 1 : 0.6,
                    pointerEvents: "auto",
                    zIndex: 0,
                    position: "relative",
                    cursor: previewMode ? "default" : "pointer",
                    outline: isMasterSelected ? "2px solid #9C27B0" : "none",
                    outlineOffset: 1,
                    borderRadius: 4,
                  }}
                  title={previewMode ? undefined : `Global element: ${el.id}`}
                >
                  <RenderElement element={el} previewMode={previewMode} liveState={liveState} themeDefaults={themeElementDefaults} />
                  {!previewMode && (
                    <div
                      style={{
                        position: "absolute",
                        top: 2,
                        left: 4,
                        fontSize: 9,
                        padding: "1px 4px",
                        borderRadius: 3,
                        background: "rgba(156,39,176,0.7)",
                        color: "#fff",
                        pointerEvents: "none",
                        zIndex: 1,
                      }}
                    >
                      Global
                    </div>
                  )}
                </div>
              );
            })}

          {/* Elements */}
          {page.elements.map((el) => (
            <CanvasElement
              key={el.id}
              element={el}
              pageId={page.id}
              selected={selectedElementIds.includes(el.id)}
              multiSelected={selectedElementIds.length > 1 && selectedElementIds.includes(el.id)}
              previewMode={previewMode}
              columns={page.grid.columns}
              rows={page.grid.rows}
              liveState={liveState}
              hasOverlap={overlappingIds.has(el.id)}
              onSelect={(id, shiftKey) => shiftKey ? toggleSelectElement(id) : selectElement(id)}
              onCommitResize={handleCommitResize}
              onContextMenu={handleContextMenu}
              themeElementDefaults={themeElementDefaults}
            />
          ))}

          {/* Snap guides (alignment lines for selected element) */}
          {!previewMode && selectedElementId && page.elements.length > 1 && (
            <SnapGuides
              elements={page.elements}
              selectedId={selectedElementId}
              columns={page.grid.columns}
              rows={page.grid.rows}
            />
          )}

          {/* Empty state */}
          {page.elements.length === 0 && !previewMode && (
            <div
              style={{
                gridColumn: "1 / -1",
                gridRow: "1 / -1",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-lg)",
                pointerEvents: "none",
                gap: "var(--space-sm)",
              }}
            >
              <span>Drag elements from the palette to get started</span>
              <a href="https://docs.openavc.com/ui-builder" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", fontSize: "var(--font-size-sm)", pointerEvents: "auto" }}>
                Learn about the UI Builder
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function GridOverlay({
  columns,
  rows,
  gap = 8,
}: {
  columns: number;
  rows: number;
  gap?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 8,
        display: "grid",
        gridTemplateColumns: `repeat(${columns}, 1fr)`,
        gridTemplateRows: `repeat(${rows}, 1fr)`,
        gap: `${gap}px`,
        pointerEvents: "none",
        zIndex: 0,
      }}
    >
      {Array.from({ length: columns * rows }).map((_, i) => (
        <div
          key={i}
          style={{
            border: "1px dashed rgba(255,255,255,0.18)",
            borderRadius: "4px",
          }}
        />
      ))}
    </div>
  );
}

function SnapGuides({
  elements,
  selectedId,
  columns,
  rows,
}: {
  elements: UIElement[];
  selectedId: string;
  columns: number;
  rows: number;
}) {
  const selected = elements.find((e) => e.id === selectedId);
  if (!selected) return null;

  const a = selected.grid_area;
  const selLeft = a.col;
  const selRight = a.col + a.col_span;
  const selTop = a.row;
  const selBottom = a.row + a.row_span;

  const vLines = new Set<number>();
  const hLines = new Set<number>();

  for (const el of elements) {
    if (el.id === selectedId) continue;
    const b = el.grid_area;
    const elLeft = b.col;
    const elRight = b.col + b.col_span;
    const elTop = b.row;
    const elBottom = b.row + b.row_span;

    // Vertical alignment (column edges match)
    if (selLeft === elLeft || selLeft === elRight) vLines.add(selLeft);
    if (selRight === elLeft || selRight === elRight) vLines.add(selRight);
    // Horizontal alignment (row edges match)
    if (selTop === elTop || selTop === elBottom) hLines.add(selTop);
    if (selBottom === elTop || selBottom === elBottom) hLines.add(selBottom);
  }

  if (vLines.size === 0 && hLines.size === 0) return null;

  // Each grid column occupies (100% / columns) width, offset by padding (8px) and gap
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        zIndex: 100,
      }}
    >
      {[...vLines].map((col) => (
        <div
          key={`v-${col}`}
          style={{
            position: "absolute",
            left: `calc(${((col - 1) / columns) * 100}% + 4px)`,
            top: 0,
            bottom: 0,
            width: 1,
            background: "rgba(33, 150, 243, 0.6)",
            zIndex: 100,
          }}
        />
      ))}
      {[...hLines].map((row) => (
        <div
          key={`h-${row}`}
          style={{
            position: "absolute",
            top: `calc(${((row - 1) / rows) * 100}% + 4px)`,
            left: 0,
            right: 0,
            height: 1,
            background: "rgba(33, 150, 243, 0.6)",
            zIndex: 100,
          }}
        />
      ))}
    </div>
  );
}
