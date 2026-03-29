import type { ProjectConfig } from "../../../api/types";
import { ActionPicker } from "./ActionPicker";

interface PressBindingEditorProps {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (value: Record<string, unknown>) => void;
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
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      <ActionPicker
        value={value}
        project={project}
        onChange={onChange}
        forChangeBinding={forChangeBinding}
      />
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
