import { useState, useEffect } from "react";
import { Info, Plus, X } from "lucide-react";
import type { ProjectConfig, DeviceInfo, DriverParamDef } from "../../../api/types";
import { ParamInput, isDynamicParamValue } from "../../shared/ParamInput";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { useConnectionStore } from "../../../store/connectionStore";
import * as api from "../../../api/restClient";

interface ActionPickerProps {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (value: Record<string, unknown>) => void;
  forChangeBinding?: boolean;
  // Restrict the offered action types (e.g. a control surface can't run
  // script.call/value_map). When omitted, all types are offered.
  allowedActions?: string[];
  // When provided, the Navigate picker offers these targets instead of the
  // project's panel pages (a control surface navigates its own deck pages).
  navigateOptions?: { value: string; label: string }[];
  // The UI-event tokens this binding slot can deliver ($value/$input/...),
  // scoped per slot by the parent. Threaded into each command param's "$"
  // picker as its "This control" group.
  eventTokens?: { key: string; label: string }[];
}

// The page move is spelled differently by subsystem, and both spellings are
// correct. A panel binding writes "ui.navigate" -- the same word the macro step
// and the WS frame use, so one move is written one way wherever you author it.
// A control surface writes "navigate", because it moves its own deck's pages by
// index rather than a panel page, and its plugin is what interprets it.
// navigateOptions is what tells the two apart here: a surface always supplies
// its own target list, a panel never does.
const ACTION_TYPES = (navigateAction: string) => [
  { value: "macro", label: "Run Macro" },
  { value: "device.command", label: "Device Command" },
  { value: "state.set", label: "Set Variable" },
  { value: navigateAction, label: "Navigate Page" },
  { value: "script.call", label: "Script Function" },
  { value: "event.emit", label: "Emit Event" },
];

