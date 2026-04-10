import { useMemo } from "react";
import type { UIElement } from "../../../api/types";

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
 * Resolve the list items: prefer state-driven items (from key_pattern binding),
 * fall back to static element.items.
 */
function resolveItems(
  element: UIElement,
  liveState: Record<string, unknown>,
): ListItem[] {
  const itemsBinding = element.bindings.items as
    | { source?: string; key_pattern?: string }
    | undefined;

  if (itemsBinding?.key_pattern) {
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
  liveState: Record<string, unknown>,
): Set<string> {
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

export function ListRenderer({ element, liveState }: Props) {
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
    () => resolveItems(element, liveState),
    [element, liveState],
  );

  const liveSelected = useMemo(
    () => resolveSelected(element, liveState),
    [element, liveState],
  );

  // In design mode for "selectable", auto-select first item for preview
  const effectiveSelected = useMemo(() => {
    if (liveSelected.size > 0) return liveSelected;
    if (listStyle === "selectable" && items.length > 0) {
      return new Set([items[0].value]);
    }
    return new Set<string>();
  }, [liveSelected, listStyle, items]);

  const isSelectable =
    listStyle === "selectable" || listStyle === "multi_select";

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

          return (
            <div
              key={`${item.value}-${idx}`}
              style={{
                height: itemHeight,
                minHeight: itemHeight,
                display: "flex",
                alignItems: "center",
                padding: "8px 12px",
                borderRadius,
                backgroundColor: isActive ? itemActiveBg : itemBg,
                color: isActive ? "#ffffff" : textColor,
                fontSize,
                userSelect: "none",
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
