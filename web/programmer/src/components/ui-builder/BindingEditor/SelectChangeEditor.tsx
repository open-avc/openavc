import type { ProjectConfig, UIElementOption } from "../../../api/types";
import { ActionPicker } from "./ActionPicker";

interface SelectChangeEditorProps {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  options: UIElementOption[];
  onChange: (value: Record<string, unknown>) => void;
  onClear: () => void;
}

export function SelectChangeEditor({
  value,
  project,
  options,
  onChange,
  onClear,
}: SelectChangeEditorProps) {
  const actionMap = (value?.map as Record<string, Record<string, unknown>>) ?? {};

  const handleOptionAction = (optionValue: string, action: Record<string, unknown>) => {
    onChange({
      action: "value_map",
      map: { ...actionMap, [optionValue]: action },
    });
  };

  const handleClearOption = (optionValue: string) => {
    const newMap = { ...actionMap };
    delete newMap[optionValue];
    if (Object.keys(newMap).length === 0) {
      onClear();
    } else {
      onChange({ action: "value_map", map: newMap });
    }
  };

  if (options.length === 0) {
    return (
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          padding: "var(--space-sm)",
        }}
      >
        Add options to this select element first (in Basic properties above).
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-md)",
      }}
    >
      {options.map((opt) => {
        const optAction = actionMap[opt.value] || null;
        return (
          <div
            key={opt.value}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "4px 8px",
                background: "var(--bg-surface)",
                fontSize: "var(--font-size-sm)",
                fontWeight: 500,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>{opt.label}</span>
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-muted)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {opt.value}
              </span>
            </div>
            <div style={{ padding: "var(--space-sm)" }}>
              <ActionPicker
                value={optAction}
                project={project}
                onChange={(a) => handleOptionAction(opt.value, a)}
              />
              {optAction && (
                <button
                  onClick={() => handleClearOption(opt.value)}
                  style={{
                    marginTop: "var(--space-xs)",
                    padding: "2px 6px",
                    borderRadius: "var(--border-radius)",
                    fontSize: 11,
                    color: "var(--color-error)",
                    background: "transparent",
                    border: "1px solid var(--border-color)",
                    cursor: "pointer",
                  }}
                >
                  Clear
                </button>
              )}
            </div>
          </div>
        );
      })}

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
            cursor: "pointer",
          }}
        >
          Remove All Bindings
        </button>
      )}
    </div>
  );
}
