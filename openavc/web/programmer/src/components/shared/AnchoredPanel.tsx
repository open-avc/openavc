/**
 * The one anchored floating panel in the Programmer.
 *
 * Every picker that drops a panel beside a trigger — the state-key picker, the
 * device-property picker, the param combobox, the colour swatch, the surface
 * preset list — used to carry its own copy of this: measure the trigger, decide
 * whether to flip up, place a `position: fixed` panel, close on an outside
 * click or an outside scroll. Five copies, and they had already drifted apart:
 * the flip-up threshold was 250px in two of them and 220px in the others, the
 * width floor was 320px in two and absent in a third, and only two of the five
 * clamped the panel back into the viewport — which is the one that matters,
 * because these triggers sit in the narrow right-docked properties pane. This
 * is that mechanic, written once.
 *
 * Two things stay parameters rather than becoming constants, because they are
 * facts about the panel and not taste:
 *
 *   - `width` — a list dropdown fills its trigger (with a readable floor); a
 *     colour wheel is whatever size a colour wheel is.
 *   - `wantsHeight` — "is there room below?" is really "room for how much?", so
 *     a 130px popover should not flip up with 200px of space beneath it.
 *
 * An intrinsically-sized panel is MEASURED once it has rendered, never
 * estimated. Guessing it from padding and border by hand is the mistake
 * `openavc/ui/control_minimums.py` exists to forbid, and the guess is exactly
 * what the clamp depends on: too small and the panel still hangs off the edge
 * the clamp was added to protect.
 *
 * Vertical placement is recomputed every render from a fresh trigger rect, so
 * the panel stays glued to its trigger when the pane reflows underneath it.
 * That is inherited from the two big pickers and is load-bearing.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, RefObject } from "react";
import { LAYER } from "./layers";

/** Gap between the trigger and the panel, on whichever side it opens. */
const PANEL_GAP = 4;
/** Keep the panel this far from the left/right edges of the window. */
const VIEWPORT_MARGIN = 8;
/** Keep the panel this far from the top/bottom edges of the window. */
const PANEL_EDGE_MARGIN = 16;
/** A dropdown list is unreadable much narrower than this. */
const DEFAULT_MIN_WIDTH = 320;
/** How much room a list-shaped panel would like before it gives up and flips. */
const DEFAULT_WANTS_HEIGHT = 250;
/** Never squeeze a panel below this, even in a short window. */
const DEFAULT_MIN_HEIGHT = 200;

export interface AnchoredPanelOptions {
  /**
   * `"fill"` (default) sizes the panel to its trigger, floored at `minWidth`,
   * which is what a list dropdown wants. `"intrinsic"` sets no width at all and
   * lets the content decide — the panel is then measured, so the clamp works
   * off its real width instead of a guess.
   */
  width?: "fill" | "intrinsic";
  /** Floor for a `"fill"` panel's width; the trigger wins when wider. */
  minWidth?: number;
  /** Height the panel would like, which is what the flip-up test compares against. */
  wantsHeight?: number;
  /** Reset panel-owned state — a search box, an inline form — as it closes. */
  onClose?: () => void;
}

interface Anchor {
  left: number;
  /** The panel's own width: computed for `"fill"`, measured for `"intrinsic"`. */
  width: number;
  flipUp: boolean;
  /** Fallback vertical origin for the render before the trigger can be measured. */
  top: number;
}

export interface AnchoredPanel<T extends HTMLElement, P extends HTMLElement = HTMLElement> {
  open: boolean;
  /** Measure the trigger and show the panel. */
  openPanel: () => void;
  /** Hide the panel and run `onClose`. */
  close: () => void;
  toggle: () => void;
  /** Wraps trigger + panel. This element is what "outside" means. */
  containerRef: RefObject<HTMLDivElement | null>;
  /** The element the panel is measured against. */
  triggerRef: RefObject<T | null>;
  /** Put on the panel element, so an intrinsic panel can be measured. */
  panelRef: RefObject<P | null>;
  /** Spread onto the panel element. Empty while closed. */
  panelStyle: CSSProperties;
}

