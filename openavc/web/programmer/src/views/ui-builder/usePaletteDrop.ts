/**
 * Adding a control from the palette: dragged in, the gesture from the drag
 * library's start to its drop or cancel, or clicked (`add`).
 *
 * The preview follows the REAL pointer, from a native listener, never the
 * drag library's delta: the delta folds in scroll compensation, so when the
 * palette list scrolled during a drag, start + delta drifted from the cursor
 * by exactly the scroll, and the footprint and the landing drifted with it.
 * Alt is tracked from the window because a drop only learns about it at the
 * drag's start; a mid-drag Alt press redraws the preview without waiting for
 * the pointer to move. The tracker registers before that redraw, so the redraw
 * sees the new state.
 *
 * Only the palette drags through the library: moving a control already on the
 * canvas is a pointer gesture the canvas owns. Where a control lands, dropped
 * or clicked, is paletteDrop.ts; making it there is the page's (`onAdd`).
 */
import { useCallback, useEffect, useRef } from "react";
import {
  PointerSensor,
  pointerWithin,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import type { Placement, UIPage } from "../../api/types";
import type { PluginExtension } from "../../api/pluginClient";
import { createDefaultElement, defaultElementSize } from "../../components/ui-builder/uiBuilderHelpers";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import {
  canvasRect,
  clickLanding,
  dropLanding,
  landingAt,
  pointerInside,
  previewAt,
  type Landing,
  type Point,
  type Size,
} from "./paletteDrop";

type DragData = { source: string; elementType?: string; elementId?: string } | undefined;

/** What the gesture remembers between the library's calls. */
interface Drag {
  start: Point | null;
  type: string | null;
  /** The ghost's words; set only while a palette drag is under way. */
  label: string | null;
  /** The box a drop will land at, in percent. */
  size: Size | null;
  /** The same box in screen pixels, for the ghost. Kept between drags. */
  ghost: Size;
  /** Where the pointer sat inside the palette button at the start. The drag
   *  overlay is anchored to the button origin while the ghost is sized to the
   *  landing box, so the ghost is re-centred under the pointer by this. */
  grab: Point;
  alt: boolean;
  pointer: Point | null;
  stopTracking: (() => void) | null;
}

export interface PaletteGhost {
  label: string;
  width: number;
  height: number;
  grabX: number;
  grabY: number;
}

function publish(preview: ReturnType<typeof previewAt> | null) {
  const store = useUIBuilderStore.getState();
  if (preview) store.setPaletteDragPreview(preview);
  else if (store.paletteDragPreview) store.setPaletteDragPreview(null);
}

/** The preview for where the pointer is now, or none off the canvas. */
function previewNow(d: Drag, page: UIPage | null, layoutId: string | null) {
  const { pointer, size, type } = d;
  if (!pointer || !size) return null;
  if (!type || !page) return null;
  const rect = canvasRect();
  if (!rect || !pointerInside(rect, pointer)) return null;
  return previewAt(pointer, rect, size, { page, layoutId, bypass: d.alt, label: type });
}

/** The palette type a drag carries, or null for any other drag. */
function paletteType(data: DragData): string | null {
  return data?.source === "palette" && data.elementType ? data.elementType : null;
}

/** True when the drag came from the palette, so the pointer is to be followed. */
function begin(d: Drag, event: DragStartEvent, panelElements: PluginExtension[] | undefined): boolean {
  const data = event.active.data.current as DragData;
  useUIBuilderStore.getState().setActiveDragSource(data?.source || null);
  d.type = data?.elementType || null;
  const pointer = event.activatorEvent as PointerEvent;
  d.start = { x: pointer.clientX, y: pointer.clientY };
  const type = paletteType(data);
  d.label = null;
  d.size = null;
  if (type) {
    d.label = createDefaultElement(type, new Set()).type;
    d.size = defaultElementSize(type, panelElements);
    d.pointer = { ...d.start };
  }
  measureGhost(d, pointer);
  return !!type;
}

/**
 * The ghost is drawn at the size the control will land at, a percentage of
 * the canvas, zoom included. The grab is measured from the palette button's
 * real rect: the library's own measurement is still empty at drag start.
 */
function measureGhost(d: Drag, pointer: PointerEvent) {
  const { label, size } = d;
  if (!label || !size) return;
  const rect = canvasRect();
  if (!rect) return;
  d.ghost = { w: (size.w / 100) * rect.width, h: (size.h / 100) * rect.height };
  const item = (pointer.target as Element | null)
    ?.closest('[aria-roledescription="draggable"]')
    ?.getBoundingClientRect();
  d.grab = { x: item ? pointer.clientX - item.left : 0, y: item ? pointer.clientY - item.top : 0 };
}

/** Everything a drag leaves behind, cleared; the pointer it last saw. */
function finish(d: Drag): Point | null {
  d.stopTracking?.();
  useUIBuilderStore.getState().setPaletteDragPreview(null);
  useUIBuilderStore.getState().setActiveDragSource(null);
  d.type = null;
  d.label = null;
  const last = d.pointer;
  d.pointer = null;
  return last;
}

/**
 * Where a drop on the canvas lands: exactly where the live preview showed it,
 * else (a drop the preview never saw: nothing moved after entering the canvas)
 * the same rule worked out from the last pointer, with Alt from the drag's
 * start or since.
 */
function landing(
  d: Drag,
  event: DragEndEvent,
  last: { preview: ReturnType<typeof previewAt> | null; pointer: Point | null },
  place: { page: UIPage; layoutId: string | null; panelElements: PluginExtension[] | undefined },
): { type: string; placement: Placement } | null {
  const drop = paletteDropOnCanvas(event, d.start);
  if (!drop) return null;
  if (last.preview) return { type: drop.type, placement: last.preview.placement };
  const bypass = !!(event.activatorEvent as PointerEvent | undefined)?.altKey || d.alt;
  const size = defaultElementSize(drop.type, place.panelElements);
  const placement = landingAt(last.pointer ?? drop.start, drop.rect, size, { ...place, bypass }).placement;
  return { type: drop.type, placement };
}

/** A palette drag the canvas accepted, from a start that was seen, with the
 *  canvas there to measure against; else null. */
function paletteDropOnCanvas(event: DragEndEvent, start: Point | null) {
  if (event.over?.id !== "canvas-drop" || !start) return null;
  const data = event.active.data.current as DragData;
  const rect = data ? canvasRect() : undefined;
  const type = rect ? paletteType(data) : null;
  return type && rect ? { type, rect, start } : null;
}

export function usePaletteDrop(opts: {
  page: UIPage | null;
  layoutId: string | null;
  panelElements: PluginExtension[] | undefined;
  /** Add a control of `type` on the page where it landed. */
  onAdd: (type: string, landing: Landing) => void;
}) {
  const { page, layoutId, panelElements, onAdd } = opts;
  const activeDragSource = useUIBuilderStore((s) => s.activeDragSource);
  const d = useRef<Drag>({
    start: null, type: null, label: null, size: null,
    ghost: { w: 120, h: 60 }, grab: { x: 0, y: 0 },
    alt: false, pointer: null, stopTracking: null,
  }).current;

  useAltTracker(d);
  const updatePreview = useCallback(() => publish(previewNow(d, page, layoutId)), [d, page, layoutId]);
  // The pointer listener outlives the render that started the drag, so it
  // redraws through the newest preview: the page as it is now, its snap too.
  const latestPreview = useRef(updatePreview);
  useEffect(() => { latestPreview.current = updatePreview; }, [updatePreview]);
  const trackPointer = usePointerTracker(d, latestPreview);

  const onDragStart = useCallback((event: DragStartEvent) => {
    if (begin(d, event, panelElements)) trackPointer();
  }, [d, panelElements, trackPointer]);

  useAltRedraw(activeDragSource === "palette", updatePreview);

  const onDragEnd = useCallback((event: DragEndEvent) => {
    const preview = useUIBuilderStore.getState().paletteDragPreview;
    const pointer = finish(d);
    const landed = page ? landing(d, event, { preview, pointer }, { page, layoutId, panelElements }) : null;
    if (!landed || !page) return;
    onAdd(landed.type, dropLanding(page, landed.placement, layoutId));
    d.start = null;
  }, [d, page, layoutId, panelElements, onAdd]);

  const onDragCancel = useCallback(() => {
    finish(d);
    d.start = null;
  }, [d]);

  const add = useCallback((type: string) => {
    if (page) onAdd(type, clickLanding(page, layoutId, defaultElementSize(type, panelElements)));
  }, [page, layoutId, panelElements, onAdd]);

  // A drag in flight when the view unmounts would leave the tracker attached
  // and its footprint in the Builder store for the next visit to draw.
  useEffect(() => () => { finish(d); }, [d]);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  return {
    add,
    ghost: activeDragSource ? ghostOf(d) : null,
    dndProps: { sensors, ...DROP_RULES, onDragStart, onDragEnd, onDragCancel },
  };
}

const DROP_RULES = {
  // Pointer-based: the drop is accepted exactly when the pointer is over the
  // canvas, which is also when the snapped preview is showing, so what you see
  // mid-drag and what a release does can never disagree.
  collisionDetection: pointerWithin,
  // No auto-scroll: a palette drag is headed for the canvas, and the list
  // crawling along under the drag both looks broken and feeds scroll
  // compensation into the library's coordinates.
  autoScroll: false,
};

function ghostOf(d: Drag): PaletteGhost | null {
  if (!d.label) return null;
  return { label: d.label, width: d.ghost.w, height: d.ghost.h, grabX: d.grab.x, grabY: d.grab.y };
}

/** Alt as the window last heard it. Registered before `useAltRedraw`, so a
 *  redraw on the same key sees the new state. */
function useAltTracker(d: Drag) {
  useEffect(() => {
    const track = (e: KeyboardEvent) => { d.alt = e.altKey; };
    window.addEventListener("keydown", track);
    window.addEventListener("keyup", track);
    return () => {
      window.removeEventListener("keydown", track);
      window.removeEventListener("keyup", track);
    };
  }, [d]);
}

/** Alt toggled mid-drag changes the answer without the pointer moving. */
function useAltRedraw(dragging: boolean, redraw: () => void) {
  useEffect(() => {
    if (!dragging) return;
    const onModifier = (e: KeyboardEvent) => {
      if (e.key === "Alt") redraw();
    };
    window.addEventListener("keydown", onModifier);
    window.addEventListener("keyup", onModifier);
    return () => {
      window.removeEventListener("keydown", onModifier);
      window.removeEventListener("keyup", onModifier);
    };
  }, [dragging, redraw]);
}

/** Follow the pointer from the window, redrawing the preview on each move;
 *  `d.stopTracking` lets go. Starting again lets go of the last one first. */
function usePointerTracker(d: Drag, redraw: { current: () => void }) {
  return useCallback(() => {
    d.stopTracking?.();
    const onMove = (ev: PointerEvent) => {
      d.pointer = { x: ev.clientX, y: ev.clientY };
      redraw.current();
    };
    window.addEventListener("pointermove", onMove);
    d.stopTracking = () => {
      window.removeEventListener("pointermove", onMove);
      d.stopTracking = null;
    };
  }, [d, redraw]);
}
