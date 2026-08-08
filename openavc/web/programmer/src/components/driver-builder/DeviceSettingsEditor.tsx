import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight, AlertTriangle } from "lucide-react";
import type { DriverDefinition, DriverDeviceSettingDef } from "../../api/types";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";
import { EnumValuesEditor } from "../shared/EnumValuesEditor";
import { normalizeOptionList } from "../shared/paramOptions";
import {
  checkSettingRename,
  nextSettingKey,
  normalizeWriteForTransport,
  oscWriteOmitsValue,
  sanitizeSettingKey,
  writeHasForeignKeys,
  OSC_VALUELESS_TAGS,
} from "./deviceSettingsHelpers";

interface DeviceSettingsEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

const helpStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
  marginTop: 2,
};

type WriteDef = NonNullable<DriverDeviceSettingDef["write"]>;
type OscArg = { type: string; value: string };

const OSC_TAGS: { tag: string; label: string }[] = [
  { tag: "f", label: "float32 (f)" },
  { tag: "i", label: "int32 (i)" },
  { tag: "s", label: "string (s)" },
  { tag: "h", label: "int64 (h)" },
  { tag: "d", label: "float64 (d)" },
  { tag: "T", label: "true (T)" },
  { tag: "F", label: "false (F)" },
  { tag: "N", label: "nil (N)" },
];

