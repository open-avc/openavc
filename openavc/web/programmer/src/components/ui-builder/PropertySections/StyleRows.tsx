/**
 * Rows that edit one field of an element's `style`.
 *
 * A style edit is a new style object with that one field changed, the rest
 * kept as they were; a colour cleared is the field removed, so the theme's
 * colour shows again.
 */
import type { UIElement } from "../../../api/types";
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { FieldRow } from "./FieldRow";

interface StyleRowProps {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
  field: string;
  label: string;
}

/** One colour in the element's style, cleared back to the theme's. */
export function StyleColorRow({ element, onChange, field, label }: StyleRowProps) {
  return (
    <FieldRow label={label}>
      <InlineColorPicker size="md" clearable
        value={String(element.style?.[field] || "")}
        onChange={(v) => onChange({ style: { ...element.style, [field]: v || undefined } })}
      />
    </FieldRow>
  );
}
