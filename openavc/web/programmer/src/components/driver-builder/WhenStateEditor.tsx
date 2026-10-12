import { useState } from "react";
import { Plus, X } from "lucide-react";
import type { DriverDefinition, DriverWhenState } from "../../api/types";
import {
  firstGateValue,
  gateValue,
  parseGateValues,
  stateGateVariables,
  type StateGateVariable,
} from "./queryEntryHelpers";

type GateScalar = string | number | boolean;

interface WhenStateEditorProps {
  draft: DriverDefinition;
  gate: DriverWhenState | undefined;
  onChange: (gate: DriverWhenState | undefined) => void;
  /** What is held back, for the row's lead-in: "Send only while". */
  lead: string;
}

function asList(value: DriverWhenState[string] | undefined): GateScalar[] {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

/** Text typed for a number or text variable. Kept as typed while it is being
 *  edited, so "0," does not snap back to "0" before the next value. */
function GateTextInput({
  variable,
  values,
  onCommit,
}: {
  variable: StateGateVariable;
  values: GateScalar[];
  onCommit: (picked: GateScalar[]) => void;
}) {
  const [text, setText] = useState(values.map(String).join(", "));
  const numeric =
    variable.type === "integer" || variable.type === "number" || variable.type === "float";
  return (
    <input
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        const picked = parseGateValues(e.target.value, variable.type);
        if (picked.length) onCommit(picked);
      }}
      placeholder={numeric ? "e.g., 0, 2" : "e.g., on"}
      aria-label={`Values of ${variable.name}`}
      title="The value it must hold. Separate several with commas: the step runs while it holds any of them."
      style={{ width: 120, fontSize: "var(--font-size-sm)" }}
    />
  );
}

/**
 * Edits a `when_state` gate: which of the device's own state variables must
 * hold which values for the entry (or the whole poll) to be sent. One row per
 * variable; every row must hold.
 */
export function WhenStateEditor({ draft, gate, onChange, lead }: WhenStateEditorProps) {
  const variables = stateGateVariables(draft);
  const entries = Object.entries(gate ?? {});
  const used = new Set(entries.map(([name]) => name));

  const commit = (next: [string, DriverWhenState[string]][]) => {
    onChange(next.length ? Object.fromEntries(next) : undefined);
  };

  const setValue = (index: number, picked: GateScalar[]) => {
    const next = [...entries];
    next[index] = [next[index][0], gateValue(picked)];
    commit(next);
  };

  const rename = (index: number, name: string) => {
    const v = variables.find((x) => x.name === name);
    if (!v) return;
    const next = [...entries];
    next[index] = [name, firstGateValue(v)];
    commit(next);
  };

  const add = () => {
    const v = variables.find((x) => !used.has(x.name));
    if (!v) return;
    commit([...entries, [v.name, firstGateValue(v)]]);
  };

  const remove = (index: number) => commit(entries.filter((_, i) => i !== index));

  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-sm)",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginTop: "var(--space-xs)",
    flexWrap: "wrap",
  };

  if (variables.length === 0) {
    return (
      <div style={{ ...rowStyle, color: "var(--text-muted)" }}>
        Declare a state variable first: a state condition reads one.
      </div>
    );
  }

  return (
    <div data-testid="when-state-editor">
      {entries.map(([name, value], i) => {
        const v = variables.find((x) => x.name === name);
        const picked = asList(value);
        return (
          <div key={`${name}-${i}`} style={rowStyle}>
            <span style={{ minWidth: 110 }}>{i === 0 ? lead : "and while"}</span>
            <select
              value={name}
              onChange={(e) => rename(i, e.target.value)}
              aria-label="State variable"
              style={{ width: 150, fontSize: "var(--font-size-sm)" }}
            >
              {!v && <option value={name}>{name} (not declared)</option>}
              {variables
                .filter((x) => x.name === name || !used.has(x.name))
                .map((x) => (
                  <option key={x.name} value={x.name}>
                    {x.label}
                  </option>
                ))}
            </select>
            <span>is</span>
            {v?.type === "boolean" ? (
              <select
                value={String(picked[0] ?? false)}
                onChange={(e) => setValue(i, [e.target.value === "true"])}
                aria-label={`Value of ${name}`}
                style={{ width: 90, fontSize: "var(--font-size-sm)" }}
              >
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            ) : v?.type === "enum" ? (
              <span style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
                {v.values.map((option) => {
                  const checked = picked.map(String).includes(option);
                  return (
                    <label key={option} style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          const next = checked
                            ? picked.filter((p) => String(p) !== option)
                            : [...picked, option];
                          if (next.length) setValue(i, next);
                        }}
                      />
                      {option}
                    </label>
                  );
                })}
              </span>
            ) : v ? (
              <GateTextInput
                key={name}
                variable={v}
                values={picked}
                onCommit={(next) => setValue(i, next)}
              />
            ) : (
              <code>{JSON.stringify(value)}</code>
            )}
            <button
              onClick={() => remove(i)}
              title="Remove this condition"
              aria-label="Remove condition"
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
      {variables.some((x) => !used.has(x.name)) && (
        <button
          onClick={add}
          title="Hold this back while the device reports a state, for example while a unit that any command wakes is in standby"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "2px var(--space-sm)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            marginTop: "var(--space-xs)",
          }}
        >
          <Plus size={12} /> {entries.length ? "And while" : "Add a state condition"}
        </button>
      )}
    </div>
  );
}
