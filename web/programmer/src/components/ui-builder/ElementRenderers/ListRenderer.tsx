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

function resolveItems(
  element: UIElement,
  liveState: Record<string, unknown>,
): ListItem[] {
  const itemsBinding = element.bindings.items as
    | { source?: string; key_pattern?: string }
    | undefined;

  if (itemsBinding?.key_pattern) {
    const pattern = itemsBinding.key_pattern;
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
      const strVal = String(val);
      if (strVal.includes(",")) {
        return new Set(strVal.split(",").map((s) => s.trim()));
      }
      return new Set([strVal]);
    }
  }

  return new Set();
}

/**
 * ListRenderer — mirrors panel.js renderList().
 * Uses .panel-list, .list-label, .list-scroll, .list-item from panel-elements.css.
 */
export function ListRenderer({ element, liveState }: Props) {
  const listStyle = (element.list_style as string) || "selectable";
  const itemHeight = element.item_height ?? 44;
  const itemBg = String(element.style.item_bg ?? "#2a2a4e");
  const itemActiveBg = String(element.style.item_active_bg ?? "#42a5f5");

  const items = useMemo(
    () => resolveItems(element, liveState),
    [element, liveState],
  );

  const liveSelected = useMemo(
    () => resolveSelected(element, liveState),
    [element, liveState],
  );

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
      className="panel-element panel-list"
      style={{ width: "100%", height: "100%" }}
    >
      {element.label && (
        <div className="list-label">{element.label}</div>
      )}

      <div className="list-scroll">
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
              className={`list-item${isActive ? " active" : ""}`}
              style={{
                height: itemHeight,
                minHeight: itemHeight,
                backgroundColor: isActive ? itemActiveBg : itemBg,
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
