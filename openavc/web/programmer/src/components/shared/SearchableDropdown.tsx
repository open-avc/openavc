/**
 * The searchable dropdown shell: a collapsed trigger, an anchored panel with a
 * search box at the top, a scrolling list of rows, and an optional pinned
 * footer.
 *
 * The state-key picker and the device-property picker were two independent
 * copies of exactly this — same open/search state, same close-on-outside-click,
 * same focus-the-search-box-on-open, same search header over a grouped
 * scrollable list, and five of the six style constants byte-identical between
 * them. What legitimately differs is only what goes IN the list: one lists
 * every namespace in live state and can create a variable inline, the other
 * lists one device's driver schema and its child entities. So the shell is
 * shared and the rows stay with their picker, which is why `children` is a
 * function of the search text rather than a plain node.
 *
 * Positioning, the viewport clamp and the close rules all come from
 * `useAnchoredPanel`, which the three dropdowns without a search box share too.
 */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { useAnchoredPanel } from "./AnchoredPanel";

export interface SearchableDropdownProps {
  /** What the collapsed trigger reads. */
  display: ReactNode;
  /** Nothing chosen yet — the trigger text goes muted. */
  empty?: boolean;
  searchPlaceholder: string;
  /** The rows, given the live search text and a `close` for when one is picked. */
  children: (ctx: { search: string; close: () => void }) => ReactNode;
  /** Pinned inside the panel below the scrolling list (an inline create form). */
  footer?: (ctx: { close: () => void }) => ReactNode;
  /** Reset caller-owned panel state as the panel closes. */
  onClose?: () => void;
  /** Told when the panel opens and closes, for work a picker defers until then. */
  onOpenChange?: (open: boolean) => void;
  /** Style for the outer container. */
  style?: CSSProperties;
}

export function SearchableDropdown({
  display,
  empty = false,
  searchPlaceholder,
  children,
  footer,
  onClose,
  onOpenChange,
  style,
}: SearchableDropdownProps) {
  const [search, setSearch] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;

  const panel = useAnchoredPanel<HTMLButtonElement>({
    onClose: () => {
      setSearch("");
      onClose?.();
    },
  });

  useEffect(() => {
    if (panel.open) searchRef.current?.focus();
    onOpenChangeRef.current?.(panel.open);
    // Told once per transition; a changing callback identity must not re-run it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel.open]);

  return (
    <div ref={panel.containerRef} style={{ position: "relative", ...style }}>
      <button
        ref={panel.triggerRef}
        type="button"
        onClick={panel.toggle}
        style={{
          ...triggerStyle,
          color: empty ? "var(--text-muted)" : "var(--text-primary)",
        }}
      >
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left" }}>
          {display}
        </span>
        <ChevronDown size={14} style={{ flexShrink: 0, opacity: 0.5 }} />
      </button>

      {panel.open && (
        <div
          style={{
            ...panel.panelStyle,
            display: "flex",
            flexDirection: "column",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border-color)" }}>
            <input
              ref={searchRef}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={searchPlaceholder}
              style={searchInputStyle}
            />
          </div>

          <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
            {children({ search, close: panel.close })}
          </div>

          {footer?.({ close: panel.close })}
        </div>
      )}
    </div>
  );
}

/* ── Row styles, shared by everything that fills one of these lists ── */

/** One row. `gap` is the caller's, because only one of the two lists wants it. */
export const dropdownRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "4px 8px 4px 16px",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
  transition: "background 0.1s",
};

export const dropdownGroupHeaderStyle: CSSProperties = {
  padding: "6px 8px 2px",
  fontSize: 11,
  color: "var(--text-muted)",
  display: "flex",
  alignItems: "baseline",
  flexWrap: "wrap",
};

export const dropdownTypeBadgeStyle: CSSProperties = {
  fontSize: 10,
  padding: "0 4px",
  borderRadius: 3,
  background: "var(--bg-hover)",
  color: "var(--text-muted)",
};

export const dropdownEmptyHintStyle: CSSProperties = {
  padding: "12px 8px",
  fontSize: 12,
  color: "var(--text-muted)",
  fontStyle: "italic",
  textAlign: "center",
};

const triggerStyle: CSSProperties = {
  width: "100%",
  padding: "4px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const searchInputStyle: CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};
