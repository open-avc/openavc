import { useRef, useState } from "react";
import {
  commitNumeric,
  liveNumeric,
  type NumericRange,
} from "../ui-builder/PropertySections/numericField";

interface NumericInputProps extends NumericRange {
  /** The committed value. null/undefined shows an empty field (placeholder). */
  value: number | null | undefined;
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
 */
export function NumericInput({
  value,
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
