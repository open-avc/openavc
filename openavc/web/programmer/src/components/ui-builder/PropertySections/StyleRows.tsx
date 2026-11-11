/**
 * Rows that edit one field of an element's `style`.
 *
 * A style edit is a new style object with that one field changed, the rest
 * kept as they were; a colour cleared is the field removed, so the theme's
 * colour shows again.
 */
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { CheckboxRow, FieldRow, type FieldProps } from "./FieldRow";

interface StyleRowProps extends FieldProps {
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

/**
 * One on/off setting in the element's style. `fallback` is what the panel does
 * while the field is unset, so the box shows what is drawn.
 */
export function StyleToggle({
  element,
  onChange,
  field,
  label,
  fallback,
  children,
}: StyleRowProps & { fallback: boolean; children: React.ReactNode }) {
  return (
    <CheckboxRow
      label={label}
      checked={(element.style?.[field] as boolean) ?? fallback}
      onToggle={(on) => onChange({ style: { ...element.style, [field]: on } })}
    >
      {children}
    </CheckboxRow>
  );
}

/**
 * One number in the element's style, showing the panel's own value while it is
 * unset; emptied, the field is removed and the panel's value applies again.
 */
export function StyleNumberRow({
  element,
  onChange,
  field,
  label,
  fallback,
  limits,
  unit,
}: StyleRowProps & {
  fallback: number;
  limits: { min: number; max: number; step?: number };
  unit?: string;
}) {
  return (
    <FieldRow label={label}>
      <input
        type="number"
        value={(element.style?.[field] as number) ?? fallback}
        onChange={(e) => onChange({ style: { ...element.style, [field]: e.target.value ? Number(e.target.value) : undefined } })}
        min={limits.min} max={limits.max} step={limits.step} style={{ flex: 1 }}
      />
      {unit && <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{unit}</span>}
    </FieldRow>
  );
}
