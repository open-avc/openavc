import { useRef, useCallback, useMemo, useEffect, useState } from "react";
import { useDroppable } from "@dnd-kit/core";
import type { UIPage, MasterElement, Placement } from "../../api/types";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import { CanvasElement } from "./CanvasElement";
import {
  commitGesturePlacements,
  moveElementsInPage,
  reviewWarningsByElement,
  sliderThemeDefaults,
  resolvePlacements,
  pageSnap,
  snapMove,
  snapResize,
  getPlacement,
  roundPlacement,
  topmostSelection,
  elementsIntersectingRect,
  dropTargetFor,
  toPageBox,
  absolutePlacements,
  lockedIdsFor,
  masterPlacement,
  layoutOrientation,
  resolveHidden,
  MIN_ELEMENT_SIZE,
} from "./uiBuilderHelpers";
import { getTunnelPrefix } from "../../api/restClient";
import { resolveProjectMatrices } from "../../api/matrixPreview";
import { useConnectionStore } from "../../store/connectionStore";
import { useUiFilesStore } from "../../store/uiFilesStore";
import { showError } from "../../store/toastStore";

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

/** A gesture in flight: what is moving, and where it has got to so far. */
interface LiveGesture {
  kind: "move" | "resize";
  /** Boxes as they look right now, keyed by element id. Local state only —
   *  the store is written once, on pointer-up (plan section 9.2). */
  placements: Record<string, Placement>;
  guidesX: number[];
  guidesY: number[];
  /** The container this gesture would drop into, when that is a change. Drawn
   *  on the canvas while the drag is live, so nobody has to guess. */
  adoptInto: string | null;
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

