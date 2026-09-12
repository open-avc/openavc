import type { MasterElement, Placement } from "../../api/types";
import { HANDLE_POSITIONS, HANDLE_SIZE } from "./CanvasElement";

interface CanvasMasterElementProps {
  master: MasterElement;
  /** Where it sits right now — live during a gesture, stored otherwise. */
  box: Placement;
  selected: boolean;
  locked: boolean;
  /** Non-null while a gesture is in flight, which suppresses the handles. */
  gestureKind: "move" | "resize" | null;
  onSelect: (id: string) => void;
  onGestureStart: (
    masterId: string,
    kind: "move" | "resize",
    direction: string,
    e: React.PointerEvent,
  ) => void;
  onContextMenu: (e: React.MouseEvent, masterId: string) => void;
}

/**
 * A master element's hit box on the canvas — selection, drag, resize, context
 * menu — sitting on top of the iframe, which paints the real element.
 *
 * The same grips and the same gesture as a page element, because "I can see it
 * and I cannot move it" is not a distinction anybody can act on. What IS
 * different is the reach: a master is not on this page, so dragging it here
 * moves it on every page it appears on, and the badge says so rather than
 * leaving that to be discovered. It draws under the page's own hit boxes
 * (z 0 against their 1), matching the order the panel draws them in, so a
 * control laid over a master is still the thing you grab.
 */
export function CanvasMasterElement({
  master,
  box,
  selected,
  locked,
  gestureKind,
  onSelect,
  onGestureStart,
  onContextMenu,
}: CanvasMasterElementProps) {
  const handlePointerDown = (e: React.PointerEvent) => {
    if (locked || e.button !== 0) return;
    // Select on the way down, so a drag that starts on an unselected master
    // moves that master rather than whatever was selected before.
    if (!selected) onSelect(master.id);
    onGestureStart(master.id, "move", "", e);
  };

  return (
    <div
      data-canvas-master={master.id}
      onPointerDown={handlePointerDown}
      onClick={(e) => {
        // The canvas background clears the selection on click, so letting this
        // bubble would deselect what the pointer-down just picked. A locked
        // master is not selectable here at all, which is what the padlock in
        // the Outline promises and what a locked page element already does --
        // the Outline row is where it gets unlocked.
        e.stopPropagation();
        if (locked) return;
        onSelect(master.id);
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (locked) return;
        onSelect(master.id);
        onContextMenu(e, master.id);
      }}
      style={{
        position: "absolute",
        left: `${box.x}%`,
        top: `${box.y}%`,
        width: `${box.w}%`,
        height: `${box.h}%`,
        cursor: locked ? "not-allowed" : "move",
        outline: selected ? "2px solid #9C27B0" : "none",
        outlineOffset: 1,
        borderRadius: 4,
        overflow: "visible",
        zIndex: selected ? 10 : 0,
      }}
      title={
        locked
          ? `Master element: ${master.id} (locked)`
          : `Master element: ${master.id} — drag or resize it here and it moves on every page it appears on`
      }
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

      {selected && !locked && !gestureKind && (
        <>
          {Object.entries(HANDLE_POSITIONS).map(([dir, style]) => (
            <div
              key={dir}
              onPointerDown={(e) => onGestureStart(master.id, "resize", dir, e)}
              style={{
                position: "absolute",
                width: HANDLE_SIZE,
                height: HANDLE_SIZE,
                ...style,
                backgroundColor: "#9C27B0",
                border: "1px solid rgba(0,0,0,0.4)",
                borderRadius: 2,
                zIndex: 20,
              }}
            />
          ))}
        </>
      )}

      {/* Live readout while it is under the pointer. */}
      {selected && gestureKind && (
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
