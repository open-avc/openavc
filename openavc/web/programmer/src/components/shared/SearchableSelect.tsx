/**
 * A `<select>` you can search, for the lists in this product that are not
 * small: a driver's commands (200+ on some devices), a project's devices,
 * macros, pages and groups, a script's functions, a driver-declared enum.
 *
 * The searchable command dropdown existed for a year in exactly one place --
 * hand-rolled inside the device page, because that is where somebody first hit
 * a 200-command device. Every other surface that picks from the same list (the
 * UI Builder press action, both macro step editors, the plugin `command_ref`
 * field, the Driver Builder) was still a native dropdown. This is that picker,
 * written once and driven by data, so adding it to a call site is one component
 * and no rows.
 *
 * It is deliberately NOT the whole answer. `DeviceValuePicker` keeps its own
 * rows: it groups properties by how well they fit the element being bound and
 * draws each one's live value, which is judgement about a specific list rather
 * than a list. What is shared with it is the shell (`SearchableDropdown`), the
 * row styles, and the panel mechanic underneath both.
 *
 * Two behaviours are worth stating because they are what a native `<select>`
 * does, not opinions layered on top:
 *
 *   - An empty value draws the placeholder. A `<select>` whose value matches no
 *     option has `selectedIndex === -1` and renders blank, so every call site
 *     converted to this already looked empty when nothing was chosen.
 *   - The placeholder is a ROW, not just trigger text. `<option value="">Select
 *     device...</option>` is a real option: picking it again is how somebody
 *     un-sets a choice, and a picker that only ever adds a value would have
 *     quietly removed the ability to clear a field on every screen this
 *     touches. `allowEmpty={false}` is for the handful of lists that genuinely
 *     had no empty option -- a driver's required enum.
 *   - A value that is NOT in the list is drawn verbatim and muted. This is the
 *     one place the behaviour improves on native, which renders that blank --
 *     and a blank is how a binding pointed at a deleted macro reads as "nothing
 *     chosen" instead of "this is broken". Nothing is rewritten either way; the
 *     picker never edits a value it was handed.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import {
  SearchableDropdown,
  dropdownRowStyle,
  dropdownGroupHeaderStyle,
  dropdownTypeBadgeStyle,
  dropdownEmptyHintStyle,
} from "./SearchableDropdown";

export interface SelectOption {
  value: string;
  /** The primary line, and what the trigger reads once this is chosen. */
  label: string;
  /** Secondary line under the label, drawn mono -- a command's id under its
   *  friendly name. Omitted when it would just repeat the label. */
  hint?: string;
  /** Small pill beside the label: a declared type, "3 steps". */
  badge?: string;
  /** Right-aligned muted text: a driver id, a live value. */
  meta?: string;
  /** Drawn before the label on both the row and the trigger -- the dot that
   *  says a device is connected. */
  prefix?: ReactNode;
  /** Matched by the search box, never drawn. */
  keywords?: string;
  /** Drawn dimmer. Still listed, still selectable. */
  dim?: boolean;
}

export interface SelectGroup {
  label?: string;
  /** Italic note beside the group heading. */
  desc?: string;
  options: SelectOption[];
}

export interface SearchableSelectProps {
  value: string;
  onChange: (value: string) => void;
  /** A flat list. Mutually exclusive with `groups`; pass whichever fits. */
  options?: SelectOption[];
  groups?: SelectGroup[];
  /** The empty choice: both the trigger text while nothing is chosen and the
   *  label on the row that clears the field. "(none)", "Select device...". */
  placeholder?: string;
  /** Off for a list whose `<select>` had no `<option value="">` -- there is
   *  then no way to hold nothing, so offering one would write a value the
   *  caller never had to handle. */
  allowEmpty?: boolean;
  searchPlaceholder?: string;
  /** Drawn in the panel when there is nothing to list at all -- the empty
   *  state a `<option disabled>` used to carry. */
  emptyHint?: ReactNode;
  style?: CSSProperties;
  /** Set on the trigger button, for tests and for the affordance inventory. */
  testId?: string;
}

/** Every option in draw order, flattened out of whichever shape was passed. */
function flatten(groups: SelectGroup[]): SelectOption[] {
  return groups.flatMap((g) => g.options);
}

function matches(option: SelectOption, q: string): boolean {
  return (
    option.label.toLowerCase().includes(q) ||
    option.value.toLowerCase().includes(q) ||
    (option.hint ?? "").toLowerCase().includes(q) ||
    (option.keywords ?? "").toLowerCase().includes(q)
  );
}

