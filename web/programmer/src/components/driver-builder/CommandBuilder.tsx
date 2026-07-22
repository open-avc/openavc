import { useEffect, useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type {
  DriverCommandDef,
  DriverDefinition,
  DriverParamDef,
} from "../../api/types";
import { EnumValuesEditor } from "../shared/EnumValuesEditor";
import { OscArgsEditor } from "./OscArgsEditor";

interface CommandBuilderProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function CommandBuilder({ draft, onUpdate }: CommandBuilderProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const commands = draft.commands;
  const commandNames = Object.keys(commands);

  const addCommand = () => {
    // Generate unique name that doesn't collide with existing commands
    let counter = commandNames.length + 1;
    let name = `command_${counter}`;
    while (name in commands) {
      counter++;
      name = `command_${counter}`;
    }
    // Seed the new command's shape from the driver's transport so users
    // see the right fields immediately.
    let initial: DriverCommandDef;
    if (draft.transport === "http") {
      initial = {
        label: "New Command",
        send: "",
        method: "GET",
        path: "/",
        params: {},
      };
    } else if (draft.transport === "osc") {
      initial = {
        label: "New Command",
        send: "",
        address: "/",
        args: [],
        params: {},
      };
    } else {
      initial = { label: "New Command", send: "", params: {} };
    }
    onUpdate({
      commands: { ...commands, [name]: initial },
    });
    setExpanded(name);
  };

  const removeCommand = (name: string) => {
    const next = { ...commands };
    delete next[name];
    onUpdate({ commands: next });
    if (expanded === name) setExpanded(null);
  };

  const updateCommand = (name: string, partial: Partial<DriverCommandDef>) => {
    // Merge, then strip any keys whose value is `undefined` so we don't
    // serialize `key: null` into YAML when the caller wanted to delete a
    // legacy field (e.g., `string` after migrating to `send`).
    const merged = { ...commands[name], ...partial } as Record<string, unknown>;
    for (const k of Object.keys(merged)) {
      if (merged[k] === undefined) delete merged[k];
    }
    onUpdate({
      commands: {
        ...commands,
        [name]: merged as unknown as DriverCommandDef,
      },
    });
  };

  const renameCommand = (oldName: string, newName: string) => {
    if (!newName || newName === oldName || newName in commands) return;
    const next: Record<string, DriverCommandDef> = {};
    for (const [k, v] of Object.entries(commands)) {
      next[k === oldName ? newName : k] = v;
    }
    onUpdate({ commands: next });
    if (expanded === oldName) setExpanded(newName);
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  return (
    <div>
      {commandNames.length === 0 && (
        <p
          style={{
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            marginBottom: "var(--space-md)",
          }}
        >
          No commands defined yet. Add commands that this driver can send to the
          device.
        </p>
      )}

      {commandNames.map((name) => {
        const cmd = commands[name];
        const isOpen = expanded === name;
        return (
          <div
            key={name}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-sm)",
              background: "var(--bg-surface)",
            }}
          >
            <button
              onClick={() => setExpanded(isOpen ? null : name)}
              style={{
                display: "flex",
                alignItems: "center",
                width: "100%",
                padding: "var(--space-sm) var(--space-md)",
                gap: "var(--space-sm)",
                textAlign: "left",
              }}
            >
              {isOpen ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
              <span
                style={{
                  flex: 1,
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {name}
              </span>
              <span
                style={{
                  color: "var(--text-muted)",
                  fontSize: "11px",
                }}
              >
                {cmd.label}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeCommand(name);
                }}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </button>

            {isOpen && (
              <div
                style={{
                  padding: "var(--space-md)",
                  borderTop: "1px solid var(--border-color)",
                }}
              >
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "var(--space-md)",
                    marginBottom: "var(--space-md)",
                  }}
                >
                  <div>
                    <label style={labelStyle}>Command ID</label>
                    <input
                      value={name}
                      onChange={(e) => renameCommand(name, e.target.value)}
                      style={{ width: "100%" }}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Display Label</label>
                    <input
                      value={cmd.label}
                      onChange={(e) =>
                        updateCommand(name, { label: e.target.value })
                      }
                      style={{ width: "100%" }}
                    />
                  </div>
                </div>

                <div style={{ marginBottom: "var(--space-md)" }}>
                  <label style={labelStyle}>Help Text</label>
                  <input
                    value={(cmd as any).help ?? ""}
                    onChange={(e) =>
                      updateCommand(name, { help: e.target.value } as any)
                    }
                    placeholder="Brief description of what this command does"
                    style={{ width: "100%" }}
                  />
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-muted)",
                      marginTop: "var(--space-xs)",
                    }}
                  >
                    Shown to users when selecting this command.
                  </div>
                </div>

                {draft.transport === "osc" ? (
                  <div style={{ marginBottom: "var(--space-md)" }}>
                    <label style={labelStyle}>OSC Address</label>
                    <input
                      value={(cmd as any).address ?? ""}
                      onChange={(e) =>
                        updateCommand(name, { address: e.target.value } as any)
                      }
                      placeholder='e.g., /ch/01/mix/fader'
                      style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                    />
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-muted)",
                        marginTop: "var(--space-xs)",
                      }}
                    >
                      OSC address path. Use {"{param_name}"} for parameter substitution.
                    </div>
                    <OscArgsEditor
                      args={(cmd as any).args ?? []}
                      onChange={(args) => updateCommand(name, { args } as any)}
                    />
                  </div>
                ) : draft.transport === "http" ? (
                  <HttpCommandFields
                    cmd={cmd}
                    onUpdate={(partial) => updateCommand(name, partial)}
                  />
                ) : (
                  <div style={{ marginBottom: "var(--space-md)" }}>
                    <label style={labelStyle}>Command String</label>
                    <input
                      value={cmd.send ?? cmd.string ?? ""}
                      onChange={(e) =>
                        updateCommand(name, {
                          send: e.target.value,
                          string: undefined,
                        })
                      }
                      placeholder="e.g., %1POWR {value}\r"
                      style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                    />
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-muted)",
                        marginTop: "var(--space-xs)",
                      }}
                    >
                      Use {"{param_name}"} for parameter placeholders. Use \r, \n
                      for control characters.
                    </div>
                    {/* Per-command opt-out of the driver's command framing.
                        Only meaningful — and only shown — when the driver
                        declares a command_prefix/command_suffix in the
                        Connection tab. */}
                    {(draft.command_prefix || draft.command_suffix) && (
                      <label
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          fontSize: "var(--font-size-sm)",
                          color: "var(--text-secondary)",
                          marginTop: "var(--space-sm)",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={!!cmd.raw}
                          onChange={(e) =>
                            updateCommand(name, {
                              raw: e.target.checked || undefined,
                            })
                          }
                        />
                        Send raw — skip the driver&apos;s command framing for this
                        command
                      </label>
                    )}
                  </div>
                )}

                <ParamEditor
                  params={cmd.params}
                  childTypes={Object.keys(draft.child_entity_types ?? {})}
                  onChange={(params) => updateCommand(name, { params })}
                />
              </div>
            )}
          </div>
        );
      })}

      <button
        onClick={addCommand}
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
        <Plus size={14} /> Add Command
      </button>
    </div>
  );
}

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;

