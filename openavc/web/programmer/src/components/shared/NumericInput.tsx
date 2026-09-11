import { useRef, useState } from "react";
import {
  commitNumeric,
  liveNumeric,
  type NumericRange,
} from "../ui-builder/PropertySections/numericField";

interface NumericInputProps extends NumericRange {
  /** The committed value. null/undefined shows an empty field (placeholder). */
  value: number | null | undefined;
  /** What this number belongs to — the element, page, theme or token whose
   *  property is being typed. Required, and required of every call site,
   *  because a field that cannot name its subject cannot tell a re-render
   *  apart from being pointed at something else: see the note below. Build it
   *  from everything that picks the value out (an element id AND the layout
   *  its geometry is stored under, not just the id). */
  owner: string;
  /** Called with the clamped value on blur/Enter, and live for every typed
   *  value that is already valid and in range. `undefined` only ever arrives
   *  when `allowEmpty` is set and the field was left empty: it means unset. */
  onCommit: (value: number | undefined) => void;
  /** Empty-on-blur unsets the property (placeholder shows the effective
   *  default). Without it, an emptied field reverts to the committed value. */
  allowEmpty?: boolean;
  placeholder?: string;
  step?: number | string;
  disabled?: boolean;
  title?: string;
  style?: React.CSSProperties;
}

/**
 * A numeric field that tolerates being mid-edit. While focused it holds the
 * raw text — empty, "-", "1." are all fine — and live-commits only values
 * that need no correction, so the preview follows your typing without ever
 * fighting it. The clamp runs once, on blur or Enter; Escape reverts.
 *
 * This replaces the commit-per-keystroke pattern (`Number(v) || fallback`
 * plus an inline clamp) that made clearing a size field snap the element to
 * 0.1% before you could type the value you meant.
 *
 * Holding a draft is also what made it possible to type one element's number
 * onto another. These fields live in property panels that re-render in place
 * when the selection changes — same input, new `value`, new `onCommit`, no
 * remount — so a draft carried over from the element you left would blur into
 * the writer of the element you arrived at, and the `v !== value` guard fired
 * precisely BECAUSE the two belonged to different elements. Hence `owner`: a
 * draft is abandoned the moment its subject changes, which also puts the new
 * subject's number in the box before any blur can read it back out. The value
 * alone cannot stand in for the subject — a parent that clamps or round-trips
 * what it was given (px→rem in the theme editor) changes `value` under a field
 * that is editing the very same thing, and dropping the draft there would yank
 * the number out from under someone still typing. A `key` per subject would
 * remount instead, but React does not reliably fire `onBlur` on unmount, so it
 * would silently discard genuine edits: the opposite failure, and a worse one.
 */
export function NumericInput({
  value,
  owner,
  onCommit,
  allowEmpty,
  min,
  max,
  integer,
  placeholder,
  step,
  disabled,
  title,
  style,
}: NumericInputProps) {
  // Non-null exactly while the field is focused.
  const [draft, setDraft] = useState<string | null>(null);
  // Enter/Escape settle the edit themselves and then blur; the blur that
  // follows must not run a second commit against the not-yet-rerendered DOM.
  const settled = useRef(false);
  // What the live draft is being typed into. Compared during render so the
  // correction lands before the next event, whichever it is: a blur reads the
  // DOM, which is already back to the new subject's number, and a keystroke
  // edits the new subject from its committed value like any other.
  const editing = useRef(owner);
  if (editing.current !== owner) {
    editing.current = owner;
    if (draft !== null) setDraft(null);
  }
  const committed = value == null ? "" : String(value);
  const range: NumericRange = { min, max, integer };

  const finish = (raw: string) => {
    setDraft(null);
    const v = commitNumeric(raw, range);
    if (v === undefined) {
      // Left empty (or junk): unset when that means something, else the
      // cleared draft simply reveals the committed value again.
      if (allowEmpty && value != null) onCommit(undefined);
      return;
    }
    if (v !== value) onCommit(v);
  };

  return (
    <input
      type="number"
      value={draft ?? committed}
      placeholder={placeholder}
      step={step}
      min={min}
      max={max}
      disabled={disabled}
      title={title}
      style={style}
      onFocus={(e) => {
        settled.current = false;
        setDraft(e.target.value);
      }}
      onChange={(e) => {
        setDraft(e.target.value);
        const v = liveNumeric(e.target.value, range);
        if (v !== undefined && v !== value) onCommit(v);
      }}
      onBlur={(e) => {
        if (settled.current) {
          settled.current = false;
          return;
        }
        finish(e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          settled.current = true;
          finish(e.currentTarget.value);
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          settled.current = true;
          setDraft(null);
          e.currentTarget.blur();
          e.stopPropagation();
        }
      }}
    />
  );
}