/** Keep a panel of this width inside the window. */
function clampLeft(triggerLeft: number, width: number): number {
  return Math.max(
    VIEWPORT_MARGIN,
    Math.min(triggerLeft, window.innerWidth - width - VIEWPORT_MARGIN),
  );
}

export function useAnchoredPanel<T extends HTMLElement, P extends HTMLElement = HTMLElement>(
  options: AnchoredPanelOptions = {},
): AnchoredPanel<T, P> {
  const {
    width: widthMode = "fill",
    minWidth = DEFAULT_MIN_WIDTH,
    wantsHeight = DEFAULT_WANTS_HEIGHT,
    onClose,
  } = options;

  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<Anchor>({ left: 0, width: 0, flipUp: false, top: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<T>(null);
  const panelRef = useRef<P>(null);

  // The close listeners below subscribe once per open, not once per render, and
  // read the current `onClose` through this ref. A listener re-added *during* a
  // dispatch is never called for that event, which is how the modal work found
  // Escape doing nothing at all in the UI Builder — same trap, same fix.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const close = useCallback(() => {
    setOpen(false);
    onCloseRef.current?.();
  }, []);

  const openPanel = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) {
      const spaceBelow = window.innerHeight - rect.bottom;
      const flipUp = spaceBelow < wantsHeight && rect.top > spaceBelow;
      // An intrinsic panel has no width yet -- it has not rendered. It opens at
      // the trigger's left and the layout effect below corrects it from the
      // real measurement, before the browser paints.
      const width = widthMode === "fill" ? Math.max(rect.width, minWidth) : 0;
      setAnchor({ left: clampLeft(rect.left, width), width, flipUp, top: rect.bottom });
    }
    setOpen(true);
  }, [minWidth, wantsHeight, widthMode]);

  const toggle = useCallback(() => {
    if (open) close();
    else openPanel();
  }, [open, close, openPanel]);

  // Measure an intrinsically-sized panel and re-clamp off its real width.
  // useLayoutEffect so the correction lands before paint -- a visible jump is
  // the panel briefly hanging off the edge it is being clamped away from.
  useLayoutEffect(() => {
    if (!open || widthMode === "fill") return;
    const el = panelRef.current;
    const trigger = triggerRef.current;
    if (!el || !trigger) return;
    const measured = el.getBoundingClientRect().width;
    const left = clampLeft(trigger.getBoundingClientRect().left, measured);
    setAnchor((a) =>
      Math.abs(a.width - measured) < 0.5 && Math.abs(a.left - left) < 0.5
        ? a
        : { ...a, width: measured, left },
    );
  });

  // Close on a click or a scroll that happened outside the panel. Scrolling
  // *inside* it must not close it — the list is the thing being scrolled.
  useEffect(() => {
    if (!open) return;
    const isOutside = (e: Event) =>
      !!containerRef.current && !containerRef.current.contains(e.target as Node);
    const onPointerDown = (e: MouseEvent) => {
      if (isOutside(e)) close();
    };
    const onScroll = (e: Event) => {
      if (isOutside(e)) close();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [open, close]);

  let panelStyle: CSSProperties = {};
  if (open) {
    const rect = triggerRef.current?.getBoundingClientRect();
    const triggerTop = rect?.top ?? anchor.top;
    const triggerBottom = rect?.bottom ?? anchor.top;
    const available = anchor.flipUp
      ? triggerTop - PANEL_EDGE_MARGIN
      : window.innerHeight - triggerBottom - PANEL_EDGE_MARGIN;
    panelStyle = {
      position: "fixed",
      top: anchor.flipUp ? undefined : triggerBottom + PANEL_GAP,
      bottom: anchor.flipUp ? window.innerHeight - triggerTop + PANEL_GAP : undefined,
      left: anchor.left,
      // An intrinsic panel is never given a width -- that is the point of it.
      ...(widthMode === "fill" ? { width: anchor.width } : {}),
      maxHeight: Math.max(Math.max(wantsHeight, DEFAULT_MIN_HEIGHT), available),
      zIndex: LAYER.popover,
    };
  }

  return { open, openPanel, close, toggle, containerRef, triggerRef, panelRef, panelStyle };
}
