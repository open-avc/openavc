import { useState } from "react";
import type { StepCondition } from "../../../api/types";
import { ConditionEditor } from "../../macros/ConditionEditor";

interface VisibilityPropertiesProps {
  element: { bindings: Record<string, unknown> };
  onChange: (patch: Record<string, unknown>) => void;
}

export function VisibilityProperties({ element, onChange }: VisibilityPropertiesProps) {
  const visibleWhen = element.bindings.visible_when as
    | { key?: string; operator?: string; value?: unknown; all?: StepCondition[] }
    | undefined;

  const hasCondition = visibleWhen != null;
  const conditions: StepCondition[] = visibleWhen?.all
    ? visibleWhen.all
    : visibleWhen?.key
      ? [{ key: visibleWhen.key, operator: visibleWhen.operator ?? "eq", value: visibleWhen.value }]
      : [];

  const updateConditions = (updated: StepCondition[]) => {
    const newBindings = { ...element.bindings };
    if (updated.length === 0) {
      delete newBindings.visible_when;
    } else if (updated.length === 1) {
      newBindings.visible_when = updated[0];
    } else {
      newBindings.visible_when = { all: updated };
    }
    onChange({ bindings: newBindings });
  };

  const toggle = (enabled: boolean) => {
    if (enabled) {
      updateConditions([{ key: "", operator: "truthy" }]);
    } else {
      updateConditions([]);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={hasCondition}
          onChange={(e) => toggle(e.target.checked)}
        />
        Show only when...
      </label>

      {hasCondition && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", marginLeft: 20 }}>
          {conditions.map((cond, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              <div style={{ flex: 1 }}>
                <ConditionEditor
                  condition={cond}
                  onChange={(updated) => {
                    const next = [...conditions];
                    next[i] = updated;
                    updateConditions(next);
                  }}
                />
              </div>
              {conditions.length > 1 && (
                <button
                  onClick={() => updateConditions(conditions.filter((_, j) => j !== i))}
                  style={{
                    padding: "2px 6px", borderRadius: "var(--border-radius)",
                    fontSize: 11, color: "var(--color-error)",
                    background: "transparent", border: "1px solid var(--border-color)",
                    cursor: "pointer", flexShrink: 0,
                  }}
                >
                  &times;
                </button>
              )}
            </div>
          ))}
          <button
            onClick={() => updateConditions([...conditions, { key: "", operator: "truthy" }])}
            style={{
              padding: "3px 10px", borderRadius: "var(--border-radius)",
              border: "1px dashed var(--border-color)", background: "transparent",
              color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
              alignSelf: "flex-start",
            }}
          >
            + Add condition (AND)
          </button>
          <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
            All conditions must be true for the element to be visible.
          </div>
        </div>
      )}
    </div>
  );
}
