/**
 * A colour the Theme Studio edits in the theme: the swatch and the text box
 * beside it, in the Theme section and the Elements section alike. While unset it
 * says what the panel draws instead: the colour it inherits when there is one to
 * name (a theme variable's panel default, which the swatch shows too), else "the
 * theme default" (an element default, which inherits from the theme). The Page
 * Background's colours are not these: they inherit nothing, and draw their own
 * pair (`PageBackgroundSection.tsx`).
 */
import { ColorPickerCell } from "./ColorPickerCell";

interface ColorFieldProps {
  value: unknown;
  /** The colour drawn while the value is unset, when there is one to name. */
  inherits?: string;
  fontSize: number;
  onChange: (next: string) => void;
}

export function ColorField({ value, inherits, fontSize, onChange }: ColorFieldProps) {
  const text = String(value ?? "");
  const unset = value == null || value === "";
  return (
    <>
      <ColorPickerCell value={text} fallback={inherits} onChange={onChange} />
      <input
        type="text"
        value={text}
        placeholder={unset ? (inherits ? `${inherits} (inherited)` : "inherited") : ""}
        title={unset ? (inherits ? `Inherits ${inherits}` : "Inherits the theme default") : undefined}
        onChange={(e) => onChange(e.target.value)}
        style={{ flex: 1, fontSize, fontFamily: "monospace" }}
      />
    </>
  );
}