function HttpCommandFields({
  cmd,
  onUpdate,
}: {
  cmd: DriverCommandDef;
  onUpdate: (partial: Partial<DriverCommandDef>) => void;
}) {
  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };
  const helpStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: "var(--space-xs)",
  };

  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "120px 1fr",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <div>
          <label style={labelStyle}>Method</label>
          <select
            value={(cmd.method ?? "GET").toUpperCase()}
            onChange={(e) => onUpdate({ method: e.target.value })}
            style={{ width: "100%" }}
          >
            {HTTP_METHODS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label style={labelStyle}>Path</label>
          <input
            value={cmd.path ?? ""}
            onChange={(e) => onUpdate({ path: e.target.value })}
            placeholder="/api/{app_key}/lights/{light_id}/state"
            style={{ width: "100%", fontFamily: "var(--font-mono)" }}
          />
        </div>
      </div>
      <div style={helpStyle}>
        Path is appended to the device's base URL. Use{" "}
        <code>{"{param_name}"}</code> for placeholders — both command params
        and device config keys (like <code>{"{host}"}</code>,{" "}
        <code>{"{app_key}"}</code>) are substituted.
      </div>

      <div style={{ marginTop: "var(--space-md)" }}>
        <label style={labelStyle}>Body</label>
        <textarea
          value={cmd.body ?? ""}
          onChange={(e) => onUpdate({ body: e.target.value })}
          placeholder='{"on": true}  or  &lt;Command>...&lt;/Command>'
          rows={4}
          style={{
            width: "100%",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            resize: "vertical",
          }}
        />
        <div style={helpStyle}>
          Sent as the request body. JSON is parsed and re-serialized; anything
          else is sent as a raw byte string (set <code>Content-Type</code> in
          headers below for XML, form-urlencoded, etc.). Leave blank for{" "}
          <code>GET</code> / <code>DELETE</code>.
        </div>
      </div>

      <div style={{ marginTop: "var(--space-md)" }}>
        <label style={labelStyle}>Headers</label>
        <KeyValueList
          values={cmd.headers ?? {}}
          onChange={(headers) =>
            onUpdate({
              headers: Object.keys(headers).length ? headers : undefined,
            })
          }
          keyPlaceholder="Header-Name"
          valuePlaceholder='e.g. text/xml'
          monoValue
        />
        <div style={helpStyle}>
          Per-request headers, applied on top of the transport's default
          headers. Common: <code>Content-Type: text/xml</code> for XML APIs.
          Values support <code>{"{param}"}</code> substitution.
        </div>
      </div>

      <div style={{ marginTop: "var(--space-md)" }}>
        <label style={labelStyle}>Query Parameters</label>
        <KeyValueList
          values={cmd.query_params ?? {}}
          onChange={(query_params) =>
            onUpdate({
              query_params: Object.keys(query_params).length
                ? query_params
                : undefined,
            })
          }
          keyPlaceholder="key"
          valuePlaceholder="value"
          monoValue
        />
        <div style={helpStyle}>
          Appended to the URL as <code>?key=value&amp;...</code>. Values support{" "}
          <code>{"{param}"}</code> substitution.
        </div>
      </div>
    </div>
  );
}

