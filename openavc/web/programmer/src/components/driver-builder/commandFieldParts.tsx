/** The parts a command's card in the Driver Builder draws its fields from:
 *  the caption, hint and warning styles, and a tick box with a title and a
 *  hint.
 *
 *  A tick box's hint sits inside its `<label>`, so it is part of the box's
 *  accessible name; the browser tests find the boxes by that name. */

import type { CSSProperties, ReactNode } from "react";

/** A field's caption above its box. */
export const FIELD_LABEL: CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

/** The line under a field saying what it does. */
export const FIELD_HINT: CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
  marginTop: "var(--space-xs)",
};

/** What a field still needs before it can work. */
export const FIELD_WARNING: CSSProperties = {
  fontSize: "11px",
  color: "var(--color-warning, #d97706)",
  padding: "var(--space-xs) var(--space-sm)",
  marginTop: "var(--space-xs)",
  borderRadius: "var(--border-radius)",
  background: "rgba(255, 152, 0, 0.12)",
  border: "1px solid rgba(255, 152, 0, 0.35)",
};

/** A tick box with its title and, under the title, its hint. `style` adds to
 *  the label's own. */
export function FlagField({
  checked,
  onChange,
  title,
  children,
  style,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  title: string;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 6,
        fontSize: "var(--font-size-sm)",
        color: "var(--text-secondary)",
        ...style,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ marginTop: 3 }}
      />
      <span>
        {title}
        <div style={FIELD_HINT}>{children}</div>
      </span>
    </label>
  );
}
