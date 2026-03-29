import { useState, useEffect } from "react";
import type { MacroStep, MacroConfig, DeviceConfig, DeviceInfo } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";
import * as api from "../../api/restClient";

interface StepEditorProps {
  step: MacroStep;
  macros: MacroConfig[];
  currentMacroId: string;
  onChange: (updated: MacroStep) => void;
}

export function StepEditor({ step, macros, currentMacroId, onChange }: StepEditorProps) {
  const devices = useProjectStore((s) => s.project?.devices) ?? [];

  const update = (patch: Partial<MacroStep>) => {
    onChange({ ...step, ...patch });
  };

  switch (step.action) {
    case "device.command":
      return (
        <DeviceCommandEditor
          step={step}
          devices={devices}
          onChange={update}
        />
      );
    case "delay":
      return (
        <div>
          <HelpText>Wait before executing the next step. Useful for device warm-up times.</HelpText>
          <div style={rowStyle}>
            <label style={labelStyle}>Seconds</label>
            <input
              type="number"
              min={0}
              step={0.1}
              value={step.seconds ?? 0}
              onChange={(e) => update({ seconds: parseFloat(e.target.value) || 0 })}
              style={{ ...inputStyle, maxWidth: 120 }}
            />
          </div>
        </div>
      );
    case "state.set":
      return <StateSetEditor step={step} onChange={update} />;
    case "event.emit":
      return <EventEmitEditor step={step} onChange={update} />;
    case "macro":
      return (
        <div>
          <HelpText>Execute another macro as a sub-routine. The steps will run in order before continuing.</HelpText>
          <div style={rowStyle}>
            <label style={labelStyle}>Macro</label>
            <select
              value={step.macro ?? ""}
              onChange={(e) => update({ macro: e.target.value })}
              style={inputStyle}
            >
              <option value="">Select macro...</option>
              {macros
                .filter((m) => m.id !== currentMacroId)
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} ({m.steps.length} steps)
                  </option>
                ))}
            </select>
          </div>
          {macros.filter((m) => m.id !== currentMacroId).length === 0 && (
            <div style={hintStyle}>No other macros available. Create another macro first.</div>
          )}
        </div>
      );
    default:
      return (
        <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
          Unknown action: {step.action}
        </div>
      );
  }
}

// --- Device Command Editor (smart dropdowns) ---

