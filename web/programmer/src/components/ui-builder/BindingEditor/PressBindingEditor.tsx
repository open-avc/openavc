import type { ProjectConfig } from "../../../api/types";
import { ActionPicker } from "./ActionPicker";

interface PressBindingEditorProps {
  value: Record<string, unknown>[];
  project: ProjectConfig;
  onChange: (value: Record<string, unknown>[]) => void;
  onClear: () => void;
  forChangeBinding?: boolean;
}

export function PressBindingEditor({
  value,
  project,
  onChange,
  onClear,
  forChangeBinding,
}: PressBindingEditorProps) {
  const actions = Array.isArray(value) ? value : [];

  const updateAction = (index: number, updated: Record<string, unknown>) => {
    const next = [...actions];
    next[index] = updated;
    onChange(next);
  };

  const removeAction = (index: number) => {
    const next = actions.filter((_, i) => i !== index);
    if (next.length === 0) {
      onClear();
    } else {
      onChange(next);
    }
  };

  const addAction = () => {
    onChange([...actions, { action: "" }]);
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      {actions.map((action, i) => (
        <div key={i}>
          {actions.length > 1 && (
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 4,
            }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Action {i + 1}
              </span>
              <button
                onClick={() => removeAction(i)}
                style={{
                  padding: "2px 6px",
                  borderRadius: "var(--border-radius)",
                  fontSize: 11,
                  color: "var(--color-error)",
                  background: "transparent",
                  border: "1px solid var(--border-color)",
                  cursor: "pointer",
                }}
              >
                Remove
              </button>
            </div>
          )}
          <ActionPicker
            value={action}
            project={project}
            onChange={(v) => updateAction(i, v)}
            forChangeBinding={forChangeBinding}
          />
          {actions.length === 1 && String(action.action || "") && (
            <button
              onClick={onClear}
              style={{
                marginTop: "var(--space-sm)",
                padding: "4px 8px",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-sm)",
                color: "var(--color-error)",
                background: "transparent",
                border: "1px solid var(--border-color)",
                alignSelf: "flex-start",
              }}
            >
              Remove Binding
            </button>
          )}
        </div>
      ))}

      {actions.length === 0 && (
        <ActionPicker
          value={null}
          project={project}
          onChange={(v) => onChange([v])}
          forChangeBinding={forChangeBinding}
        />
      )}

      {actions.length > 0 && String(actions[actions.length - 1]?.action || "") && (
        <button
          onClick={addAction}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 4,
            padding: "5px 10px",
            borderRadius: "var(--border-radius)",
            border: "1px dashed var(--border-color)",
            background: "transparent",
            color: "var(--text-muted)",
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          + Add another action
        </button>
      )}
    </div>
  );
}
