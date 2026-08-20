import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import {
  MousePointerClick, SlidersHorizontal, ChevronDown, TextCursorInput,
  Type, Circle, Image, Square, ArrowRight, Camera, Gauge, BarChart3,
  SlidersVertical, Group, Clock, Grid3X3, LayoutGrid, List, Puzzle,
  Search, Lock, Unlock, Eye, EyeOff, ChevronUp, ChevronDown as ChDown, ChevronRight,
  Star, Layers, Code,
} from "lucide-react";
import type { UIPage, UIElement, MasterElement } from "../../api/types";
import { outlineRows, outlineDropParent } from "./uiBuilderHelpers";

const ICONS: Record<string, React.ReactNode> = {
  button: <MousePointerClick size={12} />,
  slider: <SlidersHorizontal size={12} />,
  fader: <SlidersVertical size={12} />,
  select: <ChevronDown size={12} />,
  text_input: <TextCursorInput size={12} />,
  label: <Type size={12} />,
  status_led: <Circle size={12} />,
  image: <Image size={12} />,
  page_nav: <ArrowRight size={12} />,
  camera_preset: <Camera size={12} />,
  gauge: <Gauge size={12} />,
  level_meter: <BarChart3 size={12} />,
  group: <Group size={12} />,
  clock: <Clock size={12} />,
  matrix: <Grid3X3 size={12} />,
  keypad: <LayoutGrid size={12} />,
  list: <List size={12} />,
  plugin: <Puzzle size={12} />,
  custom: <Code size={12} />,
};

/** Below this much travel it was a click, and a click selects. */
const DRAG_THRESHOLD_PX = 4;

/** A row being dragged onto another row. */
interface OutlineDrag {
  id: string;
  x: number;
  y: number;
  /** The row under the pointer: an element id, `null` for the page-level zone,
   *  or `undefined` when the pointer has left the panel entirely. */
  over: string | null | undefined;
  /** Where the drop would put it, or `undefined` when it is refused. */
  parentId: string | null | undefined;
}

interface OutlinePanelProps {
  page: UIPage | null;
  masterElements: MasterElement[];
  selectedElementIds: string[];
  selectedMasterElementId: string | null;
  lockedElementIds: Set<string>;
  /** Elements the arrangement being authored leaves out. Per-layout, so the
   *  same element can be hidden here and shown in another. */
  hiddenElementIds: Set<string>;
  /** Containers the author has folded shut. A view preference, not project
   *  data, so it lives in the builder store rather than the .avc. */
  collapsedIds: string[];
  onToggleCollapse: (id: string) => void;
  onSelectElement: (id: string, shift?: boolean) => void;
  onSelectMasterElement: (id: string) => void;
  onMoveOrder: (elementId: string, neighborId: string) => void;
  onToggleLock: (elementId: string) => void;
  onToggleHidden: (elementId: string) => void;
  onReparent: (elementId: string, newParentId: string | null) => void;
}

