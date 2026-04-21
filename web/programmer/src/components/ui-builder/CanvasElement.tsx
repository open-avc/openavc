import { useRef, useState, useCallback } from "react";
import { useDraggable } from "@dnd-kit/core";
import type { UIElement, GridArea } from "../../api/types";

interface CanvasElementProps {
  element: UIElement;
  pageId: string;
  selected: boolean;
  multiSelected?: boolean;
  previewMode: boolean;
  columns: number;
  rows: number;
  hasOverlap?: boolean;
  locked?: boolean;
  hidden?: boolean;
  onSelect: (id: string, shiftKey?: boolean) => void;
  onCommitResize: (elementId: string, gridArea: GridArea) => void;
  onContextMenu: (e: React.MouseEvent, elementId: string) => void;
}

const HANDLE_SIZE = 18;

const HANDLE_POSITIONS: Record<
  string,
  React.CSSProperties
> = {
  n: {
    top: -HANDLE_SIZE / 2,
    left: "50%",
    transform: "translateX(-50%)",
    cursor: "ns-resize",
    width: HANDLE_SIZE * 2,
    height: HANDLE_SIZE,
  },
  s: {
    bottom: -HANDLE_SIZE / 2,
    left: "50%",
    transform: "translateX(-50%)",
    cursor: "ns-resize",
    width: HANDLE_SIZE * 2,
    height: HANDLE_SIZE,
  },
  e: {
    right: -HANDLE_SIZE / 2,
    top: "50%",
    transform: "translateY(-50%)",
    cursor: "ew-resize",
    width: HANDLE_SIZE,
    height: HANDLE_SIZE * 2,
  },
  w: {
    left: -HANDLE_SIZE / 2,
    top: "50%",
    transform: "translateY(-50%)",
    cursor: "ew-resize",
    width: HANDLE_SIZE,
    height: HANDLE_SIZE * 2,
  },
  ne: {
    top: -HANDLE_SIZE / 2,
    right: -HANDLE_SIZE / 2,
    cursor: "nesw-resize",
    width: HANDLE_SIZE,
    height: HANDLE_SIZE,
  },
  nw: {
    top: -HANDLE_SIZE / 2,
    left: -HANDLE_SIZE / 2,
    cursor: "nwse-resize",
    width: HANDLE_SIZE,
    height: HANDLE_SIZE,
  },
  se: {
    bottom: -HANDLE_SIZE / 2,
    right: -HANDLE_SIZE / 2,
    cursor: "nwse-resize",
    width: HANDLE_SIZE,
    height: HANDLE_SIZE,
  },
  sw: {
    bottom: -HANDLE_SIZE / 2,
    left: -HANDLE_SIZE / 2,
    cursor: "nesw-resize",
    width: HANDLE_SIZE,
    height: HANDLE_SIZE,
  },
};

