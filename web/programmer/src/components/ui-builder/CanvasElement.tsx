import type { UIElement, Placement } from "../../api/types";

interface CanvasElementProps {
  element: UIElement;
  /** Container tree, so a container draws its children inside itself. */
  childrenByParent: Map<string, UIElement[]>;
  /** Where this element sits right now — live during a gesture, stored otherwise. */
  placementFor: (id: string) => Placement;
  selectedIds: string[];
  previewMode: boolean;
  overlappingIds: Set<string>;
  outOfBoundsIds: Set<string>;
  smallTouchIds: Set<string>;
  lockedIds: Set<string>;
  /** Non-null while a gesture is in flight, which suppresses the handles. */
  gestureKind: "move" | "resize" | null;
  /** The container the live drag would drop into. Lit up so you can see where
   *  a control is about to land before you let go of it. */
  adoptTargetId: string | null;
  onSelect: (id: string, shiftKey?: boolean) => void;
  onGestureStart: (
    elementId: string,
    kind: "move" | "resize",
    direction: string,
    e: React.PointerEvent,
  ) => void;
  onContextMenu: (e: React.MouseEvent, elementId: string) => void;
}

const HANDLE_SIZE = 12;

/** The eight grips, and which edges each one drags. */
const HANDLE_POSITIONS: Record<string, React.CSSProperties> = {
  n: { top: -HANDLE_SIZE / 2, left: "50%", marginLeft: -HANDLE_SIZE / 2, cursor: "ns-resize" },
  s: { bottom: -HANDLE_SIZE / 2, left: "50%", marginLeft: -HANDLE_SIZE / 2, cursor: "ns-resize" },
  e: { right: -HANDLE_SIZE / 2, top: "50%", marginTop: -HANDLE_SIZE / 2, cursor: "ew-resize" },
  w: { left: -HANDLE_SIZE / 2, top: "50%", marginTop: -HANDLE_SIZE / 2, cursor: "ew-resize" },
  ne: { top: -HANDLE_SIZE / 2, right: -HANDLE_SIZE / 2, cursor: "nesw-resize" },
  nw: { top: -HANDLE_SIZE / 2, left: -HANDLE_SIZE / 2, cursor: "nwse-resize" },
  se: { bottom: -HANDLE_SIZE / 2, right: -HANDLE_SIZE / 2, cursor: "nwse-resize" },
  sw: { bottom: -HANDLE_SIZE / 2, left: -HANDLE_SIZE / 2, cursor: "nesw-resize" },
};

/**
 * A transparent hit box sitting on top of the iframe, which paints the real
 * element. This one handles selection, drag, resize, the context menu and the
 * warning badges — and, for a container, hosts its children's hit boxes so the
 * whole tree lines up with what the runtime draws.
 */
export function CanvasElement({
  element,
  childrenByParent,
  placementFor,
  selectedIds,
  previewMode,
  overlappingIds,
  outOfBoundsIds,
  smallTouchIds,
  lockedIds,
  gestureKind,
  adoptTargetId,
  onSelect,
  onGestureStart,
  onContextMenu,
}: CanvasElementProps) {
  const box = placementFor(element.id);
  const selected = selectedIds.includes(element.id);
  const multiSelected = selected && selectedIds.length > 1;
  const locked = lockedIds.has(element.id);
  const hasOverlap = overlappingIds.has(element.id);
  const outOfBounds = outOfBoundsIds.has(element.id);
  const smallTouch = smallTouchIds.has(element.id);
  const isAdoptTarget = adoptTargetId === element.id;
  const children = childrenByParent.get(element.id) ?? [];

  const handlePointerDown = (e: React.PointerEvent) => {
    if (previewMode || locked || e.button !== 0) return;
    // Select on the way down so a drag that starts on an unselected element
    // moves that element, not whatever was selected before.
    if (!selected) onSelect(element.id, e.shiftKey);
    else if (e.shiftKey) onSelect(element.id, true);
    onGestureStart(element.id, "move", "", e);
  };

  // Selection happens on pointer-down, but the click still has to be stopped
  // here — the canvas background clears the selection on click, so letting it
  // bubble deselects the element the pointer-down just picked.
  const handleClick = (e: React.MouseEvent) => {
    if (previewMode) return;
    e.stopPropagation();
  };

  const handleRightClick = (e: React.MouseEvent) => {
    if (previewMode || locked) return;
    e.preventDefault();
    e.stopPropagation();
    if (!selected) onSelect(element.id);
    onContextMenu(e, element.id);
  };

  const warning = outOfBounds
    ? element.parent
      ? "This element extends beyond its container"
      : "This element extends beyond the page"
    : hasOverlap
    ? "This element overlaps another"
    : smallTouch
    ? "Smaller than a comfortable touch target — see the Layout panel for the physical size"
    : null;

  return (
    <div
      data-canvas-element={element.id}
      onPointerDown={handlePointerDown}
      onClick={handleClick}
      onContextMenu={handleRightClick}
      style={{
        position: "absolute",
        left: `${box.x}%`,
        top: `${box.y}%`,
        width: `${box.w}%`,
        height: `${box.h}%`,
        outline: isAdoptTarget
          ? "2px dashed var(--accent)"
          : selected && !previewMode
            ? multiSelected
              ? "2px dashed var(--accent)"
              : "2px solid var(--accent)"
            : warning && !previewMode
            ? "1px dashed var(--color-warning)"
            : "none",
        outlineOffset: "1px",
        cursor: previewMode ? "default" : locked ? "not-allowed" : "move",
        zIndex: selected ? 10 : 1,
        // Containers do not clip, here or at runtime: a child nudged past the
        // edge stays visible and gets a badge rather than disappearing.
        overflow: "visible",
        background: isAdoptTarget ? "rgba(33, 150, 243, 0.12)" : "transparent",
      }}
    >
      {/* Children live INSIDE the container's box, because their percentages
          are of it. Nesting the hit boxes is what keeps the overlay honest. */}
      {children.map((child) => (
        <CanvasElement
          key={child.id}
          element={child}
          childrenByParent={childrenByParent}
          placementFor={placementFor}
          selectedIds={selectedIds}
          previewMode={previewMode}
          overlappingIds={overlappingIds}
          outOfBoundsIds={outOfBoundsIds}
          smallTouchIds={smallTouchIds}
          lockedIds={lockedIds}
          gestureKind={gestureKind}
          adoptTargetId={adoptTargetId}
          onSelect={onSelect}
          onGestureStart={onGestureStart}
          onContextMenu={onContextMenu}
        />
      ))}

      {selected && !previewMode && !locked && !gestureKind && (
        <>
          {Object.entries(HANDLE_POSITIONS).map(([dir, style]) => (
            <div
              key={dir}
              onPointerDown={(e) => onGestureStart(element.id, "resize", dir, e)}
              style={{
                position: "absolute",
                width: HANDLE_SIZE,
                height: HANDLE_SIZE,
                ...style,
                backgroundColor: "var(--accent)",
                border: "1px solid rgba(0,0,0,0.4)",
                borderRadius: 2,
                zIndex: 20,
              }}
            />
          ))}
        </>
      )}

      {warning && !previewMode && (
        <div
          title={warning}
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

      {/* Live readout while the element is under the pointer. */}
      {selected && gestureKind && !previewMode && (
        <div
          style={{
            position: "absolute",
            bottom: -20,
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
          {box.w.toFixed(1)}&times;{box.h.toFixed(1)}% at {box.x.toFixed(1)},{box.y.toFixed(1)}
        </div>
      )}
    </div>
  );
}
