import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition } from "../../api/types";

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
    const name = `variable_${varNames.length + 1}`;
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

  const updateVariable = (
    name: string,
    field: string,
    value: unknown
  ) => {
    onUpdate({
      state_variables: {
        ...vars,
        [name]: { ...vars[name], [field]: value },
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
            gridTemplateColumns: "1fr 1fr 1fr auto auto",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-sm)",
            alignItems: "center",
          }}
        >
          <span style={labelStyle}>Variable ID</span>
          <span style={labelStyle}>Label</span>
          <span style={labelStyle}>Help Text</span>
          <span style={labelStyle}>Type</span>
          <span />
        </div>
      )}

      {varNames.map((name) => {
        const v = vars[name];
        return (
          <div
            key={name}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr auto auto",
              gap: "var(--space-sm)",
              marginBottom: "var(--space-xs)",
              alignItems: "center",
            }}
          >
            <input
              value={name}
              readOnly
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
              value={(v as any).help ?? ""}
              onChange={(e) => updateVariable(name, "help", e.target.value)}
              placeholder="Description..."
              style={{ fontSize: "var(--font-size-sm)" }}
            />
            <select
              value={v.type}
              onChange={(e) => updateVariable(name, "type", e.target.value)}
              style={{ width: 90, fontSize: "var(--font-size-sm)" }}
            >
              <option value="string">String</option>
              <option value="integer">Integer</option>
              <option value="boolean">Boolean</option>
              <option value="enum">Enum</option>
            </select>
            <button
              onClick={() => removeVariable(name)}
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <Trash2 size={14} />
            </button>
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