function DeviceCommandEditor({
  step,
  devices,
  onChange,
}: {
  step: MacroStep;
  devices: DeviceConfig[];
  onChange: (patch: Partial<MacroStep>) => void;
}) {
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [loadingDevice, setLoadingDevice] = useState(false);

  // Fetch device info when device changes (to get available commands)
  useEffect(() => {
    if (!step.device) {
      setDeviceInfo(null);
      return;
    }
    setLoadingDevice(true);
    api
      .getDevice(step.device)
      .then(setDeviceInfo)
      .catch(() => setDeviceInfo(null))
      .finally(() => setLoadingDevice(false));
  }, [step.device]);

  const commands = (deviceInfo?.commands ?? {}) as Record<string, any>;
  const commandNames = Object.keys(commands);

  // Get param schema for selected command
  const commandDef = commands[step.command ?? ""];
  const paramSchema = (commandDef?.params ?? {}) as Record<string, any>;
  const paramKeys = Object.keys(paramSchema);

  const handleDeviceChange = (deviceId: string) => {
    onChange({ device: deviceId, command: "", params: undefined });
    setDeviceInfo(null);
  };

  const handleCommandChange = (command: string) => {
    // Reset params when command changes
    onChange({ command, params: undefined });
  };

  const handleParamChange = (key: string, value: string) => {
    const params = { ...(step.params ?? {}), [key]: value };
    onChange({ params });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <HelpText>Send a command to a device (e.g. power on a projector, switch an input).</HelpText>

      {/* Device picker */}
      <div style={rowStyle}>
        <label style={labelStyle}>Device</label>
        <select
          value={step.device ?? ""}
          onChange={(e) => handleDeviceChange(e.target.value)}
          style={inputStyle}
        >
          <option value="">Select device...</option>
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </div>

      {/* Command picker (dropdown, not text) */}
      {step.device && (
        <div style={rowStyle}>
          <label style={labelStyle}>Command</label>
          {loadingDevice ? (
            <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              Loading commands...
            </span>
          ) : commandNames.length > 0 ? (
            <div style={{ flex: 1 }}>
            <select
              value={step.command ?? ""}
              onChange={(e) => handleCommandChange(e.target.value)}
              style={{ ...inputStyle, width: "100%" }}
            >
              <option value="">Select command...</option>
              {commandNames.map((cmd) => {
                const def = commands[cmd];
                const label = def?.label ?? cmd;
                return (
                  <option key={cmd} value={cmd}>
                    {label}
                  </option>
                );
              })}
            </select>
            {step.command && commands[step.command]?.help && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                {commands[step.command].help}
              </div>
            )}
            </div>
          ) : (
            <input
              type="text"
              value={step.command ?? ""}
              onChange={(e) => onChange({ command: e.target.value })}
              placeholder="Type command name"
              style={inputStyle}
            />
          )}
        </div>
      )}

      {/* Dynamic parameters */}
      {step.command && paramKeys.length > 0 && (
        <div style={{ marginLeft: 78, display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          {paramKeys.map((paramKey) => {
            const paramDef = paramSchema[paramKey];
            const currentVal = (step.params as Record<string, unknown>)?.[paramKey] ?? "";

            return (
              <div key={paramKey}>
                <div style={rowStyle}>
                <label style={{ ...labelStyle, minWidth: 60 }}>
                  {paramKey}
                  {paramDef?.required && <span style={{ color: "#ef4444" }}> *</span>}
                </label>
                {paramDef?.type === "enum" && Array.isArray(paramDef.values) ? (
                  <select
                    value={String(currentVal)}
                    onChange={(e) => handleParamChange(paramKey, e.target.value)}
                    style={inputStyle}
                  >
                    <option value="">Select {paramKey}...</option>
                    {paramDef.values.map((v: string) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type={paramDef?.type === "integer" ? "number" : "text"}
                    value={String(currentVal)}
                    onChange={(e) => handleParamChange(paramKey, e.target.value)}
                    placeholder={paramDef?.type ?? "text"}
                    style={inputStyle}
                  />
                )}
                </div>
                {paramDef?.help && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2, marginLeft: 78 }}>
                    {paramDef.help}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- State Set Editor (uses shared VariableKeyPicker) ---

function StateSetEditor({
  step,
  onChange,
}: {
  step: MacroStep;
  onChange: (patch: Partial<MacroStep>) => void;
}) {
  const variables = useProjectStore((s) => s.project?.variables) ?? [];
  const selectedVar = variables.find((v) => `var.${v.id}` === step.key);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <HelpText>
        Set a shared variable that the whole system can see — UI elements update, scripts
        can react, other macros can read it.
      </HelpText>

      <div style={rowStyle}>
        <label style={labelStyle}>Variable</label>
        <VariableKeyPicker
          value={step.key ?? ""}
          onChange={(key) => onChange({ key })}
          showDeviceState
          placeholder="Select variable..."
          style={{ flex: 1 }}
        />
      </div>

      {/* Value field */}
      {step.key && (
        <div style={rowStyle}>
          <label style={labelStyle}>Value</label>
          {selectedVar?.type === "boolean" ? (
            <select
              value={step.value != null ? String(step.value) : ""}
              onChange={(e) => onChange({ value: e.target.value === "true" })}
              style={inputStyle}
            >
              <option value="">Select...</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : (
            <input
              type={selectedVar?.type === "number" ? "number" : "text"}
              value={step.value != null ? String(step.value) : ""}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "true") onChange({ value: true });
                else if (v === "false") onChange({ value: false });
                else if (v !== "" && !isNaN(Number(v))) onChange({ value: Number(v) });
                else onChange({ value: v });
              }}
              placeholder={
                selectedVar
                  ? `${selectedVar.type} (default: ${JSON.stringify(selectedVar.default)})`
                  : "Value"
              }
              style={inputStyle}
            />
          )}
        </div>
      )}
    </div>
  );
}

// --- Event Emit Editor (with suggestions) ---

const COMMON_EVENTS = [
  { value: "", label: "Type a custom event name..." },
  { value: "custom.", label: "custom.* — Custom application events" },
  { value: "system.room_occupied", label: "Room occupied" },
  { value: "system.room_vacant", label: "Room vacant" },
  { value: "system.panic", label: "System panic/emergency" },
  { value: "schedule.morning_on", label: "Scheduled: morning startup" },
  { value: "schedule.evening_off", label: "Scheduled: evening shutdown" },
];

function EventEmitEditor({
  step,
  onChange,
}: {
  step: MacroStep;
  onChange: (patch: Partial<MacroStep>) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <HelpText>
        Fire a named event that scripts and other macros can listen for. Use dot-separated
        names (e.g. <code style={{ background: "var(--bg-hover)", padding: "0 4px", borderRadius: 2 }}>custom.room_reset</code>).
      </HelpText>
      <div style={rowStyle}>
        <label style={labelStyle}>Event</label>
        <input
          type="text"
          value={step.event ?? ""}
          onChange={(e) => onChange({ event: e.target.value })}
          placeholder="e.g. custom.my_event"
          style={inputStyle}
          list="event-suggestions"
        />
        <datalist id="event-suggestions">
          {COMMON_EVENTS.filter((e) => e.value).map((e) => (
            <option key={e.value} value={e.value}>
              {e.label}
            </option>
          ))}
        </datalist>
      </div>
      <div style={hintStyle}>
        Scripts use <code style={{ background: "var(--bg-hover)", padding: "0 4px", borderRadius: 2 }}>@on_event("custom.my_event")</code> to
        respond to emitted events.
      </div>
    </div>
  );
}

// --- Shared components ---

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 12,
        color: "var(--text-muted)",
        lineHeight: 1.4,
        marginBottom: "var(--space-xs)",
      }}
    >
      {children}
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

const hintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  fontStyle: "italic",
  marginTop: 2,
};
