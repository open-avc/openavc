import { useState, useEffect, useRef } from "react";
import type { MacroStep, MacroConfig, DeviceConfig, DeviceInfo } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import type { StepPathSegment } from "../../store/logStore";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";
import { ConditionEditor } from "./ConditionEditor";
import { STEP_TYPES, getStepType } from "./macroHelpers";
import * as api from "../../api/restClient";

interface StepEditorProps {
  step: MacroStep;
  macros: MacroConfig[];
  currentMacroId: string;
  onChange: (updated: MacroStep) => void;
  activeStepPath?: StepPathSegment[];
}

export function StepEditor({ step, macros, currentMacroId, onChange, activeStepPath }: StepEditorProps) {
  const devices = useProjectStore((s) => s.project?.devices) ?? [];

  const update = (patch: Partial<MacroStep>) => {
    onChange({ ...step, ...patch });
  };

  // Main editor per action type
  let editor: React.ReactNode;

  switch (step.action) {
    case "device.command":
      editor = (
        <DeviceCommandEditor
          step={step}
          devices={devices}
          onChange={update}
        />
      );
      break;
    case "group.command":
      editor = (
        <GroupCommandEditor
          step={step}
          onChange={update}
        />
      );
      break;
    case "delay":
      editor = (
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
      break;
    case "state.set":
      editor = <StateSetEditor step={step} onChange={update} />;
      break;
    case "event.emit":
      editor = <EventEmitEditor step={step} onChange={update} />;
      break;
    case "macro":
      editor = (
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
      break;
    case "conditional":
      editor = (
        <ConditionalEditor
          step={step}
          macros={macros}
          currentMacroId={currentMacroId}
          onChange={onChange}
          activeStepPath={activeStepPath}
        />
      );
      break;
    case "wait_until":
      editor = <WaitUntilEditor step={step} onChange={update} />;
      break;
    default:
      editor = (
        <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
          Unknown action: {step.action}
        </div>
      );
  }

  // For non-conditional steps, show optional skip_if and skip_if_offline toggles
  const showSkipOptions = step.action !== "conditional";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {editor}

      {/* Optional description for panel progress display */}
      {step.action !== "conditional" && (
        <div style={rowStyle}>
          <label style={labelStyle}>Description</label>
          <input
            value={step.description ?? ""}
            onChange={(e) => update({ description: e.target.value || undefined })}
            placeholder="e.g., Powering on projector (shown in panel progress)"
            style={inputStyle}
          />
        </div>
      )}

      {showSkipOptions && (
        <StepGuards step={step} onChange={update} />
      )}
    </div>
  );
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

  const handleParamChange = (key: string, value: unknown) => {
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
            const isDynamic = typeof currentVal === "string" && currentVal.startsWith("$");

            return (
              <div key={paramKey}>
                <div style={rowStyle}>
                <label style={{ ...labelStyle, minWidth: 60 }}>
                  {paramKey}
                  {paramDef?.required && <span style={{ color: "#ef4444" }}> *</span>}
                </label>
                {isDynamic ? (
                  /* Dynamic mode: state key picker */
                  <VariableKeyPicker
                    value={String(currentVal).slice(1)}
                    onChange={(key) => handleParamChange(paramKey, `$${key}`)}
                    showDeviceState
                    placeholder="Select state key..."
                    style={{ flex: 1 }}
                  />
                ) : paramDef?.type === "enum" && Array.isArray(paramDef.values) ? (
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
                {/* Dynamic toggle */}
                <button
                  onClick={() => {
                    if (isDynamic) {
                      handleParamChange(paramKey, "");
                    } else {
                      handleParamChange(paramKey, "$var.");
                    }
                  }}
                  title={isDynamic ? "Switch to static value" : "Use dynamic value from state variable"}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "3px 6px",
                    borderRadius: "var(--border-radius)",
                    border: `1px solid ${isDynamic ? "var(--accent)" : "var(--border-color)"}`,
                    background: isDynamic ? "rgba(33,150,243,0.15)" : "transparent",
                    color: isDynamic ? "var(--accent)" : "var(--text-muted)",
                    fontSize: 11,
                    cursor: "pointer",
                    flexShrink: 0,
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  $
                </button>
                </div>
                {isDynamic && (
                  <div style={{ fontSize: 11, color: "var(--accent)", marginTop: 2, marginLeft: 78 }}>
                    Value will be read from state at runtime
                  </div>
                )}
                {!isDynamic && paramDef?.help && (
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

// --- Group Command Editor ---

function GroupCommandEditor({
  step,
  onChange,
}: {
  step: MacroStep;
  onChange: (patch: Partial<MacroStep>) => void;
}) {
  const groups = useProjectStore((s) => s.project?.device_groups) ?? [];

  const selectedGroup = groups.find((g) => g.id === step.group);

  // Fetch device info for all group members to find shared commands
  const [sharedCommands, setSharedCommands] = useState<Record<string, any>>({});
  const [loadingCommands, setLoadingCommands] = useState(false);

  useEffect(() => {
    if (!selectedGroup || selectedGroup.device_ids.length === 0) {
      setSharedCommands({});
      return;
    }
    setLoadingCommands(true);
    Promise.all(
      selectedGroup.device_ids.map((id) =>
        api.getDevice(id).catch(() => null)
      )
    ).then((infos) => {
      const validInfos = infos.filter(Boolean);
      if (validInfos.length === 0) {
        setSharedCommands({});
        return;
      }
      // Intersection of command sets
      const commandSets = validInfos.map((info) => info!.commands ?? {});
      const firstCommands = commandSets[0] as Record<string, any>;
      const shared: Record<string, any> = {};
      for (const [cmd, def] of Object.entries(firstCommands)) {
        if (commandSets.every((cs) => cmd in (cs as Record<string, any>))) {
          shared[cmd] = def;
        }
      }
      setSharedCommands(shared);
    }).finally(() => setLoadingCommands(false));
  }, [selectedGroup]);

  const commandNames = Object.keys(sharedCommands);
  const commandDef = sharedCommands[step.command ?? ""];
  const paramSchema = (commandDef?.params ?? {}) as Record<string, any>;
  const paramKeys = Object.keys(paramSchema);

  const handleGroupChange = (groupId: string) => {
    onChange({ group: groupId, command: "", params: undefined });
  };

  const handleCommandChange = (command: string) => {
    onChange({ command, params: undefined });
  };

  const handleParamChange = (key: string, value: unknown) => {
    const params = { ...(step.params ?? {}), [key]: value };
    onChange({ params });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <HelpText>Send a command to all devices in a group at once. Only commands shared by every device in the group are shown.</HelpText>

      {/* Group picker */}
      <div style={rowStyle}>
        <label style={labelStyle}>Group</label>
        <select
          value={step.group ?? ""}
          onChange={(e) => handleGroupChange(e.target.value)}
          style={inputStyle}
        >
          <option value="">Select group...</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name} ({g.device_ids.length} devices)
            </option>
          ))}
        </select>
      </div>

      {/* Command picker */}
      {step.group && (
        <div style={rowStyle}>
          <label style={labelStyle}>Command</label>
          {loadingCommands ? (
            <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              Loading shared commands...
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
                  const def = sharedCommands[cmd];
                  const label = def?.label ?? cmd;
                  return (
                    <option key={cmd} value={cmd}>{label}</option>
                  );
                })}
              </select>
              {step.command && sharedCommands[step.command]?.help && (
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  {sharedCommands[step.command].help}
                </div>
              )}
            </div>
          ) : selectedGroup && selectedGroup.device_ids.length === 0 ? (
            <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              No devices in this group yet
            </span>
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

      {/* Parameters */}
      {step.command && paramKeys.length > 0 && (
        <div style={{ marginLeft: 78, display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          {paramKeys.map((paramKey) => {
            const paramDef = paramSchema[paramKey];
            const currentVal = (step.params as Record<string, unknown>)?.[paramKey] ?? "";
            const isDynamic = typeof currentVal === "string" && currentVal.startsWith("$");
            return (
              <div key={paramKey}>
                <div style={rowStyle}>
                  <label style={{ ...labelStyle, minWidth: 60 }}>
                    {paramDef?.label ?? paramKey}
                    {paramDef?.required && <span style={{ color: "#ef4444" }}> *</span>}
                  </label>
                  {isDynamic ? (
                    <VariableKeyPicker
                      value={String(currentVal).slice(1)}
                      onChange={(key) => handleParamChange(paramKey, `$${key}`)}
                      showDeviceState
                      placeholder="Select state key..."
                      style={{ flex: 1 }}
                    />
                  ) : paramDef?.type === "enum" && Array.isArray(paramDef.values) ? (
                    <select
                      value={String(currentVal)}
                      onChange={(e) => handleParamChange(paramKey, e.target.value)}
                      style={inputStyle}
                    >
                      <option value="">Select {paramKey}...</option>
                      {paramDef.values.map((v: string) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={String(currentVal)}
                      onChange={(e) => handleParamChange(paramKey, e.target.value)}
                      placeholder={paramDef?.type === "number" ? "0" : ""}
                      style={inputStyle}
                    />
                  )}
                  <button
                    onClick={() => {
                      if (isDynamic) handleParamChange(paramKey, "");
                      else handleParamChange(paramKey, "$var.");
                    }}
                    title={isDynamic ? "Switch to static value" : "Use dynamic value from state variable"}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      padding: "3px 6px",
                      borderRadius: "var(--border-radius)",
                      border: `1px solid ${isDynamic ? "var(--accent)" : "var(--border-color)"}`,
                      background: isDynamic ? "rgba(33,150,243,0.15)" : "transparent",
                      color: isDynamic ? "var(--accent)" : "var(--text-muted)",
                      fontSize: 11,
                      cursor: "pointer",
                      flexShrink: 0,
                      fontFamily: "var(--font-mono)",
                    }}
                  >
                    $
                  </button>
                </div>
                {isDynamic && (
                  <div style={{ fontSize: 11, color: "var(--accent)", marginTop: 2, marginLeft: 78 }}>
                    Value will be read from state at runtime
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
      {step.key && (() => {
        const isDynamic = typeof step.value === "string" && step.value.startsWith("$");
        return (
          <div style={rowStyle}>
            <label style={labelStyle}>Value</label>
            {isDynamic ? (
              <VariableKeyPicker
                value={String(step.value).slice(1)}
                onChange={(key) => onChange({ value: `$${key}` })}
                showDeviceState
                placeholder="Select state key..."
                style={{ flex: 1 }}
              />
            ) : selectedVar?.type === "boolean" ? (
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
            <button
              onClick={() => {
                if (isDynamic) onChange({ value: "" });
                else onChange({ value: "$var." });
              }}
              title={isDynamic ? "Switch to static value" : "Use dynamic value from state variable"}
              style={{
                display: "flex",
                alignItems: "center",
                padding: "3px 6px",
                borderRadius: "var(--border-radius)",
                border: `1px solid ${isDynamic ? "var(--accent)" : "var(--border-color)"}`,
                background: isDynamic ? "rgba(33,150,243,0.15)" : "transparent",
                color: isDynamic ? "var(--accent)" : "var(--text-muted)",
                fontSize: 11,
                cursor: "pointer",
                flexShrink: 0,
                fontFamily: "var(--font-mono)",
              }}
            >
              $
            </button>
          </div>
        );
      })()}
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

// --- Wait Until Editor ---

function WaitUntilEditor({
  step,
  onChange,
}: {
  step: MacroStep;
  onChange: (patch: Partial<MacroStep>) => void;
}) {
  const condition = step.condition ?? { key: "", operator: "eq", value: "" };
  const never = step.timeout == null;
  const onTimeout = step.on_timeout ?? "fail";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
      <HelpText>
        Pause the macro until a state value matches a condition. Great for "wait until the projector
        reports it's warm" or "wait until a user presses a confirm button on the panel."
      </HelpText>

      <div>
        <label style={{ ...labelStyle, display: "block", marginBottom: 4 }}>Wait until</label>
        <ConditionEditor
          condition={condition}
          onChange={(c) => onChange({ condition: c })}
        />
      </div>

      <div style={rowStyle}>
        <label style={labelStyle}>Timeout</label>
        <input
          type="number"
          min={0}
          step={0.5}
          value={never ? "" : String(step.timeout ?? 0)}
          disabled={never}
          onChange={(e) => onChange({ timeout: parseFloat(e.target.value) || 0 })}
          style={{ ...inputStyle, maxWidth: 120 }}
          placeholder={never ? "never" : "seconds"}
        />
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>seconds</span>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12,
            color: "var(--text-secondary)",
            cursor: "pointer",
            marginLeft: "var(--space-sm)",
          }}
        >
          <input
            type="checkbox"
            checked={never}
            onChange={(e) =>
              onChange({ timeout: e.target.checked ? null : 30 })
            }
          />
          Never time out
        </label>
      </div>

      {!never && (
        <div style={rowStyle}>
          <label style={labelStyle}>If timeout</label>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              color: "var(--text-secondary)",
              cursor: "pointer",
            }}
          >
            <input
              type="radio"
              name={`wait_until_on_timeout_${step.condition?.key ?? ""}_${step.timeout ?? ""}`}
              checked={onTimeout === "fail"}
              onChange={() => onChange({ on_timeout: "fail" })}
            />
            Fail the macro
          </label>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              color: "var(--text-secondary)",
              cursor: "pointer",
            }}
          >
            <input
              type="radio"
              name={`wait_until_on_timeout_${step.condition?.key ?? ""}_${step.timeout ?? ""}`}
              checked={onTimeout === "continue"}
              onChange={() => onChange({ on_timeout: "continue" })}
            />
            Continue anyway
          </label>
        </div>
      )}

      {never && (
        <div style={hintStyle}>
          With no timeout, the macro waits forever. It can still be stopped by cancelling the
          macro or by another macro in the same cancel group.
        </div>
      )}
    </div>
  );
}

// --- Conditional Editor (if/else branching) ---

function ConditionalEditor({
  step,
  macros,
  currentMacroId,
  onChange,
  activeStepPath,
}: {
  step: MacroStep;
  macros: MacroConfig[];
  currentMacroId: string;
  onChange: (updated: MacroStep) => void;
  activeStepPath?: StepPathSegment[];
}) {
  const condition = step.condition ?? { key: "", operator: "eq", value: "" };
  const thenSteps = step.then_steps ?? [];
  const elseSteps = step.else_steps ?? [];

  // Determine which branch step is active from the path.
  // Path looks like [..., parentIdx, "then"|"else", branchStepIdx]
  // We need to find the branch and index from the tail of the path.
  const activeBranch = activeStepPath && activeStepPath.length >= 2
    ? activeStepPath[activeStepPath.length - 2] as "then" | "else"
    : null;
  const activeBranchIdx = activeStepPath && activeStepPath.length >= 1
    ? activeStepPath[activeStepPath.length - 1] as number
    : null;

  const updateThenStep = (index: number, updated: MacroStep) => {
    const steps = [...thenSteps];
    steps[index] = updated;
    onChange({ ...step, then_steps: steps });
  };

  const updateElseStep = (index: number, updated: MacroStep) => {
    const steps = [...elseSteps];
    steps[index] = updated;
    onChange({ ...step, else_steps: steps });
  };

  const addThenStep = (action: string) => {
    const typeInfo = getStepType(action);
    if (!typeInfo) return;
    onChange({ ...step, then_steps: [...thenSteps, { action, ...typeInfo.defaults() }] });
  };

  const addElseStep = (action: string) => {
    const typeInfo = getStepType(action);
    if (!typeInfo) return;
    onChange({ ...step, else_steps: [...elseSteps, { action, ...typeInfo.defaults() }] });
  };

  const removeThenStep = (index: number) => {
    onChange({ ...step, then_steps: thenSteps.filter((_, i) => i !== index) });
  };

  const removeElseStep = (index: number) => {
    onChange({ ...step, else_steps: elseSteps.filter((_, i) => i !== index) });
  };

  const moveThenStep = (index: number, direction: -1 | 1) => {
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= thenSteps.length) return;
    const arr = [...thenSteps];
    [arr[index], arr[newIndex]] = [arr[newIndex], arr[index]];
    onChange({ ...step, then_steps: arr });
  };

  const moveElseStep = (index: number, direction: -1 | 1) => {
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= elseSteps.length) return;
    const arr = [...elseSteps];
    [arr[index], arr[newIndex]] = [arr[newIndex], arr[index]];
    onChange({ ...step, else_steps: arr });
  };

  const moveThenToElse = (index: number) => {
    const moved = thenSteps[index];
    onChange({
      ...step,
      then_steps: thenSteps.filter((_, i) => i !== index),
      else_steps: [...elseSteps, moved],
    });
  };

  const moveElseToThen = (index: number) => {
    const moved = elseSteps[index];
    onChange({
      ...step,
      else_steps: elseSteps.filter((_, i) => i !== index),
      then_steps: [...thenSteps, moved],
    });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
      <HelpText>
        Run different steps based on a condition. If the condition is true, the "Then" steps run.
        Otherwise, the "Else" steps run (optional).
      </HelpText>

      {/* Condition */}
      <div>
        <label style={{ ...labelStyle, display: "block", marginBottom: 4 }}>If</label>
        <ConditionEditor
          condition={condition}
          onChange={(c) => onChange({ ...step, condition: c })}
        />
      </div>

      {/* Then steps */}
      <div>
        <label style={{ ...labelStyle, display: "block", marginBottom: 4, color: "#10b981" }}>Then</label>
        <div style={{ borderLeft: "2px solid #10b981", paddingLeft: 12, display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          {thenSteps.map((s, i) => (
            <InlineStepCard
              key={i}
              step={s}
              index={i}
              total={thenSteps.length}
              macros={macros}
              currentMacroId={currentMacroId}
              onChange={(updated) => updateThenStep(i, updated)}
              onDelete={() => removeThenStep(i)}
              onMove={(dir) => moveThenStep(i, dir)}
              onMoveToBranch={() => moveThenToElse(i)}
              isActive={activeBranch === "then" && activeBranchIdx === i}
            />
          ))}
          <AddStepDropdown onAdd={addThenStep} />
        </div>
      </div>

      {/* Else steps */}
      <div>
        <label style={{ ...labelStyle, display: "block", marginBottom: 4, color: "#ef4444" }}>Else (optional)</label>
        <div style={{ borderLeft: "2px solid #ef4444", paddingLeft: 12, display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          {elseSteps.map((s, i) => (
            <InlineStepCard
              key={i}
              step={s}
              index={i}
              total={elseSteps.length}
              macros={macros}
              currentMacroId={currentMacroId}
              onChange={(updated) => updateElseStep(i, updated)}
              onDelete={() => removeElseStep(i)}
              onMove={(dir) => moveElseStep(i, dir)}
              onMoveToBranch={() => moveElseToThen(i)}
              isActive={activeBranch === "else" && activeBranchIdx === i}
            />
          ))}
          <AddStepDropdown onAdd={addElseStep} />
        </div>
      </div>
    </div>
  );
}

/** Compact step card used inside conditional then/else lists */
function InlineStepCard({
  step,
  index,
  total,
  macros,
  currentMacroId,
  onChange,
  onDelete,
  onMove,
  onMoveToBranch,
  isActive,
}: {
  step: MacroStep;
  index: number;
  total: number;
  macros: MacroConfig[];
  currentMacroId: string;
  onChange: (updated: MacroStep) => void;
  onDelete: () => void;
  onMove?: (direction: -1 | 1) => void;
  onMoveToBranch?: () => void;
  isActive?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const typeInfo = getStepType(step.action);
  const devices = useProjectStore((s) => s.project?.devices) ?? [];

  return (
    <div style={{
      border: `1px solid ${isActive ? "var(--accent)" : "var(--border-color)"}`,
      borderRadius: "var(--border-radius)",
      background: isActive ? "rgba(33, 150, 243, 0.08)" : "var(--bg-surface)",
    }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "var(--space-xs) var(--space-sm)",
          cursor: "pointer",
          fontSize: "var(--font-size-sm)",
        }}
      >
        <span style={{
          fontSize: 10,
          fontWeight: 600,
          color: "#fff",
          background: typeInfo?.color ?? "#666",
          padding: "1px 5px",
          borderRadius: 3,
          textTransform: "uppercase",
          flexShrink: 0,
        }}>
          {typeInfo?.label ?? step.action}
        </span>
        <span style={{ flex: 1, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {typeInfo?.summary(step, devices as any) ?? ""}
        </span>
        <div style={{ display: "flex", gap: 1, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
          {onMove && (
            <>
              <button
                onClick={() => onMove(-1)}
                disabled={index === 0}
                style={inlineIconBtn}
                title="Move up"
              >
                ▲
              </button>
              <button
                onClick={() => onMove(1)}
                disabled={index === total - 1}
                style={inlineIconBtn}
                title="Move down"
              >
                ▼
              </button>
            </>
          )}
          {onMoveToBranch && (
            <button
              onClick={onMoveToBranch}
              style={{ ...inlineIconBtn, fontSize: 10 }}
              title="Move to other branch"
            >
              ⇄
            </button>
          )}
          <button
            onClick={onDelete}
            style={{ ...inlineIconBtn, color: "var(--color-error)" }}
            title="Remove step"
          >
            ×
          </button>
        </div>
      </div>
      {expanded && (
        <div style={{ padding: "var(--space-sm)", borderTop: "1px solid var(--border-color)" }}>
          <StepEditor step={step} macros={macros} currentMacroId={currentMacroId} onChange={onChange} />
        </div>
      )}
    </div>
  );
}

const inlineIconBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--text-muted)",
  cursor: "pointer",
  padding: "1px 3px",
  fontSize: 11,
  lineHeight: 1,
  display: "flex",
  alignItems: "center",
};

/** Small "Add Step" dropdown for inside conditional blocks */
function AddStepDropdown({ onAdd }: { onAdd: (action: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "3px 10px",
          borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)",
          background: "transparent",
          color: "var(--text-muted)",
          fontSize: 12,
          cursor: "pointer",
        }}
      >
        + Add step
      </button>
      {open && (
        <div style={{
          position: "absolute",
          top: "100%",
          left: 0,
          marginTop: 4,
          background: "var(--bg-surface)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)",
          boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
          zIndex: 20,
          minWidth: 200,
        }}>
          {STEP_TYPES.map((t) => (
            <div
              key={t.action}
              onClick={() => { onAdd(t.action); setOpen(false); }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "var(--space-xs) var(--space-sm)",
                cursor: "pointer",
                fontSize: 12,
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: t.color, flexShrink: 0 }} />
              <span style={{ color: "var(--text-primary)" }}>{t.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Step Guards (skip_if, skip_if_offline) ---

function StepGuards({ step, onChange }: { step: MacroStep; onChange: (patch: Partial<MacroStep>) => void }) {
  const hasSkipIf = step.skip_if != null;
  const isDeviceCommand = step.action === "device.command";

  return (
    <div style={{ borderTop: "1px solid var(--border-color)", paddingTop: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-xs)", fontWeight: 500 }}>Guards</div>

      {/* skip_if_offline (device commands only) */}
      {isDeviceCommand && (
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer", marginBottom: "var(--space-xs)" }}>
          <input
            type="checkbox"
            checked={step.skip_if_offline ?? false}
            onChange={(e) => onChange({ skip_if_offline: e.target.checked })}
          />
          Skip if device is offline
        </label>
      )}

      {/* skip_if toggle */}
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={hasSkipIf}
          onChange={(e) => {
            if (e.target.checked) {
              onChange({ skip_if: { key: "", operator: "eq", value: "" } });
            } else {
              onChange({ skip_if: undefined });
            }
          }}
        />
        Skip this step if...
      </label>

      {hasSkipIf && (
        <div style={{ marginTop: "var(--space-xs)", marginLeft: 20 }}>
          <ConditionEditor
            condition={step.skip_if!}
            onChange={(c) => onChange({ skip_if: c })}
          />
        </div>
      )}
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
