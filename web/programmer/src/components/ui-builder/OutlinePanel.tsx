import { useState, useMemo } from "react";
import {
  MousePointerClick, SlidersHorizontal, ChevronDown, TextCursorInput,
  Type, Circle, Image, Square, ArrowRight, Camera, Gauge, BarChart3,
  SlidersVertical, Group, Clock, Grid3X3, LayoutGrid, List, Puzzle,
  Search, Star, Eye, EyeOff, Lock, Unlock, ChevronUp, ChevronDown as ChDown,
} from "lucide-react";
import type { UIElement, MasterElement } from "../../api/types";

const ICONS: Record<string, React.ReactNode> = {
  button: <MousePointerClick size={12} />,
  slider: <SlidersHorizontal size={12} />,
  fader: <SlidersVertical size={12} />,
  select: <ChevronDown size={12} />,
  text_input: <TextCursorInput size={12} />,
  label: <Type size={12} />,
  status_led: <Circle size={12} />,
  image: <Image size={12} />,
  spacer: <Square size={12} />,
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
};

interface OutlinePanelProps {
  elements: UIElement[];
  masterElements: MasterElement[];
  selectedElementIds: string[];
  selectedMasterElementId: string | null;
  lockedElementIds: Set<string>;
  hiddenElementIds: Set<string>;
  onSelectElement: (id: string, shift?: boolean) => void;
  onSelectMasterElement: (id: string) => void;
  onMoveOrder: (elementId: string, direction: "up" | "down") => void;
  onToggleLock: (elementId: string) => void;
  onToggleHide: (elementId: string) => void;
}

