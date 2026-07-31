import { useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent } from "react";
import { ChevronDown } from "lucide-react";
import type { ParamOption } from "./paramOptions";
import { LAYER } from "./layers";

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

interface DropdownPos {
  top: number;
  left: number;
  width: number;
  flipUp: boolean;
}

export function ParamCombobox({
  value,
  onChange,
  options,
  placeholder,
  style,
}: ParamComboboxProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<DropdownPos>({ top: 0, left: 0, width: 0, flipUp: false });
  const [highlight, setHighlight] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

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

  // Close on outside click or any scroll (the dropdown is position:fixed, so a
  // scroll would otherwise leave it stranded).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onScroll = () => setOpen(false);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  const openDropdown = () => {
    const rect = inputRef.current?.getBoundingClientRect();
    if (rect) {
      const spaceBelow = window.innerHeight - rect.bottom;
      const flipUp = spaceBelow < 220 && rect.top > spaceBelow;
      setPos({ top: rect.bottom + 2, left: rect.left, width: rect.width, flipUp });
    }
    setHighlight(0);
    setOpen(true);
  };

  const choose = (v: string) => {
    onChange(v);
    setOpen(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
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

  const rect = inputRef.current?.getBoundingClientRect();
  const ddTop = pos.flipUp ? undefined : (rect?.bottom ?? pos.top) + 2;
  const ddBottom = pos.flipUp ? window.innerHeight - (rect?.top ?? 0) + 2 : undefined;
  const ddMaxH = pos.flipUp
    ? (rect?.top ?? 0) - 16
    : window.innerHeight - (rect?.bottom ?? 0) - 16;

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
        style={{ flex: 1, width: "100%", paddingRight: 24 }}
      />
      <ChevronDown
        size={14}
        onMouseDown={(e) => {
          // mousedown (not click) so the input doesn't blur-close first
          e.preventDefault();
          if (open) {
            setOpen(false);
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
          style={{
            position: "fixed",
            top: ddTop,
            bottom: ddBottom,
            left: pos.left,
            width: pos.width,
            maxHeight: Math.max(160, ddMaxH),
            overflowY: "auto",
            margin: 0,
            padding: 4,
            listStyle: "none",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            boxShadow: "var(--shadow-lg)",
            zIndex: LAYER.popover,
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
                  padding: "5px 8px",
                  borderRadius: 4,
                  cursor: "pointer",
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 8,
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
                      fontSize: 11,
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
