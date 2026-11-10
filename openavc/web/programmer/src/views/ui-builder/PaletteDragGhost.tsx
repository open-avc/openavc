/**
 * A palette drag as drawn outside the canvas: the drag library's overlay, with
 * no drop animation, holding the ghost while a drag is under way
 * (`PaletteDragOverlay`, which the page places inside its drag context).
 *
 * The ghost is an outlined box the size the control will land at, labelled with
 * its type, centred under the pointer. It hides itself the moment the snapped
 * canvas footprint takes over, and it subscribes to the store itself so the
 * per-pointer-move re-render stays contained here instead of re-running the
 * whole page.
 */
import { DragOverlay } from "@dnd-kit/core";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import type { PaletteGhost } from "./usePaletteDrop";

export function PaletteDragOverlay({ ghost }: { ghost: PaletteGhost | null }) {
  return <DragOverlay dropAnimation={null}>{ghost ? <PaletteDragGhost {...ghost} /> : null}</DragOverlay>;
}

export function PaletteDragGhost({
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
