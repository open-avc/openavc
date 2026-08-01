/**
 * The layout-scoped default colors a deck's keys inherit.
 */
import { InlineColorPicker } from "../../shared/InlineColorPicker";

export function AppearanceEditor({
  viewConfig,
  onViewChange,
  inherits,
}: {
  viewConfig: Record<string, unknown>;
  onViewChange: (next: Record<string, unknown>) => void;
  inherits: boolean;
}) {
  const setField = (field: string, value: unknown) => {
    const next = { ...viewConfig };
    if (value === undefined || value === "") {
      delete next[field];
    } else {
      next[field] = value;
    }
    onViewChange(next);
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", maxWidth: 420 }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", width: 130 }}>
          Default key color
        </label>
        <InlineColorPicker
          value={typeof viewConfig.button_color === "string" ? (viewConfig.button_color as string) : ""}
          onChange={(c) => setField("button_color", c || undefined)}
        />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", width: 130 }}>
          Text color
        </label>
        <InlineColorPicker
          value={typeof viewConfig.text_color === "string" ? (viewConfig.text_color as string) : ""}
          onChange={(c) => setField("text_color", c || undefined)}
        />
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
        Keys without their own colors use these.
        {inherits && " Blank values fall back to the shared layout's colors."}
      </div>
    </div>
  );
}