export function ActionPicker({ value, project, onChange, forChangeBinding, allowedActions, navigateOptions, eventTokens }: ActionPickerProps) {
  const actionType = String(value?.action || "");
  const navigateAction = navigateOptions ? "navigate" : "ui.navigate";
  const allTypes = ACTION_TYPES(navigateAction);
  const actionTypes = allowedActions
    ? allTypes.filter((t) => allowedActions.includes(t.value))
    : allTypes;

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
          {actionTypes.map((t) => (
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
          eventTokens={eventTokens}
        />
      )}
      {actionType === "state.set" && (
        <StateSetConfig value={value} onChange={onChange} forChangeBinding={forChangeBinding} />
      )}
      {actionType === navigateAction && (
        <NavigateConfig
          value={value}
          project={project}
          onChange={onChange}
          navigateOptions={navigateOptions}
          navigateAction={navigateAction}
        />
      )}
      {actionType === "script.call" && (
        <ScriptCallConfig value={value} onChange={onChange} eventTokens={eventTokens} />
      )}
      {actionType === "event.emit" && (
        <EmitEventConfig value={value} onChange={onChange} eventTokens={eventTokens} />
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
        {project.macros.length === 0 && (
          <option disabled>No macros yet: create one in the Macros view</option>
        )}
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
  eventTokens,
}: {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (v: Record<string, unknown>) => void;
  eventTokens?: { key: string; label: string }[];
}) {
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const selectedDevice = String(value?.device || "");
  const selectedCommand = String(value?.command || "");

  useEffect(() => {
    if (!selectedDevice) {
      setDeviceInfo(null);
      return;
    }
    // Guard against out-of-order responses: if the user switches devices
    // before an earlier getDevice() resolves, ignore the stale result so it
    // can't overwrite the newly-selected device's command list / param schema.
    let cancelled = false;
    api.getDevice(selectedDevice)
      .then((info) => { if (!cancelled) setDeviceInfo(info); })
      .catch(() => { if (!cancelled) setDeviceInfo(null); });
    return () => { cancelled = true; };
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
                {connected ? "\u25CF " : "\u25CB "}{d.name} ({d.driver})
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
                background: "rgba(138,180,147,0.08)", border: "1px solid rgba(138,180,147,0.15)",
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
                  <ParamInput
                    def={paramDef as Partial<DriverParamDef>}
                    value={String(currentParams[param] ?? "")}
                    onChange={(val) =>
                      onChange({
                        action: "device.command",
                        device: selectedDevice,
                        command: selectedCommand,
                        params: { ...currentParams, [param]: val },
                      })
                    }
                    deviceId={selectedDevice}
                    values={currentParams}
                    params={commandDef?.params as Record<string, Partial<DriverParamDef>> | undefined}
                    allowDynamic
                    eventContext={eventTokens}
                    placeholder={paramHelp || `Enter ${param}...`}
                    style={{ flex: 1 }}
                  />
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
        <label style={labelStyle}>Variable to set</label>
        <VariableKeyPicker
          value={String(value?.key || "")}
          onChange={(key) =>
            onChange({
              action: "state.set",
              key,
              ...(useElementValue
                ? { value_from: "element" }
                : { value: value?.value }),
            })
          }
          showDeviceState={false}
          placeholder="Select or create a variable..."
          style={{ width: "100%" }}
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

      {!useElementValue && (() => {
        // A fixed value (with true/false/number coercion) or a "$" reference to
        // a variable / device state / system value, resolved at runtime — the
        // same picker pattern as a command parameter. The "touched value" lives
        // on the "Use element's selected value" option above, so this picker
        // deliberately offers no "This control" group (no eventContext).
        const isDynamic = isDynamicParamValue(value?.value);
        return (
          <div>
            <label style={labelStyle}>Value</label>
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {isDynamic ? (
                <VariableKeyPicker
                  value={String(value?.value).slice(1)}
                  onChange={(key) =>
                    onChange({ action: "state.set", key: value?.key, value: `$${key}` })
                  }
                  showDeviceState
                  placeholder="Select state key..."
                  style={{ flex: 1 }}
                />
              ) : (
                <input
                  value={String(value?.value ?? "")}
                  onChange={(e) => {
                    let parsed: unknown = e.target.value;
                    if (parsed === "true") parsed = true;
                    else if (parsed === "false") parsed = false;
                    else if (parsed !== "" && !isNaN(Number(parsed)))
                      parsed = Number(parsed);
                    onChange({ action: "state.set", key: value?.key, value: parsed });
                  }}
                  placeholder="Value..."
                  style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
                />
              )}
              <button
                type="button"
                onClick={() =>
                  onChange({
                    action: "state.set",
                    key: value?.key,
                    value: isDynamic ? "" : "$var.",
                  })
                }
                title={
                  isDynamic
                    ? "Switch to a fixed value"
                    : "Use a dynamic value read from state at runtime"
                }
                style={{
                  display: "flex",
                  alignItems: "center",
                  padding: "3px 6px",
                  borderRadius: "var(--border-radius)",
                  border: `1px solid ${isDynamic ? "var(--accent)" : "var(--border-color)"}`,
                  background: isDynamic ? "rgba(138,180,147,0.15)" : "transparent",
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
          </div>
        );
      })()}
    </>
  );
}

function NavigateConfig({
  value,
  project,
  onChange,
  navigateOptions,
  navigateAction,
}: {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  onChange: (v: Record<string, unknown>) => void;
  navigateOptions?: { value: string; label: string }[];
  navigateAction: string;
}) {
  // Control surfaces navigate their own deck pages, not the project's panel
  // pages, so they supply an explicit target list.
  if (navigateOptions) {
    return (
      <div>
        <label style={labelStyle}>Go To</label>
        <select
          value={String(value?.page || "")}
          onChange={(e) => onChange({ action: navigateAction, page: e.target.value })}
          style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Select...</option>
          {navigateOptions.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
    );
  }

  const pages = project.ui.pages;
  const regularPages = pages.filter((p) => (p.page_type ?? "page") === "page");
  const overlayPages = pages.filter((p) => {
    const t = p.page_type ?? "page";
    return t === "overlay" || t === "sidebar";
  });

  return (
    <div>
      <label style={labelStyle}>Page</label>
      <select
        value={String(value?.page || "")}
        onChange={(e) =>
          onChange({ action: navigateAction, page: e.target.value })
        }
        style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
      >
        <option value="">Select page...</option>
        {regularPages.length > 0 && (
          <optgroup label="Pages">
            {regularPages.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </optgroup>
        )}
        {overlayPages.length > 0 && (
          <optgroup label="Overlays / Sidebars">
            {overlayPages.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </optgroup>
        )}
        {/* Not pages, and that is the point: a Cancel button pointed at one of
            these works from wherever the dialog was opened, so the dialog stays
            reusable instead of always landing the operator on one page. */}
        <optgroup label="Go back">
          <option value="$back">Back (close overlay, or previous page)</option>
          <option value="$dismiss">Close this overlay</option>
        </optgroup>
        <optgroup label="Special">
          <option value="$back">$back: previous page (or close overlay if one is open)</option>
          <option value="$dismiss">$dismiss: close topmost overlay only</option>
        </optgroup>
      </select>
    </div>
  );
}

/** A script parameter's declared type, in the vocabulary ParamInput draws.
 *  Only what the script itself said: an annotation, or the type of a default.
 *  Anything else is a text box, which is what "we were not told" looks like. */
function scriptParamDef(param: api.ScriptFunctionParam): Partial<DriverParamDef> {
  const byAnnotation: Record<string, string> = {
    int: "integer", float: "number", bool: "boolean", str: "string",
  };
  const type = param.type ? byAnnotation[param.type] : undefined;
  const def: Record<string, unknown> = { required: param.required };
  if (type) def.type = type;
  if (param.default !== undefined) def.default = param.default;
  return def as Partial<DriverParamDef>;
}

/** Store what the author typed as the JSON type the parameter asked for.
 *  A function taking a level wants 7, not "7" -- a driver coerces its own
 *  params by declared type and a Python function does not.
 *  A "$" reference is left alone by falling through: it parses as no number
 *  and matches no boolean, and its type comes from whatever it resolves to at
 *  press time. */
function coerceForParam(raw: string, param: api.ScriptFunctionParam | undefined): unknown {
  if (raw === "") return raw;
  if (param?.type === "int" || param?.type === "float") {
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n;
  }
  if (param?.type === "bool") {
    if (raw === "true") return true;
    if (raw === "false") return false;
  }
  return raw;
}

/** Key/value rows the author names themselves.
 *  Used where nothing declares the names: an emitted event's payload, and a
 *  function that takes **kwargs. */
function NamedValueRows({
  values,
  onChange,
  eventTokens,
  keyPlaceholder,
  addLabel,
}: {
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  eventTokens?: { key: string; label: string }[];
  keyPlaceholder: string;
  addLabel: string;
}) {
  const entries = Object.entries(values);

  const rename = (from: string, to: string) => {
    // Rebuilt in order so a rename doesn't reshuffle the rows under the cursor.
    const next: Record<string, unknown> = {};
    for (const [k, v] of entries) next[k === from ? to : k] = v;
    onChange(next);
  };

  return (
    <div>
      {entries.map(([key, val]) => (
        <div key={key} style={{ display: "flex", alignItems: "center", gap: 3, marginBottom: 4 }}>
          <input
            value={key}
            onChange={(e) => rename(key, e.target.value)}
            placeholder={keyPlaceholder}
            style={{ width: 110, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
          />
          <ParamInput
            def={{}}
            value={String(val ?? "")}
            onChange={(v) => onChange({ ...values, [key]: v })}
            allowDynamic
            eventContext={eventTokens}
            placeholder="Value"
            style={{ flex: 1 }}
          />
          <button
            type="button"
            onClick={() => {
              const next = { ...values };
              delete next[key];
              onChange(next);
            }}
            title="Remove"
            style={{
              padding: "3px 6px", fontSize: 11, lineHeight: 1,
              background: "transparent", color: "var(--text-muted)",
              border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)",
              cursor: "pointer", flexShrink: 0,
            }}
          >
            <X size={12} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => {
          let name = "value";
          let n = 2;
          while (name in values) name = `value${n++}`;
          onChange({ ...values, [name]: "" });
        }}
        style={{
          display: "flex", alignItems: "center", gap: 4, padding: "3px 8px",
          fontSize: 11, background: "transparent", color: "var(--text-secondary)",
          border: "1px dashed var(--border-color)", borderRadius: "var(--border-radius)",
          cursor: "pointer",
        }}
      >
        <Plus size={12} /> {addLabel}
      </button>
    </div>
  );
}

function EmitEventConfig({
  value,
  onChange,
  eventTokens,
}: {
  value: Record<string, unknown> | null;
  onChange: (v: Record<string, unknown>) => void;
  eventTokens?: { key: string; label: string }[];
}) {
  const eventName = String(value?.event || "");
  const payload = (value?.payload as Record<string, unknown>) ?? {};

  return (
    <>
      <div>
        <label style={labelStyle}>Event Name</label>
        <input
          value={eventName}
          onChange={(e) => onChange({ ...(value ?? {}), action: "event.emit", event: e.target.value })}
          placeholder="custom.select_source"
          style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3, paddingLeft: 2 }}>
          Scripts listen with <code>@on_event</code>, and a trigger or a plugin can listen too.
        </div>
      </div>
      <div>
        <label style={labelStyle}>Payload</label>
        <NamedValueRows
          values={payload}
          onChange={(next) => onChange({ ...(value ?? {}), action: "event.emit", event: eventName, payload: next })}
          eventTokens={eventTokens}
          keyPlaceholder="name"
          addLabel="Add value"
        />
      </div>
    </>
  );
}

function ScriptCallConfig({
  value,
  onChange,
  eventTokens,
}: {
  value: Record<string, unknown> | null;
  onChange: (v: Record<string, unknown>) => void;
  eventTokens?: { key: string; label: string }[];
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
  const currentScript = String(value?.script || "");
  const currentParams = (value?.params as Record<string, unknown>) ?? {};
  // The script id is written alongside the name so two scripts defining one
  // name stay distinguishable; matching on it first is what makes that work.
  const selected =
    functions.find((f) => f.function === currentValue && f.script === currentScript)
    ?? functions.find((f) => f.function === currentValue);
  const declared = selected?.params ?? [];

  const write = (fields: Record<string, unknown>) =>
    onChange({
      action: "script.call",
      function: currentValue,
      ...(currentScript ? { script: currentScript } : {}),
      params: currentParams,
      ...fields,
    });

  const pickFunction = (fn: api.ScriptFunction | undefined, name: string) => {
    // Parameters belong to the function that declared them, so CHOOSING a
    // different one drops them rather than carrying names the new one refuses.
    const next: Record<string, unknown> = { action: "script.call", function: name, params: {} };
    if (fn) next.script = fn.script;
    onChange(next);
  };

  // Typing a name is not choosing a different function: the fallback field
  // fires on every keystroke, so clearing there would empty the parameters
  // letter by letter while somebody fixes a typo.
  const typeFunctionName = (name: string) =>
    onChange({ action: "script.call", function: name, params: currentParams });

  const paramRows = (
    <>
      {declared.length > 0 && (
        <div>
          <label style={labelStyle}>Parameters</label>
          {declared.map((param) => (
            <div key={param.name} style={{ marginBottom: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
                <span style={{ fontSize: 11, color: "var(--text-secondary)", fontWeight: 500 }}>
                  {param.name}
                </span>
                {param.type && (
                  <span style={{
                    fontSize: 10, padding: "0 4px", borderRadius: 3,
                    background: "var(--bg-hover)", color: "var(--text-muted)",
                  }}>
                    {param.type}
                  </span>
                )}
                {param.required && (
                  <span style={{ fontSize: 10, color: "#ef4444" }}>required</span>
                )}
                {param.default !== undefined && (
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    default: {String(param.default)}
                  </span>
                )}
              </div>
              <ParamInput
                def={scriptParamDef(param)}
                value={String(currentParams[param.name] ?? "")}
                onChange={(val) =>
                  write({ params: { ...currentParams, [param.name]: coerceForParam(val, param) } })
                }
                values={currentParams}
                allowDynamic
                eventContext={eventTokens}
                placeholder={`Enter ${param.name}...`}
                style={{ flex: 1 }}
              />
            </div>
          ))}
        </div>
      )}
      {(selected?.accepts_extra || (!selected && currentValue)) && (
        <div>
          <label style={labelStyle}>
            {declared.length > 0 ? "Other parameters" : "Parameters"}
          </label>
          <NamedValueRows
            values={Object.fromEntries(
              Object.entries(currentParams).filter(
                ([k]) => !declared.some((p) => p.name === k),
              ),
            )}
            onChange={(extra) => {
              const kept: Record<string, unknown> = {};
              for (const p of declared) {
                if (p.name in currentParams) kept[p.name] = currentParams[p.name];
              }
              write({ params: { ...kept, ...extra } });
            }}
            eventTokens={eventTokens}
            keyPlaceholder="name"
            addLabel="Add parameter"
          />
        </div>
      )}
    </>
  );

  // Use dropdown if we have functions, text input as fallback
  if (loaded && functions.length > 0) {
    return (
      <>
        <div>
          <label style={labelStyle}>Function</label>
          <select
            value={selected ? `${selected.script}.${selected.function}` : ""}
            onChange={(e) => {
              const fn = functions.find(
                (f) => `${f.script}.${f.function}` === e.target.value,
              );
              pickFunction(fn, fn?.function ?? "");
            }}
            style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
          >
            <option value="">Select function...</option>
            {[...grouped.entries()].map(([scriptId, fns]) => (
              <optgroup key={scriptId} label={scriptId}>
                {fns.map((fn) => (
                  <option key={`${fn.script}.${fn.function}`} value={`${fn.script}.${fn.function}`}>
                    {fn.function}{fn.doc ? `: ${fn.doc}` : ""}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          {currentValue && !selected && (
            <div style={{ fontSize: 11, color: "#f59e0b", marginTop: 3, paddingLeft: 2 }}>
              No enabled script defines <code>{currentValue}</code>. Pressing this does nothing.
            </div>
          )}
        </div>
        {paramRows}
      </>
    );
  }

  return (
    <>
      <div>
        <label style={labelStyle}>Function Name</label>
        <input
          value={currentValue}
          onChange={(e) => typeFunctionName(e.target.value)}
          placeholder="my_function"
          style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        {loaded && (
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3, paddingLeft: 2 }}>
            No callable functions found. A control calls a plain function in an
            enabled script, not an <code>@on_event</code> handler.
          </div>
        )}
      </div>
      {paramRows}
    </>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};
