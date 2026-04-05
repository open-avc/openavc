/**
 * Per-trigger-type editors + conditions + advanced settings.
 */
import { useState, useEffect } from "react";
import { Trash2, ChevronDown, ChevronRight, Plus, HelpCircle } from "lucide-react";
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
import * as api from "../../api/restClient";

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

// Cron examples (8.5)
const CRON_EXAMPLES = [
  { label: "Every weekday at 8:00 AM", cron: "0 8 * * 1-5" },
  { label: "Every weekday at 6:00 PM", cron: "0 18 * * 1-5" },
  { label: "Every day at midnight", cron: "0 0 * * *" },
  { label: "Every hour", cron: "0 * * * *" },
  { label: "Every 15 minutes during business hours", cron: "*/15 8-17 * * 1-5" },
  { label: "Every 30 minutes", cron: "*/30 * * * *" },
  { label: "First day of every month at 9:00 AM", cron: "0 9 1 * *" },
  { label: "First Monday of month at 8:00 AM", cron: "0 8 1-7 * 1" },
  { label: "Weekends at noon", cron: "0 12 * * 0,6" },
];

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
  const [showFieldEditor, setShowFieldEditor] = useState(false);
  const [showExamples, setShowExamples] = useState(false);

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

  // Field-by-field cron editing (8.4)
  const cronFields = hasValidParts ? parts : ["*", "*", "*", "*", "*"];
  const updateField = (fieldIdx: number, value: string) => {
    const newParts = [...cronFields];
    newParts[fieldIdx] = value || "*";
    onChange({ cron: newParts.join(" ") });
    setPresetIdx(4);
  };

  const activeDays = getActiveDays();

  const FIELD_LABELS = [
    { label: "Minute", hint: "0-59, */5, 0,30", placeholder: "*" },
    { label: "Hour", hint: "0-23, 8-17, */2", placeholder: "*" },
    { label: "Day", hint: "1-31, 1,15", placeholder: "*" },
    { label: "Month", hint: "1-12", placeholder: "*" },
    { label: "Weekday", hint: "0-7 (0,7=Sun), 1-5", placeholder: "*" },
  ];

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

      {/* Field-by-field editor (8.4) */}
      <div>
        <div
          onClick={() => setShowFieldEditor(!showFieldEditor)}
          style={{ fontSize: 11, color: "var(--text-muted)", cursor: "pointer" }}
        >
          {showFieldEditor ? "▾" : "▸"} Field-by-field editor
        </div>
        {showFieldEditor && (
          <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", gap: 4 }}>
              {FIELD_LABELS.map((f, i) => (
                <div key={f.label} style={{ flex: 1, textAlign: "center" }}>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 2, fontWeight: 600 }}>{f.label}</div>
                  <input
                    type="text"
                    value={cronFields[i]}
                    onChange={(e) => updateField(i, e.target.value)}
                    placeholder={f.placeholder}
                    style={{
                      ...inputStyle,
                      width: "100%",
                      textAlign: "center",
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                      padding: "3px 4px",
                    }}
                  />
                  <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 1 }}>{f.hint}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

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

      {/* Examples dropdown (8.5) */}
      <div>
        <div
          onClick={() => setShowExamples(!showExamples)}
          style={{ fontSize: 11, color: "var(--text-muted)", cursor: "pointer" }}
        >
          {showExamples ? "▾" : "▸"} Common examples
        </div>
        {showExamples && (
          <div style={{
            marginTop: 4,
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            overflow: "hidden",
          }}>
            {CRON_EXAMPLES.map((ex) => (
              <div
                key={ex.cron}
                onClick={() => { onChange({ cron: ex.cron }); setPresetIdx(4); setShowExamples(false); }}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "4px 8px",
                  fontSize: 11,
                  cursor: "pointer",
                  borderBottom: "1px solid var(--border-color)",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <span style={{ color: "var(--text-primary)" }}>{ex.label}</span>
                <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{ex.cron}</code>
              </div>
            ))}
          </div>
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

      {/* Timing controls with help */}
      <TimingHelp />

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
        Wait this long, then re-check conditions before executing.
        Example: "Turn off projector 10 minutes after room empties."
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
        Restarts timer each time the value changes. Fires once changes stop.
        Example: "Wait for volume to settle before sending to DSP."
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

  // Build all event suggestions for autocomplete (8.8)
  const allSuggestions = EVENT_CATEGORIES
    .filter((c) => c.label !== "Custom")
    .flatMap((c) => c.options(devices, macros, schedules));
  // Also add script events from configured scripts
  const scripts = project?.scripts ?? [];
  for (const s of scripts) {
    allSuggestions.push({ label: `Script: ${s.id}`, pattern: `script.${s.id}` });
  }

  // Filter suggestions when typing custom pattern
  const customPattern = trigger.event_pattern ?? "";
  const filteredSuggestions = isCustom && customPattern
    ? allSuggestions.filter((s) =>
        s.pattern.toLowerCase().includes(customPattern.toLowerCase()) ||
        s.label.toLowerCase().includes(customPattern.toLowerCase())
      )
    : [];

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
        <div style={{ position: "relative" }}>
          <div style={rowStyle}>
            <label style={labelStyle}>Pattern</label>
            <input
              type="text"
              value={trigger.event_pattern ?? ""}
              onChange={(e) => onChange({ event_pattern: e.target.value })}
              placeholder="Type to search all events..."
              list="event-autocomplete"
              style={{ ...inputStyle, fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
          </div>
          <datalist id="event-autocomplete">
            {allSuggestions.map((s) => (
              <option key={s.pattern} value={s.pattern}>{s.label}</option>
            ))}
          </datalist>
          {filteredSuggestions.length > 0 && filteredSuggestions.length <= 10 && (
            <div style={{
              marginTop: 4,
              marginLeft: 78,
              fontSize: 11,
              color: "var(--text-muted)",
              display: "flex",
              flexWrap: "wrap",
              gap: 4,
            }}>
              {filteredSuggestions.slice(0, 8).map((s) => (
                <span
                  key={s.pattern}
                  onClick={() => onChange({ event_pattern: s.pattern })}
                  style={{
                    padding: "1px 6px",
                    borderRadius: 3,
                    background: "var(--bg-hover)",
                    cursor: "pointer",
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                  }}
                  title={s.label}
                >
                  {s.pattern}
                </span>
              ))}
            </div>
          )}
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

      {/* Condition preview (8.9) */}
      <ConditionPreview conditions={conditions} />
    </div>
  );
}

// --- Condition Preview (8.9) ---

function ConditionPreview({ conditions }: { conditions: TriggerCondition[] }) {
  const [currentState, setCurrentState] = useState<Record<string, unknown>>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchState = async () => {
      try {
        const state = await api.getState();
        if (!cancelled) { setCurrentState(state); setLoaded(true); }
      } catch { /* ignore */ }
    };
    fetchState();
    const interval = setInterval(fetchState, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  if (!loaded || conditions.length === 0) return null;

  const evaluateCondition = (cond: TriggerCondition): boolean => {
    const actual = currentState[cond.key];
    const op = cond.operator ?? "eq";
    const expected = cond.value;

    switch (op) {
      case "eq": return actual == expected; // eslint-disable-line eqeqeq
      case "ne": return actual != expected; // eslint-disable-line eqeqeq
      case "gt": return actual != null && expected != null && Number(actual) > Number(expected);
      case "lt": return actual != null && expected != null && Number(actual) < Number(expected);
      case "gte": return actual != null && expected != null && Number(actual) >= Number(expected);
      case "lte": return actual != null && expected != null && Number(actual) <= Number(expected);
      case "truthy": return !!actual;
      case "falsy": return !actual;
      default: return false;
    }
  };

  const allPass = conditions.every(evaluateCondition);

  return (
    <div
      style={{
        marginTop: "var(--space-sm)",
        padding: "var(--space-xs) var(--space-sm)",
        borderRadius: "var(--border-radius)",
        border: `1px solid ${allPass ? "rgba(16,185,129,0.3)" : "rgba(239,68,68,0.3)"}`,
        background: allPass ? "rgba(16,185,129,0.06)" : "rgba(239,68,68,0.06)",
        fontSize: 11,
      }}
    >
      <div style={{
        fontWeight: 600,
        color: allPass ? "#10b981" : "#ef4444",
        marginBottom: conditions.length > 1 ? 4 : 0,
      }}>
        Evaluated now: {allPass ? "ALL TRUE — trigger would fire" : "FALSE — trigger would not fire"}
      </div>
      {conditions.map((cond, i) => {
        const actual = currentState[cond.key];
        const passes = evaluateCondition(cond);
        return (
          <div key={i} style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            color: "var(--text-secondary)",
            padding: "1px 0",
          }}>
            <span style={{ color: passes ? "#10b981" : "#ef4444", fontWeight: 600 }}>
              {passes ? "T" : "F"}
            </span>
            <code style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>
              {cond.key}
            </code>
            <span style={{ color: "var(--text-muted)" }}>
              = {actual === undefined ? <em>undefined</em> : JSON.stringify(actual)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// --- Timing Help Component ---

function TimingHelp() {
  const [expanded, setExpanded] = useState(false);
  return (
    <div>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "flex", alignItems: "center", gap: 4,
          cursor: "pointer", fontSize: 11, color: "var(--text-muted)",
        }}
      >
        <HelpCircle size={12} />
        <span style={{ textDecoration: "underline" }}>How do delay, debounce, and cooldown work?</span>
      </div>
      {expanded && (
        <div style={{
          marginTop: 6, padding: "var(--space-sm)", borderRadius: 4,
          background: "rgba(33,150,243,0.06)", border: "1px solid rgba(33,150,243,0.15)",
          fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6,
        }}>
          <div style={{ marginBottom: 6 }}>
            <strong>Debounce</strong> &mdash; Waits for the value to stop changing. Each new change resets the timer. Fires once after the value settles. Use for flickering sensors or rapid adjustments.
          </div>
          <div style={{ marginBottom: 6 }}>
            <strong>Delay</strong> &mdash; Waits a set time after the debounce settles, then re-checks the condition before firing. If the condition is no longer true, the trigger is skipped. Use for "turn off after X minutes of inactivity."
          </div>
          <div style={{ marginBottom: 6 }}>
            <strong>Cooldown</strong> (in Advanced) &mdash; After the trigger fires, prevents it from firing again for this many seconds. Use to avoid rapid re-triggering.
          </div>
          <div style={{ fontStyle: "italic", color: "var(--text-muted)" }}>
            Order: State changes → Debounce → Delay (re-check) → Fire → Cooldown
          </div>
        </div>
      )}
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
