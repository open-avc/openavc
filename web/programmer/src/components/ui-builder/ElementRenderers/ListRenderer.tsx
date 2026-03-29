import { useState, useEffect, useMemo } from "react";
import type { UIElement } from "../../../api/types";
import * as wsClient from "../../../api/wsClient";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

interface ListItem {
  label: string;
  value: string;
}

/**
 * Resolve the list items: prefer state-driven items (from key_pattern binding)
 * in preview mode, fall back to static element.items.
 */
function resolveItems(
  element: UIElement,
  previewMode: boolean,
  liveState: Record<string, unknown>,
): ListItem[] {
  const itemsBinding = element.bindings.items as
    | { source?: string; key_pattern?: string }
    | undefined;

  if (previewMode && itemsBinding?.key_pattern) {
    const pattern = itemsBinding.key_pattern;
    // Convert glob pattern "var.source_list.*" to a prefix match
    const prefix = pattern.replace(/\.\*$/, ".");
    const items: ListItem[] = [];

    for (const key of Object.keys(liveState)) {
      if (key.startsWith(prefix)) {
        const val = liveState[key];
        if (val !== undefined && val !== null) {
          items.push({ label: String(val), value: key.slice(prefix.length) });
        }
      }
    }

    if (items.length > 0) return items;
  }

  return element.items ?? [];
}

/**
 * Resolve the currently selected value(s) from live state.
 */
function resolveSelected(
  element: UIElement,
  previewMode: boolean,
  liveState: Record<string, unknown>,
): Set<string> {
  if (!previewMode) return new Set();

  const selectedBinding = element.bindings.selected as
    | { source?: string; key?: string }
    | undefined;

  if (selectedBinding?.key) {
    const val = liveState[selectedBinding.key];
    if (val !== undefined && val !== null) {
      // Support comma-separated values for multi_select
      const strVal = String(val);
      if (strVal.includes(",")) {
        return new Set(strVal.split(",").map((s) => s.trim()));
      }
      return new Set([strVal]);
    }
  }

  return new Set();
}

const scrollbarStyles = `
  .oavc-list-scroll::-webkit-scrollbar {
    width: 6px;
  }
  .oavc-list-scroll::-webkit-scrollbar-track {
    background: transparent;
  }
  .oavc-list-scroll::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 3px;
  }
  .oavc-list-scroll::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.35);
  }
`;

export function ListRenderer({ element, previewMode, liveState }: Props) {
  const listStyle = (element.list_style as string) || "selectable";
  const itemHeight = element.item_height ?? 44;
  const itemBg = String(element.style.item_bg ?? "#2a2a4e");
  const itemActiveBg = String(element.style.item_active_bg ?? "#42a5f5");
  const borderRadius = element.style.border_radius
    ? `${element.style.border_radius}px`
    : "6px";
  const fontSize = element.style.font_size
    ? `${element.style.font_size}px`
    : "14px";
  const textColor = String(element.style.text_color || "#ffffff");

  const items = useMemo(
    () => resolveItems(element, previewMode, liveState),
    [element, previewMode, liveState],
  );

  const liveSelected = useMemo(
    () => resolveSelected(element, previewMode, liveState),
    [element, previewMode, liveState],
  );

  // Local selection state for design mode or immediate feedback
  const [localSelected, setLocalSelected] = useState<Set<string>>(new Set());
  const [hoveredIndex, setHoveredIndex] = useState<number>(-1);

  // Reset local selection when exiting preview mode
  useEffect(() => {
    if (!previewMode) {
      setLocalSelected(new Set());
    }
  }, [previewMode]);

  // In design mode for "selectable", auto-select first item
  const effectiveSelected = useMemo(() => {
    if (previewMode) {
      // Merge live state with local selections; live state takes priority
      return liveSelected.size > 0 ? liveSelected : localSelected;
    }
    // Design mode: show first item selected for selectable style
    if (listStyle === "selectable" && items.length > 0) {
      return new Set([items[0].value]);
    }
    return new Set<string>();
  }, [previewMode, liveSelected, localSelected, listStyle, items]);

  const handleItemClick = (item: ListItem) => {
    if (!previewMode) return;

    if (listStyle === "selectable") {
      setLocalSelected(new Set([item.value]));
      wsClient.send({
        type: "ui.change",
        element_id: element.id,
        value: item.value,
      });
    } else if (listStyle === "multi_select") {
      setLocalSelected((prev) => {
        const next = new Set(prev);
        if (next.has(item.value)) {
          next.delete(item.value);
        } else {
          next.add(item.value);
        }
        return next;
      });
      wsClient.send({
        type: "ui.change",
        element_id: element.id,
        value: item.value,
      });
    } else if (listStyle === "action") {
      wsClient.send({
        type: "ui.press",
        element_id: element.id,
        value: item.value,
      });
    }
    // "static" style: no interaction
  };

  const isSelectable =
    listStyle === "selectable" || listStyle === "multi_select";
  const isClickable = isSelectable || listStyle === "action";

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        boxSizing: "border-box",
        overflow: "hidden",
      }}
    >
      <style>{scrollbarStyles}</style>

      {/* Label */}
      {element.label && (
        <div
          style={{
            fontSize: 12,
            color: "#cccccc",
            padding: "6px 8px 2px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            flexShrink: 0,
          }}
        >
          {element.label}
        </div>
      )}

      {/* Scrollable list area */}
      <div
        className="oavc-list-scroll"
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          padding: "4px",
          boxSizing: "border-box",
          display: "flex",
          flexDirection: "column",
          gap: "2px",
        }}
      >
        {items.length === 0 && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "#666666",
              fontSize: 12,
              fontStyle: "italic",
            }}
          >
            No items configured
          </div>
        )}

        {items.map((item, idx) => {
          const isActive = isSelectable && effectiveSelected.has(item.value);
          const isHovered = hoveredIndex === idx;

          let bg = itemBg;
          if (isActive) {
            bg = itemActiveBg;
          } else if (isHovered && isClickable) {
            // Slightly lighter hover
            bg = lightenColor(itemBg, 0.12);
          }

          return (
            <div
              key={`${item.value}-${idx}`}
              onClick={() => handleItemClick(item)}
              onMouseEnter={() => setHoveredIndex(idx)}
              onMouseLeave={() => setHoveredIndex(-1)}
              style={{
                height: itemHeight,
                minHeight: itemHeight,
                display: "flex",
                alignItems: "center",
                padding: "8px 12px",
                borderRadius,
                backgroundColor: bg,
                color: isActive ? "#ffffff" : textColor,
                fontSize,
                cursor:
                  previewMode && isClickable ? "pointer" : "default",
                userSelect: "none",
                transition: "background-color 0.15s ease",
                boxSizing: "border-box",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                flexShrink: 0,
              }}
            >
              {item.label}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Lighten a CSS color string by a given amount (0-1).
 * Handles hex colors; returns the original string for non-hex values.
 */
function lightenColor(color: string, amount: number): string {
  if (!color.startsWith("#")) return color;

  let hex = color.slice(1);
  if (hex.length === 3) {
    hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
  }
  if (hex.length !== 6) return color;

  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);

  const lr = Math.min(255, Math.round(r + (255 - r) * amount));
  const lg = Math.min(255, Math.round(g + (255 - g) * amount));
  const lb = Math.min(255, Math.round(b + (255 - b) * amount));

  return `#${lr.toString(16).padStart(2, "0")}${lg.toString(16).padStart(2, "0")}${lb.toString(16).padStart(2, "0")}`;
}
