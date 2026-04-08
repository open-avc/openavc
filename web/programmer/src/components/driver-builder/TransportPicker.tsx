import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverDefinition } from "../../api/types";

interface TransportPickerProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function TransportPicker({ draft, onUpdate }: TransportPickerProps) {
  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  const rowStyle: React.CSSProperties = {
    marginBottom: "var(--space-md)",
  };

  return (
    <div>
      <div style={rowStyle}>
        <label style={labelStyle}>Transport Type</label>
        <select
          value={draft.transport}
          onChange={(e) => onUpdate({ transport: e.target.value })}
          style={{ width: "100%" }}
        >
          <option value="tcp">TCP</option>
          <option value="serial">Serial</option>
          <option value="http">HTTP / REST API</option>
        </select>
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          {draft.transport === "http"
            ? "Choose HTTP for devices with REST APIs (JSON, SOAP, etc.)."
            : "Choose TCP for network devices, Serial for RS-232/RS-485, or HTTP for REST API devices."}
        </div>
      </div>

      {draft.transport !== "http" && <div style={rowStyle}>
        <label style={labelStyle}>Message Delimiter</label>
        <select
          value={draft.delimiter}
          onChange={(e) => onUpdate({ delimiter: e.target.value })}
          style={{ width: "100%" }}
        >
          <option value="\r\n">CR+LF (\r\n) — most common</option>
          <option value="\r">CR only (\r) — Extron, PJLink</option>
          <option value="\n">LF only (\n) — Biamp, QSC</option>
          {!["\r\n", "\r", "\n"].includes(draft.delimiter) && (
            <option value={draft.delimiter}>Custom: {draft.delimiter}</option>
          )}
        </select>
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          How the device marks the end of each message. Check the device&apos;s
          protocol manual if unsure.
          {!["\r\n", "\r", "\n"].includes(draft.delimiter) && (
            <span> Current value is a custom delimiter: <code>{draft.delimiter}</code></span>
          )}
        </div>
      </div>}

      {draft.transport === "http" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Port</label>
            <input
              type="number"
              value={
                (draft.default_config.port as number | undefined) ?? 80
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    port: parseInt(e.target.value) || 80,
                  },
                })
              }
              style={{ width: 120 }}
            />
          </div>
          <div style={rowStyle}>
            <label style={labelStyle}>Authentication</label>
            <select
              value={
                (draft.default_config.auth_type as string | undefined) ?? "none"
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    auth_type: e.target.value,
                  },
                })
              }
              style={{ width: "100%" }}
            >
              <option value="none">None</option>
              <option value="basic">HTTP Basic Auth</option>
              <option value="digest">HTTP Digest Auth</option>
              <option value="bearer">Bearer Token</option>
              <option value="api_key">API Key (custom header)</option>
            </select>
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-muted)",
                marginTop: "var(--space-xs)",
              }}
            >
              Users configure credentials per device. This sets the auth method.
            </div>
          </div>
          <div style={{ display: "flex", gap: "var(--space-lg)", ...rowStyle }}>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <input
                type="checkbox"
                checked={(draft.default_config.ssl as boolean | undefined) ?? false}
                onChange={(e) =>
                  onUpdate({
                    default_config: {
                      ...draft.default_config,
                      ssl: e.target.checked,
                    },
                  })
                }
              />
              Use HTTPS
            </label>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <input
                type="checkbox"
                checked={(draft.default_config.verify_ssl as boolean | undefined) ?? false}
                onChange={(e) =>
                  onUpdate({
                    default_config: {
                      ...draft.default_config,
                      verify_ssl: e.target.checked,
                    },
                  })
                }
              />
              Verify SSL Certificate
            </label>
          </div>
        </>
      )}

      {draft.transport === "tcp" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Port</label>
            <input
              type="number"
              value={
                (draft.default_config.port as number | undefined) ?? 23
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    port: parseInt(e.target.value) || 23,
                  },
                })
              }
              style={{ width: 120 }}
            />
          </div>
        </>
      )}

      {draft.transport === "serial" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Baud Rate</label>
            <select
              value={
                String(
                  (draft.default_config.baudrate as number | undefined) ?? 9600
                )
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    baudrate: parseInt(e.target.value),
                  },
                })
              }
              style={{ width: 160 }}
            >
              {[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200].map(
                (r) => (
                  <option key={r} value={String(r)}>
                    {r}
                  </option>
                )
              )}
            </select>
          </div>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Parity</label>
            <select
              value={
                (draft.default_config.parity as string | undefined) ?? "N"
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    parity: e.target.value,
                  },
                })
              }
              style={{ width: 120 }}
            >
              <option value="N">None</option>
              <option value="E">Even</option>
              <option value="O">Odd</option>
            </select>
          </div>
        </>
      )}

      {/* Inter-command delay — TCP and serial only */}
      {draft.transport !== "http" && <div style={rowStyle}>
        <label style={labelStyle}>Inter-Command Delay (seconds)</label>
        <input
          type="number"
          value={
            (draft.default_config.inter_command_delay as number | undefined) ?? 0
          }
          onChange={(e) =>
            onUpdate({
              default_config: {
                ...draft.default_config,
                inter_command_delay: parseFloat(e.target.value) || 0,
              },
            })
          }
          min={0}
          step={0.01}
          style={{ width: 120 }}
        />
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          Minimum delay between commands. Some devices need this to avoid
          command flooding (e.g., Extron recommends 0.1s).
        </div>
      </div>}

      {/* Config schema editor */}
      <ConfigSchemaEditor draft={draft} onUpdate={onUpdate} />
    </div>
  );
}


