import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverCommandDef, DriverDefinition } from "../../api/types";

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
                  </div>
                )}

                <ParamEditor
                  params={cmd.params}
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

function KeyValueList({
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

function ParamEditor({
  params,
  onChange,
}: {
  params: Record<
    string,
    { type: string; required?: boolean; values?: string[] }
  >;
  onChange: (
    params: Record<
      string,
      { type: string; required?: boolean; values?: string[] }
    >
  ) => void;
}) {
  const paramNames = Object.keys(params);

  const addParam = () => {
    const name = `param${paramNames.length + 1}`;
    onChange({ ...params, [name]: { type: "string" } });
  };

  const removeParam = (name: string) => {
    const next = { ...params };
    delete next[name];
    onChange(next);
  };

  const renameParam = (oldName: string, newName: string) => {
    const cleaned = newName.replace(/[^a-zA-Z0-9_]/g, "");
    if (!cleaned || cleaned === oldName) return;
    if (cleaned in params) return; // prevent collision
    const entries = Object.entries(params);
    const next: typeof params = {};
    for (const [k, v] of entries) {
      next[k === oldName ? cleaned : k] = v;
    }
    onChange(next);
  };

  return (
    <div>
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
        <div
          key={name}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
          }}
        >
          <input
            value={name}
            onChange={(e) => renameParam(name, e.target.value)}
            onBlur={(e) => renameParam(name, e.target.value)}
            style={{
              width: 120,
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-mono)",
            }}
          />
          <select
            value={params[name].type}
            onChange={(e) =>
              onChange({
                ...params,
                [name]: { ...params[name], type: e.target.value },
              })
            }
            style={{ width: 100, fontSize: "var(--font-size-sm)" }}
          >
            <option value="string">String</option>
            <option value="integer">Integer</option>
            <option value="number">Number</option>
            <option value="boolean">Boolean</option>
            <option value="enum">Enum</option>
          </select>
          <button
            onClick={() => removeParam(name)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
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

function OscArgsEditor({
  args,
  onChange,
}: {
  args: { type: string; value: string }[];
  onChange: (args: { type: string; value: string }[]) => void;
}) {
  const addArg = () => {
    onChange([...args, { type: "f", value: "" }]);
  };

  const removeArg = (index: number) => {
    onChange(args.filter((_, i) => i !== index));
  };

  const updateArg = (index: number, partial: Partial<{ type: string; value: string }>) => {
    const next = [...args];
    next[index] = { ...next[index], ...partial };
    onChange(next);
  };

  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-xs)",
        }}
      >
        Arguments
      </div>
      {args.length === 0 && (
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginBottom: "var(--space-xs)",
          }}
        >
          No arguments — message will be sent as a query (address only).
        </div>
      )}
      {args.map((arg, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
          }}
        >
          <select
            value={arg.type}
            onChange={(e) => updateArg(i, { type: e.target.value })}
            style={{ width: 100, fontSize: "var(--font-size-sm)" }}
          >
            <option value="f">Float</option>
            <option value="i">Integer</option>
            <option value="s">String</option>
            <option value="T">True</option>
            <option value="F">False</option>
          </select>
          {!["T", "F", "N"].includes(arg.type) && (
            <input
              value={arg.value}
              onChange={(e) => updateArg(i, { value: e.target.value })}
              placeholder={
                arg.type === "f" ? "0.0" : arg.type === "i" ? "0" : "text"
              }
              style={{
                flex: 1,
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
              }}
            />
          )}
          <button
            onClick={() => removeArg(i)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        onClick={addArg}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Add Argument
      </button>
    </div>
  );
}