export function DeviceSettingsEditor({ draft, onUpdate }: DeviceSettingsEditorProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const settings = (draft.device_settings ?? {}) as Record<string, DriverDeviceSettingDef>;
  const settingKeys = Object.keys(settings);

  const addSetting = () => {
    const key = nextSettingKey(settingKeys);
    onUpdate({
      device_settings: {
        ...settings,
        [key]: { label: "New Setting", type: "string", help: "" },
      },
    });
    setExpanded(key);
  };

  const removeSetting = (key: string) => {
    const next = { ...settings };
    delete next[key];
    onUpdate({ device_settings: next });
    if (expanded === key) setExpanded(null);
  };

  const updateSetting = (key: string, partial: Partial<DriverDeviceSettingDef>) => {
    onUpdate({
      device_settings: {
        ...settings,
        [key]: { ...settings[key], ...partial },
      },
    });
  };

  // Write edits go through here: normalize the existing write to the current
  // transport first (dropping stale OSC/HTTP/TCP fields a transport switch left
  // behind) then merge the change, so the runtime never dispatches on a foreign
  // protocol field.
  const setWrite = (key: string, partial: Partial<WriteDef>) => {
    const base = normalizeWriteForTransport(settings[key].write, draft.transport);
    updateSetting(key, { write: { ...base, ...partial } });
  };

  const renameSetting = (oldKey: string, newKey: string): RenameResult => {
    const cleaned = sanitizeSettingKey(newKey);
    const check = checkSettingRename(cleaned, oldKey, settingKeys);
    if (!check.ok || cleaned === oldKey) return check;
    const next: Record<string, DriverDeviceSettingDef> = {};
    for (const [k, v] of Object.entries(settings)) {
      next[k === oldKey ? cleaned : k] = v;
    }
    onUpdate({ device_settings: next });
    if (expanded === oldKey) setExpanded(cleaned);
    return { ok: true };
  };

  const isHttp = draft.transport === "http";
  const isOsc = draft.transport === "osc";

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Device settings are configurable values that live on the device hardware
        (not in your project). Examples: display name, network settings, NDI channel name.
        Unlike connection config, these are written to the device over the protocol.
      </p>

      {settingKeys.map((key) => {
        const setting = settings[key];
        if (!setting) return null;
        const isOpen = expanded === key;
        return (
          <div
            key={key}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-sm)",
              background: "var(--bg-surface)",
            }}
          >
            <button
              onClick={() => setExpanded(isOpen ? null : key)}
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
                {key}
              </span>
              <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>
                {setting.label}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeSetting(key);
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
                    <label style={labelStyle}>Setting Key</label>
                    <IdRenameInput
                      value={key}
                      sanitize={sanitizeSettingKey}
                      onCommit={(next) => renameSetting(key, next)}
                      style={{ fontFamily: "var(--font-mono)" }}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Display Label</label>
                    <input
                      value={setting.label}
                      onChange={(e) =>
                        updateSetting(key, { label: e.target.value })
                      }
                      style={{ width: "100%" }}
                    />
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "var(--space-md)",
                    marginBottom: "var(--space-md)",
                  }}
                >
                  <div>
                    <label style={labelStyle}>Type</label>
                    <select
                      value={setting.type}
                      onChange={(e) =>
                        updateSetting(key, { type: e.target.value })
                      }
                      style={{ width: "100%" }}
                    >
                      <option value="string">String</option>
                      <option value="integer">Integer</option>
                      <option value="number">Number</option>
                      <option value="float">Float</option>
                      <option value="boolean">Boolean</option>
                      <option value="enum">Enum</option>
                    </select>
                  </div>
                  <div>
                    <label style={labelStyle}>State Key (optional)</label>
                    <input
                      value={setting.state_key ?? ""}
                      onChange={(e) =>
                        updateSetting(key, { state_key: e.target.value || undefined })
                      }
                      placeholder={key}
                      style={{
                        width: "100%",
                        fontFamily: "var(--font-mono)",
                      }}
                    />
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 2 }}>
                      State variable to store this value. Defaults to the setting key.
                    </div>
                  </div>
                </div>

                {setting.type === "enum" && (
                  <div style={{ marginBottom: "var(--space-md)" }}>
                    <label style={labelStyle}>Values</label>
                    <EnumValuesEditor
                      values={setting.values}
                      onChange={(values) => updateSetting(key, { values })}
                    />
                  </div>
                )}

                <div style={{ marginBottom: "var(--space-md)" }}>
                  <label style={labelStyle}>Help Text</label>
                  <input
                    value={setting.help ?? ""}
                    onChange={(e) =>
                      updateSetting(key, { help: e.target.value })
                    }
                    placeholder="Description shown to users"
                    style={{ width: "100%" }}
                  />
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "var(--space-md)",
                    marginBottom: "var(--space-md)",
                  }}
                >
                  <div>
                    <label style={labelStyle}>Default value (optional)</label>
                    {setting.type === "boolean" ? (
                      <select
                        value={setting.default === undefined ? "" : String(setting.default)}
                        onChange={(e) =>
                          updateSetting(key, {
                            default: e.target.value === "" ? undefined : e.target.value === "true",
                          })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">(none)</option>
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : setting.type === "enum" ? (
                      <select
                        value={setting.default === undefined ? "" : String(setting.default)}
                        onChange={(e) =>
                          updateSetting(key, { default: e.target.value || undefined })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">(none)</option>
                        {normalizeOptionList(setting.values ?? []).map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                    ) : setting.type === "integer" || setting.type === "number" || setting.type === "float" ? (
                      <input
                        type="number"
                        value={setting.default === undefined ? "" : String(setting.default)}
                        onChange={(e) =>
                          updateSetting(key, {
                            default:
                              e.target.value === ""
                                ? undefined
                                : setting.type === "integer"
                                  ? parseInt(e.target.value, 10)
                                  : parseFloat(e.target.value),
                          })
                        }
                        style={{ width: "100%" }}
                      />
                    ) : (
                      <input
                        value={setting.default === undefined ? "" : String(setting.default)}
                        onChange={(e) =>
                          updateSetting(key, { default: e.target.value || undefined })
                        }
                        style={{ width: "100%" }}
                      />
                    )}
                    <div style={helpStyle}>
                      Pre-fills the prompt when this setting is set up on a device.
                    </div>
                  </div>

                  {setting.type === "integer" || setting.type === "number" || setting.type === "float" ? (
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: "var(--space-sm)",
                      }}
                    >
                      <div>
                        <label style={labelStyle}>Min (optional)</label>
                        <input
                          type="number"
                          value={setting.min === undefined ? "" : String(setting.min)}
                          onChange={(e) =>
                            updateSetting(key, {
                              min: e.target.value === "" ? undefined : parseFloat(e.target.value),
                            })
                          }
                          style={{ width: "100%" }}
                        />
                      </div>
                      <div>
                        <label style={labelStyle}>Max (optional)</label>
                        <input
                          type="number"
                          value={setting.max === undefined ? "" : String(setting.max)}
                          onChange={(e) =>
                            updateSetting(key, {
                              max: e.target.value === "" ? undefined : parseFloat(e.target.value),
                            })
                          }
                          style={{ width: "100%" }}
                        />
                      </div>
                    </div>
                  ) : setting.type === "string" ? (
                    <div>
                      <label style={labelStyle}>Validation pattern (optional)</label>
                      <input
                        value={setting.regex ?? ""}
                        onChange={(e) =>
                          updateSetting(key, { regex: e.target.value || undefined })
                        }
                        placeholder="regex, e.g. ^[0-9.]+$"
                        style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                      />
                      <div style={helpStyle}>
                        Setup input must match this pattern.
                      </div>
                    </div>
                  ) : (
                    <div />
                  )}
                </div>

                <div style={{ marginBottom: "var(--space-md)" }}>
                  <label style={labelStyle}>
                    Write Command {isOsc ? "(OSC)" : isHttp ? "(HTTP)" : "(Protocol String)"}
                  </label>
                  {writeHasForeignKeys(setting.write, draft.transport) && (
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "var(--space-xs)",
                        fontSize: 11,
                        color: "var(--color-warning, #b8860b)",
                        marginBottom: "var(--space-xs)",
                      }}
                    >
                      <AlertTriangle size={12} />
                      This write has fields from a different transport — they'll be
                      cleared when you edit it.
                    </div>
                  )}
                  {isOsc ? (
                    <div>
                      <input
                        value={setting.write?.address ?? ""}
                        onChange={(e) => setWrite(key, { address: e.target.value })}
                        placeholder="/device/setting/name"
                        style={{
                          width: "100%",
                          fontFamily: "var(--font-mono)",
                          fontSize: "var(--font-size-sm)",
                        }}
                      />
                      <OscArgsEditor
                        args={(setting.write?.args ?? []) as OscArg[]}
                        onChange={(args) => setWrite(key, { args: args.length ? args : undefined })}
                      />
                      {oscWriteOmitsValue(setting.write) && (
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "var(--space-xs)",
                            fontSize: 11,
                            color: "var(--color-warning, #b8860b)",
                            marginTop: 4,
                          }}
                        >
                          <AlertTriangle size={12} />
                          This write sends no value — add an argument referencing
                          {" {value}"} (or{" "}
                          <button
                            type="button"
                            onClick={() =>
                              setWrite(key, {
                                args: [
                                  ...((setting.write?.args ?? []) as OscArg[]),
                                  { type: "f", value: "{value}" },
                                ],
                              })
                            }
                            style={{ textDecoration: "underline", color: "var(--accent)" }}
                          >
                            send the value as a float
                          </button>
                          ).
                        </div>
                      )}
                      <div style={helpStyle}>
                        OSC address to write the setting, plus the typed arguments
                        sent with it. Use {"{value}"} for the new value.
                      </div>
                    </div>
                  ) : isHttp ? (
                    <div>
                      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-sm)" }}>
                        <select
                          value={setting.write?.method ?? "POST"}
                          onChange={(e) => setWrite(key, { method: e.target.value })}
                          style={{ width: 90 }}
                        >
                          <option value="POST">POST</option>
                          <option value="PUT">PUT</option>
                          <option value="GET">GET</option>
                        </select>
                        <input
                          value={setting.write?.path ?? ""}
                          onChange={(e) => setWrite(key, { path: e.target.value })}
                          placeholder="/api/settings"
                          style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
                        />
                      </div>
                      <label style={{ ...labelStyle, marginTop: "var(--space-sm)" }}>
                        Request body (optional)
                      </label>
                      <textarea
                        value={setting.write?.body ?? ""}
                        onChange={(e) => setWrite(key, { body: e.target.value || undefined })}
                        placeholder={'JSON, e.g. {"name": "{value}"}'}
                        rows={2}
                        style={{
                          width: "100%",
                          fontFamily: "var(--font-mono)",
                          fontSize: "var(--font-size-sm)",
                          resize: "vertical",
                        }}
                      />
                      <label style={{ ...labelStyle, marginTop: "var(--space-sm)" }}>
                        Headers (optional)
                      </label>
                      <HttpHeadersEditor
                        headers={setting.write?.headers ?? {}}
                        onChange={(headers) =>
                          setWrite(key, {
                            headers: Object.keys(headers).length ? headers : undefined,
                          })
                        }
                      />
                      <div style={helpStyle}>
                        Body and headers support {"{value}"} and config
                        placeholders. Set Content-Type here for non-JSON bodies.
                      </div>
                    </div>
                  ) : (
                    <>
                      <input
                        value={setting.write?.send ?? ""}
                        onChange={(e) => setWrite(key, { send: e.target.value })}
                        placeholder={'e.g., SET {value}\\r'}
                        style={{
                          width: "100%",
                          fontFamily: "var(--font-mono)",
                          fontSize: "var(--font-size-sm)",
                        }}
                      />
                      <div style={helpStyle}>
                        Command sent to write this setting. Use {"{value}"} for the new value.
                      </div>
                    </>
                  )}
                </div>

                <div style={{ display: "flex", gap: "var(--space-lg)" }}>
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
                      checked={setting.setup ?? false}
                      onChange={(e) =>
                        updateSetting(key, { setup: e.target.checked || undefined })
                      }
                    />
                    Prompt during setup
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
                      checked={setting.unique ?? false}
                      onChange={(e) =>
                        updateSetting(key, { unique: e.target.checked || undefined })
                      }
                    />
                    Unique per device
                  </label>
                </div>
              </div>
            )}
          </div>
        );
      })}

      <button
        onClick={addSetting}
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
        <Plus size={14} /> Add Device Setting
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// OSC args sub-editor — typed arguments sent with the OSC write. Without at
// least one arg referencing {value}, the OSC message carries no value.
// ──────────────────────────────────────────────────────────────────────────
function OscArgsEditor({
  args,
  onChange,
}: {
  args: OscArg[];
  onChange: (args: OscArg[]) => void;
}) {
  const update = (i: number, partial: Partial<OscArg>) =>
    onChange(args.map((a, j) => (j === i ? { ...a, ...partial } : a)));
  const remove = (i: number) => onChange(args.filter((_, j) => j !== i));
  const add = () => onChange([...args, { type: "f", value: "{value}" }]);

  return (
    <div style={{ marginTop: "var(--space-xs)" }}>
      {args.map((arg, i) => {
        const valueless = OSC_VALUELESS_TAGS.has(arg.type);
        return (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: "130px 1fr auto",
              gap: "var(--space-xs)",
              alignItems: "center",
              marginBottom: "var(--space-xs)",
            }}
          >
            <select
              value={arg.type}
              onChange={(e) => update(i, { type: e.target.value })}
              style={{ fontSize: "var(--font-size-sm)" }}
            >
              {OSC_TAGS.map((t) => (
                <option key={t.tag} value={t.tag}>{t.label}</option>
              ))}
            </select>
            <input
              value={valueless ? "" : arg.value}
              disabled={valueless}
              onChange={(e) => update(i, { value: e.target.value })}
              placeholder={valueless ? "(no value)" : "{value} or a literal"}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
              }}
            />
            <button
              type="button"
              onClick={() => remove(i)}
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={add}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "11px",
          color: "var(--accent)",
        }}
      >
        <Plus size={12} /> Add OSC argument
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// HTTP headers sub-editor — key/value pairs sent with the HTTP write.
// ──────────────────────────────────────────────────────────────────────────
function HttpHeadersEditor({
  headers,
  onChange,
}: {
  headers: Record<string, string>;
  onChange: (headers: Record<string, string>) => void;
}) {
  const rows = Object.entries(headers);

  const setRow = (index: number, name: string, value: string) => {
    const next: Record<string, string> = {};
    rows.forEach(([k, v], i) => {
      if (i === index) {
        if (name) next[name] = value;
      } else {
        next[k] = v;
      }
    });
    onChange(next);
  };
  const removeRow = (index: number) =>
    onChange(Object.fromEntries(rows.filter((_, i) => i !== index)));
  const add = () => onChange({ ...headers, "": "" });

  return (
    <div style={{ marginTop: "var(--space-xs)" }}>
      {rows.map(([name, value], i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr auto",
            gap: "var(--space-xs)",
            alignItems: "center",
            marginBottom: "var(--space-xs)",
          }}
        >
          <input
            value={name}
            onChange={(e) => setRow(i, e.target.value, value)}
            placeholder="Header"
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <input
            value={value}
            onChange={(e) => setRow(i, name, e.target.value)}
            placeholder="Value"
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <button
            type="button"
            onClick={() => removeRow(i)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "11px",
          color: "var(--accent)",
        }}
      >
        <Plus size={12} /> Add header
      </button>
    </div>
  );
}
