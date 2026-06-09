import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverDefinition } from "../../api/types";

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

interface ConfigSchemaEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the user-facing configuration fields surfaced on the Add Device dialog
 * (display IDs, instance tags, custom passwords, anything that isn't the
 * baseline transport host/port/baudrate). Built-in transport keys are
 * filtered out so authors don't accidentally redefine them.
 */
export function ConfigSchemaEditor({ draft, onUpdate }: ConfigSchemaEditorProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const schema = (draft.config_schema ?? {}) as Record<string, ConfigField>;
  const defaultConfig = draft.default_config ?? {};
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
    <div>
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
              <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>
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
                      onChange={(e) => {
                        const t = e.target.value;
                        const partial: Partial<ConfigField> = { type: t };
                        if (t !== "enum") partial.values = undefined;
                        updateField(name, partial);
                      }}
                      style={{ width: "100%" }}
                    >
                      <option value="string">String</option>
                      <option value="text">Text (multi-line)</option>
                      <option value="integer">Integer</option>
                      <option value="number">Number</option>
                      <option value="boolean">Boolean</option>
                      <option value="enum">Enum (dropdown)</option>
                    </select>
                  </div>
                  <div>
                    <label style={labelStyle}>Default Value</label>
                    <input
                      value={field.secret ? "" : String(defaultConfig[name] ?? "")}
                      onChange={(e) => updateDefault(name, e.target.value)}
                      disabled={field.secret}
                      placeholder={field.secret ? "Secret fields can't have a default" : ""}
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
                        onChange={(e) => {
                          const isSecret = e.target.checked;
                          // A secret must never carry a default value — clear it
                          // when the field is marked secret.
                          onUpdate(
                            isSecret
                              ? {
                                  config_schema: { ...schema, [name]: { ...schema[name], secret: true } },
                                  default_config: { ...defaultConfig, [name]: "" },
                                }
                              : {
                                  config_schema: { ...schema, [name]: { ...schema[name], secret: false } },
                                },
                          );
                        }}
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

                {field.type === "enum" && (
                  <div style={{ marginTop: "var(--space-md)" }}>
                    <label style={labelStyle}>Allowed Values</label>
                    <input
                      value={(field.values ?? []).join(", ")}
                      onChange={(e) => {
                        const values = e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean);
                        updateField(name, {
                          values: values.length ? values : undefined,
                        });
                      }}
                      placeholder="e.g. tcp, udp, http"
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
                      Comma-separated. The Add Device dialog renders these as
                      a dropdown.
                    </div>
                  </div>
                )}
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
