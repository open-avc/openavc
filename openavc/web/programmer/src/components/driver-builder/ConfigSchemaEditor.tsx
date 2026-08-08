import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverDefinition } from "../../api/types";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";
import {
  type ConfigFieldDef as ConfigField,
  NUMERIC_CONFIG_TYPES,
  applyConfigFieldTypeChange,
  applyConfigSecretToggle,
  coerceConfigDefault,
} from "./configSchemaHelpers";

const sanitizeFieldName = (raw: string) =>
  raw.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();

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
        [name]: { type: "string", label: "New Config Field" },
      },
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

  const renameField = (oldName: string, newName: string): RenameResult => {
    const cleaned = sanitizeFieldName(newName);
    if (!cleaned) return { ok: false, reason: "ID can't be empty." };
    if (cleaned === oldName) return { ok: true };
    if (cleaned in schema) {
      return { ok: false, reason: `"${cleaned}" already exists.` };
    }
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
    return { ok: true };
  };

  // `undefined` = no default: the key is dropped so the exported YAML doesn't
  // carry empty or wrong-typed defaults.
  const updateDefault = (
    name: string,
    value: string | number | boolean | undefined,
  ) => {
    const next = { ...defaultConfig };
    if (value === undefined) delete next[name];
    else next[name] = value;
    onUpdate({ default_config: next });
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
                    <IdRenameInput
                      value={name}
                      sanitize={sanitizeFieldName}
                      onCommit={(next) => renameField(name, next)}
                      style={{ fontFamily: "var(--font-mono)" }}
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
                        // One atomic write: the type switch, stripping
                        // type-incompatible attributes, and re-coercing the
                        // stored default into the new type together.
                        const changed = applyConfigFieldTypeChange(
                          field,
                          defaultConfig[name],
                          e.target.value,
                        );
                        const nextDefaults = { ...defaultConfig };
                        if (changed.defaultValue === undefined) {
                          delete nextDefaults[name];
                        } else {
                          nextDefaults[name] = changed.defaultValue;
                        }
                        onUpdate({
                          config_schema: { ...schema, [name]: changed.field },
                          default_config: nextDefaults,
                        });
                      }}
                      style={{ width: "100%" }}
                    >
                      <option value="string">String</option>
                      <option value="text">Text (multi-line)</option>
                      <option value="integer">Integer</option>
                      <option value="number">Number</option>
                      <option value="float">Float</option>
                      <option value="boolean">Boolean</option>
                      <option value="enum">Enum (dropdown)</option>
                    </select>
                  </div>
                  <div>
                    <label style={labelStyle}>Default Value</label>
                    {field.secret ? (
                      <input
                        value=""
                        disabled
                        placeholder="Secret fields can't have a default"
                        style={{ width: "100%" }}
                      />
                    ) : field.type === "boolean" ? (
                      <select
                        value={
                          defaultConfig[name] === undefined
                            ? ""
                            : String(defaultConfig[name])
                        }
                        onChange={(e) =>
                          updateDefault(
                            name,
                            coerceConfigDefault(field.type, e.target.value),
                          )
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">(none)</option>
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : field.type === "enum" ? (
                      <select
                        value={
                          defaultConfig[name] === undefined
                            ? ""
                            : String(defaultConfig[name])
                        }
                        onChange={(e) =>
                          updateDefault(
                            name,
                            coerceConfigDefault(field.type, e.target.value),
                          )
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">(none)</option>
                        {(field.values ?? []).map((v) => (
                          <option key={v} value={v}>{v}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={
                          NUMERIC_CONFIG_TYPES.has(field.type)
                            ? "number"
                            : "text"
                        }
                        value={
                          defaultConfig[name] === undefined
                            ? ""
                            : String(defaultConfig[name])
                        }
                        onChange={(e) =>
                          updateDefault(
                            name,
                            coerceConfigDefault(field.type, e.target.value),
                          )
                        }
                        style={{ width: "100%" }}
                      />
                    )}
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
                          // A secret must never carry a default value — marking
                          // secret purges the default entirely (including one
                          // imported from a hand-authored file).
                          onUpdate(
                            applyConfigSecretToggle(
                              schema,
                              defaultConfig,
                              name,
                              e.target.checked,
                            ),
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

      <ComputedFieldsEditor draft={draft} onUpdate={onUpdate} />
    </div>
  );
}

/**
 * Edits the optional `config_derived` map — computed config values, each a
 * template substituted from other config fields when the device connects
 * (e.g. ws: "/workspace/{workspace_id}"). If any {field} a template
 * references is empty or missing, the computed value is "" — so an optional
 * prefixed address segment simply disappears. Computed values are visible to
 * every command, on_connect entry, response, and poll query, just like a
 * real config field.
 */
function ComputedFieldsEditor({
  draft,
  onUpdate,
}: {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}) {
  const derived = draft.config_derived ?? {};
  const names = Object.keys(derived);

  // An empty map drops the key entirely so minimal YAML stays minimal.
  const writeDerived = (next: Record<string, string>) => {
    onUpdate({
      config_derived: Object.keys(next).length > 0 ? next : undefined,
    });
  };

  const addField = () => {
    let counter = names.length + 1;
    let name = `computed_${counter}`;
    while (name in derived) {
      counter++;
      name = `computed_${counter}`;
    }
    writeDerived({ ...derived, [name]: "" });
  };

  const removeField = (name: string) => {
    const next = { ...derived };
    delete next[name];
    writeDerived(next);
  };

  // Rename in place, preserving declaration order — templates may reference
  // earlier computed names, so order matters at runtime.
  const renameField = (oldName: string, newName: string): RenameResult => {
    const cleaned = sanitizeFieldName(newName);
    if (!cleaned) return { ok: false, reason: "ID can't be empty." };
    if (cleaned === oldName) return { ok: true };
    if (cleaned in derived) {
      return { ok: false, reason: `"${cleaned}" already exists.` };
    }
    const next: Record<string, string> = {};
    for (const [k, v] of Object.entries(derived)) {
      next[k === oldName ? cleaned : k] = v;
    }
    writeDerived(next);
    return { ok: true };
  };

  return (
    <div style={{ marginTop: "var(--space-xl)" }}>
      <h4
        style={{
          fontSize: "var(--font-size-sm)",
          fontWeight: 600,
          marginBottom: "var(--space-xs)",
        }}
      >
        Computed Fields
      </h4>
      <p
        style={{
          fontSize: "11px",
          color: "var(--text-muted)",
          marginBottom: "var(--space-sm)",
        }}
      >
        Optional values computed from other config fields when the device
        connects — each is a template like{" "}
        <code>{"/workspace/{workspace_id}"}</code>. If a referenced field is
        empty or missing, the computed value is <code>&quot;&quot;</code>, so
        an optional prefixed segment simply disappears. Use a computed field
        in commands, responses, and queries exactly like a config field.
      </p>

      {names.map((name) => (
        <div
          key={name}
          style={{
            display: "flex",
            gap: "var(--space-sm)",
            alignItems: "center",
            marginBottom: "var(--space-xs)",
          }}
        >
          <IdRenameInput
            value={name}
            sanitize={sanitizeFieldName}
            onCommit={(next) => renameField(name, next)}
            placeholder="name"
            style={{ width: 160, fontFamily: "var(--font-mono)" }}
          />
          <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>
            =
          </span>
          <input
            value={derived[name] ?? ""}
            onChange={(e) =>
              writeDerived({ ...derived, [name]: e.target.value })
            }
            placeholder={"e.g. /workspace/{workspace_id}"}
            style={{ flex: 1, fontFamily: "var(--font-mono)" }}
          />
          <button
            onClick={() => removeField(name)}
            title="Remove this computed field"
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}

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
          marginTop: "var(--space-xs)",
        }}
      >
        <Plus size={14} /> Add Computed Field
      </button>
    </div>
  );
}
