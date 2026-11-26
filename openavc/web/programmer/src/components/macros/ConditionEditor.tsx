import { useState } from "react";
import { HelpCircle } from "lucide-react";
import type { StepCondition } from "../../api/types";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";
import { BooleanOptions } from "../shared/BooleanOptions";
import { valueText } from "../shared/booleanWords";
import { OPERATORS, type Operator } from "../../api/conditionOperators.gen";
import { knownOperator, readsNoValue, referencedKey } from "../../api/conditionEval";
import { operatorPicker } from "./operatorChoices";

/** Each operator's words here; the operators and their order are the
 *  runtime's (operatorChoices.ts). */
const OPERATOR_INFO: Readonly<Record<Operator, { label: string; hint: string }>> = {
  eq: { label: "equals", hint: "Matches the value (text ignores case)" },
  ne: { label: "not equals", hint: "Matches a different reported value (text ignores case)" },
  gt: { label: "greater than", hint: "Numeric comparison: value > target" },
  lt: { label: "less than", hint: "Numeric comparison: value < target" },
  gte: { label: "greater or equal", hint: "Numeric comparison: value >= target" },
  lte: { label: "less or equal", hint: "Numeric comparison: value <= target" },
  truthy: { label: "has a value", hint: "True when the value is not empty, not zero, and not null" },
  falsy: { label: "is empty or zero", hint: "True when the value is empty, zero, false, or null" },
};

const OPERATOR_WORDS = Object.fromEntries(
  OPERATORS.map((op) => [op, OPERATOR_INFO[op].label]),
) as Record<Operator, string>;

/** The words for the operator `op` names, itself or under another spelling. */
function operatorInfo(op: string): { label: string; hint: string } | undefined {
  const named = knownOperator(op);
  return named === null ? undefined : OPERATOR_INFO[named];
}

interface ConditionEditorProps {
  condition: StepCondition;
  onChange: (updated: StepCondition) => void;
  /** Offer $trigger.<field> refs as condition keys. Set only where the
   *  condition is evaluated with a firing trigger's context (a macro's
   *  conditional / skip_if), not for wait_until or trigger guard conditions. */
  showTriggerContext?: boolean;
}

export function ConditionEditor({ condition, onChange, showTriggerContext = false }: ConditionEditorProps) {
  const picker = operatorPicker(condition.operator, OPERATOR_WORDS);
  const needsValue = !readsNoValue(picker.value);
  const [showHelp, setShowHelp] = useState(false);
  const liveValue = useConnectionStore((s) => s.liveState[condition.key]);
  // A value that names a key ($var.wanted) is compared with that key's value.
  const named = needsValue ? referencedKey(condition.value) : null;
  const namedValue = useConnectionStore((s) => (named ? s.liveState[named] : undefined));
  const selectedOp = operatorInfo(picker.value);
  // When the key is a boolean variable, offer a true/false picker for the
  // comparison value instead of a free-text box (matches the trigger editors
  // and the Set Variable step).
  const variables = useProjectStore((s) => s.project?.variables) ?? [];
  const boundVar = condition.key?.startsWith("var.")
    ? variables.find((v) => `var.${v.id}` === condition.key)
    : undefined;
  const isBoolKey = boundVar?.type === "boolean" && named === null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
        <VariableKeyPicker
          value={condition.key}
          onChange={(key) => onChange({ ...condition, key })}
          showDeviceState
          showTriggerContext={showTriggerContext}
          placeholder="State key..."
          style={{ flex: 2, minWidth: 140 }}
        />
        <select
          value={picker.value}
          onChange={(e) => onChange({ ...condition, operator: e.target.value })}
          style={{ ...selectStyle, flex: 1, minWidth: 100 }}
        >
          {picker.choices.map((op) => (
            <option key={op.value} value={op.value}>{op.label}</option>
          ))}
        </select>
        {needsValue && (
          isBoolKey ? (
            <select
              value={condition.value === true ? "true" : condition.value === false ? "false" : ""}
              onChange={(e) => onChange({ ...condition, value: e.target.value === "true" })}
              style={{ ...selectStyle, flex: 1, minWidth: 80 }}
            >
              <option value="">Select...</option>
              <BooleanOptions />
            </select>
          ) : (
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
          )
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
              Current value: <strong>{valueText(liveValue)}</strong>
            </span>
          )}
          {named && (
            <span style={{ marginLeft: 8, color: "var(--text-secondary)" }}>
              Compared with {named}
              {namedValue !== undefined && <>: <strong>{valueText(namedValue)}</strong></>}
            </span>
          )}
        </div>
      )}
      {/* Expanded help */}
      {showHelp && (
        <div style={{
          padding: "var(--space-sm)", borderRadius: 4, fontSize: 11,
          background: "rgba(138,180,147,0.06)", border: "1px solid rgba(138,180,147,0.15)",
          color: "var(--text-secondary)", lineHeight: 1.5,
        }}>
          {OPERATORS.map((op) => (
            <div key={op} style={{ marginBottom: 2 }}>
              <strong>{OPERATOR_INFO[op].label}</strong> &mdash; {OPERATOR_INFO[op].hint}
            </div>
          ))}
          <div style={{ marginTop: 4 }}>
            To compare with another key, start the value with $, as in <code>$var.wanted</code>.
          </div>
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
