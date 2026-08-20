import type { ProjectConfig } from "../../../api/types";
import { ActionPicker } from "./ActionPicker";
import { ActionListEditor } from "../../shared/ActionListEditor";

interface PressBindingEditorProps {
  value: Record<string, unknown>[];
  project: ProjectConfig;
  onChange: (value: Record<string, unknown>[]) => void;
  onClear: () => void;
  forChangeBinding?: boolean;
  // UI-event tokens this binding slot can deliver ($value/$input/...), passed
  // through to each command param's "$" picker.
  eventTokens?: { key: string; label: string }[];
}

/**
 * The actions one non-button interaction runs — a slider's "on change", a
 * matrix cell's "on route", a select's "on select". A flat list, in order,
 * with no press styles: those belong to a button and live in
 * `shared/ButtonBindingEditor`. The list itself is the shared
 * `ActionListEditor`, which is what both editors were spelling out separately.
 */
export function PressBindingEditor({
  value,
  project,
  onChange,
  onClear,
  forChangeBinding,
  eventTokens,
}: PressBindingEditorProps) {
  const actions = Array.isArray(value) ? value : [];
  const lastAction = actions[actions.length - 1];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      <ActionListEditor
        actions={actions}
        project={project}
        // Removing the last action removes the binding, rather than leaving an
        // empty slot behind that still reads as configured.
        onChange={(next) => (next.length === 0 ? onClear() : onChange(next))}
        canAdd={!!String(lastAction?.action || "")}
        forChangeBinding={forChangeBinding}
        eventTokens={eventTokens}
        footer={
          actions.length === 1 && String(actions[0].action || "") ? (
            <button
              onClick={onClear}
              style={{
                padding: "var(--space-xs) var(--space-sm)",
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
          ) : undefined
        }
      />

      {actions.length === 0 && (
        <ActionPicker
          value={null}
          project={project}
          onChange={(v) => onChange([v])}
          forChangeBinding={forChangeBinding}
          eventTokens={eventTokens}
        />
      )}
    </div>
  );
}
