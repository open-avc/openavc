/**
 * Per-trigger-type editors + conditions + advanced settings.
 */
import { useState } from "react";
import { Trash2, ChevronDown, ChevronRight, Plus } from "lucide-react";
import type { TriggerConfig, TriggerCondition } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";
import {
  STATE_OPERATORS,
  CONDITION_OPERATORS,
  CRON_PRESETS,
  isValidCron,
  describeCron,
  EVENT_CATEGORIES,
} from "./triggerHelpers";

interface TriggerEditorProps {
  trigger: TriggerConfig;
  onChange: (updated: TriggerConfig) => void;
}

export function TriggerEditor({ trigger, onChange }: TriggerEditorProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const update = (patch: Partial<TriggerConfig>) => {
    onChange({ ...trigger, ...patch });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
      {/* Type-specific editor */}
      {trigger.type === "schedule" && (
        <ScheduleEditor trigger={trigger} onChange={update} />
      )}
      {trigger.type === "state_change" && (
        <StateChangeEditor trigger={trigger} onChange={update} />
      )}
      {trigger.type === "event" && (
        <EventEditor trigger={trigger} onChange={update} />
      )}
      {trigger.type === "startup" && (
        <StartupEditor trigger={trigger} onChange={update} />
      )}

      {/* Conditions (shared) */}
      <ConditionsEditor
        conditions={trigger.conditions ?? []}
        onChange={(conditions) => update({ conditions })}
      />

      {/* Advanced */}
      <div>
        <div
          onClick={() => setShowAdvanced(!showAdvanced)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            cursor: "pointer",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
          }}
        >
          {showAdvanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          Advanced
        </div>
        {showAdvanced && (
          <div style={{ marginTop: "var(--space-sm)", display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            <div style={rowStyle}>
              <label style={labelStyle}>Overlap</label>
              <select
                value={trigger.overlap ?? "skip"}
                onChange={(e) => update({ overlap: e.target.value })}
                style={inputStyle}
              >
                <option value="skip">Skip (recommended)</option>
                <option value="queue">Queue</option>
                <option value="allow">Allow concurrent</option>
              </select>
            </div>
            <div style={rowStyle}>
              <label style={labelStyle}>Cooldown</label>
              <input
                type="number"
                min={0}
                value={trigger.cooldown_seconds ?? 0}
                onChange={(e) => update({ cooldown_seconds: parseFloat(e.target.value) || 0 })}
                style={{ ...inputStyle, maxWidth: 100 }}
              />
              <span style={unitStyle}>seconds</span>
            </div>
            <div style={hintStyle}>
              Cooldown prevents the trigger from firing more than once within this window.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// --- Schedule Editor ---

function ScheduleEditor({
  trigger,
  onChange,
}: {
  trigger: TriggerConfig;
  onChange: (patch: Partial<TriggerConfig>) => void;
}) {
  const cron = trigger.cron ?? "";
  const parts = cron.split(/\s+/);
  const hasValidParts = parts.length === 5;

  // Detect current preset
  const currentHour = hasValidParts && parts[1] !== "*" ? parseInt(parts[1]) : 18;
  const currentMinute = hasValidParts && parts[0] !== "*" ? parseInt(parts[0]) : 0;

  // Detect preset from cron
  const detectPreset = (): number => {
    if (!cron) return 4; // Custom
    if (cron === "0 * * * *") return 3; // Every hour
    if (hasValidParts) {
      const dow = parts[4];
      if (dow === "*") return 0; // Every day
      if (dow === "1-5") return 1; // Weekdays
      if (dow === "0,6" || dow === "6,0") return 2; // Weekends
    }
    return 4; // Custom
  };

  const [presetIdx, setPresetIdx] = useState(detectPreset);
  const [showRaw, setShowRaw] = useState(false);

  const handlePresetChange = (idx: number) => {
    setPresetIdx(idx);
    if (idx < CRON_PRESETS.length) {
      const newCron = CRON_PRESETS[idx].make(currentHour, currentMinute);
      if (newCron) onChange({ cron: newCron });
    }
  };

  const handleTimeChange = (hour: number, minute: number) => {
    if (presetIdx < CRON_PRESETS.length && presetIdx !== 3 && presetIdx !== 4) {
      onChange({ cron: CRON_PRESETS[presetIdx].make(hour, minute) });
    }
  };

  // Day toggles for weekday view
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const DAY_VALUES = [1, 2, 3, 4, 5, 6, 0]; // cron day numbers

  const getActiveDays = (): Set<number> => {
    if (!hasValidParts) return new Set();
    const dow = parts[4];
    if (dow === "*") return new Set(DAY_VALUES);
    if (dow === "1-5") return new Set([1, 2, 3, 4, 5]);
    if (dow === "0,6") return new Set([0, 6]);
    const days = new Set<number>();
    for (const part of dow.split(",")) {
      if (part.includes("-")) {
        const [start, end] = part.split("-").map(Number);
        for (let i = start; i <= end; i++) days.add(i);
      } else {
        days.add(Number(part));
      }
    }
    return days;
  };

  const toggleDay = (dayNum: number) => {
    const active = getActiveDays();
    if (active.has(dayNum)) active.delete(dayNum);
    else active.add(dayNum);
    if (active.size === 0) return;
    const sorted = [...active].sort((a, b) => a - b);
    const dowStr = sorted.join(",");
    onChange({ cron: `${currentMinute} ${currentHour} * * ${dowStr}` });
    setPresetIdx(4); // Switch to custom since user is manually toggling
  };

  const activeDays = getActiveDays();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div style={rowStyle}>
        <label style={labelStyle}>Preset</label>
        <select
          value={presetIdx}
          onChange={(e) => handlePresetChange(Number(e.target.value))}
          style={inputStyle}
        >
          {CRON_PRESETS.map((p, i) => (
            <option key={i} value={i}>{p.label}</option>
          ))}
        </select>
      </div>

      {/* Day toggles */}
      {presetIdx !== 3 && (
        <div style={{ display: "flex", gap: 4, marginLeft: 78 }}>
          {DAYS.map((day, i) => (
            <button
              key={day}
              onClick={() => toggleDay(DAY_VALUES[i])}
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                border: "1px solid var(--border-color)",
                background: activeDays.has(DAY_VALUES[i]) ? "var(--accent)" : "transparent",
                color: activeDays.has(DAY_VALUES[i]) ? "#fff" : "var(--text-secondary)",
                fontSize: 11,
                cursor: "pointer",
                fontWeight: activeDays.has(DAY_VALUES[i]) ? 600 : 400,
              }}
            >
              {day}
            </button>
          ))}
        </div>
      )}

      {/* Time */}
      {presetIdx !== 3 && presetIdx !== 4 && (
        <div style={rowStyle}>
          <label style={labelStyle}>Time</label>
          <input
            type="number"
            min={0}
            max={23}
            value={currentHour}
            onChange={(e) => handleTimeChange(parseInt(e.target.value) || 0, currentMinute)}
            style={{ ...inputStyle, maxWidth: 60, textAlign: "center" }}
          />
          <span style={{ color: "var(--text-secondary)" }}>:</span>
          <input
            type="number"
            min={0}
            max={59}
            value={currentMinute}
            onChange={(e) => handleTimeChange(currentHour, parseInt(e.target.value) || 0)}
            style={{ ...inputStyle, maxWidth: 60, textAlign: "center" }}
          />
        </div>
      )}

      {/* Raw cron toggle */}
      <div>
        <div
          onClick={() => setShowRaw(!showRaw)}
          style={{ fontSize: 11, color: "var(--text-muted)", cursor: "pointer" }}
        >
          {showRaw ? "▾" : "▸"} Raw cron expression
        </div>
        {showRaw && (
          <input
            type="text"
            value={cron}
            onChange={(e) => onChange({ cron: e.target.value })}
            placeholder="0 18 * * 1-5"
            style={{ ...inputStyle, marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
        )}
      </div>

      {/* Validation + Preview */}
      {cron && !isValidCron(cron) && (
        <div style={{ fontSize: 12, color: "var(--color-error, #f44336)", fontWeight: 500 }}>
          Invalid cron expression — must have 5 fields (minute hour day month weekday)
        </div>
      )}
      {cron && isValidCron(cron) && (
        <div style={{ fontSize: 12, color: "var(--accent)", fontWeight: 500 }}>
          {describeCron(cron)}
        </div>
      )}
    </div>
  );
}

// --- State Change Editor ---

function StateChangeEditor({
  trigger,
  onChange,
}: {
  trigger: TriggerConfig;
  onChange: (patch: Partial<TriggerConfig>) => void;
}) {
  const variables = useProjectStore((s) => s.project?.variables) ?? [];
  const op = trigger.state_operator ?? "any";
  const needsValue = !["any", "truthy", "falsy"].includes(op);

  // Determine variable type for value input
  const selectedKey = trigger.state_key ?? "";
  const selectedVar = selectedKey.startsWith("var.")
    ? variables.find((v) => `var.${v.id}` === selectedKey)
    : undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div style={rowStyle}>
        <label style={labelStyle}>When</label>
        <VariableKeyPicker
          value={trigger.state_key ?? ""}
          onChange={(key) => onChange({ state_key: key })}
          showDeviceState
          placeholder="Select state key..."
          style={{ flex: 1 }}
        />
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>Operator</label>
        <select
          value={op}
          onChange={(e) => onChange({ state_operator: e.target.value })}
          style={inputStyle}
        >
          {STATE_OPERATORS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
      {needsValue && (
        <div style={rowStyle}>
          <label style={labelStyle}>Value</label>
          {selectedVar?.type === "boolean" ? (
            <select
              value={trigger.state_value != null ? String(trigger.state_value) : ""}
              onChange={(e) => onChange({ state_value: e.target.value === "true" })}
              style={inputStyle}
            >
              <option value="">Select...</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : (
            <input
              type={selectedVar?.type === "number" ? "number" : "text"}
              value={trigger.state_value != null ? String(trigger.state_value) : ""}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "true") onChange({ state_value: true });
                else if (v === "false") onChange({ state_value: false });
                else if (v !== "" && !isNaN(Number(v))) onChange({ state_value: Number(v) });
                else onChange({ state_value: v });
              }}
              placeholder="Value"
              style={inputStyle}
            />
          )}
        </div>
      )}

      {/* Delay */}
      <div style={rowStyle}>
        <label style={labelStyle}>Delay</label>
        <input
          type="number"
          min={0}
          value={trigger.delay_seconds ?? 0}
          onChange={(e) => onChange({ delay_seconds: parseFloat(e.target.value) || 0 })}
          style={{ ...inputStyle, maxWidth: 100 }}
        />
        <span style={unitStyle}>seconds</span>
      </div>
      <div style={hintStyle}>
        Wait, then re-check before executing. Good for occupancy timeouts.
      </div>

      {/* Debounce */}
      <div style={rowStyle}>
        <label style={labelStyle}>Debounce</label>
        <input
          type="number"
          min={0}
          value={trigger.debounce_seconds ?? 0}
          onChange={(e) => onChange({ debounce_seconds: parseFloat(e.target.value) || 0 })}
          style={{ ...inputStyle, maxWidth: 100 }}
        />
        <span style={unitStyle}>seconds</span>
      </div>
      <div style={hintStyle}>
        Wait for changes to settle. Good for flickering devices.
      </div>
    </div>
  );
}

// --- Event Editor ---

function EventEditor({
  trigger,
  onChange,
}: {
  trigger: TriggerConfig;
  onChange: (patch: Partial<TriggerConfig>) => void;
}) {
  const project = useProjectStore((s) => s.project);
  const devices = project?.devices ?? [];
  const macros = project?.macros ?? [];
  const schedules = project?.schedules ?? [];

  const [category, setCategory] = useState(0);

  const catDef = EVENT_CATEGORIES[category];
  const options = catDef?.options(devices, macros, schedules) ?? [];
  const isCustom = catDef?.label === "Custom";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div style={rowStyle}>
        <label style={labelStyle}>Category</label>
        <select
          value={category}
          onChange={(e) => setCategory(Number(e.target.value))}
          style={inputStyle}
        >
          {EVENT_CATEGORIES.map((c, i) => (
            <option key={i} value={i}>{c.label}</option>
          ))}
        </select>
      </div>

      {isCustom ? (
        <div style={rowStyle}>
          <label style={labelStyle}>Pattern</label>
          <input
            type="text"
            value={trigger.event_pattern ?? ""}
            onChange={(e) => onChange({ event_pattern: e.target.value })}
            placeholder="e.g. custom.my_event"
            style={{ ...inputStyle, fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
        </div>
      ) : options.length > 0 ? (
        <div style={rowStyle}>
          <label style={labelStyle}>Event</label>
          <select
            value={trigger.event_pattern ?? ""}
            onChange={(e) => onChange({ event_pattern: e.target.value })}
            style={inputStyle}
          >
            <option value="">Select event...</option>
            {options.map((o) => (
              <option key={o.pattern} value={o.pattern}>{o.label}</option>
            ))}
          </select>
        </div>
      ) : (
        <div style={hintStyle}>
          No events available in this category. Add devices, macros, or schedules first.
        </div>
      )}

      {trigger.event_pattern && (
        <div style={{ fontSize: 12, color: "var(--accent)", fontFamily: "var(--font-mono)" }}>
          {trigger.event_pattern}
        </div>
      )}
    </div>
  );
}

// --- Startup Editor ---

function StartupEditor({
  trigger,
  onChange,
}: {
  trigger: TriggerConfig;
  onChange: (patch: Partial<TriggerConfig>) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.4 }}>
        Runs when the system starts up. Use the delay to wait for devices to connect.
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>Delay</label>
        <input
          type="number"
          min={0}
          value={trigger.delay_seconds ?? 5}
          onChange={(e) => onChange({ delay_seconds: parseFloat(e.target.value) || 0 })}
          style={{ ...inputStyle, maxWidth: 100 }}
        />
        <span style={unitStyle}>seconds</span>
      </div>
      <div style={hintStyle}>
        Wait for devices to connect before executing.
      </div>
    </div>
  );
}

// --- Conditions Editor ---

function ConditionsEditor({
  conditions,
  onChange,
}: {
  conditions: TriggerCondition[];
  onChange: (conditions: TriggerCondition[]) => void;
}) {
  const addCondition = () => {
    onChange([...conditions, { key: "", operator: "eq", value: "" }]);
  };

  const updateCondition = (index: number, patch: Partial<TriggerCondition>) => {
    const updated = [...conditions];
    updated[index] = { ...updated[index], ...patch };
    onChange(updated);
  };

  const deleteCondition = (index: number) => {
    onChange(conditions.filter((_, i) => i !== index));
  };

  const variables = useProjectStore((s) => s.project?.variables) ?? [];

  return (
    <div>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          fontWeight: 600,
          marginBottom: "var(--space-sm)",
        }}
      >
        Conditions {conditions.length > 0 && `(${conditions.length})`}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        Only run if ALL conditions are true
      </div>

      {conditions.map((cond, i) => {
        const needsValue = !["truthy", "falsy"].includes(cond.operator);
        const selectedKey = cond.key ?? "";
        const selectedVar = selectedKey.startsWith("var.")
          ? variables.find((v) => `var.${v.id}` === selectedKey)
          : undefined;

        return (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              marginBottom: "var(--space-xs)",
              padding: "var(--space-xs)",
              background: "var(--bg-surface)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
            }}
          >
            <VariableKeyPicker
              value={cond.key}
              onChange={(key) => updateCondition(i, { key })}
              showDeviceState
              placeholder="Key..."
              style={{ flex: 1 }}
            />
            <select
              value={cond.operator}
              onChange={(e) => updateCondition(i, { operator: e.target.value })}
              style={{ ...inputStyle, flex: "none", width: 100 }}
            >
              {CONDITION_OPERATORS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            {needsValue && (
              selectedVar?.type === "boolean" ? (
                <select
                  value={cond.value != null ? String(cond.value) : ""}
                  onChange={(e) => updateCondition(i, { value: e.target.value === "true" })}
                  style={{ ...inputStyle, flex: "none", width: 80 }}
                >
                  <option value="">...</option>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  type="text"
                  value={cond.value != null ? String(cond.value) : ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "true") updateCondition(i, { value: true });
                    else if (v === "false") updateCondition(i, { value: false });
                    else if (v !== "" && !isNaN(Number(v))) updateCondition(i, { value: Number(v) });
                    else updateCondition(i, { value: v });
                  }}
                  placeholder="Value"
                  style={{ ...inputStyle, flex: "none", width: 80 }}
                />
              )
            )}
            <button
              onClick={() => deleteCondition(i)}
              style={iconBtnStyle}
              title="Remove condition"
            >
              <Trash2 size={14} />
            </button>
          </div>
        );
      })}

      <button
        onClick={addCondition}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "2px var(--space-sm)",
          borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)",
          background: "transparent",
          color: "var(--text-muted)",
          fontSize: 11,
          cursor: "pointer",
        }}
      >
        <Plus size={12} /> Add Condition
      </button>
    </div>
  );
}

// --- Shared styles ---

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
};

const labelStyle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  minWidth: 70,
  flexShrink: 0,
};

const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};

const unitStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  flexShrink: 0,
};

const hintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  fontStyle: "italic",
  marginTop: -4,
  marginLeft: 78,
};

const iconBtnStyle: React.CSSProperties = {
  display: "flex",
  padding: 2,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};
