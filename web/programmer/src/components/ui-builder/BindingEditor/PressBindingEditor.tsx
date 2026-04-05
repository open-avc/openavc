import { Play } from "lucide-react";
import type { ProjectConfig } from "../../../api/types";
import * as api from "../../../api/restClient";
import { showSuccess, showError } from "../../../store/toastStore";
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

  const testAction = async (action: Record<string, unknown>) => {
    try {
      if (action.action === "device.command" && action.device && action.command) {
        await api.sendCommand(String(action.device), String(action.command), (action.params as Record<string, unknown>) ?? {});
        showSuccess("Command sent");
      } else if (action.action === "macro" && action.macro) {
        await api.executeMacro(String(action.macro));
        showSuccess("Macro triggered");
      } else {
        showError("Cannot test this action type");
      }
    } catch (e) {
      showError(`Test failed: ${e}`);
    }
  };

  const isTestable = (action: Record<string, unknown>): boolean =>
    (action.action === "device.command" && !!action.device && !!action.command) ||
    (action.action === "macro" && !!action.macro);

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
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 4,
          }}>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {actions.length > 1 ? `Action ${i + 1}` : ""}
            </span>
            <div style={{ display: "flex", gap: 4 }}>
              {isTestable(action) && (
                <button
                  onClick={() => testAction(action)}
                  title="Test this action now"
                  style={{
                    display: "flex", alignItems: "center", gap: 3,
                    padding: "2px 6px", borderRadius: "var(--border-radius)",
                    fontSize: 11, color: "var(--accent)",
                    background: "transparent", border: "1px solid var(--border-color)",
                    cursor: "pointer",
                  }}
                >
                  <Play size={10} /> Test
                </button>
              )}
              {actions.length > 1 && (
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
              )}
            </div>
          </div>
          <ActionPicker
            value={action}
            project={project}
            onChange={(v) => updateAction(i, v)}
            forChangeBinding={forChangeBinding}
          />
          {actions.length === 1 && String(action.action || "") && (
            <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
              {isTestable(action) && (
                <button
                  onClick={() => testAction(action)}
                  title="Test this action now"
                  style={{
                    display: "flex", alignItems: "center", gap: 3,
                    padding: "4px 8px", borderRadius: "var(--border-radius)",
                    fontSize: "var(--font-size-sm)", color: "var(--accent)",
                    background: "transparent", border: "1px solid var(--border-color)",
                    cursor: "pointer",
                  }}
                >
                  <Play size={11} /> Test
                </button>
              )}
              <button
                onClick={onClear}
                style={{
                  padding: "4px 8px",
                  borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-error)",
                  background: "transparent",
                  border: "1px solid var(--border-color)",
                }}
              >
                Remove Binding
              </button>
            </div>
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
