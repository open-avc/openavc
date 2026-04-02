import type { StepCondition } from "../../api/types";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";

const OPERATORS = [
  { value: "eq", label: "equals" },
  { value: "ne", label: "not equals" },
  { value: "gt", label: "greater than" },
  { value: "lt", label: "less than" },
  { value: "gte", label: "greater or equal" },
  { value: "lte", label: "less or equal" },
  { value: "truthy", label: "is truthy" },
  { value: "falsy", label: "is falsy" },
];

const NO_VALUE_OPS = new Set(["truthy", "falsy"]);

interface ConditionEditorProps {
  condition: StepCondition;
  onChange: (updated: StepCondition) => void;
}

export function ConditionEditor({ condition, onChange }: ConditionEditorProps) {
  const needsValue = !NO_VALUE_OPS.has(condition.operator);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
      <VariableKeyPicker
        value={condition.key}
        onChange={(key) => onChange({ ...condition, key })}
        showDeviceState
        placeholder="State key..."
        style={{ flex: 2, minWidth: 140 }}
      />
      <select
        value={condition.operator}
        onChange={(e) => onChange({ ...condition, operator: e.target.value })}
        style={{ ...selectStyle, flex: 1, minWidth: 100 }}
      >
        {OPERATORS.map((op) => (
          <option key={op.value} value={op.value}>{op.label}</option>
        ))}
      </select>
      {needsValue && (
        <input
          value={condition.value != null ? String(condition.value) : ""}
          onChange={(e) => {
            let v: unknown = e.target.value;
            if (v === "true") v = true;
            else if (v === "false") v = false;
            else if (v !== "" && !isNaN(Number(v))) v = Number(v);
            onChange({ ...condition, value: v });
          }}
          placeholder="Value"
          style={{ ...inputStyle, flex: 1, minWidth: 80 }}
        />
      )}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};

const inputStyle: React.CSSProperties = {
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};