export function KeyValueList({
  values,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  monoValue,
}: {
  values: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  monoValue?: boolean;
}) {
  // Render insertion-ordered to keep the user's typing flow stable.
  const entries = Object.entries(values);

  const updateRow = (index: number, key: string, value: string) => {
    const next: Record<string, string> = {};
    entries.forEach(([k, v], i) => {
      if (i === index) {
        if (key) next[key] = value;
      } else {
        next[k] = v;
      }
    });
    onChange(next);
  };

  const removeRow = (index: number) => {
    const next: Record<string, string> = {};
    entries.forEach(([k, v], i) => {
      if (i !== index) next[k] = v;
    });
    onChange(next);
  };

  const addRow = () => {
    // Insert an empty placeholder key the user can fill in.
    let key = "";
    let counter = 1;
    while (key === "" || key in values) {
      key = `key${counter}`;
      counter++;
    }
    onChange({ ...values, [key]: "" });
  };

  return (
    <div>
      {entries.map(([k, v], i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 2fr auto",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
            alignItems: "center",
          }}
        >
          <input
            value={k}
            onChange={(e) => updateRow(i, e.target.value, v)}
            placeholder={keyPlaceholder}
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <input
            value={v}
            onChange={(e) => updateRow(i, k, e.target.value)}
            placeholder={valuePlaceholder}
            style={{
              fontFamily: monoValue ? "var(--font-mono)" : "inherit",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <button
            onClick={() => removeRow(i)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        onClick={addRow}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Add
      </button>
    </div>
  );
}

export function ParamEditor({
  params,
  childTypes,
  onChange,
}: {
  params: Record<string, DriverParamDef>;
  /** Driver-declared child types — passed to ParamRow so the
   *  ``child_id`` type can render a dropdown of valid child types. Empty
   *  when the driver hasn't declared any. */
  childTypes: string[];
  onChange: (params: Record<string, DriverParamDef>) => void;
}) {
  const paramNames = Object.keys(params);

  const addParam = () => {
    let counter = paramNames.length + 1;
    let name = `param${counter}`;
    while (name in params) {
      counter++;
      name = `param${counter}`;
    }
    onChange({ ...params, [name]: { type: "string" } });
  };

  const removeParam = (name: string) => {
    const next = { ...params };
    delete next[name];
    onChange(next);
  };

  const renameParam = (oldName: string, newName: string): { ok: boolean; reason?: string } => {
    const trimmed = newName.trim();
    if (!trimmed || trimmed === oldName) return { ok: true };
    if (trimmed in params) {
      return { ok: false, reason: `A parameter named "${trimmed}" already exists.` };
    }
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(trimmed)) {
      return {
        ok: false,
        reason: "Use letters, digits, and underscores only — must start with a letter or underscore.",
      };
    }
    const next: typeof params = {};
    for (const [k, v] of Object.entries(params)) {
      next[k === oldName ? trimmed : k] = v;
    }
    onChange(next);
    return { ok: true };
  };

  const updateParam = (name: string, partial: Partial<DriverParamDef>) => {
    const merged = { ...params[name], ...partial } as Record<string, unknown>;
    for (const k of Object.keys(merged)) {
      if (merged[k] === undefined) delete merged[k];
    }
    onChange({ ...params, [name]: merged as unknown as DriverParamDef });
  };

  return (
    <div style={{ marginTop: "var(--space-md)" }}>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-xs)",
        }}
      >
        Parameters
      </div>
      {paramNames.map((name) => (
        <ParamRow
          key={name}
          name={name}
          def={params[name]}
          childTypes={childTypes}
          tryRename={(next) => renameParam(name, next)}
          onUpdate={(partial) => updateParam(name, partial)}
          onRemove={() => removeParam(name)}
        />
      ))}
      <button
        onClick={addParam}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Add Parameter
      </button>
    </div>
  );
}