  const selectedElementIds = useUIBuilderStore((s) => s.selectedElementIds);
  // A palette drag over this canvas, already snapped by the view. Drawing it
  // here — footprint, guides, adoption highlight — is what makes dragging IN
  // look exactly like moving.
  const paletteDragPreview = useUIBuilderStore((s) => s.paletteDragPreview);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);
  const activeLayoutId = useUIBuilderStore((s) => s.activeLayoutId);
  const selectElement = useUIBuilderStore((s) => s.selectElement);
  const selectElements = useUIBuilderStore((s) => s.selectElements);
  const toggleSelectElement = useUIBuilderStore((s) => s.toggleSelectElement);
  const selectMasterElement = useUIBuilderStore((s) => s.selectMasterElement);
  const pushUndo = useUIBuilderStore((s) => s.pushUndo);
  const touchMutation = useUIBuilderStore((s) => s.touchMutation);
  const setContextMenu = useUIBuilderStore((s) => s.setContextMenu);

  // Lock lives on the element now, so it survives a reload. One set covering
  // page elements and masters alike, because every consumer only asks "can
  // this move?".
  const lockedElementIds = useMemo(
    () => lockedIdsFor(page, masterElements),
    [page, masterElements],
  );

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
  // The vmin the iframe should pin its type scale to. Read through a ref so the
  // load handler's closure stays stable.
  const vminRef = useRef(8);

  // A custom control draws for real in the canvas, so it needs something to
  // draw. A snapshot of what the room is actually reporting beats invented
  // numbers, and the panel scopes it per element through that element's grant
  // exactly as it does on glass. Read once, not subscribed: the live store
  // updates constantly and this component also subscribes to others.
  const sampleState = () => useConnectionStore.getState().liveState;
  const uiFilesVersion = useUiFilesStore((s) => s.version);

  const handleIframeLoad = useCallback(() => {
    const iframe = iframeRef.current;
    const p = projectRef.current;
    if (!iframe?.contentWindow || !p) return;
    console.log("[canvas] iframe loaded — posting editor-init");
    // Resolved, not raw: the canvas IS the panel, and the panel reads a matrix
    // as two finished lists rather than expanding a generator itself (D6).
    void resolveProjectMatrices(p).then((resolved) =>
      iframe.contentWindow?.postMessage(
        {
          type: "openavc:editor-init",
          project: resolved,
          pageId: pageIdRef.current,
          showGrid,
          vmin: vminRef.current,
          demoState: sampleState(),
          uiFilesVersion,
        },
        "*",
      ),
    );
    setIframeReady(true);
  }, [showGrid, uiFilesVersion]);

  // Push project → iframe on every edit (both edit and preview modes), and on
  // every save into ui/ — a custom control's markup is not project data, so
  // that is the only thing that redraws it.
  useEffect(() => {
    if (!project) return;
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow) return;
    let live = true;
    const timer = setTimeout(() => {
      void resolveProjectMatrices(project).then((resolved) => {
        if (!live) return;
        iframe.contentWindow?.postMessage(
          {
            type: "openavc:editor-project",
            project: resolved,
            pageId: page.id,
            showGrid,
            vmin: vminRef.current,
            demoState: sampleState(),
            uiFilesVersion,
          },
          "*",
        );
      });
    }, 50);
    return () => { live = false; clearTimeout(timer); };
  }, [project, page.id, showGrid, uiFilesVersion]);

  // A custom control runs in its own sandboxed frame, so a script error in it
  // is invisible out here and invisible on a wall panel. The panel shows what
  // it can in the element's box and forwards it; this is the half that puts it
  // in front of the person who wrote it.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const msg = event.data as { type?: string; elementId?: string; message?: string };
      if (msg?.type !== "openavc:element-error") return;
      showError(`${msg.elementId || "Custom control"}: ${msg.message || "failed"}`);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // iframeReady is informational for now — kept to allow future gating if needed.
  void iframeReady;
  void themeVariables;

  // Everything this arrangement will draw wrong, per element, from the same
  // review the AI write path answers with: a control starved of the parts inside
  // it that don't shrink, two controls on top of each other, one drawn over a
  // master element the panel draws underneath it, one hanging out of its
  // container, one with no box at all, one too small for a finger, and a
  // binding this element type's renderer never reads. Live, not just on
  // Validate -- a status light gets starved by dragging as easily as by sizing,
  // and the badge is the moment to notice. Each message carries the pixels AND
  // the percentage of the real container to type instead.
  //
  // The theme slice is stable across renders -- it is the store's own object,
  // or null -- so it can be a dependency without re-reviewing every render.
  const sliderTheme = sliderThemeDefaults(project);
  // The masters draw under this page and are not on it, so the review cannot
  // see them unless they are handed over. Dragging a control onto the nav bar
  // is a canvas gesture, which is exactly where this has to be said.
  const warningsByElement = useMemo(
    () =>
      previewMode
        ? new Map<string, string>()
        : reviewWarningsByElement(page, activeLayoutId, sliderTheme, masterElements ?? []),
    [page, previewMode, activeLayoutId, sliderTheme, masterElements],
  );

  const placements = useMemo(
    () => resolvePlacements(page, activeLayoutId),
    [page, activeLayoutId],
  );
  // The same boxes with container nesting flattened. Adoption is decided in
  // page space, because "is this inside that" is a question about what is
  // drawn, not about percentages of different parents.
  const absolute = useMemo(
    () => absolutePlacements(page, activeLayoutId),
    [page, activeLayoutId],
  );
  const snap = useMemo(() => pageSnap(page), [page]);
  // The shape of glass this arrangement is for. Masters are keyed by it, and
  // so is the canvas the preset hands us.
  const canvasOrientation = layoutOrientation(page, activeLayoutId);
  // Elements this arrangement hides. They still get a hit-box so they can be
  // selected and un-hidden; the badge on them says why they are not drawn.
  const hiddenIds = useMemo(
    () => (previewMode ? new Set<string>() : resolveHidden(page, activeLayoutId)),
    [page, previewMode, activeLayoutId],
  );

  // A page that hands the whole screen to markup the author wrote. Its own
  // elements are kept and are not drawn, so the canvas must not offer hit-boxes
  // for them: dragging a box the panel will never render is a gesture that
  // silently does nothing.
  const isCustomPage = page.render_mode === "custom" && !!page.custom_file;

  // The container tree the hit-box overlay mirrors. An element whose named
  // parent is missing renders at page level rather than vanishing — the
  // validator flags it, the canvas still shows it.
  const { topLevel, childrenByParent } = useMemo(() => {
    if (isCustomPage) return { topLevel: [], childrenByParent: new Map() };
    const ids = new Set(page.elements.map((e) => e.id));
    const byParent = new Map<string, typeof page.elements>();
    const roots: typeof page.elements = [];
    for (const el of page.elements) {
      if (el.parent && ids.has(el.parent) && el.parent !== el.id) {
        const list = byParent.get(el.parent);
        if (list) list.push(el);
        else byParent.set(el.parent, [el]);
      } else {
        roots.push(el);
      }
    }
    return { topLevel: roots, childrenByParent: byParent };
  }, [page.elements, isCustomPage]);

  // A finished marquee is followed by a click on the canvas, and the canvas
  // clears the selection on click -- so without this the band selects a row and
  // then immediately unselects it.
  const swallowNextClick = useRef(false);

  const handleCanvasClick = useCallback(() => {
    if (previewMode) return;
    if (swallowNextClick.current) {
      swallowNextClick.current = false;
      return;
    }
    selectElement(null);
    selectMasterElement(null);
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

  // --- Marquee: drag on empty canvas to select everything it touches ---
  //
  // Touches, not encloses. Sweeping a band across a row of buttons should take
  // the row -- an integrator should not have to lasso every control completely
  // to pick it up. Shift adds to the selection instead of replacing it.

  const [marquee, setMarquee] = useState<Placement | null>(null);
  const cleanupMarqueeRef = useRef<(() => void) | null>(null);

  /** Below this much travel it was a click, and a click clears the selection. */
  const MARQUEE_THRESHOLD_PX = 3;

  const handleOverlayPointerDown = useCallback(
    (e: React.PointerEvent) => {
      // Only from bare canvas: a pointer-down that landed on an element (even a
      // locked one, which does not start a drag) is not a marquee.
      if (previewMode || e.button !== 0 || e.target !== e.currentTarget) return;
      const rect = overlayRef.current?.getBoundingClientRect();
      if (!rect || !(rect.width > 0) || !(rect.height > 0)) return;

      cleanupMarqueeRef.current?.();
      const startX = e.clientX;
      const startY = e.clientY;
      const baseSelection = e.shiftKey
        ? useUIBuilderStore.getState().selectedElementIds
        : [];
      let live: Placement | null = null;

      const toRect = (ev: PointerEvent): Placement => {
        const x0 = ((startX - rect.left) / rect.width) * 100;
        const y0 = ((startY - rect.top) / rect.height) * 100;
        const x1 = ((ev.clientX - rect.left) / rect.width) * 100;
        const y1 = ((ev.clientY - rect.top) / rect.height) * 100;
        return { x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) };
      };

      const onMove = (ev: PointerEvent) => {
        if (
          !live &&
          Math.abs(ev.clientX - startX) < MARQUEE_THRESHOLD_PX &&
          Math.abs(ev.clientY - startY) < MARQUEE_THRESHOLD_PX
        ) {
          return;
        }
        live = toRect(ev);
        setMarquee(live);
      };

      const cleanup = () => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("keydown", onKey);
        cleanupMarqueeRef.current = null;
      };

      const onUp = () => {
        cleanup();
        setMarquee(null);
        if (!live) return;
        swallowNextClick.current = true;
        // A locked element cannot be picked up on the canvas, so a band that
        // sweeps over one leaves it alone rather than quietly selecting it.
        const hits = elementsIntersectingRect(page, live, activeLayoutId).filter(
          (id) => !lockedElementIds.has(id),
        );
        if (!hits.length && !baseSelection.length) {
          selectElement(null);
          return;
        }
        const merged = [...baseSelection];
        for (const id of hits) if (!merged.includes(id)) merged.push(id);
        selectElements(merged);
      };

      const onKey = (ev: KeyboardEvent) => {
        if (ev.key !== "Escape") return;
        cleanup();
        live = null;
        setMarquee(null);
      };

      cleanupMarqueeRef.current = cleanup;
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
      document.addEventListener("keydown", onKey);
    },
    [previewMode, page, activeLayoutId, lockedElementIds, selectElement, selectElements],
  );

  // A band still being dragged when the canvas unmounts would leave document
  // listeners holding a dead closure.
  useEffect(() => () => cleanupMarqueeRef.current?.(), []);

  // --- Gestures: free drag and 8-handle resize, both in percentages ---
  //
  // Geometry reaches the store ONCE, on pointer-up. Everything in between is
  // local state plus a live message to the iframe, so a drag costs one save
  // rather than one per frame — the same budget the dnd-kit end-of-drag commit
  // used to hold, kept by construction rather than by tuning.

  const [gesture, setGesture] = useState<LiveGesture | null>(null);
  const gestureRef = useRef<LiveGesture | null>(null);
  const cleanupGestureRef = useRef<(() => void) | null>(null);

  const previewRaf = useRef<number | null>(null);
  const pendingPreview = useRef<Record<string, Placement> | null>(null);
  const pushLivePreview = useCallback((next: Record<string, Placement>) => {
    pendingPreview.current = next;
    if (previewRaf.current !== null) return;
    previewRaf.current = requestAnimationFrame(() => {
      previewRaf.current = null;
      const payload = pendingPreview.current;
      pendingPreview.current = null;
      if (!payload) return;
      iframeRef.current?.contentWindow?.postMessage(
        { type: "openavc:editor-placements", placements: payload },
        "*",
      );
    });
  }, []);

  const commitPlacements = useCallback(
    (next: Record<string, Placement>, kind: "move" | "resize") => {
      if (!project) return;
      pushUndo({ pages: project.ui.pages }, kind === "move" ? "Move element" : "Resize element");
      update({
        ui: {
          ...project.ui,
          // A MOVE says where it landed and what it landed in, in one mutation:
          // dragging a control onto a container is how anyone expects to put it
          // inside one. A resize only ever reshapes the box you grabbed --
          // shrinking a button until it happens to fit inside a frame behind it
          // is not a request to join that frame.
          pages:
            kind === "move"
              ? commitGesturePlacements(project.ui.pages, page.id, next, activeLayoutId)
              : moveElementsInPage(project.ui.pages, page.id, next, activeLayoutId),
        },
      });
      touchMutation();
    },
    [project, page.id, activeLayoutId, pushUndo, update, touchMutation],
  );

  const beginGesture = useCallback(
    (elementId: string, kind: "move" | "resize", direction: string, e: React.PointerEvent) => {
      if (previewMode || !project) return;
      e.stopPropagation();
      e.preventDefault();
      cleanupGestureRef.current?.();

      const startX = e.clientX;
      const startY = e.clientY;
      const store = useUIBuilderStore.getState();

      // A move carries the whole selection; a resize only ever reshapes the
      // element whose handle you grabbed.
      const selection =
        kind === "move" && store.selectedElementIds.includes(elementId)
          ? store.selectedElementIds
          : [elementId];
      // A container already carries its children (their percentages are of
      // it), so writing both moves the child twice. Easy to hit now a marquee
      // can sweep a container and its contents in one gesture.
      const moving = topmostSelection(page, selection).filter(
        (id) => !lockedElementIds.has(id),
      );
      if (!moving.length) return;

      // Whichever box actually moves drives the gesture. Grab a child while
      // its container is also selected and the container is what travels, so
      // the container is what the snapping and the guides are measured on.
      const anchorId = (() => {
        if (moving.includes(elementId)) return elementId;
        const byId = new Map(page.elements.map((el) => [el.id, el]));
        const seen = new Set<string>([elementId]);
        let cursor = byId.get(elementId)?.parent ?? null;
        while (cursor && byId.has(cursor) && !seen.has(cursor)) {
          if (moving.includes(cursor)) return cursor;
          seen.add(cursor);
          cursor = byId.get(cursor)?.parent ?? null;
        }
        return null;
      })();
      if (!anchorId) return;

      // Percentages are of the PARENT box, so each element converts pixels
      // against its own: the same travel is four times as many percent inside
      // a quarter-page container as it is on the page.
      const parentRects = new Map<string, DOMRect>();
      const startBoxes: Record<string, Placement> = {};
      for (const id of moving) {
        const node = overlayRef.current?.querySelector(`[data-canvas-element="${CSS.escape(id)}"]`);
        const rect = (node?.parentElement ?? overlayRef.current)?.getBoundingClientRect();
        if (rect) parentRects.set(id, rect);
        startBoxes[id] = getPlacement(page, id, activeLayoutId);
      }

      const primaryRect = parentRects.get(anchorId);
      if (!primaryRect || !(primaryRect.width > 0) || !(primaryRect.height > 0)) return;

      const element = page.elements.find((el) => el.id === anchorId);
      const lock =
        typeof element?.aspect_lock === "number" && element.aspect_lock > 0
          ? element.aspect_lock
          : null;

      // Siblings under the same parent are what edges snap to — a box in
      // another container is not a line this one should stick to.
      const movingSet = new Set(moving);
      const parentId = element?.parent ?? null;
      const siblings = page.elements
        .filter((el) => (el.parent ?? null) === parentId && !movingSet.has(el.id))
        .map((el) => placements[el.id])
        .filter((p): p is Placement => !!p);

      const apply = (ev: PointerEvent | KeyboardEvent, dx: number, dy: number) => {
        const bypass = ev.altKey;
        const constrain = ev.shiftKey;
        const next: Record<string, Placement> = {};
        let guidesX: number[] = [];
        let guidesY: number[] = [];

        if (kind === "move") {
          // Shift locks the drag to whichever axis has travelled further —
          // the standard "keep it in line" modifier.
          let px = dx;
          let py = dy;
          if (constrain) {
            if (Math.abs(dx) >= Math.abs(dy)) py = 0;
            else px = 0;
          }
          const start = startBoxes[anchorId];
          const raw = {
            ...start,
            x: start.x + (px / primaryRect.width) * 100,
            y: start.y + (py / primaryRect.height) * 100,
          };
          const snapped = snapMove(raw, { snap, bypass, others: siblings });
          guidesX = snapped.guidesX;
          guidesY = snapped.guidesY;
          // The primary decides where the selection lands; everyone else
          // travels by the same amount so the group keeps its shape.
          const deltaX = snapped.placement.x - start.x;
          const deltaY = snapped.placement.y - start.y;
          for (const id of moving) {
            const rect = parentRects.get(id) ?? primaryRect;
            const b = startBoxes[id];
            if (id === anchorId) {
              next[id] = snapped.placement;
            } else {
              // Convert the primary's percentage travel back to pixels, then
              // into this element's own parent's percentages.
              next[id] = roundPlacement({
                ...b,
                x: b.x + ((deltaX / 100) * primaryRect.width / rect.width) * 100,
                y: b.y + ((deltaY / 100) * primaryRect.height / rect.height) * 100,
              });
            }
          }
        } else {
          const start = startBoxes[anchorId];
          const raw = { ...start };
          const ddx = (dx / primaryRect.width) * 100;
          const ddy = (dy / primaryRect.height) * 100;
          if (direction.includes("e")) raw.w = start.w + ddx;
          if (direction.includes("w")) {
            raw.x = start.x + ddx;
            raw.w = start.w - ddx;
          }
          if (direction.includes("s")) raw.h = start.h + ddy;
          if (direction.includes("n")) {
            raw.y = start.y + ddy;
            raw.h = start.h - ddy;
          }
          raw.w = Math.max(MIN_ELEMENT_SIZE, raw.w);
          raw.h = Math.max(MIN_ELEMENT_SIZE, raw.h);

          const snapped = snapResize(raw, direction, { snap, bypass, others: siblings });
          const box = { ...snapped.placement };
          guidesX = snapped.guidesX;
          guidesY = snapped.guidesY;

          // An aspect-locked element keeps its ratio while you resize it, and
          // shift asks any element to keep the ratio it started with. The
          // ratio is in PIXELS, so converting it back to percentages has to go
          // through the parent's own proportions.
          const ratio =
            lock ??
            (constrain && start.h > 0
              ? ((start.w / 100) * primaryRect.width) / ((start.h / 100) * primaryRect.height)
              : null);
          if (ratio) {
            const drivenByWidth = direction.includes("e") || direction.includes("w");
            if (drivenByWidth) {
              const hPx = ((box.w / 100) * primaryRect.width) / ratio;
              const newH = (hPx / primaryRect.height) * 100;
              if (direction.includes("n")) box.y += box.h - newH;
              box.h = Math.max(MIN_ELEMENT_SIZE, newH);
            } else {
              const wPx = ((box.h / 100) * primaryRect.height) * ratio;
              const newW = (wPx / primaryRect.width) * 100;
              if (direction.includes("w")) box.x += box.w - newW;
              box.w = Math.max(MIN_ELEMENT_SIZE, newW);
            }
          }
          next[anchorId] = roundPlacement(box);
        }

        // What this drag would put the element into, live. Only a move can
        // change a parent -- a resize reshapes the box you grabbed and nothing
        // else -- and only a change is worth drawing.
        let adoptInto: string | null = null;
        if (kind === "move") {
          const landed = next[anchorId];
          if (landed) {
            const target = dropTargetFor(
              page,
              anchorId,
              toPageBox(landed, parentId ? absolute[parentId] : null),
              activeLayoutId,
            );
            if (target !== parentId) adoptInto = target;
          }
        }

        const live: LiveGesture = { kind, placements: next, guidesX, guidesY, adoptInto };
        gestureRef.current = live;
        setGesture(live);
        pushLivePreview(next);
      };

      let lastEvent: PointerEvent | null = null;
      const onMove = (ev: PointerEvent) => {
        lastEvent = ev;
        apply(ev, ev.clientX - startX, ev.clientY - startY);
      };

      const cleanup = () => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("keydown", onKey);
        // keyup too — a leaked keyup handler re-runs the snap math after the
        // commit, so releasing Alt after the drop visibly re-snapped the
        // element while the store kept the bypassed position.
        document.removeEventListener("keyup", onKey);
        cleanupGestureRef.current = null;
      };

      const revert = () => {
        pushLivePreview(startBoxes);
        gestureRef.current = null;
        setGesture(null);
      };

      const onUp = () => {
        const finished = gestureRef.current;
        cleanup();
        gestureRef.current = null;
        setGesture(null);
        if (!finished) return;
        const changed = Object.entries(finished.placements).some(([id, p]) => {
          const before = startBoxes[id];
          return !before || before.x !== p.x || before.y !== p.y || before.w !== p.w || before.h !== p.h;
        });
        if (changed) {
          commitPlacements(finished.placements, kind);
        } else {
          // Nothing moved, but the iframe has been told about intermediate
          // boxes — put it back where the store says it is.
          pushLivePreview(startBoxes);
        }
      };

      const onKey = (ev: KeyboardEvent) => {
        if (ev.key === "Escape") {
          cleanup();
          revert();
          return;
        }
        // Alt and Shift change the answer mid-gesture, so re-run the last
        // pointer position through the new modifiers rather than waiting for
        // the pointer to twitch.
        if ((ev.key === "Alt" || ev.key === "Shift") && lastEvent) {
          apply(ev, lastEvent.clientX - startX, lastEvent.clientY - startY);
        }
      };

      cleanupGestureRef.current = cleanup;
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
      document.addEventListener("keydown", onKey);
      document.addEventListener("keyup", onKey);
    },
    [previewMode, project, page, placements, absolute, snap, activeLayoutId,
     commitPlacements, pushLivePreview],
  );

  // A gesture in flight when the canvas unmounts (page switched, project
  // reloaded) would otherwise leave document listeners holding a dead closure.
  useEffect(() => () => cleanupGestureRef.current?.(), []);

  // Where a hit box draws: the live box while it is being dragged, the stored
  // one otherwise.
  const placementFor = useCallback(
    (id: string): Placement =>
      gesture?.placements[id] ?? placements[id] ?? { x: 0, y: 0, w: 25, h: 12.5 },
    [gesture, placements],
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

  // Overlay/sidebar pages use their configured box, which is a percentage of
  // the viewport now rather than raw px.
  //
  // EDIT mode only. Authoring a dialog means seeing its contents at true size,
  // so the canvas becomes the dialog. PREVIEW means behaving like the runtime,
  // where a dialog floats over a full screen with a backdrop behind it -- so
  // preview keeps the whole screen and lets the panel draw the overlay itself.
  // Shrinking it here was half of why a working Cancel button looked broken:
  // the preview never rendered an overlay at all, it rendered the dialog's page
  // flat inside a dialog-sized box.
  const pageType = page.page_type || "page";
  const isOverlay = pageType === "overlay" || pageType === "sidebar";
  const boxedOverlay = isOverlay && !previewMode;
  const overlayWidth = boxedOverlay
    ? Math.round((((page.overlay?.width ?? (pageType === "sidebar" ? 25 : 31.25)) / 100) * screenWidth))
    : screenWidth;
  const overlayHeight = boxedOverlay
    ? (pageType === "sidebar"
        ? screenHeight
        : Math.round((((page.overlay?.height ?? 37.5) / 100) * screenHeight)))
    : screenHeight;

  // Type scales with vmin, which on real glass resolves against the SCREEN —
  // including inside an overlay, where screen-sized text is exactly right. The
  // builder sizes an overlay preview to the overlay box, so without this the
  // preview renders type against a 400x300 iframe and lands ~2.7x too small.
  // This is the one place preview and runtime can silently disagree.
  const previewVmin = useMemo(
    () => Math.min(screenWidth, screenHeight) / 100,
    [screenWidth, screenHeight],
  );

  useEffect(() => {
    vminRef.current = previewVmin;
    iframeRef.current?.contentWindow?.postMessage(
      { type: "openavc:editor-page", pageId: page.id, vmin: previewVmin },
      "*",
    );
  }, [previewVmin, page.id]);

  const tunnelPrefix = getTunnelPrefix();
  // Trailing slash on /panel/ so the StaticFiles mount serves index.html
  // directly. Without it the mount 307-redirects /panel -> /panel/, and that
  // redirect's absolute localhost Location does not survive the cloud tunnel
  // (the iframe ends up pointing at http://localhost and fails to load).
  const iframeSrc = previewMode
    ? `${tunnelPrefix}/panel/?page=${encodeURIComponent(page.id)}`
    : `${tunnelPrefix}/panel/?page=${encodeURIComponent(page.id)}&edit=1`;

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
            onPointerDown={handleOverlayPointerDown}
            onContextMenu={handleBackgroundContextMenu}
            style={{
              position: "absolute",
              inset: 0,
              pointerEvents: "auto",
              overflow: "visible",
              borderRadius: "inherit",
            }}
          >
            {/* The snap increment itself is drawn INSIDE the iframe (panel.js
                renders it behind the elements so dropped controls aren't
                see-through); this is just the readout. */}
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
                {snap.enabled
                  ? `snap ${snap.x.toFixed(2)}% × ${snap.y.toFixed(2)}%`
                  : "snap off"}
              </div>
            )}

            {/* Master element hit-boxes (selection + badge, iframe renders the pixels) */}
            {(masterElements || [])
              .filter((m) => m.pages === "*" || (Array.isArray(m.pages) && m.pages.includes(page.id)))
              .map((el) => {
                // The same box the panel would draw at this shape of glass, so
                // the hit-box sits on top of the pixels rather than beside them
                // the moment a portrait arrangement is being authored.
                const box = masterPlacement(el, canvasOrientation);
                if (!box) return null;
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
                      position: "absolute",
                      left: `${box.x}%`,
                      top: `${box.y}%`,
                      width: `${box.w}%`,
                      height: `${box.h}%`,
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

            {/* Element hit-boxes (selection + drag + resize, iframe renders the
                pixels). Rendered as a tree: a container's children are absolute
                percentages OF IT, exactly like the runtime, so a hit box lands
                on the control it belongs to however deep it sits. */}
            {topLevel.map((el) => (
              <CanvasElement
                key={el.id}
                element={el}
                childrenByParent={childrenByParent}
                placementFor={placementFor}
                selectedIds={selectedElementIds}
                previewMode={false}
                warningsByElement={warningsByElement}
                lockedIds={lockedElementIds}
                hiddenIds={hiddenIds}
                gestureKind={gesture?.kind ?? null}
                adoptTargetId={gesture?.adoptInto ?? paletteDragPreview?.adoptInto ?? null}
                onSelect={(id, shiftKey) => (shiftKey ? toggleSelectElement(id) : selectElement(id))}
                onGestureStart={beginGesture}
                onContextMenu={handleContextMenu}
              />
            ))}

            {/* Live smart-guides — the lines a gesture is actually stuck to. */}
            {gesture && (
              <SnapGuides guidesX={gesture.guidesX} guidesY={gesture.guidesY} />
            )}

            {/* A palette drag, previewed with the same footprint, guides and
                snap the drop will commit — the box drawn here IS the landing
                spot. */}
            {paletteDragPreview && (
              <>
                <div
                  style={{
                    position: "absolute",
                    left: `${paletteDragPreview.placement.x}%`,
                    top: `${paletteDragPreview.placement.y}%`,
                    width: `${paletteDragPreview.placement.w}%`,
                    height: `${paletteDragPreview.placement.h}%`,
                    opacity: 0.85,
                    pointerEvents: "none",
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
                    zIndex: 95,
                  }}
                >
                  {paletteDragPreview.label.replace(/_/g, " ")}
                </div>
                <SnapGuides
                  guidesX={paletteDragPreview.guidesX}
                  guidesY={paletteDragPreview.guidesY}
                />
              </>
            )}

            {/* The rubber band itself. */}
            {marquee && (
              <div
                data-canvas-marquee=""
                style={{
                  position: "absolute",
                  left: `${marquee.x}%`,
                  top: `${marquee.y}%`,
                  width: `${marquee.w}%`,
                  height: `${marquee.h}%`,
                  border: "1px solid var(--accent)",
                  background: "rgba(33, 150, 243, 0.12)",
                  pointerEvents: "none",
                  zIndex: 90,
                }}
              />
            )}

            {/* Empty state */}
            {page.elements.length === 0 && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
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
        )}
      </div>
    </div>
  );
}

/**
 * The lines a gesture is currently magnetised to.
 *
 * This is what "SnapGuides gains magnetism" means: it used to compute exact
 * edge matches and draw hairlines that attracted nothing. Now the snapping is
 * done by the gesture and these are simply the answer, drawn.
 */
function SnapGuides({ guidesX, guidesY }: { guidesX: number[]; guidesY: number[] }) {
  if (!guidesX.length && !guidesY.length) return null;
  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 100 }}>
      {guidesX.map((x) => (
        <div
          key={`v-${x}`}
          style={{
            position: "absolute",
            left: `${x}%`,
            top: 0,
            bottom: 0,
            width: 1,
            background: "rgba(33, 150, 243, 0.9)",
          }}
        />
      ))}
      {guidesY.map((y) => (
        <div
          key={`h-${y}`}
          style={{
            position: "absolute",
            top: `${y}%`,
            left: 0,
            right: 0,
            height: 1,
            background: "rgba(33, 150, 243, 0.9)",
          }}
        />
      ))}
    </div>
  );
}
