import { ConditionGroupEditor, type ConditionGroup } from "../../shared/ConditionGroupEditor";

interface VisibilityPropertiesProps {
  element: { bindings: Record<string, unknown> };
  onChange: (patch: Record<string, unknown>) => void;
}

export function VisibilityProperties({ element, onChange }: VisibilityPropertiesProps) {
  const visibleWhen = element.bindings.visible_when as ConditionGroup | undefined;
  const hasCondition = visibleWhen != null;

  const setGroup = (group: ConditionGroup | undefined) => {
    const newBindings = { ...element.bindings };
    if (group === undefined) {
      delete newBindings.visible_when;
    } else {
      newBindings.visible_when = group;
    }
    onChange({ bindings: newBindings });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={hasCondition}
          onChange={(e) => setGroup(e.target.checked ? { key: "", operator: "truthy" } : undefined)}
        />
        Show only when...
      </label>

      {hasCondition && (
        <div style={{ marginLeft: "var(--space-xl)" }}>
          <ConditionGroupEditor
            value={visibleWhen}
            onChange={setGroup}
            required
            anyHint="Element is visible when any condition is true."
            allHint="Element is visible when all conditions are true."
          />
        </div>
      )}
    </div>
  );
}