function ParamRow({
  name,
  def,
  childTypes,
  tryRename,
  onUpdate,
  onRemove,
}: {
  name: string;
  def: DriverParamDef;
  childTypes: string[];
  tryRename: (next: string) => { ok: boolean; reason?: string };
  onUpdate: (partial: Partial<DriverParamDef>) => void;
  onRemove: () => void;
}) {
  // Local buffer for the name input — lets the user keep typing illegal
  // characters without us rewriting the field underfoot. We commit (or
  // revert) on blur, and surface the rejection reason inline so the user
  // knows what happened.
  const [draftName, setDraftName] = useState(name);
  const [renameError, setRenameError] = useState<string | null>(null);

  // Re-sync if the canonical name changes from outside (parent rename).
  useEffect(() => {
    setDraftName(name);
    setRenameError(null);
  }, [name]);

  const commitRename = () => {
    if (draftName === name) {
      setRenameError(null);
      return;
    }
    const result = tryRename(draftName);
    if (!result.ok) {
      setRenameError(result.reason ?? "Invalid name.");
      // Leave the bad text in the input so the user can fix it instead
      // of guessing what they typed.
    } else {
      setRenameError(null);
    }
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "11px",
    color: "var(--text-muted)",
    marginBottom: 2,
  };
  const isNumeric = def.type === "integer" || def.type === "number";
  const isEnum = def.type === "enum";
  const isBool = def.type === "boolean";
  const isChildId = def.type === "child_id";

  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        padding: "var(--space-sm) var(--space-md)",
        marginBottom: "var(--space-xs)",
        background: "var(--bg-surface)",
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 110px auto auto",
          gap: "var(--space-sm)",
          alignItems: "end",
        }}
      >
        <div>
          <span style={labelStyle}>Name</span>
          <input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") {
                setDraftName(name);
                setRenameError(null);
              }
            }}
            style={{
              width: "100%",
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-mono)",
              borderColor: renameError ? "var(--color-error)" : undefined,
            }}
          />
          {renameError && (
            <div
              style={{
                fontSize: 11,
                color: "var(--color-error)",
                marginTop: 2,
              }}
            >
              {renameError}
            </div>
          )}
        </div>
        <div>
          <span style={labelStyle}>Display Label</span>
          <input
            value={def.label ?? ""}
            onChange={(e) =>
              onUpdate({ label: e.target.value || undefined })
            }
            placeholder={name}
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <div>
          <span style={labelStyle}>Type</span>
          <select
            value={def.type}
            onChange={(e) => {
              const t = e.target.value;
              const partial: Partial<DriverParamDef> = { type: t };
              // Strip type-incompatible fields when switching types so
              // round-tripped YAML stays clean.
              if (t !== "integer" && t !== "number") {
                partial.min = undefined;
                partial.max = undefined;
              }
              if (t !== "number") {
                partial.decimals = undefined;
              }
              if (t !== "enum") {
                partial.values = undefined;
              }
              if (t !== "child_id") {
                partial.child_type = undefined;
              } else {
                // Default the child_type to the first declared type so the
                // param is valid the moment it's switched (if any exist).
                partial.child_type = def.child_type ?? childTypes[0];
              }
              onUpdate(partial);
            }}
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          >
            <option value="string">String</option>
            <option value="integer">Integer</option>
            <option value="number">Number</option>
            <option value="boolean">Boolean</option>
            <option value="enum">Enum</option>
            <option value="child_id">Child ID</option>
          </select>
        </div>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            paddingBottom: 6,
          }}
        >
          <input
            type="checkbox"
            checked={!!def.required}
            onChange={(e) =>
              onUpdate({ required: e.target.checked || undefined })
            }
          />
          Required
        </label>
        <button
          onClick={onRemove}
          style={{
            padding: "4px",
            color: "var(--text-muted)",
            alignSelf: "center",
          }}
          title="Remove parameter"
        >
          <Trash2 size={14} />
        </button>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            def.type === "number"
              ? "1fr 1fr 1fr 1fr 1fr"
              : isNumeric
                ? "1fr 1fr 1fr 1fr"
                : "1fr 1fr",
          gap: "var(--space-sm)",
          marginTop: "var(--space-sm)",
        }}
      >
        <div>
          <span style={labelStyle}>Help Text</span>
          <input
            value={def.help ?? def.description ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              // Always write `help`. If the existing def used `description`
              // (legacy), drop it so we don't ship two equivalent fields.
              onUpdate({
                help: v || undefined,
                description: undefined,
              });
            }}
            placeholder="Brief description shown in tooltips"
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <div>
          <span style={labelStyle}>Default</span>
          <input
            value={def.default == null ? "" : String(def.default)}
            onChange={(e) => {
              const raw = e.target.value;
              if (!raw) {
                onUpdate({ default: undefined });
                return;
              }
              if (def.type === "integer") {
                const n = parseInt(raw, 10);
                onUpdate({ default: Number.isFinite(n) ? n : raw });
              } else if (def.type === "number") {
                const n = parseFloat(raw);
                onUpdate({ default: Number.isFinite(n) ? n : raw });
              } else if (def.type === "boolean") {
                onUpdate({ default: raw === "true" });
              } else {
                onUpdate({ default: raw });
              }
            }}
            placeholder={
              isBool ? "true / false" : isEnum ? "must be one of values" : ""
            }
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
        {isNumeric && (
          <>
            <div>
              <span style={labelStyle}>Min</span>
              <input
                type="number"
                value={def.min ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  onUpdate({
                    min:
                      v === ""
                        ? undefined
                        : def.type === "integer"
                          ? parseInt(v, 10)
                          : parseFloat(v),
                  });
                }}
                style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
              />
            </div>
            <div>
              <span style={labelStyle}>Max</span>
              <input
                type="number"
                value={def.max ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  onUpdate({
                    max:
                      v === ""
                        ? undefined
                        : def.type === "integer"
                          ? parseInt(v, 10)
                          : parseFloat(v),
                  });
                }}
                style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
              />
            </div>
            {def.type === "number" && (
              <div>
                <span style={labelStyle}>Decimals</span>
                <input
                  type="number"
                  value={def.decimals ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    onUpdate({ decimals: v === "" ? undefined : parseInt(v, 10) });
                  }}
                  min={0}
                  max={6}
                  step={1}
                  placeholder="Any"
                  title="Round the value to this many decimal places on the wire (0 = whole number)."
                  style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
                />
              </div>
            )}
          </>
        )}
      </div>

      {isEnum && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <span style={labelStyle}>Allowed Values</span>
          <EnumValuesEditor
            values={def.values}
            onChange={(values) => onUpdate({ values })}
          />
        </div>
      )}

      {isChildId && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <span style={labelStyle}>Child Type</span>
          {childTypes.length === 0 ? (
            <div
              style={{
                fontSize: "11px",
                color: "var(--color-warning, #d97706)",
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "rgba(255, 152, 0, 0.12)",
                border: "1px solid rgba(255, 152, 0, 0.35)",
              }}
            >
              No child entity types declared yet. Add one in the Child Entity
              Types section above, then pick it here.
            </div>
          ) : (
            <select
              value={def.child_type ?? ""}
              onChange={(e) =>
                onUpdate({ child_type: e.target.value || undefined })
              }
              style={{
                width: "100%",
                fontSize: "var(--font-size-sm)",
                fontFamily: "var(--font-mono)",
              }}
            >
              <option value="">(select a child type)</option>
              {childTypes.map((ct) => (
                <option key={ct} value={ct}>
                  {ct}
                </option>
              ))}
            </select>
          )}
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginTop: 2,
            }}
          >
            The runtime command picker shows a dropdown of registered{" "}
            {def.child_type ? <code>{def.child_type}</code> : "children"} for
            this parameter. The value sent is the integer local ID.
          </div>
        </div>
      )}
      <ParamWireMapEditor
        map={def.map}
        onChange={(map) => onUpdate({ map })}
      />
    </div>
  );
}