export function SearchableSelect({
  value,
  onChange,
  options,
  groups,
  placeholder = "Select...",
  allowEmpty = true,
  searchPlaceholder = "Search...",
  emptyHint,
  style,
  testId,
}: SearchableSelectProps) {
  const [search, setSearch] = useState("");
  const [highlight, setHighlight] = useState(0);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Only a keyboard move scrolls. Hovering sets the highlight too, and
  // scrolling a partly-visible row under a stationary cursor makes the list
  // feel like it is fighting the mouse.
  const scrollNext = useRef(false);
  // `children` runs during the shell's render, which is where `close` is
  // handed over; the keyboard handler needs it from an event instead.
  const closeRef = useRef<() => void>(() => {});

  const allGroups = useMemo<SelectGroup[]>(() => {
    const given = groups ?? [{ options: options ?? [] }];
    if (!allowEmpty) return given;
    // Its own group so it sits above the first heading rather than under it.
    const empty: SelectGroup = {
      options: [{ value: "", label: placeholder, dim: true }],
    };
    return [empty, ...given];
  }, [groups, options, allowEmpty, placeholder]);
  const all = useMemo(() => flatten(allGroups), [allGroups]);

  const q = search.trim().toLowerCase();
  const shown = useMemo<SelectGroup[]>(
    () =>
      allGroups
        .map((g) => (q ? { ...g, options: g.options.filter((o) => matches(o, q)) } : g))
        .filter((g) => g.options.length > 0),
    [allGroups, q],
  );
  const flatShown = useMemo(() => flatten(shown), [shown]);

  const selected = value ? all.find((o) => o.value === value) : undefined;

  // Keep the highlight on a row that still exists after a filter narrows.
  const clamped = flatShown.length === 0 ? 0 : Math.min(highlight, flatShown.length - 1);

  useEffect(() => {
    if (!scrollNext.current) return;
    scrollNext.current = false;
    rowRefs.current[clamped]?.scrollIntoView?.({ block: "nearest" });
  }, [clamped]);

  const choose = (option: SelectOption) => {
    onChange(option.value);
    closeRef.current();
  };

  const onSearchKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = flatShown.length;
      if (n === 0) return;
      scrollNext.current = true;
      setHighlight(e.key === "ArrowDown" ? (clamped + 1) % n : (clamped - 1 + n) % n);
      return;
    }
    if (e.key === "Enter") {
      const option = flatShown[clamped];
      if (!option) return;
      // A picker inside a dialog must not also submit it.
      e.preventDefault();
      e.stopPropagation();
      choose(option);
    }
  };

  const display: ReactNode = selected ? (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {selected.prefix}
      <span>{selected.label}</span>
    </span>
  ) : value ? (
    <span style={{ fontFamily: "var(--font-mono)", fontStyle: "italic" }} title={value}>
      {value}
    </span>
  ) : (
    placeholder
  );

  return (
    <SearchableDropdown
      display={display}
      empty={!value}
      searchPlaceholder={searchPlaceholder}
      search={search}
      onSearchChange={(text) => {
        setSearch(text);
        setHighlight(0);
      }}
      onSearchKeyDown={onSearchKeyDown}
      onClose={() => {
        setSearch("");
        setHighlight(0);
      }}
      style={style}
      testId={testId}
    >
      {({ close }) => {
        closeRef.current = close;
        rowRefs.current = [];
        let index = -1;
        return (
          // The list carries listbox/option roles: it is the one thing on the
          // screen a person is choosing from, and it is what a test asks for
          // by name. The rows exist only while the panel is open, so nothing
          // that walks the closed IDE sees them.
          <div role="listbox">
            {all.length === 0 && emptyHint && (
              <div style={dropdownEmptyHintStyle}>{emptyHint}</div>
            )}
            {all.length > 0 && flatShown.length === 0 && (
              <div style={dropdownEmptyHintStyle}>
                Nothing matching &ldquo;{search}&rdquo;
              </div>
            )}
            {shown.map((group, gi) => (
              <div key={group.label ?? gi}>
                {group.label && (
                  <div style={dropdownGroupHeaderStyle}>
                    <span style={{ fontWeight: 600 }}>{group.label}</span>
                    {group.desc && (
                      <span style={{ fontWeight: 400, fontStyle: "italic", marginLeft: 6 }}>
                        {group.desc}
                      </span>
                    )}
                  </div>
                )}
                {group.options.map((option) => {
                  index += 1;
                  const i = index;
                  const isSelected = option.value === value;
                  const isHighlighted = i === clamped;
                  return (
                    <div
                      key={option.value}
                      ref={(el) => {
                        rowRefs.current[i] = el;
                      }}
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => choose(option)}
                      onMouseEnter={() => setHighlight(i)}
                      style={{
                        ...rowStyle,
                        opacity: option.dim ? 0.75 : 1,
                        background: isHighlighted
                          ? "var(--bg-hover)"
                          : isSelected
                            ? "var(--bg-hover)"
                            : "transparent",
                      }}
                    >
                      {option.prefix}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span
                            style={{
                              fontSize: 12,
                              color: "var(--text-primary)",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {option.label}
                          </span>
                          {option.badge && (
                            <span style={dropdownTypeBadgeStyle}>{option.badge}</span>
                          )}
                        </div>
                        {option.hint && option.hint !== option.label && (
                          <div
                            style={{
                              fontSize: 10,
                              color: "var(--text-muted)",
                              fontFamily: "var(--font-mono)",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {option.hint}
                          </div>
                        )}
                      </div>
                      {option.meta && (
                        <span
                          style={{
                            fontSize: 11,
                            color: "var(--text-muted)",
                            flexShrink: 0,
                            maxWidth: 130,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={option.meta}
                        >
                          {option.meta}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        );
      }}
    </SearchableDropdown>
  );
}

const rowStyle: CSSProperties = { ...dropdownRowStyle, gap: 6 };