export function OutlinePanel({
  elements,
  masterElements,
  selectedElementIds,
  selectedMasterElementId,
  lockedElementIds,
  hiddenElementIds,
  onSelectElement,
  onSelectMasterElement,
  onMoveOrder,
  onToggleLock,
  onToggleHide,
}: OutlinePanelProps) {
  const [search, setSearch] = useState("");
  const searchLower = search.toLowerCase();

  const filteredElements = useMemo(() => {
    if (!searchLower) return elements;
    return elements.filter(
      (el) => el.id.toLowerCase().includes(searchLower) ||
        el.type.toLowerCase().includes(searchLower) ||
        (el.label || "").toLowerCase().includes(searchLower),
    );
  }, [elements, searchLower]);

  const filteredMasters = useMemo(() => {
    if (!searchLower) return masterElements;
    return masterElements.filter(
      (el) => el.id.toLowerCase().includes(searchLower) ||
        el.type.toLowerCase().includes(searchLower) ||
        (el.label || "").toLowerCase().includes(searchLower),
    );
  }, [masterElements, searchLower]);

  const hasBindings = (el: UIElement) => {
    return Object.values(el.bindings || {}).some(
      (v) => v && typeof v === "object" && Object.keys(v as object).length > 0,
    );
  };

  const iconBtnStyle: React.CSSProperties = {
    display: "flex", padding: 1, background: "transparent", border: "none",
    cursor: "pointer", borderRadius: 2, flexShrink: 0,
  };

  const renderRow = (el: UIElement, isMaster: boolean, idx: number, total: number) => {
    const isSelected = isMaster
      ? selectedMasterElementId === el.id
      : selectedElementIds.includes(el.id);
    const isLocked = lockedElementIds.has(el.id);
    const isHidden = hiddenElementIds.has(el.id);
    const displayLabel = el.label || el.text || "";
    const icon = ICONS[el.type] || <Square size={12} />;

    return (
      <div
        key={el.id}
        onClick={(e) => {
          if (isMaster) {
            onSelectMasterElement(el.id);
          } else {
            onSelectElement(el.id, e.shiftKey);
          }
        }}
        style={{
          display: "flex", alignItems: "center", gap: 4,
          padding: "3px 8px", cursor: "pointer", fontSize: 11,
          borderRadius: 3, userSelect: "none",
          background: isSelected ? "var(--accent-dim)" : "transparent",
          color: isHidden ? "var(--text-muted)" : isSelected ? "var(--accent)" : "var(--text-primary)",
          borderLeft: isSelected ? "2px solid var(--accent)" : "2px solid transparent",
          opacity: isHidden ? 0.5 : 1,
        }}
        onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)"; }}
        onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
        title={`${el.id} (${el.type}) — ${el.grid_area.col_span}×${el.grid_area.row_span} at col ${el.grid_area.col}, row ${el.grid_area.row}`}
      >
        <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>{icon}</span>
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {el.id}
        </span>
        {displayLabel && (
          <span style={{ color: "var(--text-muted)", fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 50 }}>
            {displayLabel}
          </span>
        )}
        {!hasBindings(el) && ["button", "slider", "fader", "select", "text_input", "keypad"].includes(el.type) && (
          <span style={{ color: "#ff9800", fontSize: 9, flexShrink: 0 }} title="No bindings">!</span>
        )}
        {isMaster && (
          <Star size={10} style={{ color: "var(--accent)", flexShrink: 0 }} />
        )}
        {/* Z-order buttons (page elements only) */}
        {!isMaster && isSelected && (
          <>
            <button
              onClick={(e) => { e.stopPropagation(); onMoveOrder(el.id, "up"); }}
              disabled={idx === 0}
              style={{ ...iconBtnStyle, color: idx === 0 ? "var(--border-color)" : "var(--text-muted)" }}
              title="Move backward (lower z-order)"
            >
              <ChevronUp size={10} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onMoveOrder(el.id, "down"); }}
              disabled={idx === total - 1}
              style={{ ...iconBtnStyle, color: idx === total - 1 ? "var(--border-color)" : "var(--text-muted)" }}
              title="Move forward (higher z-order)"
            >
              <ChDown size={10} />
            </button>
          </>
        )}
        {/* Lock toggle */}
        <button
          onClick={(e) => { e.stopPropagation(); onToggleLock(el.id); }}
          style={{ ...iconBtnStyle, color: isLocked ? "var(--accent)" : "var(--border-color)" }}
          title={isLocked ? "Unlock element" : "Lock element (prevent selection on canvas)"}
        >
          {isLocked ? <Lock size={10} /> : <Unlock size={10} />}
        </button>
        {/* Hide toggle */}
        <button
          onClick={(e) => { e.stopPropagation(); onToggleHide(el.id); }}
          style={{ ...iconBtnStyle, color: isHidden ? "var(--color-warning)" : "var(--border-color)" }}
          title={isHidden ? "Show element on canvas" : "Hide element on canvas"}
        >
          {isHidden ? <EyeOff size={10} /> : <Eye size={10} />}
        </button>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", fontSize: 12 }}>
      {/* Search */}
      <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border-color)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--border-color)", background: "var(--bg-base)" }}>
          <Search size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search elements..."
            style={{ flex: 1, border: "none", background: "transparent", outline: "none", fontSize: 11, color: "var(--text-primary)" }}
          />
          {search && (
            <button onClick={() => setSearch("")} style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 0 }}>
              ×
            </button>
          )}
        </div>
      </div>

      {/* Element list */}
      <div style={{ flex: 1, overflowY: "auto", padding: "4px 0" }}>
        {filteredMasters.length > 0 && (
          <>
            <div style={{ padding: "4px 8px", fontSize: 10, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Master Elements
            </div>
            {filteredMasters.map((el, i) => renderRow(el, true, i, filteredMasters.length))}
            <div style={{ height: 1, margin: "4px 8px", background: "var(--border-color)" }} />
          </>
        )}

        <div style={{ padding: "4px 8px", fontSize: 10, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Elements ({filteredElements.length})
        </div>
        {filteredElements.length === 0 ? (
          <div style={{ padding: "8px 12px", color: "var(--text-muted)", fontSize: 11, fontStyle: "italic" }}>
            {search ? "No matching elements" : "No elements on this page"}
          </div>
        ) : (
          filteredElements.map((el, i) => renderRow(el, false, i, filteredElements.length))
        )}
      </div>
    </div>
  );
}
