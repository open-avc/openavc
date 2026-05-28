/**
 * ConditionGroupEditor — edits a single state condition or a compound
 * AND (`all`) / OR (`any`) group of conditions, using the shared
 * ConditionEditor for each row.
 *
 * Emits the same shape the panel runtime and the platform condition evaluator
 * understand:
 *   - one condition  -> { key, operator, value? }
 *   - AND of several -> { all: [ ...conditions ] }
 *   - OR of several  -> { any: [ ...conditions ] }
 *   - none           -> undefined  (only when `required` is false)
 *
 * Used by VisibilityProperties (panel element visible_when) and the Stream
 * Deck Surface Configurator (button visible_when + auto_page rules), so a
 * condition behaves and reads identically everywhere.
 */
import type { StepCondition } from "../../api/types";
import { ConditionEditor } from "../macros/ConditionEditor";

export type ConditionGroup =
  | StepCondition
  | { all: StepCondition[] }
  | { any: StepCondition[] };

/** Normalize any stored condition group into a flat list + mode. */
export function unpackConditionGroup(
  group: ConditionGroup | undefined
): { conditions: StepCondition[]; mode: "all" | "any" } {
  if (group && typeof group === "object") {
    const g = group as {
      all?: StepCondition[];
      any?: StepCondition[];
      key?: string;
      operator?: string;
      value?: unknown;
    };
    if (Array.isArray(g.all)) return { conditions: g.all, mode: "all" };
    if (Array.isArray(g.any)) return { conditions: g.any, mode: "any" };
    if (typeof g.key === "string") {
      return {
        conditions: [{ key: g.key, operator: g.operator ?? "eq", value: g.value }],
        mode: "all",
      };
    }
  }
  return { conditions: [], mode: "all" };
}

/** Pack a flat list + mode back into the stored condition group shape. */
export function packConditionGroup(
  conditions: StepCondition[],
  mode: "all" | "any"
): ConditionGroup | undefined {
  if (conditions.length === 0) return undefined;
  if (conditions.length === 1) return conditions[0];
  return mode === "any" ? { any: conditions } : { all: conditions };
}

interface ConditionGroupEditorProps {
  value: ConditionGroup | undefined;
  onChange: (value: ConditionGroup | undefined) => void;
  /**
   * When true the editor always keeps at least one condition row and never
   * emits `undefined` (for contexts where the condition is mandatory, e.g. an
   * auto-page rule or an enabled visible_when binding).
   */
  required?: boolean;
  anyHint?: string;
  allHint?: string;
}

export function ConditionGroupEditor({
  value,
  onChange,
  required = false,
  anyHint,
  allHint,
}: ConditionGroupEditorProps) {
  const unpacked = unpackConditionGroup(value);
  const conditions: StepCondition[] =
    unpacked.conditions.length === 0 && required
      ? [{ key: "", operator: "truthy" }]
      : unpacked.conditions;
  const mode = unpacked.mode;

  const update = (updated: StepCondition[], newMode: "all" | "any" = mode) => {
    if (updated.length === 0) {
      onChange(required ? { key: "", operator: "truthy" } : undefined);
      return;
    }
    onChange(packConditionGroup(updated, newMode));
  };

  const canRemove = conditions.length > 1 || !required;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {conditions.map((cond, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
          <div style={{ flex: 1 }}>
            <ConditionEditor
              condition={cond}
              onChange={(updated) => {
                const next = [...conditions];
                next[i] = updated;
                update(next);
              }}
            />
          </div>
          {canRemove && (
            <button
              onClick={() => update(conditions.filter((_, j) => j !== i))}
              style={removeBtnStyle}
              title="Remove condition"
            >
              &times;
            </button>
          )}
        </div>
      ))}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          onClick={() => update([...conditions, { key: "", operator: "truthy" }])}
          style={addBtnStyle}
        >
          + Add condition
        </button>
        {conditions.length > 1 && (
          <div style={{ display: "flex", gap: 2, fontSize: 11 }}>
            {(["all", "any"] as const).map((m) => (
              <button
                key={m}
                onClick={() => update(conditions, m)}
                style={{
                  padding: "2px 8px",
                  borderRadius: 3,
                  fontSize: 11,
                  cursor: "pointer",
                  border: "1px solid var(--border-color)",
                  background: mode === m ? "var(--accent-dim)" : "transparent",
                  color: mode === m ? "var(--accent)" : "var(--text-muted)",
                  fontWeight: mode === m ? 600 : 400,
                }}
              >
                {m === "all" ? "AND" : "OR"}
              </button>
            ))}
          </div>
        )}
      </div>
      {conditions.length > 1 && (anyHint || allHint) && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          {mode === "any" ? anyHint : allHint}
        </div>
      )}
    </div>
  );
}

const removeBtnStyle: React.CSSProperties = {
  padding: "2px 6px",
  borderRadius: "var(--border-radius)",
  fontSize: 11,
  color: "var(--color-error)",
  background: "transparent",
  border: "1px solid var(--border-color)",
  cursor: "pointer",
  flexShrink: 0,
};

const addBtnStyle: React.CSSProperties = {
  padding: "3px 10px",
  borderRadius: "var(--border-radius)",
  border: "1px dashed var(--border-color)",
  background: "transparent",
  color: "var(--text-muted)",
  fontSize: 12,
  cursor: "pointer",
};
