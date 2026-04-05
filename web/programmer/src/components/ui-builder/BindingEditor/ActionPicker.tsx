import { useState, useEffect } from "react";
import { Info } from "lucide-react";
import type { ProjectConfig, DeviceInfo } from "../../../api/types";
import { useConnectionStore } from "../../../store/connectionStore";
import * as api from "../../../api/restClient";

interface ActionPickerProps {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (value: Record<string, unknown>) => void;
  forChangeBinding?: boolean;
}

const ACTION_TYPES = [
  { value: "macro", label: "Run Macro" },
  { value: "device.command", label: "Device Command" },
  { value: "state.set", label: "Set Variable" },
  { value: "navigate", label: "Navigate Page" },
  { value: "script.call", label: "Script Function" },
];

export function ActionPicker({ value, project, onChange, forChangeBinding }: ActionPickerProps) {
  const actionType = String(value?.action || "");

  const handleActionTypeChange = (action: string) => {
    onChange({ action });
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      {/* Action type selector */}
      <div>
        <label style={labelStyle}>Action Type</label>
        <select
          value={actionType}
          onChange={(e) => handleActionTypeChange(e.target.value)}
          style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Select action...</option>
          {ACTION_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      {/* Action-specific config */}
      {actionType === "macro" && (
        <MacroConfig value={value} project={project} onChange={onChange} />
      )}
      {actionType === "device.command" && (
        <DeviceCommandConfig
          value={value}
          project={project}
          onChange={onChange}
        />
      )}
      {actionType === "state.set" && (
        <StateSetConfig value={value} onChange={onChange} forChangeBinding={forChangeBinding} />
      )}
      {actionType === "navigate" && (
        <NavigateConfig value={value} project={project} onChange={onChange} />
      )}
      {actionType === "script.call" && (
        <ScriptCallConfig value={value} onChange={onChange} />
      )}
    </div>
  );
}

function MacroConfig({
  value,
  project,
  onChange,
}: {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (v: Record<string, unknown>) => void;
}) {
  return (
    <div>
      <label style={labelStyle}>Macro</label>
      <select
        value={String(value?.macro || "")}
        onChange={(e) =>
          onChange({ action: "macro", macro: e.target.value })
        }
        style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
      >
        <option value="">Select macro...</option>
        {project.macros.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function DeviceCommandConfig({
  value,
  project,
  onChange,
}: {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (v: Record<string, unknown>) => void;
}) {
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const selectedDevice = String(value?.device || "");
  const selectedCommand = String(value?.command || "");

  useEffect(() => {
    if (!selectedDevice) {
      setDeviceInfo(null);
      return;
    }
    api.getDevice(selectedDevice).then(setDeviceInfo).catch(() => setDeviceInfo(null));
  }, [selectedDevice]);

  const commands = deviceInfo?.commands ?? {};
  const commandNames = Object.keys(commands);
  const commandDef = commands[selectedCommand] as
    | Record<string, unknown>
    | undefined;
  const paramKeys = Object.keys(
    (commandDef?.params as Record<string, unknown>) ?? {},
  );

  const currentParams = (value?.params as Record<string, unknown>) ?? {};

  return (
    <>
      <div>
        <label style={labelStyle}>Device</label>
        <select
          value={selectedDevice}
          onChange={(e) =>
            onChange({
              action: "device.command",
              device: e.target.value,
              command: "",
              params: {},
            })
          }
          style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Select device...</option>
          {project.devices.map((d) => {
            const connected = useConnectionStore.getState().liveState[`device.${d.id}.connected`];
            return (
              <option key={d.id} value={d.id}>
                {connected ? "\u25CF " : "\u25CB "}{d.name} — {d.driver}
              </option>
            );
          })}
        </select>
        {/* Device info tooltip */}
        {selectedDevice && deviceInfo && (
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            fontSize: 11, color: "var(--text-muted)", marginTop: 3, paddingLeft: 2,
          }}>
            <span style={{
              width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
              background: deviceInfo.connected ? "#10b981" : "#ef4444",
            }} />
            <span>{deviceInfo.connected ? "Connected" : "Offline"}</span>
            <span style={{ color: "var(--border-color)" }}>|</span>
            <span>{deviceInfo.driver}</span>
            {deviceInfo.state && String(deviceInfo.state.host || "") && (
              <>
                <span style={{ color: "var(--border-color)" }}>|</span>
                <span>{String(deviceInfo.state.host || "")}</span>
              </>
            )}
          </div>
        )}
      </div>
      {selectedDevice && (
        <div>
          <label style={labelStyle}>Command</label>
          <select
            value={selectedCommand}
            onChange={(e) =>
              onChange({
                action: "device.command",
                device: selectedDevice,
                command: e.target.value,
                params: {},
              })
            }
            style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
          >
            <option value="">Select command...</option>
            {commandNames.map((cmd) => (
              <option key={cmd} value={cmd}>
                {cmd}
              </option>
            ))}
          </select>
          {/* Command help text — prominent info box */}
          {selectedCommand && (() => {
            const cmdDef = commands[selectedCommand] as Record<string, unknown> | undefined;
            const cmdHelp = cmdDef?.help as string | undefined;
            return cmdHelp ? (
              <div style={{
                display: "flex", alignItems: "flex-start", gap: 6,
                marginTop: 4, padding: "6px 8px", borderRadius: 4,
                background: "rgba(33,150,243,0.08)", border: "1px solid rgba(33,150,243,0.15)",
                fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.4,
              }}>
                <Info size={13} style={{ flexShrink: 0, marginTop: 1, color: "var(--accent)" }} />
                {cmdHelp}
              </div>
            ) : null;
          })()}
        </div>
      )}
      {paramKeys.length > 0 && (
        <div>
          <label style={labelStyle}>Parameters</label>
          {paramKeys.map((param) => {
            const paramDef = (commandDef?.params as Record<string, Record<string, unknown>> | undefined)?.[param] ?? {};
            const paramType = paramDef.type as string | undefined;
            const paramHelp = paramDef.help as string | undefined;
            const paramRequired = paramDef.required as boolean | undefined;
            const paramDefault = paramDef.default;
            const paramValues = paramDef.values as string[] | undefined;
            return (
              <div key={param} style={{ marginBottom: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)", fontWeight: 500 }}>
                    {param}
                  </span>
                  {paramType && (
                    <span style={{
                      fontSize: 10, padding: "0 4px", borderRadius: 3,
                      background: "var(--bg-hover)", color: "var(--text-muted)",
                    }}>
                      {paramType}
                    </span>
                  )}
                  {paramRequired && (
                    <span style={{ fontSize: 10, color: "#ef4444" }}>required</span>
                  )}
                  {paramDefault !== undefined && (
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      default: {String(paramDefault)}
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                  {paramValues && paramValues.length > 0 ? (
                    <select
                      value={String(currentParams[param] ?? "")}
                      onChange={(e) =>
                        onChange({
                          action: "device.command",
                          device: selectedDevice,
                          command: selectedCommand,
                          params: { ...currentParams, [param]: e.target.value },
                        })
                      }
                      style={{ flex: 1, padding: "3px 6px", fontSize: "var(--font-size-sm)" }}
                    >
                      <option value="">Select...</option>
                      {paramValues.map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={String(currentParams[param] ?? "")}
                      onChange={(e) =>
                        onChange({
                          action: "device.command",
                          device: selectedDevice,
                          command: selectedCommand,
                          params: { ...currentParams, [param]: e.target.value },
                        })
                      }
                      placeholder={paramHelp || `Enter ${param}...`}
                      style={{ flex: 1, padding: "3px 6px", fontSize: "var(--font-size-sm)" }}
                    />
                  )}
                  {/* Dynamic value template buttons */}
                  <button
                    onClick={() =>
                      onChange({
                        action: "device.command",
                        device: selectedDevice,
                        command: selectedCommand,
                        params: { ...currentParams, [param]: "$value" },
                      })
                    }
                    title="Insert $value — resolves to the element's current value at runtime (slider position, select choice, etc.)"
                    style={{
                      padding: "2px 5px", fontSize: 10, borderRadius: 3, cursor: "pointer",
                      background: "var(--bg-hover)", color: "var(--text-muted)", border: "1px solid var(--border-color)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    $value
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

function StateSetConfig({
  value,
  onChange,
  forChangeBinding,
}: {
  value: Record<string, unknown> | null;
  onChange: (v: Record<string, unknown>) => void;
  forChangeBinding?: boolean;
}) {
  const useElementValue = value?.value_from === "element";

  return (
    <>
      <div>
        <label style={labelStyle}>State Key</label>
        <input
          value={String(value?.key || "")}
          onChange={(e) =>
            onChange({
              action: "state.set",
              key: e.target.value,
              ...(useElementValue
                ? { value_from: "element" }
                : { value: value?.value }),
            })
          }
          placeholder="var.my_variable"
          style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
      </div>

      {forChangeBinding && (
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: "var(--font-size-sm)",
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={useElementValue}
            onChange={(e) =>
              onChange({
                action: "state.set",
                key: value?.key,
                ...(e.target.checked
                  ? { value_from: "element" }
                  : { value: "" }),
              })
            }
          />
          Use element's selected value
        </label>
      )}

      {!useElementValue && (
        <div>
          <label style={labelStyle}>Value</label>
          <input
            value={String(value?.value ?? "")}
            onChange={(e) => {
              let parsed: unknown = e.target.value;
              if (parsed === "true") parsed = true;
              else if (parsed === "false") parsed = false;
              else if (parsed !== "" && !isNaN(Number(parsed)))
                parsed = Number(parsed);
              onChange({
                action: "state.set",
                key: value?.key,
                value: parsed,
              });
            }}
            placeholder="Value..."
            style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
          />
        </div>
      )}
    </>
  );
}

function NavigateConfig({
  value,
  project,
  onChange,
}: {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (v: Record<string, unknown>) => void;
}) {
  return (
    <div>
      <label style={labelStyle}>Page</label>
      <select
        value={String(value?.page || "")}
        onChange={(e) =>
          onChange({ action: "navigate", page: e.target.value })
        }
        style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
      >
        <option value="">Select page...</option>
        {project.ui.pages.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function ScriptCallConfig({
  value,
  onChange,
}: {
  value: Record<string, unknown> | null;
  onChange: (v: Record<string, unknown>) => void;
}) {
  const [functions, setFunctions] = useState<api.ScriptFunction[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api.getScriptFunctions()
      .then((fns) => { setFunctions(fns); setLoaded(true); })
      .catch(() => setLoaded(true)); // Fall back to text input on error
  }, []);

  // Group by script
  const grouped = new Map<string, api.ScriptFunction[]>();
  for (const fn of functions) {
    if (!grouped.has(fn.script)) grouped.set(fn.script, []);
    grouped.get(fn.script)!.push(fn);
  }

  const currentValue = String(value?.function || "");

  // Use dropdown if we have functions, text input as fallback
  if (loaded && functions.length > 0) {
    return (
      <div>
        <label style={labelStyle}>Function</label>
        <select
          value={currentValue}
          onChange={(e) =>
            onChange({ action: "script.call", function: e.target.value })
          }
          style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Select function...</option>
          {[...grouped.entries()].map(([scriptId, fns]) => (
            <optgroup key={scriptId} label={scriptId}>
              {fns.map((fn) => (
                <option key={`${fn.script}.${fn.function}`} value={fn.function}>
                  {fn.function}{fn.doc ? ` — ${fn.doc}` : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div>
      <label style={labelStyle}>Function Name</label>
      <input
        value={currentValue}
        onChange={(e) =>
          onChange({ action: "script.call", function: e.target.value })
        }
        placeholder="my_function"
        style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
      />
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};
