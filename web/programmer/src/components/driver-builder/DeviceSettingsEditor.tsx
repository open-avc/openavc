import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverDefinition, DriverDeviceSettingDef } from "../../api/types";

interface DeviceSettingsEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function DeviceSettingsEditor({ draft, onUpdate }: DeviceSettingsEditorProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const settings = (draft.device_settings ?? {}) as Record<string, DriverDeviceSettingDef>;
  const settingKeys = Object.keys(settings);

  const addSetting = () => {
    let counter = settingKeys.length + 1;
    let key = `setting_${counter}`;
    while (key in settings) {
      counter++;
      key = `setting_${counter}`;
    }
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

  const renameSetting = (oldKey: string, newKey: string) => {
    const cleaned = newKey.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();
    if (!cleaned || cleaned === oldKey || cleaned in settings) return;
    const next: Record<string, DriverDeviceSettingDef> = {};
    for (const [k, v] of Object.entries(settings)) {
      next[k === oldKey ? cleaned : k] = v;
    }
    onUpdate({ device_settings: next });
    if (expanded === oldKey) setExpanded(cleaned);
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
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
                    <input
                      value={key}
                      onChange={(e) => renameSetting(key, e.target.value)}
                      style={{
                        width: "100%",
                        fontFamily: "var(--font-mono)",
                      }}
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
                    <label style={labelStyle}>Values (comma-separated)</label>
                    <input
                      value={(setting.values ?? []).join(", ")}
                      onChange={(e) =>
                        updateSetting(key, {
                          values: e.target.value
                            .split(",")
                            .map((s) => s.trim())
                            .filter(Boolean),
                        })
                      }
                      placeholder="e.g., auto, manual, off"
                      style={{ width: "100%" }}
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

                <div style={{ marginBottom: "var(--space-md)" }}>
                  <label style={labelStyle}>
                    Write Command {isOsc ? "(OSC)" : isHttp ? "(HTTP)" : "(Protocol String)"}
                  </label>
                  {isOsc ? (
                    <div>
                      <input
                        value={setting.write?.address ?? ""}
                        onChange={(e) =>
                          updateSetting(key, {
                            write: { ...(setting.write ?? {}), address: e.target.value },
                          })
                        }
                        placeholder="/device/setting/name"
                        style={{
                          width: "100%",
                          fontFamily: "var(--font-mono)",
                          fontSize: "var(--font-size-sm)",
                        }}
                      />
                      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 2 }}>
                        OSC address to write the setting. The value is sent as a float argument.
                        Use {"{value}"} in custom args if needed.
                      </div>
                    </div>
                  ) : isHttp ? (
                    <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-sm)" }}>
                      <select
                        value={setting.write?.method ?? "POST"}
                        onChange={(e) =>
                          updateSetting(key, {
                            write: { ...(setting.write ?? {}), method: e.target.value },
                          })
                        }
                        style={{ width: 90 }}
                      >
                        <option value="POST">POST</option>
                        <option value="PUT">PUT</option>
                        <option value="GET">GET</option>
                      </select>
                      <input
                        value={setting.write?.path ?? ""}
                        onChange={(e) =>
                          updateSetting(key, {
                            write: { ...(setting.write ?? {}), path: e.target.value },
                          })
                        }
                        placeholder="/api/settings"
                        style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
                      />
                    </div>
                  ) : (
                    <input
                      value={setting.write?.send ?? ""}
                      onChange={(e) =>
                        updateSetting(key, {
                          write: { send: e.target.value },
                        })
                      }
                      placeholder={'e.g., SET {value}\\r'}
                      style={{
                        width: "100%",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                      }}
                    />
                  )}
                  <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 2 }}>
                    Command sent to write this setting to the device. Use {"{value}"} for the new value.
                  </div>
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