export function CanvasElement({
  element,
  pageId,
  selected,
  multiSelected,
  previewMode,
  columns,
  rows,
  hasOverlap,
  locked,
  hidden,
  onSelect,
  onCommitResize,
  onContextMenu,
}: CanvasElementProps) {
  const [tempGridArea, setTempGridArea] = useState<GridArea | null>(null);
  const tempGridAreaRef = useRef<GridArea | null>(null);
  const isResizing = useRef(false);

  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `canvas-${element.id}`,
    data: { source: "canvas", elementId: element.id, pageId },
    disabled: previewMode || isResizing.current || !!locked,
  });

  const gridArea = tempGridArea || element.grid_area;

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      if (previewMode || locked) return;
      e.stopPropagation();
      onSelect(element.id, e.shiftKey);
    },
    [previewMode, locked, onSelect, element.id],
  );

  const handleRightClick = useCallback(
    (e: React.MouseEvent) => {
      if (previewMode || locked) return;
      e.preventDefault();
      e.stopPropagation();
      if (!selected) onSelect(element.id);
      onContextMenu(e, element.id);
    },
    [previewMode, locked, selected, onSelect, onContextMenu, element.id],
  );

  const handleResizeStart = useCallback(
    (direction: string, e: React.PointerEvent) => {
      e.stopPropagation();
      e.preventDefault();
      isResizing.current = true;

      const startX = e.clientX;
      const startY = e.clientY;
      const startGrid = { ...element.grid_area };

      // Measure grid cells from the canvas grid container (attached by Canvas.tsx via data-canvas-grid).
      const gridEl = (e.currentTarget as HTMLElement).closest(
        "[data-canvas-grid]",
      );
      const gridRect = gridEl?.getBoundingClientRect();
      if (!gridRect) return;

      const cellW = gridRect.width / columns;
      const cellH = gridRect.height / rows;

      const handlePointerMove = (moveEvent: PointerEvent) => {
        const dxCells = Math.round((moveEvent.clientX - startX) / cellW);
        const dyCells = Math.round((moveEvent.clientY - startY) / cellH);

        let { col, row, col_span, row_span } = startGrid;

        if (direction.includes("e"))
          col_span = Math.max(1, startGrid.col_span + dxCells);
        if (direction.includes("w")) {
          col = startGrid.col + dxCells;
          col_span = startGrid.col_span - dxCells;
        }
        if (direction.includes("s"))
          row_span = Math.max(1, startGrid.row_span + dyCells);
        if (direction.includes("n")) {
          row = startGrid.row + dyCells;
          row_span = startGrid.row_span - dyCells;
        }

        // Clamp
        col = Math.max(1, Math.min(columns, col));
        row = Math.max(1, Math.min(rows, row));
        col_span = Math.max(1, Math.min(columns - col + 1, col_span));
        row_span = Math.max(1, Math.min(rows - row + 1, row_span));

        const newArea = { col, row, col_span, row_span };
        setTempGridArea(newArea);
        tempGridAreaRef.current = newArea;
      };

      const cleanup = () => {
        document.removeEventListener("pointermove", handlePointerMove);
        document.removeEventListener("pointerup", handlePointerUp);
        document.removeEventListener("keydown", handleKeyDown);
        isResizing.current = false;
      };

      const handlePointerUp = () => {
        const finalGrid = tempGridAreaRef.current;
        cleanup();
        setTempGridArea(null);
        tempGridAreaRef.current = null;

        if (
          finalGrid &&
          (finalGrid.col !== element.grid_area.col ||
            finalGrid.row !== element.grid_area.row ||
            finalGrid.col_span !== element.grid_area.col_span ||
            finalGrid.row_span !== element.grid_area.row_span)
        ) {
          onCommitResize(element.id, finalGrid);
        }
      };

      const handleKeyDown = (keyEvent: KeyboardEvent) => {
        if (keyEvent.key === "Escape") {
          cleanup();
          setTempGridArea(null);
          tempGridAreaRef.current = null;
        }
      };

      document.addEventListener("pointermove", handlePointerMove);
      document.addEventListener("pointerup", handlePointerUp);
      document.addEventListener("keydown", handleKeyDown);
    },
    [element.grid_area, element.id, columns, rows, onCommitResize],
  );

  // Transparent hit-box sitting on top of the iframe. The iframe renders the element's
  // pixels; this wrapper handles selection, drag, resize, context menu, and the selection
  // outline / overlap badge.
  return (
    <div
      ref={setNodeRef}
      {...(!previewMode ? { ...listeners, ...attributes } : {})}
      onClick={handleClick}
      onContextMenu={handleRightClick}
      style={{
        gridColumn: `${gridArea.col} / span ${gridArea.col_span}`,
        gridRow: `${gridArea.row} / span ${gridArea.row_span}`,
        position: "relative",
        outline: selected && !previewMode
          ? multiSelected
            ? "2px dashed var(--accent)"
            : "2px solid var(--accent)"
          : hasOverlap && !previewMode
          ? "1px dashed var(--color-warning)"
          : "none",
        outlineOffset: "1px",
        opacity: isDragging ? 0.3 : 1,
        cursor: previewMode ? "default" : locked ? "not-allowed" : "move",
        zIndex: selected ? 10 : 1,
        minWidth: 0,
        minHeight: 0,
        // Hit-box itself is transparent — the iframe below paints the real element.
        background: "transparent",
      }}
    >
      {selected && !previewMode && (
        <>
          {Object.entries(HANDLE_POSITIONS).map(([dir, style]) => (
            <div
              key={dir}
              onPointerDown={(e) => handleResizeStart(dir, e)}
              style={{
                position: "absolute",
                ...style,
                backgroundColor: "var(--accent)",
                borderRadius: 2,
                zIndex: 20,
              }}
            />
          ))}
        </>
      )}
      {/* Overlap warning indicator */}
      {hasOverlap && !previewMode && (
        <div
          title="This element overlaps with another element"
          style={{
            position: "absolute",
            top: 2,
            right: 2,
            width: 14,
            height: 14,
            borderRadius: "50%",
            background: "#ff9800",
            color: "#000",
            fontSize: 10,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 25,
            lineHeight: 1,
            pointerEvents: "none",
          }}
        >
          !
        </div>
      )}
      {/* Resize tooltip showing position/size */}
      {tempGridArea && !previewMode && (
        <div
          style={{
            position: "absolute",
            bottom: -22,
            left: "50%",
            transform: "translateX(-50%)",
            padding: "2px 8px",
            borderRadius: 4,
            background: "rgba(0,0,0,0.85)",
            color: "#fff",
            fontSize: 10,
            whiteSpace: "nowrap",
            pointerEvents: "none",
            zIndex: 30,
            fontFamily: "monospace",
          }}
        >
          {tempGridArea.col_span}&times;{tempGridArea.row_span} at col {tempGridArea.col}, row {tempGridArea.row}
        </div>
      )}
      {/* Hidden overlay — dims the element and blocks interaction */}
      {hidden && !previewMode && (
        <div
          style={{
            position: "absolute", inset: 0,
            background: "rgba(0,0,0,0.6)",
            borderRadius: 4, pointerEvents: "none",
            display: "flex", alignItems: "center", justifyContent: "center",
            zIndex: 15,
          }}
        >
          <span style={{ fontSize: 9, color: "rgba(255,255,255,0.5)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Hidden</span>
        </div>
      )}
    </div>
  );
}
