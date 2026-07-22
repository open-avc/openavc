import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition } from "../../api/types";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";
import {
  applyStateVarTypeChange,
  nextStateVariableName,
} from "./stateVariableHelpers";

const sanitizeVariableName = (raw: string) =>
  raw.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();

interface StateVariableEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function StateVariableEditor({
  draft,
  onUpdate,
}: StateVariableEditorProps) {
  const vars = draft.state_variables;
  const varNames = Object.keys(vars);

  const addVariable = () => {
    const name = nextStateVariableName(varNames);
    onUpdate({
      state_variables: {
        ...vars,
        [name]: { type: "string", label: "New Variable" },
      },
    });
  };

  const removeVariable = (name: string) => {
    const next = { ...vars };
    delete next[name];
    onUpdate({ state_variables: next });
  };

  const renameVariable = (oldName: string, newName: string): RenameResult => {
    const cleaned = sanitizeVariableName(newName);
    if (!cleaned) return { ok: false, reason: "ID can't be empty." };
    if (cleaned === oldName) return { ok: true };
    if (cleaned in vars) {
      return { ok: false, reason: `"${cleaned}" already exists.` };
    }
    const next: typeof vars = {};
    for (const [k, v] of Object.entries(vars)) {
      next[k === oldName ? cleaned : k] = v;
    }
    onUpdate({ state_variables: next });
    return { ok: true };
  };

  const updateVariable = (
    name: string,
    field: string,
    value: unknown
  ) => {
    const merged = { ...vars[name], [field]: value } as Record<string, unknown>;
    if (value === undefined) delete merged[field];
    onUpdate({
      state_variables: {
        ...vars,
        [name]: merged as unknown as (typeof vars)[string],
      },
    });
  };

  const labelStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
  };

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Define the state variables this driver will expose. These are updated
        by response patterns and visible in the device state.
      </p>

      {varNames.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr auto auto auto",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-sm)",
            alignItems: "center",
          }}
        >
          <span style={labelStyle}>Variable ID</span>
          <span style={labelStyle}>Label</span>
          <span style={labelStyle}>Help Text</span>
          <span style={labelStyle}>Type</span>
          <span style={labelStyle} title="Mark variables a control would bind to — the UI Builder's value picker lists them first. Unmarked variables stay available.">Control</span>
          <span />
        </div>
      )}

      {varNames.map((name) => {
        const v = vars[name];
        const isNumeric = v.type === "integer" || v.type === "number" || v.type === "float";
        return (
          <div key={name} style={{ marginBottom: "var(--space-xs)" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr auto auto auto",
                gap: "var(--space-sm)",
                alignItems: "center",
              }}
            >
              <IdRenameInput
                value={name}
                sanitize={sanitizeVariableName}
                onCommit={(next) => renameVariable(name, next)}
                style={{
                  fontSize: "var(--font-size-sm)",
                  fontFamily: "var(--font-mono)",
                }}
              />
              <input
                value={v.label}
                onChange={(e) => updateVariable(name, "label", e.target.value)}
                style={{ fontSize: "var(--font-size-sm)" }}
              />
              <input
                value={v.help ?? ""}
                onChange={(e) => updateVariable(name, "help", e.target.value)}
                placeholder="Description..."
                style={{ fontSize: "var(--font-size-sm)" }}
              />
              <select
                value={v.type}
                onChange={(e) => {
                  // One atomic write: type switch + stripping type-incompatible
                  // fields together, so no update clobbers another.
                  onUpdate({
                    state_variables: {
                      ...vars,
                      [name]: applyStateVarTypeChange(v, e.target.value),
                    },
                  });
                }}
                style={{ width: 100, fontSize: "var(--font-size-sm)" }}
              >
                <option value="string">String</option>
                <option value="integer">Integer</option>
                <option value="number">Number</option>
                <option value="float">Float</option>
                <option value="boolean">Boolean</option>
                <option value="enum">Enum</option>
              </select>
              <input
                type="checkbox"
                checked={v.control ?? false}
                onChange={(e) =>
                  updateVariable(name, "control", e.target.checked || undefined)
                }
                title="Mark as a control variable — the UI Builder's value picker lists it first"
                style={{ justifySelf: "center" }}
              />
              <button
                onClick={() => removeVariable(name)}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </div>

            {isNumeric && (
              <div
                style={{
                  marginTop: "var(--space-xs)",
                  marginLeft: "var(--space-sm)",
                  paddingLeft: "var(--space-sm)",
                  borderLeft: "2px solid var(--border-color)",
                  display: "grid",
                  gridTemplateColumns: "100px 100px 100px 100px 1fr",
                  gap: "var(--space-sm)",
                  alignItems: "center",
                }}
              >
                <input
                  type="number"
                  value={v.min ?? ""}
                  onChange={(e) => {
                    const raw = e.target.value;
                    updateVariable(
                      name,
                      "min",
                      raw === ""
                        ? undefined
                        : v.type === "integer"
                          ? parseInt(raw, 10)
                          : parseFloat(raw),
                    );
                  }}
                  placeholder="min"
                  style={{ fontSize: "var(--font-size-sm)" }}
                />
                <input
                  type="number"
                  value={v.max ?? ""}
                  onChange={(e) => {
                    const raw = e.target.value;
                    updateVariable(
                      name,
                      "max",
                      raw === ""
                        ? undefined
                        : v.type === "integer"
                          ? parseInt(raw, 10)
                          : parseFloat(raw),
                    );
                  }}
                  placeholder="max"
                  style={{ fontSize: "var(--font-size-sm)" }}
                />
                <input
                  type="number"
                  value={v.step ?? ""}
                  onChange={(e) => {
                    const raw = e.target.value;
                    updateVariable(
                      name,
                      "step",
                      raw === "" ? undefined : parseFloat(raw),
                    );
                  }}
                  placeholder="step"
                  style={{ fontSize: "var(--font-size-sm)" }}
                />
                <input
                  value={v.unit ?? ""}
                  onChange={(e) =>
                    updateVariable(name, "unit", e.target.value || undefined)
                  }
                  placeholder="unit (dB)"
                  style={{ fontSize: "var(--font-size-sm)" }}
                />
                <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                  Numeric bounds and unit — used by panel sliders, the UI
                  Builder&apos;s range matching, and the simulator UI.
                </div>
              </div>
            )}

            {v.type === "enum" && (
              <div
                style={{
                  marginTop: "var(--space-xs)",
                  marginLeft: "var(--space-sm)",
                  paddingLeft: "var(--space-sm)",
                  borderLeft: "2px solid var(--border-color)",
                }}
              >
                <input
                  value={(v.values ?? []).join(", ")}
                  onChange={(e) => {
                    const values = e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean);
                    updateVariable(name, "values", values);
                  }}
                  placeholder="Comma-separated values, e.g.: off, on, warming, cooling"
                  style={{
                    width: "100%",
                    fontSize: "var(--font-size-sm)",
                    fontFamily: "var(--font-mono)",
                  }}
                />
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    marginTop: 2,
                  }}
                >
                  Allowed values for this enum, separated by commas.
                </div>
              </div>
            )}
          </div>
        );
      })}

      <button
        onClick={addVariable}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-md)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          fontSize: "var(--font-size-sm)",
          marginTop: "var(--space-sm)",
        }}
      >
        <Plus size={14} /> Add State Variable
      </button>
    </div>
  );
}