// ── Config Schema Editor ──

interface ConfigField {
  type: string;
  label: string;
  default?: unknown;
  description?: string;
  secret?: boolean;
  required?: boolean;
  min?: number;
  max?: number;
  values?: string[];
}

function ConfigSchemaEditor({
  draft,
  onUpdate,
}: {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const schema = (draft.config_schema ?? {}) as Record<string, ConfigField>;
  const defaultConfig = draft.default_config ?? {};
  // Filter out built-in fields that are handled by other parts of the UI
  const builtinKeys = new Set([
    "host", "port", "baudrate", "parity", "poll_interval", "inter_command_delay",
  ]);
  const fieldNames = Object.keys(schema).filter((k) => !builtinKeys.has(k));

  const addField = () => {
    let counter = fieldNames.length + 1;
    let name = `config_${counter}`;
    while (name in schema) {
      counter++;
      name = `config_${counter}`;
    }
    onUpdate({
      config_schema: {
        ...schema,
        [name]: { type: "string", label: "New Config Field", default: "" },
      },
      default_config: { ...defaultConfig, [name]: "" },
    });
    setExpanded(name);
  };

  const removeField = (name: string) => {
    const nextSchema = { ...schema };
    delete nextSchema[name];
    const nextDefault = { ...defaultConfig };
    delete nextDefault[name];
    onUpdate({ config_schema: nextSchema, default_config: nextDefault });
    if (expanded === name) setExpanded(null);
  };

  const updateField = (name: string, partial: Partial<ConfigField>) => {
    onUpdate({
      config_schema: {
        ...schema,
        [name]: { ...schema[name], ...partial },
      },
    });
  };

  const renameField = (oldName: string, newName: string) => {
    const cleaned = newName.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();
    if (!cleaned || cleaned === oldName || cleaned in schema) return;
    const nextSchema: Record<string, ConfigField> = {};
    for (const [k, v] of Object.entries(schema)) {
      nextSchema[k === oldName ? cleaned : k] = v;
    }
    const nextDefault: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(defaultConfig)) {
      nextDefault[k === oldName ? cleaned : k] = v;
    }
    onUpdate({ config_schema: nextSchema, default_config: nextDefault });
    if (expanded === oldName) setExpanded(cleaned);
  };

  const updateDefault = (name: string, value: string) => {
    onUpdate({ default_config: { ...defaultConfig, [name]: value } });
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  return (
    <div style={{ marginTop: "var(--space-lg)" }}>
      <h3
        style={{
          fontSize: "var(--font-size-base)",
          marginBottom: "var(--space-xs)",
        }}
      >
        Configuration Fields
      </h3>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Define custom settings users configure per device (display IDs,
        instance tags, passwords, etc.). These become {"{field_name}"}
        placeholders in command strings.
      </p>

      {fieldNames.map((name) => {
        const field = schema[name];
        if (!field) return null;
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
              {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
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
                style={{ color: "var(--text-muted)", fontSize: "11px" }}
              >
                {field.label}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeField(name);
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
                    <label style={labelStyle}>Field ID</label>
                    <input
                      value={name}
                      onChange={(e) => renameField(name, e.target.value)}
                      style={{
                        width: "100%",
                        fontFamily: "var(--font-mono)",
                      }}
                    />
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-muted)",
                        marginTop: 2,
                      }}
                    >
                      Use as {`{${name}}`} in command strings.
                    </div>
                  </div>
                  <div>
                    <label style={labelStyle}>Display Label</label>
                    <input
                      value={field.label}
                      onChange={(e) =>
                        updateField(name, { label: e.target.value })
                      }
                      style={{ width: "100%" }}
                    />
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr 1fr",
                    gap: "var(--space-md)",
                    marginBottom: "var(--space-md)",
                  }}
                >
                  <div>
                    <label style={labelStyle}>Type</label>
                    <select
                      value={field.type}
                      onChange={(e) =>
                        updateField(name, { type: e.target.value })
                      }
                      style={{ width: "100%" }}
                    >
                      <option value="string">String</option>
                      <option value="integer">Integer</option>
                      <option value="number">Number</option>
                    </select>
                  </div>
                  <div>
                    <label style={labelStyle}>Default Value</label>
                    <input
                      value={String(defaultConfig[name] ?? "")}
                      onChange={(e) => updateDefault(name, e.target.value)}
                      style={{ width: "100%" }}
                    />
                  </div>
                  <div
                    style={{
                      display: "flex",
                      gap: "var(--space-lg)",
                      alignItems: "end",
                      paddingBottom: "var(--space-xs)",
                    }}
                  >
                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "var(--space-xs)",
                        fontSize: "var(--font-size-sm)",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={field.required ?? false}
                        onChange={(e) =>
                          updateField(name, { required: e.target.checked })
                        }
                      />
                      Required
                    </label>
                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "var(--space-xs)",
                        fontSize: "var(--font-size-sm)",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={field.secret ?? false}
                        onChange={(e) =>
                          updateField(name, { secret: e.target.checked })
                        }
                      />
                      Secret
                    </label>
                  </div>
                </div>

                <div>
                  <label style={labelStyle}>Description</label>
                  <input
                    value={field.description ?? ""}
                    onChange={(e) =>
                      updateField(name, { description: e.target.value })
                    }
                    placeholder="Help text shown to users"
                    style={{ width: "100%" }}
                  />
                </div>
              </div>
            )}
          </div>
        );
      })}

      <button
        onClick={addField}
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
        <Plus size={14} /> Add Config Field
      </button>
    </div>
  );
}
