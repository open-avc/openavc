/**
 * An editable list of actions: one picker per action, add, remove, reorder, and
 * a Test button on each one that can be fired now.
 *
 * This is the part the two press editors genuinely shared. They are otherwise
 * different components and stay that way — a button has press styles (tap,
 * toggle, hold-repeat, tap/hold, each with its own config block) and a slider's
 * "on change" does not — but both spell out the same list mechanic underneath,
 * and having spelled it twice they had drifted into offering different things:
 * the slider side could Test an action and could not reorder, the button side
 * could reorder and could not Test. Same picker, same command, two different
 * sets of affordances depending on which control you happened to select.
 *
 * With the mechanic in one place both sides get both, and anything added here
 * later lands on both by construction.
 */
import { Play } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import type { ProjectConfig } from "../../api/types";
import * as api from "../../api/restClient";
import { showSuccess, showError } from "../../store/toastStore";
import { useConnectionStore } from "../../store/connectionStore";
import { ActionPicker } from "../ui-builder/BindingEditor/ActionPicker";
import {
  resolveTestParams,
  testBlockedMessage,
} from "../ui-builder/BindingEditor/testActionParams";

/** Can this action be fired from the properties pane as it stands? */
export function isTestableAction(action: Record<string, unknown> | null | undefined): boolean {
  if (!action) return false;
  return (
    (action.action === "device.command" && !!action.device && !!action.command) ||
    (action.action === "macro" && !!action.macro)
  );
}

async function runTestAction(action: Record<string, unknown>) {
  try {
    if (action.action === "device.command" && action.device && action.command) {
      // Params can hold $-references; a raw send would put the literal
      // "$value" on the wire. Resolve what has a live value, refuse the
      // rest with a message instead of sending a malformed command.
      const result = resolveTestParams(
        (action.params as Record<string, unknown>) ?? {},
        useConnectionStore.getState().liveState,
      );
      if (!result.ok) {
        showError(testBlockedMessage(result));
        return;
      }
      await api.sendCommand(String(action.device), String(action.command), result.params);
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
}

/**
 * Fire one action now. Renders nothing for an action that can't be tested —
 * a half-built command, or a type with nothing to send.
 */
export function ActionTestButton({
  action,
  size = "sm",
}: {
  action: Record<string, unknown> | null | undefined;
  size?: "sm" | "md";
}) {
  if (!isTestableAction(action)) return null;
  return (
    <button
      type="button"
      onClick={() => runTestAction(action as Record<string, unknown>)}
      title="Test this action now"
      style={size === "md" ? testBtnMdStyle : testBtnSmStyle}
    >
      <Play size={size === "md" ? 11 : 10} /> Test
    </button>
  );
}

export interface ActionListEditorProps {
  actions: Record<string, unknown>[];
  project: ProjectConfig;
  onChange: (actions: Record<string, unknown>[]) => void;
  /** The first row reads "Action {numberFrom + 1}". */
  numberFrom?: number;
  /** Caption a lone row too, rather than only numbering once there are several. */
  numberSingle?: boolean;
  /** Give each row its own bordered box. */
  boxed?: boolean;
  /** Offer Remove on a lone row. Off where the caller has its own clear affordance. */
  removeSingle?: boolean;
  /** Show "+ Add another action" under the list. */
  canAdd?: boolean;
  /** Sits between the last row and the add button (a "remove the whole binding"). */
  footer?: ReactNode;
  /* ── passed straight through to each ActionPicker ── */
  forChangeBinding?: boolean;
  eventTokens?: { key: string; label: string }[];
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
}

export function ActionListEditor({
  actions,
  project,
  onChange,
  numberFrom = 0,
  numberSingle = false,
  boxed = false,
  removeSingle = false,
  canAdd = false,
  footer,
  forChangeBinding,
  eventTokens,
  allowedActions,
  navigateOptions,
}: ActionListEditorProps) {
  const update = (index: number, updated: Record<string, unknown>) => {
    const next = [...actions];
    next[index] = updated;
    onChange(next);
  };

  const remove = (index: number) => {
    onChange(actions.filter((_, i) => i !== index));
  };

  const swap = (a: number, b: number) => {
    const next = [...actions];
    [next[a], next[b]] = [next[b], next[a]];
    onChange(next);
  };

  const showCaption = numberSingle || actions.length > 1;
  const showRemove = removeSingle || actions.length > 1;

  return (
    <>
      {actions.map((action, i) => (
        <div key={i} style={boxed ? boxedRowStyle : undefined}>
          <div style={rowHeaderStyle}>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {showCaption ? `Action ${numberFrom + i + 1}` : ""}
            </span>
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <ActionTestButton action={action} />
              {i > 0 && (
                <button
                  type="button"
                  onClick={() => swap(i - 1, i)}
                  title="Move up"
                  style={reorderBtnStyle}
                >
                  &#9650;
                </button>
              )}
              {i < actions.length - 1 && (
                <button
                  type="button"
                  onClick={() => swap(i, i + 1)}
                  title="Move down"
                  style={reorderBtnStyle}
                >
                  &#9660;
                </button>
              )}
              {showRemove && (
                <button type="button" onClick={() => remove(i)} style={removeBtnStyle}>
                  Remove
                </button>
              )}
            </div>
          </div>
          <ActionPicker
            value={action}
            project={project}
            onChange={(v) => update(i, v)}
            forChangeBinding={forChangeBinding}
            eventTokens={eventTokens}
            allowedActions={allowedActions}
            navigateOptions={navigateOptions}
          />
        </div>
      ))}

      {footer}

      {canAdd && (
        <button
          type="button"
          onClick={() => onChange([...actions, { action: "" }])}
          style={addBtnStyle}
        >
          + Add another action
        </button>
      )}
    </>
  );
}

const rowHeaderStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  marginBottom: 4,
};

const boxedRowStyle: CSSProperties = {
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-sm)",
};

const testBtnSmStyle: CSSProperties = {
  display: "flex", alignItems: "center", gap: 3,
  padding: "2px 6px", borderRadius: "var(--border-radius)",
  fontSize: 11, color: "var(--accent)",
  background: "transparent", border: "1px solid var(--border-color)",
  cursor: "pointer",
};

const testBtnMdStyle: CSSProperties = {
  display: "flex", alignItems: "center", gap: 3,
  padding: "4px 8px", borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)", color: "var(--accent)",
  background: "transparent", border: "1px solid var(--border-color)",
  cursor: "pointer",
};

const reorderBtnStyle: CSSProperties = {
  padding: "2px 5px", borderRadius: "var(--border-radius)",
  fontSize: 9, color: "var(--text-muted)",
  background: "transparent", border: "1px solid var(--border-color)",
  cursor: "pointer", lineHeight: 1,
};

const removeBtnStyle: CSSProperties = {
  padding: "2px 6px",
  borderRadius: "var(--border-radius)",
  fontSize: 11,
  color: "var(--color-error)",
  background: "transparent",
  border: "1px solid var(--border-color)",
  cursor: "pointer",
};

const addBtnStyle: CSSProperties = {
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
};