export function OutlinePanel({
  page,
  masterElements,
  selectedElementIds,
  selectedMasterElementId,
  lockedElementIds,
  hiddenElementIds,
  collapsedIds,
  onToggleCollapse,
  onSelectElement,
  onSelectMasterElement,
  onMoveOrder,
  onToggleLock,
  onToggleHidden,
  onReparent,
}: OutlinePanelProps) {
  const [search, setSearch] = useState("");
  const searchLower = search.toLowerCase();
  const elements = useMemo(() => page?.elements ?? [], [page]);

  const matches = (el: UIElement) =>
    el.id.toLowerCase().includes(searchLower) ||
    el.type.toLowerCase().includes(searchLower) ||
    (el.label || "").toLowerCase().includes(searchLower);

  const matchIds = useMemo(() => {
    if (!searchLower) return null;
    return new Set(elements.filter(matches).map((el) => el.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements, searchLower]);

  const rows = useMemo(
    () => outlineRows(elements, { collapsed: collapsedIds, matchIds }),
    [elements, collapsedIds, matchIds],
  );

  const elementById = useMemo(
    () => new Map(elements.map((el) => [el.id, el])),
    [elements],
  );

  const filteredMasters = useMemo(() => {
    if (!searchLower) return masterElements;
    return masterElements.filter(matches);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [masterElements, searchLower]);

  // --- Drag to reparent ---
  //
  // Pointer events rather than HTML5 drag-and-drop, matching how the canvas
  // does its gestures: the palette is already a dnd-kit draggable and a second
  // drag system in the same tree is a fight nobody wins.

  const [drag, setDrag] = useState<OutlineDrag | null>(null);
  const dragRef = useRef<OutlineDrag | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const pageRef = useRef(page);
  useEffect(() => { pageRef.current = page; }, [page]);

  // A drag in flight when the panel unmounts (tab switched, page changed) would
  // leave document listeners holding a dead closure.
  useEffect(() => () => cleanupRef.current?.(), []);

  const resolveTarget = useCallback((clientX: number, clientY: number) => {
    const node = document.elementFromPoint(clientX, clientY);
    const row = node?.closest?.("[data-outline-row]") as HTMLElement | null;
    if (row) return row.dataset.outlineRow ?? null;
    // Anywhere else inside the list -- the header, the blank space under the
    // last row -- means page level. Masters carry no parent, so their block is
    // not a drop zone at all.
    if (node?.closest?.("[data-outline-masters]")) return undefined;
    return node?.closest?.("[data-outline-list]") ? null : undefined;
  }, []);

  const beginDrag = useCallback(
    (elementId: string, e: React.PointerEvent) => {
      if (e.button !== 0) return;
      if ((e.target as HTMLElement)?.closest?.("button")) return;
      if (lockedElementIds.has(elementId)) return;
      cleanupRef.current?.();

      const startX = e.clientX;
      const startY = e.clientY;
      let started = false;

      const update = (ev: PointerEvent) => {
        const current = pageRef.current;
        const over = resolveTarget(ev.clientX, ev.clientY);
        const parentId =
          current && over !== undefined
            ? outlineDropParent(current, elementId, over)
            : undefined;
        const next: OutlineDrag = { id: elementId, x: ev.clientX, y: ev.clientY, over, parentId };
        dragRef.current = next;
        setDrag(next);
      };

      const onMove = (ev: PointerEvent) => {
        if (
          !started &&
          Math.abs(ev.clientX - startX) < DRAG_THRESHOLD_PX &&
          Math.abs(ev.clientY - startY) < DRAG_THRESHOLD_PX
        ) {
          return;
        }
        started = true;
        update(ev);
      };

      const cleanup = () => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("keydown", onKey);
        cleanupRef.current = null;
        dragRef.current = null;
        setDrag(null);
      };

      const onUp = () => {
        const finished = dragRef.current;
        cleanup();
        if (!finished || finished.parentId === undefined) return;
        const element = pageRef.current?.elements.find((el) => el.id === finished.id);
        if (!element || (element.parent ?? null) === finished.parentId) return;
        onReparent(finished.id, finished.parentId);
      };

      const onKey = (ev: KeyboardEvent) => {
        if (ev.key === "Escape") cleanup();
      };

      cleanupRef.current = cleanup;
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
      document.addEventListener("keydown", onKey);
    },
    [lockedElementIds, onReparent, resolveTarget],
  );

  const hasBindings = (el: UIElement) => {
    return Object.values(el.bindings || {}).some(
      (v) => v && typeof v === "object" && Object.keys(v as object).length > 0,
    );
  };

  const iconBtnStyle: React.CSSProperties = {
    display: "flex", padding: "var(--space-2xs)", background: "transparent", border: "none",
    cursor: "pointer", borderRadius: "var(--radius-sm)", flexShrink: 0,
  };

  const renderRow = (
    el: UIElement,
    isMaster: boolean,
    tree?: { depth: number; hasChildren: boolean; collapsed: boolean; prevId?: string; nextId?: string },
  ) => {
    const isSelected = isMaster
      ? selectedMasterElementId === el.id
      : selectedElementIds.includes(el.id);
    const isLocked = lockedElementIds.has(el.id);
    const isHidden = !isMaster && hiddenElementIds.has(el.id);
    const displayLabel = el.label || el.text || "";
    const icon = ICONS[el.type] || <Square size={12} />;
    const depth = tree?.depth ?? 0;
    const isDragging = drag?.id === el.id;
    const isDropTarget = !!drag && !isMaster && drag.over === el.id;
    const dropRefused = isDropTarget && drag?.parentId === undefined;
    const dropAccepted = isDropTarget && drag?.parentId !== undefined;

    const background = dropRefused
      ? "rgba(244,67,54,0.18)"
      : dropAccepted
        ? "var(--accent-dim)"
        : isSelected
          ? "var(--accent-dim)"
          : "transparent";

    return (
      <div
        key={el.id}
        {...(isMaster ? {} : { "data-outline-row": el.id })}
        onPointerDown={(e) => { if (!isMaster) beginDrag(el.id, e); }}
        onClick={(e) => {
          if (isMaster) {
            onSelectMasterElement(el.id);
          } else {
            onSelectElement(el.id, e.shiftKey);
          }
        }}
        style={{
          display: "flex", alignItems: "center", gap: "var(--space-xs)",
          padding: "var(--space-xs) var(--space-sm)", paddingLeft: 8 + depth * 12,
          cursor: isMaster ? "pointer" : isLocked ? "pointer" : "grab",
          fontSize: "var(--font-size-xs)", borderRadius: "var(--border-radius)", userSelect: "none",
          opacity: isDragging ? 0.45 : isHidden ? 0.55 : 1,
          background,
          color: isSelected ? "var(--accent)" : "var(--text-primary)",
          borderLeft: isSelected ? "2px solid var(--accent)" : "2px solid transparent",
          outline: dropAccepted ? "1px solid var(--accent)" : "none",
          outlineOffset: -1,
        }}
        onMouseEnter={(e) => { if (!isSelected && !drag) e.currentTarget.style.background = "var(--bg-hover)"; }}
        onMouseLeave={(e) => { if (!isSelected && !drag) e.currentTarget.style.background = "transparent"; }}
        title={`${el.id} (${el.type})`}
      >
        {/* Fold/unfold. Held open for anything with nothing in it, so the
            indent column still lines up. */}
        {tree?.hasChildren ? (
          <button
            onClick={(e) => { e.stopPropagation(); onToggleCollapse(el.id); }}
            style={{ ...iconBtnStyle, color: "var(--text-muted)" }}
            title={tree.collapsed ? "Show contents" : "Hide contents"}
          >
            {tree.collapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
          </button>
        ) : (
          !isMaster && <span style={{ width: 15, flexShrink: 0 }} />
        )}
        <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>{icon}</span>
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {el.id}
        </span>
        {displayLabel && (
          <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-2xs)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 50 }}>
            {displayLabel}
          </span>
        )}
        {!isMaster && !hasBindings(el) && ["button", "slider", "fader", "select", "text_input", "keypad"].includes(el.type) && (
          <span style={{ color: "#ff9800", fontSize: "var(--font-size-2xs)", flexShrink: 0 }} title="No bindings">!</span>
        )}
        {isMaster && (
          <Star size={10} style={{ color: "var(--accent)", flexShrink: 0 }} />
        )}
        {/* Z-order buttons. They swap with the neighbour under the SAME parent,
            because inside a container z-order is position among its siblings. */}
        {!isMaster && isSelected && (
          <>
            <button
              onClick={(e) => { e.stopPropagation(); if (tree?.prevId) onMoveOrder(el.id, tree.prevId); }}
              disabled={!tree?.prevId}
              style={{ ...iconBtnStyle, color: !tree?.prevId ? "var(--border-color)" : "var(--text-muted)" }}
              title="Move backward (lower z-order among its neighbours)"
            >
              <ChevronUp size={10} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); if (tree?.nextId) onMoveOrder(el.id, tree.nextId); }}
              disabled={!tree?.nextId}
              style={{ ...iconBtnStyle, color: !tree?.nextId ? "var(--border-color)" : "var(--text-muted)" }}
              title="Move forward (higher z-order among its neighbours)"
            >
              <ChDown size={10} />
            </button>
          </>
        )}
        {/* Per-layout visibility. Not a binding — this is the arrangement
            leaving a control out, and the control is still there in the rest. */}
        {!isMaster && (
          <button
            onClick={(e) => { e.stopPropagation(); onToggleHidden(el.id); }}
            style={{ ...iconBtnStyle, color: isHidden ? "var(--accent)" : "var(--border-color)" }}
            title={isHidden
              ? "Show in this layout"
              : "Hide in this layout (the control stays, and other layouts still show it)"}
          >
            {isHidden ? <EyeOff size={10} /> : <Eye size={10} />}
          </button>
        )}
        {/* Lock toggle */}
        <button
          onClick={(e) => { e.stopPropagation(); onToggleLock(el.id); }}
          style={{ ...iconBtnStyle, color: isLocked ? "var(--accent)" : "var(--border-color)" }}
          title={isLocked ? "Unlock element" : "Lock element (no selecting, dragging or aligning on the canvas; saved with the project)"}
        >
          {isLocked ? <Lock size={10} /> : <Unlock size={10} />}
        </button>
      </div>
    );
  };

  const dragDestination = (() => {
    if (!drag) return null;
    if (drag.parentId === undefined) return "can't drop here";
    if (drag.parentId === null) return "out to page level";
    const container = elementById.get(drag.parentId);
    return `into ${container?.label || drag.parentId}`;
  })();

  const pageZoneActive = !!drag && drag.over === null && drag.parentId !== undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", fontSize: "var(--font-size-sm)" }}>
      {/* Search */}
      <div style={{ padding: "var(--space-sm)", borderBottom: "1px solid var(--border-color)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", padding: "var(--space-xs) var(--space-sm)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", background: "var(--bg-base)" }}>
          <Search size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search elements..."
            style={{ flex: 1, border: "none", background: "transparent", outline: "none", fontSize: "var(--font-size-xs)", color: "var(--text-primary)" }}
          />
          {search && (
            <button onClick={() => setSearch("")} style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 0 }}>
              ×
            </button>
          )}
        </div>
      </div>

      {/* Element list. Everything in here that isn't a row is the page-level
          drop zone, so dragging a control out of a container is a flick at the
          blank space under the list. */}
      <div data-outline-list="" style={{ flex: 1, overflowY: "auto", padding: "var(--space-xs) 0" }}>
        {filteredMasters.length > 0 && (
          <div data-outline-masters="">
            <div style={{ padding: "var(--space-xs) var(--space-sm)", fontSize: "var(--font-size-2xs)", fontWeight: "var(--font-weight-semibold)", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "var(--tracking-wide)" }}>
              Master Elements
            </div>
            {filteredMasters.map((el) => renderRow(el, true))}
            <div style={{ height: 1, margin: "var(--space-xs) var(--space-sm)", background: "var(--border-color)" }} />
          </div>
        )}

        <div
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-xs)",
            padding: "var(--space-xs) var(--space-sm)", fontSize: "var(--font-size-2xs)", fontWeight: "var(--font-weight-semibold)",
            color: pageZoneActive ? "var(--accent)" : "var(--text-muted)",
            textTransform: "uppercase", letterSpacing: "var(--tracking-wide)",
            background: pageZoneActive ? "var(--accent-dim)" : "transparent",
            borderRadius: "var(--border-radius)",
          }}
          title="Drop a control here to take it out of its container"
        >
          <Layers size={11} />
          <span>Page ({rows.length})</span>
        </div>
        {rows.length === 0 ? (
          <div style={{ padding: "var(--space-sm) var(--space-md)", color: "var(--text-muted)", fontSize: "var(--font-size-xs)", fontStyle: "italic" }}>
            {search ? "No matching elements" : "No elements on this page"}
          </div>
        ) : (
          rows.map((row) => {
            const el = elementById.get(row.id);
            if (!el) return null;
            return renderRow(el, false, {
              depth: row.depth,
              hasChildren: row.hasChildren,
              collapsed: row.collapsed,
              prevId: row.prevSiblingId,
              nextId: row.nextSiblingId,
            });
          })
        )}
        {/* Blank tail so there is always somewhere to drop, even on a full page. */}
        <div style={{ minHeight: 28 }} />
      </div>

      {/* What the drop would do, riding the pointer. */}
      {drag && drag.over !== undefined && (
        <div
          style={{
            position: "fixed",
            left: drag.x + 14,
            top: drag.y + 10,
            pointerEvents: "none",
            // A drag ghost lives inside the page, so it stays out of the
            // shared floating ladder in components/shared/layers.ts.
            zIndex: 200,
            padding: "var(--space-xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-xs)",
            whiteSpace: "nowrap",
            background: "var(--bg-surface)",
            border: `1px solid ${drag.parentId === undefined ? "var(--color-error)" : "var(--accent)"}`,
            color: drag.parentId === undefined ? "var(--color-error)" : "var(--text-primary)",
            boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
          }}
        >
          {drag.id} &rarr; {dragDestination}
        </div>
      )}
    </div>
  );
}
