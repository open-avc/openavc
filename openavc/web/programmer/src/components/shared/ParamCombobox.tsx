import { useState } from "react";
import type { CSSProperties, KeyboardEvent } from "react";
import { ChevronDown } from "lucide-react";
import type { ParamOption } from "./paramOptions";
import { useAnchoredPanel } from "./AnchoredPanel";

/** A "pick or type" field for a param whose known values come from an option
 *  provider (options_state / options_from). Unlike an HTML
 *  <datalist>, the dropdown opens on click/focus and shows the *full* list even
 *  when a value is already chosen (a datalist hides everything once the text
 *  matches), filters as you type, and keeps a typed value the platform hasn't
 *  discovered yet (offline device, undiscovered control, escape-hatch command).
 *
 *  Value in/out is a string, like the rest of ParamInput; numeric/boolean
 *  coercion happens at submit. */
export interface ParamComboboxProps {
  value: string;
  onChange: (value: string) => void;
  options: ParamOption[];
  placeholder?: string;
  style?: CSSProperties;
}

export function ParamCombobox({
  value,
  onChange,
  options,
  placeholder,
  style,
}: ParamComboboxProps) {
  const [highlight, setHighlight] = useState(0);
  // A combobox panel is exactly its input's width -- that is the look, and the
  // 320px floor the two big pickers want would make every param field in the
  // properties pane sprout a dropdown wider than the field. What it gains from
  // the shared panel is the viewport clamp and the close rules, not a new size.
  const panel = useAnchoredPanel<HTMLInputElement, HTMLUListElement>({ minWidth: 0 });
  const { open, containerRef, triggerRef: inputRef } = panel;

  // Show the full list when nothing is typed or the text exactly matches an
  // option (so reopening after a selection still shows everything); otherwise
  // filter by substring of either the value or the human label.
  const q = value.trim().toLowerCase();
  const exact = options.some((o) => o.value.toLowerCase() === q);
  const filtered =
    !q || exact
      ? options
      : options.filter(
          (o) =>
            o.value.toLowerCase().includes(q) ||
            o.label.toLowerCase().includes(q),
        );

  const openDropdown = () => {
    setHighlight(0);
    panel.openPanel();
  };

  const choose = (v: string) => {
    onChange(v);
    panel.close();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      panel.close();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!open) {
        openDropdown();
        return;
      }
      e.preventDefault();
      setHighlight((h) => {
        const n = filtered.length;
        if (n === 0) return 0;
        return e.key === "ArrowDown" ? (h + 1) % n : (h - 1 + n) % n;
      });
      return;
    }
    if (e.key === "Enter" && open && filtered[highlight]) {
      e.preventDefault();
      choose(filtered[highlight].value);
    }
  };

  return (
    <div ref={containerRef} style={{ position: "relative", display: "flex", alignItems: "center", ...style }}>
      <input
        ref={inputRef}
        type="text"
        value={value}
        placeholder={placeholder ?? ""}
        autoComplete="off"
        onChange={(e) => {
          onChange(e.target.value);
          setHighlight(0);
          if (!open) openDropdown();
        }}
        onFocus={openDropdown}
        onClick={openDropdown}
        onKeyDown={onKeyDown}
        style={{ flex: 1, width: "100%", paddingRight: "var(--space-xl)" }}
      />
      <ChevronDown
        size={14}
        onMouseDown={(e) => {
          // mousedown (not click) so the input doesn't blur-close first
          e.preventDefault();
          if (open) {
            panel.close();
          } else {
            inputRef.current?.focus();
            openDropdown();
          }
        }}
        style={{
          position: "absolute",
          right: 6,
          opacity: 0.5,
          cursor: "pointer",
          flexShrink: 0,
        }}
      />
      {open && filtered.length > 0 && (
        <ul
          ref={panel.panelRef}
          style={{
            ...panel.panelStyle,
            overflowY: "auto",
            margin: 0,
            padding: "var(--space-xs)",
            listStyle: "none",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          {filtered.map((o, i) => {
            const selected = o.value === value;
            const active = i === highlight;
            return (
              <li
                key={o.value}
                // mousedown fires before the input's blur, so the pick registers
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(o.value);
                }}
                onMouseEnter={() => setHighlight(i)}
                style={{
                  padding: "var(--space-xs) var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  cursor: "pointer",
                  display: "flex",
                  justifyContent: "space-between",
                  gap: "var(--space-sm)",
                  fontSize: "var(--font-size-sm)",
                  background: active
                    ? "var(--bg-hover)"
                    : selected
                      ? "rgba(138,180,147,0.15)"
                      : "transparent",
                  color: selected ? "var(--accent)" : "var(--text-primary)",
                }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {o.label}
                </span>
                {o.label !== o.value && (
                  <span
                    style={{
                      color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--font-size-xs)",
                      flexShrink: 0,
                    }}
                  >
                    {o.value}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
