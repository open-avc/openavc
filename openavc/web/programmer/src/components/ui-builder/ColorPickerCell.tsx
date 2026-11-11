/**
 * The Theme Studio's colour swatch, beside every colour's text box. The
 * browser's colour input only speaks six-digit hex, so it is used whenever the
 * value (or, while unset, the colour it inherits) is fully opaque, three-digit
 * hex and named colours included: those once read as "not a colour" and were
 * reset to #000000 by a single click. A transparent, translucent or unset value
 * shows a swatch (checked when clear) over a hidden colour input seeded with the
 * nearest colour, so only a deliberate pick changes it.
 */
import type { CSSProperties } from "react";
import { parseColor, rgbToHex6 } from "./colorUtils";

const CHECKER_BG =
  "linear-gradient(45deg, #888 25%, transparent 25%, transparent 75%, #888 75%), " +
  "linear-gradient(45deg, #888 25%, transparent 25%, transparent 75%, #888 75%)";

const FRAME: CSSProperties = {
  border: "1px solid var(--border-color)",
  borderRadius: 3,
  cursor: "pointer",
  flexShrink: 0,
};

type Rgba = NonNullable<ReturnType<typeof parseColor>>;

interface ColorPickerCellProps {
  value: string;
  onChange: (next: string) => void;
  /** Effective value shown when `value` is empty (the inherited/fallback color). */
  fallback?: string;
}

export function ColorPickerCell({ value, onChange, fallback }: ColorPickerCellProps) {
  const raw = value || "";
  const parsed = parseColor(raw);
  const fallbackParsed = fallback ? parseColor(fallback) : null;
  const opaque = opaqueColor(raw, parsed, fallbackParsed);
  if (opaque) return nativePicker(rgbToHex6(opaque), onChange);
  return clearSwatch(raw, parsed, fallbackParsed, onChange);
}

/** The colour the browser's own input can show without loss: the value, or while unset the fallback. */
function opaqueColor(raw: string, parsed: Rgba | null, fallback: Rgba | null): Rgba | null {
  if (isOpaque(parsed)) return parsed;
  if (!raw && isOpaque(fallback)) return fallback;
  return null;
}

const isOpaque = (color: Rgba | null): color is Rgba => color?.a === 1;

function nativePicker(hex: string, onChange: (next: string) => void) {
  return (
    <input
      type="color"
      value={hex}
      onChange={(e) => onChange(e.target.value)}
      style={{ width: 28, height: 22, padding: 0, ...FRAME }}
    />
  );
}

function swatchTitle(raw: string): string {
  if (!raw) return "Inherits the theme default: pick or type a color to set one";
  if (raw === "transparent") return "Transparent: pick or type a color to change";
  return `${raw}: pick or type a color to change`;
}

function clearSwatch(raw: string, parsed: Rgba | null, fallback: Rgba | null, onChange: (next: string) => void) {
  const isClear = !raw || raw === "transparent" || (parsed != null && parsed.a === 0);
  const seedColor = parsed ?? fallback;
  const checks: CSSProperties = isClear
    ? { background: CHECKER_BG, backgroundSize: "8px 8px", backgroundPosition: "0 0, 4px 4px" }
    : { background: raw, backgroundSize: undefined, backgroundPosition: undefined };
  return (
    <label title={swatchTitle(raw)} style={{ position: "relative", width: 28, height: 22, ...FRAME, display: "block", ...checks }}>
      <input
        type="color"
        aria-label="Pick a color"
        value={seedColor ? rgbToHex6(seedColor) : "#888888"}
        onChange={(e) => onChange(e.target.value)}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          opacity: 0,
          border: "none",
          padding: 0,
          cursor: "pointer",
        }}
      />
    </label>
  );
}
