import { useState } from "react";
import type { ProjectConfig } from "../../../api/types";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";

interface TextBindingEditorProps {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (value: Record<string, unknown>) => void;
  onClear: () => void;
}

type TextMode = "static" | "state" | "conditional" | "macro_progress";

export function TextBindingEditor({
  value,
  project,
  onChange,
  onClear,
}: TextBindingEditorProps) {
  const currentMode: TextMode = value
    ? value.source === "macro_progress"
      ? "macro_progress"
      : value.condition
        ? "conditional"
        : "state"
    : "static";

  const [mode, setMode] = useState<TextMode>(currentMode);

  const handleModeChange = (newMode: TextMode) => {
    setMode(newMode);
    if (newMode === "static") {
      onClear();
    } else if (newMode === "state") {
      onChange({ source: "state", key: value?.key || "" });
    } else if (newMode === "macro_progress") {
      onChange({ source: "macro_progress", macro: "", idle_text: "Ready" });
    } else {
      onChange({
        source: "state",
        key: value?.key || "",
        condition: { equals: "" },
        text_true: "",
        text_false: "",
      });
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      <div style={helpStyle}>
        Display a live value on the panel. Use device properties to show hardware
        status, or variables for custom text.
      </div>

      {/* Mode radio buttons */}
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        {(
          [
            { key: "static", label: "Static" },
            { key: "state", label: "State Variable" },
            { key: "conditional", label: "Conditional" },
            { key: "macro_progress", label: "Macro Progress" },
          ] as const
        ).map(({ key, label }) => (
          <label
            key={key}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: "var(--font-size-sm)",
              cursor: "pointer",
            }}
          >
            <input
              type="radio"
              name="text-mode"
              checked={mode === key}
              onChange={() => handleModeChange(key)}
            />
            {label}
          </label>
        ))}
      </div>

      {/* State Variable mode */}
      {mode === "state" && (
        <>
          <div>
            <label style={labelStyle}>State Key</label>
            <VariableKeyPicker
              value={String(value?.key || "")}
              onChange={(key) =>
                onChange({
                  source: "state",
                  key,
                  format: value?.format as string | undefined,
                })
              }
              placeholder="Select variable..."
            />
          </div>
          <div>
            <label style={labelStyle}>
              Format (use {"{value}"} as placeholder)
            </label>
            <input
              value={String(value?.format || "")}
              onChange={(e) =>
                onChange({
                  source: "state",
                  key: value?.key,
                  format: e.target.value || undefined,
                })
              }
              placeholder="e.g., Active: {value}"
              style={inputStyle}
            />
          </div>
        </>
      )}

      {/* Conditional mode */}
      {mode === "conditional" && (
        <>
          <div>
            <label style={labelStyle}>State Key</label>
            <VariableKeyPicker
              value={String(value?.key || "")}
              onChange={(key) =>
                onChange({ ...value!, key })
              }
              placeholder="Select variable..."
            />
          </div>
          <div>
            <label style={labelStyle}>Equals</label>
            <input
              value={String(
                (value?.condition as Record<string, unknown>)?.equals ?? "",
              )}
              onChange={(e) => {
                let parsed: unknown = e.target.value;
                if (parsed === "true") parsed = true;
                else if (parsed === "false") parsed = false;
                onChange({
                  ...value!,
                  condition: { equals: parsed },
                });
              }}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Text when true</label>
            <input
              value={String(value?.text_true || "")}
              onChange={(e) =>
                onChange({ ...value!, text_true: e.target.value })
              }
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Text when false</label>
            <input
              value={String(value?.text_false || "")}
              onChange={(e) =>
                onChange({ ...value!, text_false: e.target.value })
              }
              style={inputStyle}
            />
          </div>
        </>
      )}

      {/* Macro Progress mode */}
      {mode === "macro_progress" && (
        <>
          <div style={helpStyle}>
            Shows the current step description while a macro is running.
            When the macro is idle, shows the text below.
          </div>
          <div>
            <label style={labelStyle}>Macro</label>
            <select
              value={String(value?.macro || "")}
              onChange={(e) =>
                onChange({ source: "macro_progress", macro: e.target.value, idle_text: value?.idle_text || "Ready" })
              }
              style={inputStyle}
            >
              <option value="">Select macro...</option>
              {project.macros.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Idle Text</label>
            <input
              value={String(value?.idle_text || "")}
              onChange={(e) =>
                onChange({ source: "macro_progress", macro: value?.macro, idle_text: e.target.value })
              }
              placeholder="e.g., Ready"
              style={inputStyle}
            />
          </div>
        </>
      )}

      {value && (
        <button
          onClick={onClear}
          style={{
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
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
};

const helpStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  lineHeight: 1.4,
  fontStyle: "italic",
};
