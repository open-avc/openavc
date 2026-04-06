import { useState } from "react";
import { HelpCircle } from "lucide-react";
import type { StepCondition } from "../../api/types";
import { useConnectionStore } from "../../store/connectionStore";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";

const OPERATORS = [
  { value: "eq", label: "equals", hint: "Matches exactly (case-sensitive for text)" },
  { value: "ne", label: "not equals", hint: "True when the value is anything except this" },
  { value: "gt", label: "greater than", hint: "Numeric comparison: value > target" },
  { value: "lt", label: "less than", hint: "Numeric comparison: value < target" },
  { value: "gte", label: "greater or equal", hint: "Numeric comparison: value >= target" },
  { value: "lte", label: "less or equal", hint: "Numeric comparison: value <= target" },
  { value: "truthy", label: "has a value", hint: "True when the value is not empty, not zero, and not null" },
  { value: "falsy", label: "is empty or zero", hint: "True when the value is empty, zero, false, or null" },
];

const NO_VALUE_OPS = new Set(["truthy", "falsy"]);

interface ConditionEditorProps {
  condition: StepCondition;
  onChange: (updated: StepCondition) => void;
}

export function ConditionEditor({ condition, onChange }: ConditionEditorProps) {
  const needsValue = !NO_VALUE_OPS.has(condition.operator);
  const [showHelp, setShowHelp] = useState(false);
  const liveValue = useConnectionStore((s) => s.liveState[condition.key]);
  const selectedOp = OPERATORS.find((o) => o.value === condition.operator);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
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
        <span
          onClick={() => setShowHelp(!showHelp)}
          style={{ cursor: "pointer", display: "flex", flexShrink: 0 }}
          title="About condition operators"
        >
          <HelpCircle size={14} style={{ color: "var(--text-muted)" }} />
        </span>
      </div>
      {/* Operator hint */}
      {selectedOp && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", paddingLeft: 2 }}>
          {selectedOp.hint}
          {condition.key && liveValue !== undefined && (
            <span style={{ marginLeft: 8, color: "var(--text-secondary)" }}>
              Current value: <strong>{String(liveValue)}</strong>
            </span>
          )}
        </div>
      )}
      {/* Expanded help */}
      {showHelp && (
        <div style={{
          padding: "var(--space-sm)", borderRadius: 4, fontSize: 11,
          background: "rgba(33,150,243,0.06)", border: "1px solid rgba(33,150,243,0.15)",
          color: "var(--text-secondary)", lineHeight: 1.5,
        }}>
          {OPERATORS.map((op) => (
            <div key={op.value} style={{ marginBottom: 2 }}>
              <strong>{op.label}</strong> &mdash; {op.hint}
            </div>
          ))}
        </div>
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
