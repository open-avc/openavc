/**
 * The pieces a properties section is built from: a labelled row, a row that
 * is one checkbox, the note under a row, and the heading over a group of them.
 */
import type { UIElement } from "../../../api/types";

/** What a row needs: the element, and where its edits go. */
export interface FieldProps {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
}

/**
 * One labelled row in the properties panel.
 *
 * Lived twice — identically — in `PropertiesPanel.tsx` and
 * `BasicProperties.tsx`, which is one copy too many for four lines of flexbox
 * and the reason a third would have appeared the moment a section moved into
 * its own file.
 */
export function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
      }}
    >
      <label
        style={{
          width: 72,
          flexShrink: 0,
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

/**
 * A note under a row, lined up with the fields rather than the labels. A
 * warning says what is missing for the row above to do anything.
 */
export function FieldHint({
  tone = "muted",
  italic = false,
  children,
}: {
  tone?: "muted" | "warning";
  italic?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        fontSize: 10,
        color: tone === "warning" ? "var(--color-warning)" : "var(--text-muted)",
        padding: "0 0 0 76px",
        ...(italic ? { fontStyle: "italic" } : {}),
      }}
    >
      {children}
    </div>
  );
}

/** A heading that starts a group of rows within a section. */
export function SubSection({ label }: { label: string }) {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 600,
        color: "var(--text-muted)",
        textTransform: "uppercase",
        letterSpacing: 1,
        marginTop: 6,
        paddingBottom: 2,
        borderBottom: "1px solid var(--border-color)",
      }}
    >
      {label}
    </div>
  );
}

/** A row whose field is one checkbox, with words saying what ticking it does. */
export function CheckboxRow({
  label,
  checked,
  onToggle,
  children,
}: {
  label: string;
  checked: boolean;
  onToggle: (checked: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <FieldRow label={label}>
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onToggle(e.target.checked)}
        />
        {children}
      </label>
    </FieldRow>
  );
}
