import { useRef, useCallback, useMemo, useEffect, useState } from "react";
import { useDroppable } from "@dnd-kit/core";
import type { UIPage, UIElement, MasterElement, GridArea } from "../../api/types";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import { CanvasElement } from "./CanvasElement";
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
  themeVariables?: Record<string, unknown>;
}

export function Canvas({
  page,
  previewMode,
  showGrid,
  zoom,
  screenWidth,
  masterElements,
  screenHeight,
  themeVariables,
}: CanvasProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const [iframeReady, setIframeReady] = useState(false);
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
  const lockedElementIds = useUIBuilderStore((s) => s.lockedElementIds);
  const hiddenElementIds = useUIBuilderStore((s) => s.hiddenElementIds);

  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);

  // Ref-based read of current project/page so the onLoad handler closure stays stable.
  const projectRef = useRef(project);
  const pageIdRef = useRef(page.id);
  useEffect(() => { projectRef.current = project; }, [project]);
  useEffect(() => { pageIdRef.current = page.id; }, [page.id]);

  // Seed the iframe when it finishes loading. Panel.js has a parallel fetch fallback
  // for the initial render, so missing this message only costs a minor render.
  // The iframe always receives the in-memory project via postMessage — in edit
  // mode this is the only source; in preview mode it takes priority over any
  // WS ui.definition from the server so unsaved edits stay visible.
  const handleIframeLoad = useCallback(() => {
    const iframe = iframeRef.current;
    const p = projectRef.current;
    if (!iframe?.contentWindow || !p) return;
    console.log("[canvas] iframe loaded — posting editor-init");
    iframe.contentWindow.postMessage(
      { type: "openavc:editor-init", project: p, pageId: pageIdRef.current, showGrid },
      "*",
    );
    setIframeReady(true);
  }, [showGrid]);

  // Push project → iframe on every edit (both edit and preview modes).
  useEffect(() => {
    if (!project) return;
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow) return;
    const timer = setTimeout(() => {
      iframe.contentWindow?.postMessage(
        { type: "openavc:editor-project", project, pageId: page.id, showGrid },
        "*",
      );
    }, 50);
    return () => clearTimeout(timer);
  }, [project, page.id, showGrid]);

  // iframeReady is informational for now — kept to allow future gating if needed.
  void iframeReady;

  // Outer padding must match the iframe's #panel-root padding (var(--panel-grid-gap), default 8)
  // so the overlay grid aligns with the iframe's panel-page grid cell-for-cell.
  const outerGap = useMemo(() => {
    const v = themeVariables?.grid_gap;
    return typeof v === "number" ? v : 8;
  }, [themeVariables]);

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

  const handleBackgroundContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      if (previewMode) return;
      selectElement(null);
      selectMasterElement(null);
    },
    [previewMode, selectElement, selectMasterElement],
  );

  const handleCommitResize = useCallback(
    (elementId: string, gridArea: GridArea) => {
      if (!project) return;
      pushUndo({ pages: project.ui.pages }, "Resize element");
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
      overlayRef.current = el;
      setNodeRef(el);
    },
    [setNodeRef],
  );

  // Overlay/sidebar pages use their configured dimensions.
  const pageType = page.page_type || "page";
  const isOverlay = pageType === "overlay" || pageType === "sidebar";
  const overlayWidth = isOverlay ? (page.overlay?.width ?? (pageType === "sidebar" ? 320 : 400)) : screenWidth;
  const overlayHeight = isOverlay
    ? (pageType === "sidebar" ? screenHeight : (page.overlay?.height ?? 300))
    : screenHeight;

  const iframeSrc = previewMode
    ? `/panel?page=${encodeURIComponent(page.id)}`
    : `/panel?page=${encodeURIComponent(page.id)}&edit=1`;

  // dnd-kit reads the bounding rect of the element carrying data-canvas-grid for drop math.
  // In edit mode we attach it to the overlay grid; in preview mode there's no drop target.
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
          position: "relative",
          borderRadius: isOverlay ? "12px" : "8px",
          boxShadow: isOverlay
            ? "0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.1)"
            : "0 4px 24px rgba(0,0,0,0.5)",
        }}
      >
        {/* Iframe renders the real panel. Reload per page.id so panel.js rebuilds cleanly. */}
        <iframe
          key={`${page.id}-${previewMode ? "p" : "e"}`}
          ref={iframeRef}
          src={iframeSrc}
          onLoad={handleIframeLoad}
          title={`Panel page ${page.id}`}
          tabIndex={previewMode ? 0 : -1}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            border: "none",
            borderRadius: "inherit",
            background: "transparent",
            pointerEvents: previewMode ? "auto" : "none",
            display: "block",
          }}
        />

        {/* Overlay — only rendered in edit mode */}
        {!previewMode && (
          <div
            ref={combinedRef}
            data-canvas-grid=""
            onClick={handleCanvasClick}
            onContextMenu={handleBackgroundContextMenu}
            style={{
              position: "absolute",
              inset: 0,
              padding: `${outerGap}px`,
              pointerEvents: "auto",
              overflow: "visible",
              borderRadius: "inherit",
            }}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: `repeat(${page.grid.columns}, 1fr)`,
                gridTemplateRows: `repeat(${page.grid.rows}, 1fr)`,
                gap: `${page.grid_gap ?? outerGap}px`,
                width: "100%",
                height: "100%",
                position: "relative",
              }}
            >
              {/* Grid lines are rendered INSIDE the iframe's .panel-page now
                  (see panel.js renderCurrentPage). That keeps them behind
                  element backgrounds so dropped controls aren't "see-through". */}
              {showGrid && (
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

              {/* Master element hit-boxes (selection + badge, iframe renders the pixels) */}
              {(masterElements || [])
                .filter((m) => m.pages === "*" || (Array.isArray(m.pages) && m.pages.includes(page.id)))
                .map((el) => {
                  const isMasterSelected = selectedMasterElementId === el.id;
                  return (
                    <div
                      key={`master-${el.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        selectMasterElement(el.id);
                      }}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setContextMenu({ x: e.clientX, y: e.clientY, elementId: el.id, isMaster: true });
                      }}
                      style={{
                        gridColumn: `${el.grid_area.col} / span ${el.grid_area.col_span}`,
                        gridRow: `${el.grid_area.row} / span ${el.grid_area.row_span}`,
                        position: "relative",
                        cursor: "pointer",
                        outline: isMasterSelected ? "2px solid #9C27B0" : "none",
                        outlineOffset: 1,
                        borderRadius: 4,
                        zIndex: 0,
                      }}
                      title={`Master element: ${el.id}`}
                    >
                      <div
                        style={{
                          position: "absolute",
                          top: 2,
                          left: 4,
                          fontSize: 9,
                          padding: "1px 5px",
                          borderRadius: 3,
                          background: "rgba(156,39,176,0.85)",
                          color: "#fff",
                          pointerEvents: "none",
                          zIndex: 1,
                          fontWeight: 600,
                          letterSpacing: "0.02em",
                        }}
                      >
                        Master
                      </div>
                    </div>
                  );
                })}

              {/* Element hit-boxes (selection + drag + resize, iframe renders the pixels) */}
              {page.elements.map((el) => (
                <CanvasElement
                  key={el.id}
                  element={el}
                  pageId={page.id}
                  selected={selectedElementIds.includes(el.id)}
                  multiSelected={selectedElementIds.length > 1 && selectedElementIds.includes(el.id)}
                  previewMode={false}
                  columns={page.grid.columns}
                  rows={page.grid.rows}
                  hasOverlap={overlappingIds.has(el.id)}
                  locked={lockedElementIds.has(el.id)}
                  hidden={hiddenElementIds.has(el.id)}
                  onSelect={(id, shiftKey) => (shiftKey ? toggleSelectElement(id) : selectElement(id))}
                  onCommitResize={handleCommitResize}
                  onContextMenu={handleContextMenu}
                />
              ))}

              {/* Snap guides (alignment lines for selected element) */}
              {selectedElementId && page.elements.length > 1 && (
                <SnapGuides
                  elements={page.elements}
                  selectedId={selectedElementId}
                  columns={page.grid.columns}
                  rows={page.grid.rows}
                />
              )}

              {/* Empty state */}
              {page.elements.length === 0 && (
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
                  <a
                    href="https://docs.openavc.com/ui-builder"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "var(--accent)", fontSize: "var(--font-size-sm)", pointerEvents: "auto" }}
                  >
                    Learn about the UI Builder
                  </a>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
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