/** Optional wire-value translation for a param: rows of "value" -> "wire
 *  value" applied by the runtime after validation, before the value is
 *  substituted into the send template. Use when the value the integrator
 *  picks differs from what the protocol wants on the wire — a 1-based child
 *  ID on a 0-based protocol, a named preset that sends a code. Unmapped
 *  values pass through unchanged. */
function ParamWireMapEditor({
  map,
  onChange,
}: {
  map: Record<string, string | number> | undefined;
  onChange: (map: Record<string, string | number> | undefined) => void;
}) {
  const rows = Object.entries(map ?? {});
  if (rows.length === 0) {
    return (
      <button
        onClick={() => onChange({ "": "" })}
        title="Translate the picked value to a different value on the wire (e.g. child ID 1 sends channel 0)"
        style={{
          fontSize: "11px",
          color: "var(--accent)",
          padding: "2px 0",
          display: "block",
          marginTop: "var(--space-xs)",
        }}
      >
        + Wire value map
      </button>
    );
  }
  const rebuild = (
    mutate: (next: Record<string, string | number>) => void,
  ) => {
    const next: Record<string, string | number> = { ...(map ?? {}) };
    mutate(next);
    onChange(Object.keys(next).length > 0 ? next : undefined);
  };
  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
        Wire value map (value → what is sent)
      </div>
      {rows.map(([from, to], ri) => (
        <div
          key={ri}
          style={{
            display: "flex",
            gap: "var(--space-xs)",
            alignItems: "center",
            marginBottom: 2,
          }}
        >
          <input
            value={from}
            onChange={(e) =>
              rebuild((next) => {
                const rebuilt: Record<string, string | number> = {};
                for (const [k, v] of Object.entries(next)) {
                  rebuilt[k === from ? e.target.value : k] = v;
                }
                for (const k of Object.keys(next)) delete next[k];
                Object.assign(next, rebuilt);
              })
            }
            placeholder="value"
            style={{
              width: 90,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>→</span>
          <input
            value={String(to)}
            onChange={(e) =>
              rebuild((next) => {
                next[from] = e.target.value;
              })
            }
            placeholder="wire value"
            style={{
              width: 90,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <button
            onClick={() => rebuild((next) => delete next[from])}
            style={{ padding: 1, color: "var(--text-muted)" }}
          >
            <Trash2 size={10} />
          </button>
        </div>
      ))}
      <button
        onClick={() =>
          rebuild((next) => {
            if (!("" in next)) next[""] = "";
          })
        }
        style={{ fontSize: "11px", color: "var(--accent)", padding: "2px 0" }}
      >
        + Add
      </button>
    </div>
  );
}
