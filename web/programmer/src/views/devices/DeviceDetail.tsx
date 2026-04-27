import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Send, Pencil, Trash2, Wifi, Power, RefreshCw, Copy, Settings, Check, X, Loader2 } from "lucide-react";
import { CopyButton } from "../../components/shared/CopyButton";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { useLogStore } from "../../store/logStore";
import * as api from "../../api/restClient";
import type { DeviceConfig, DeviceInfo, DeviceSettingValue } from "../../api/types";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { findDeviceReferences } from "./deviceUtils";

export function DeviceDetail({
  deviceId,
  onEdit,
  onDeleted,
  onDuplicate,
  onBrowseDrivers,
}: {
  deviceId: string;
  onEdit: (config: DeviceConfig) => void;
  onDeleted: (deviceId: string) => void;
  onDuplicate: (config: DeviceConfig) => void;
  onBrowseDrivers?: () => void;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const liveState = useConnectionStore((s) => s.liveState);
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [commandResult, setCommandResult] = useState<string | null>(null);
  const [selectedCommand, setSelectedCommand] = useState("");
  const [commandParams, setCommandParams] = useState<Record<string, string>>({});
  const [sending, setSending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    error: string | null;
    latency_ms: number | null;
    protocol_status?: string | null;
  } | null>(null);
  const [testing, setTesting] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => {
    api.getDevice(deviceId).then(setDeviceInfo).catch(console.error);
  }, [deviceId]);

  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const isEnabled = deviceConfig?.enabled !== false;

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await api.deleteDevice(deviceId);
      onDeleted(deviceId);
    } catch (e) {
      console.error(e);
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [deviceId, onDeleted]);

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testDeviceConnection(deviceId);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, error: String(e), latency_ms: null });
    } finally {
      setTesting(false);
    }
  };

  const handleToggleEnabled = async () => {
    if (!project || !deviceConfig) return;
    const updatedDevices = project.devices.map((d) =>
      d.id === deviceId ? { ...d, enabled: !isEnabled } : d
    );
    update({ devices: updatedDevices });
    useProjectStore.getState().debouncedSave();
  };

  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      await api.reconnectDevice(deviceId);
    } catch (e) {
      console.error(e);
    } finally {
      setTimeout(() => setReconnecting(false), 2000);
    }
  };

  // Extract device state from flat liveState
  const prefix = `device.${deviceId}.`;
  const stateEntries: [string, string][] = [];
  for (const [key, value] of Object.entries(liveState)) {
    if (key.startsWith(prefix)) {
      stateEntries.push([key.slice(prefix.length), String(value ?? "")]);
    }
  }

  const deviceName = String(liveState[`device.${deviceId}.name`] ?? deviceId);
  const connected = Boolean(liveState[`device.${deviceId}.connected`]);

  const commands = deviceInfo?.commands ?? {};
  const commandNames = Object.keys(commands);

  const handleSendCommand = useCallback(async () => {
    if (!selectedCommand) return;
    setSending(true);
    setCommandResult(null);
    try {
      const params: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(commandParams)) {
        if (v === "") continue;
        params[k] = v;
      }
      const result = await api.sendCommand(deviceId, selectedCommand, params);
      setCommandResult(JSON.stringify(result, null, 2));
    } catch (e) {
      setCommandResult(String(e));
    } finally {
      setSending(false);
    }
  }, [deviceId, selectedCommand, commandParams]);

  // Get param fields for selected command
  const commandDef = commands[selectedCommand] as Record<string, unknown> | undefined;
  const paramKeys = Object.keys((commandDef?.params as Record<string, unknown>) ?? {});

  const sectionStyle: React.CSSProperties = {
    marginBottom: "var(--space-xl)",
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "var(--space-md)",
    fontWeight: 600,
  };

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "var(--space-xl)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-md)",
            flexWrap: "wrap",
          }}
        >
          <DeviceStatusDot connected={connected} orphaned={Boolean(liveState[`device.${deviceId}.orphaned`])} size={12} />
          <h2 style={{ fontSize: "var(--font-size-xl)", flex: 1 }}>{deviceName}</h2>
          <button
            onClick={handleToggleEnabled}
            title={isEnabled ? "Disable device" : "Enable device"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: isEnabled ? "rgba(76,175,80,0.15)" : "var(--bg-hover)",
              color: isEnabled ? "var(--color-success)" : "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Power size={14} /> {isEnabled ? "Enabled" : "Disabled"}
          </button>
          <button
            onClick={handleTestConnection}
            disabled={testing}
            title="Test device connection"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
              opacity: testing ? 0.6 : 1,
            }}
          >
            <Wifi size={14} /> {testing ? "Testing..." : "Test"}
          </button>
          {!connected && isEnabled && (
            <button
              onClick={handleReconnect}
              disabled={reconnecting}
              title="Force reconnect"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                color: "var(--accent)",
                fontSize: "var(--font-size-sm)",
                opacity: reconnecting ? 0.6 : 1,
              }}
            >
              <RefreshCw size={14} /> {reconnecting ? "Reconnecting..." : "Reconnect"}
            </button>
          )}
          <button
            onClick={() => deviceConfig && onEdit(deviceConfig)}
            title="Edit device settings"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Pencil size={14} /> Edit
          </button>
          <button
            onClick={() => deviceConfig && onDuplicate(deviceConfig)}
            title="Duplicate device"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Copy size={14} /> Duplicate
          </button>
          {confirmDelete ? (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>
                  Delete this device?
                </span>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--color-error)",
                    color: "#fff",
                    fontSize: "var(--font-size-sm)",
                    opacity: deleting ? 0.6 : 1,
                  }}
                >
                  {deleting ? "Deleting..." : "Yes, Delete"}
                </button>
                <button
                  onClick={() => setConfirmDelete(false)}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                  }}
                >
                  Cancel
                </button>
              </div>
              {project && (() => {
                const refs = findDeviceReferences(project, deviceId);
                if (refs.length === 0) return null;
                return (
                  <div style={{ marginTop: "var(--space-xs)", padding: "var(--space-sm)", background: "rgba(244,67,54,0.08)", borderRadius: "var(--border-radius)", fontSize: 12, color: "var(--text-secondary)" }}>
                    <strong>Warning:</strong> This device is referenced in {refs.length} place(s):
                    <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                      {refs.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}
                      {refs.length > 5 && <li>...and {refs.length - 5} more</li>}
                    </ul>
                  </div>
                );
              })()}
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              title="Delete device"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                color: "var(--color-error)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <Trash2 size={14} /> Delete
            </button>
          )}
        </div>
        <div style={{ marginLeft: 22, display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            {deviceInfo?.driver ?? ""}
          </span>
          <span style={{ color: "var(--border-color)" }}>&middot;</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {deviceId}
            </code>
            <CopyButton value={deviceId} title="Copy device ID" />
          </span>
        </div>
      </div>

      {/* Orphaned device banner */}
      {Boolean(liveState[`device.${deviceId}.orphaned`]) && (
        <div
          style={{
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            background: "rgba(239, 68, 68, 0.1)",
            border: "2px solid rgba(239, 68, 68, 0.4)",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: "var(--space-sm)", color: "#ef4444", fontSize: "var(--font-size-md)" }}>
            Driver Not Installed
          </div>
          <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
            This device needs the driver "{deviceConfig?.driver}" which is not installed.
            Install the driver from the community repository or reassign to a different driver.
          </div>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <button
              onClick={() => onBrowseDrivers?.()}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--color-warning, #f59e0b)",
                color: "#000",
                fontSize: "var(--font-size-sm)",
                fontWeight: 500,
              }}
            >
              Install from Community
            </button>
            <button
              onClick={() => deviceConfig && onEdit(deviceConfig)}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              Reassign Driver
            </button>
          </div>
        </div>
      )}

      {/* Test connection result */}
      {testResult && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            background: testResult.success
              ? "rgba(76,175,80,0.15)"
              : "var(--color-error-bg)",
            color: testResult.success ? "var(--color-success)" : "var(--color-error)",
          }}
        >
          {testResult.success
            ? `Connected successfully (${testResult.latency_ms}ms)${
                testResult.protocol_status === "verified" ? " — protocol verified"
                : testResult.protocol_status === "not_verified" ? " — protocol not verified"
                : ""
              }`
            : `Connection failed: ${testResult.error}`}
        </div>
      )}

      {/* Live State */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Live State</h3>
        <div
          style={{
            background: "var(--bg-surface)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            overflow: "hidden",
          }}
        >
          {stateEntries.length === 0 ? (
            <div
              style={{
                padding: "var(--space-lg)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              No state values yet
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {stateEntries.map(([key, value]) => (
                  <tr
                    key={key}
                    style={{ borderBottom: "1px solid var(--border-color)" }}
                  >
                    <td
                      style={{
                        padding: "var(--space-sm) var(--space-md)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        color: "var(--text-secondary)",
                        width: "40%",
                      }}
                    >
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                        {key}
                        <CopyButton value={`device.${deviceId}.${key}`} size={11} title="Copy full state key" />
                      </span>
                    </td>
                    <td
                      style={{
                        padding: "var(--space-sm) var(--space-md)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                      }}
                    >
                      {value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Device Settings */}
      <DeviceSettingsSection deviceId={deviceId} connected={connected} />

      {/* Command Testing */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Command Testing</h3>
        <div
          style={{
            background: "var(--bg-surface)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            padding: "var(--space-lg)",
          }}
        >
          {commandNames.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              {!connected
                ? "Device is not connected. Commands will be available once the device connects."
                : "No commands available. The driver may not be loaded or may not define any commands."}
            </div>
          ) : (
            <>
              <div style={{ marginBottom: "var(--space-md)" }}>
                <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <select
                  value={selectedCommand}
                  onChange={(e) => {
                    setSelectedCommand(e.target.value);
                    setCommandParams({});
                    setCommandResult(null);
                  }}
                  style={{ flex: 1 }}
                >
                  <option value="">Select a command...</option>
                  {commandNames.map((cmd) => (
                    <option key={cmd} value={cmd}>
                      {cmd}
                    </option>
                  ))}
                </select>
                <button
                  onClick={handleSendCommand}
                  disabled={!selectedCommand || sending}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-xs)",
                    padding: "var(--space-sm) var(--space-lg)",
                    borderRadius: "var(--border-radius)",
                    background: selectedCommand ? "var(--accent-bg)" : "var(--bg-hover)",
                    color: selectedCommand ? "var(--text-on-accent)" : "var(--text-muted)",
                    opacity: sending ? 0.6 : 1,
                  }}
                >
                  <Send size={14} /> Send
                </button>
                </div>
                {selectedCommand && (() => {
                  const cmdDef = commands[selectedCommand] as Record<string, unknown> | undefined;
                  const cmdHelp = cmdDef?.help as string | undefined;
                  return cmdHelp ? (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                      {cmdHelp}
                    </div>
                  ) : null;
                })()}
              </div>

              {/* Param fields */}
              {paramKeys.length > 0 && (
                <div style={{ marginBottom: "var(--space-md)" }}>
                  {paramKeys.map((paramName) => {
                    const pDef = (commands[selectedCommand] as Record<string, unknown>)?.params as Record<string, Record<string, unknown>> | undefined;
                    const paramHelp = pDef?.[paramName]?.help as string | undefined;
                    return (
                    <div
                      key={paramName}
                      style={{
                        marginBottom: "var(--space-sm)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      <label
                        style={{
                          width: 120,
                          fontSize: "var(--font-size-sm)",
                          color: "var(--text-secondary)",
                        }}
                      >
                        {paramName}
                      </label>
                      <input
                        value={commandParams[paramName] ?? ""}
                        onChange={(e) =>
                          setCommandParams((p) => ({
                            ...p,
                            [paramName]: e.target.value,
                          }))
                        }
                        placeholder={paramName}
                        style={{ flex: 1 }}
                      />
                      </div>
                      {paramHelp && (
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2, marginLeft: 120 }}>
                          {paramHelp}
                        </div>
                      )}
                    </div>
                    );
                  })}
                </div>
              )}

              {/* Result */}
              {commandResult !== null && (
                <pre
                  style={{
                    background: "var(--bg-base)",
                    padding: "var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    fontSize: "var(--font-size-sm)",
                    fontFamily: "var(--font-mono)",
                    overflow: "auto",
                    maxHeight: 200,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {commandResult}
                </pre>
              )}
            </>
          )}
        </div>
      </div>

      {/* Plugin Device Panels */}
      <DevicePanelSlot
        deviceId={deviceId}
        driverId={deviceConfig?.driver ?? ""}
      />

      {/* Plugin Context Actions */}
      <ContextActionRenderer context="device" deviceId={deviceId} driverId={deviceConfig?.driver} />

      {/* Device Log */}
      <DeviceLog deviceId={deviceId} />
    </div>
  );
}

// --- Device Settings Section ---

function DeviceSettingsSection({ deviceId, connected }: { deviceId: string; connected: boolean }) {
  const project = useProjectStore((s) => s.project);
  const pendingSettings = useMemo(() => {
    const dev = project?.devices.find((d) => d.id === deviceId);
    return dev?.pending_settings ?? {};
  }, [project, deviceId]);

  const [settings, setSettings] = useState<Record<string, DeviceSettingValue>>({});
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [saveResult, setSaveResult] = useState<{ key: string; success: boolean; error?: string } | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadSettings = useCallback(() => {
    api.getDeviceSettings(deviceId).then((data) => {
      setSettings(data.settings);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, [deviceId]);

  useEffect(() => {
    loadSettings();
    const interval = setInterval(loadSettings, 5000);
    return () => clearInterval(interval);
  }, [loadSettings]);

  const settingKeys = Object.keys(settings);
  if (!loaded || settingKeys.length === 0) return null;

  const handleStartEdit = (key: string) => {
    const current = settings[key]?.current_value;
    setEditingKey(key);
    setEditValue(current != null ? String(current) : String(settings[key]?.default ?? ""));
    setSaveResult(null);
  };

  const handleSave = async (key: string) => {
    setSaving(key);
    setSaveResult(null);
    try {
      const def = settings[key];
      const fieldType = String(def?.type ?? "string");
      let coerced: unknown = editValue;
      if (fieldType === "integer") coerced = parseInt(editValue, 10) || 0;
      else if (fieldType === "number") coerced = parseFloat(editValue) || 0;
      else if (fieldType === "boolean") coerced = editValue === "true";

      await api.setDeviceSetting(deviceId, key, coerced);
      setSaveResult({ key, success: true });
      setEditingKey(null);
      // Refresh settings to get updated current_value
      setTimeout(loadSettings, 1000);
    } catch (e) {
      setSaveResult({ key, success: false, error: String(e) });
    } finally {
      setSaving(null);
    }
  };

  const handleCancel = () => {
    setEditingKey(null);
    setSaveResult(null);
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "var(--space-md)",
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    gap: "var(--space-sm)",
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3 style={sectionTitleStyle}>
        <Settings size={14} /> Device Settings
      </h3>
      <div
        style={{
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          overflow: "hidden",
        }}
      >
        {settingKeys.map((key) => {
          const def = settings[key];
          const label = String(def?.label ?? key);
          const help = String(def?.help ?? "");
          const fieldType = String(def?.type ?? "string");
          const values = def?.values as string[] | undefined;
          const currentValue = def?.current_value;
          const isPending = key in pendingSettings;
          const isEditing = editingKey === key;
          const isSaving = saving === key;
          const result = saveResult?.key === key ? saveResult : null;

          return (
            <div
              key={key}
              style={{
                padding: "var(--space-md)",
                borderBottom: "1px solid var(--border-color)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>{label}</div>
                  {help && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{help}</div>
                  )}
                </div>
                {isEditing ? (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                    {fieldType === "boolean" ? (
                      <select
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        style={{ fontSize: "var(--font-size-sm)", padding: "2px 6px" }}
                      >
                        <option value="true">Yes</option>
                        <option value="false">No</option>
                      </select>
                    ) : fieldType === "enum" && values ? (
                      <select
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        style={{ fontSize: "var(--font-size-sm)", padding: "2px 6px" }}
                      >
                        {values.map((v) => (
                          <option key={v} value={v}>{v}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        type={fieldType === "integer" || fieldType === "number" ? "number" : "text"}
                        style={{
                          fontSize: "var(--font-size-sm)",
                          padding: "2px 6px",
                          width: 180,
                        }}
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleSave(key);
                          if (e.key === "Escape") handleCancel();
                        }}
                      />
                    )}
                    <button
                      onClick={() => handleSave(key)}
                      disabled={isSaving}
                      title="Save"
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--color-success-bg)",
                        color: "var(--color-success)",
                        fontSize: "var(--font-size-sm)",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      {isSaving ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Check size={14} />}
                    </button>
                    <button
                      onClick={handleCancel}
                      title="Cancel"
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--bg-hover)",
                        fontSize: "var(--font-size-sm)",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ) : (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        color: currentValue != null ? "var(--text-primary)" : "var(--text-muted)",
                      }}
                    >
                      {currentValue != null ? String(currentValue) : "(not set)"}
                    </span>
                    {isPending && (
                      <span
                        style={{
                          fontSize: 10,
                          color: "var(--accent)",
                          padding: "1px 6px",
                          borderRadius: "var(--border-radius)",
                          background: "var(--accent-dim)",
                        }}
                        title={`Pending: ${String(pendingSettings[key])} — will be applied when device connects`}
                      >
                        pending
                      </span>
                    )}
                    <button
                      onClick={() => handleStartEdit(key)}
                      disabled={!connected}
                      title={connected ? "Edit setting" : "Device must be connected to change settings"}
                      style={{
                        padding: "2px 8px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--bg-hover)",
                        fontSize: "var(--font-size-sm)",
                        opacity: connected ? 1 : 0.4,
                      }}
                    >
                      <Pencil size={12} />
                    </button>
                  </div>
                )}
              </div>
              {result && (
                <div
                  style={{
                    marginTop: "var(--space-xs)",
                    fontSize: 11,
                    color: result.success ? "var(--color-success)" : "var(--color-error)",
                  }}
                >
                  {result.success ? "Setting saved successfully" : `Error: ${result.error}`}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- Device Log ---

type DeviceLogTab = "protocol" | "state";

function DeviceLog({ deviceId }: { deviceId: string }) {
  const [tab, setTab] = useState<DeviceLogTab>("protocol");

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <h3
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
            margin: 0,
          }}
        >
          Device Log
        </h3>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setTab("protocol")}
          style={{
            padding: "2px 8px",
            borderRadius: "var(--border-radius)",
            background: tab === "protocol" ? "var(--accent-bg)" : "var(--bg-hover)",
            color: tab === "protocol" ? "#fff" : "var(--text-secondary)",
            fontSize: 11,
            fontWeight: tab === "protocol" ? 600 : 400,
            border: "none",
            cursor: "pointer",
          }}
        >
          Protocol
        </button>
        <button
          onClick={() => setTab("state")}
          style={{
            padding: "2px 8px",
            borderRadius: "var(--border-radius)",
            background: tab === "state" ? "var(--accent-bg)" : "var(--bg-hover)",
            color: tab === "state" ? "#fff" : "var(--text-secondary)",
            fontSize: 11,
            fontWeight: tab === "state" ? 600 : 400,
            border: "none",
            cursor: "pointer",
          }}
        >
          State Changes
        </button>
      </div>
      {tab === "protocol" ? (
        <DeviceProtocolLog deviceId={deviceId} />
      ) : (
        <DeviceStateLog deviceId={deviceId} />
      )}
    </div>
  );
}

function DeviceProtocolLog({ deviceId }: { deviceId: string }) {
  const logEntries = useLogStore((s) => s.logEntries);
  const listRef = useRef<HTMLDivElement>(null);

  const deviceLogs = logEntries.filter(
    (e) => e.message.toLowerCase().includes(deviceId.toLowerCase())
      || (e.category === "device" && e.source?.toLowerCase().includes(deviceId.toLowerCase()))
  );
  const recent = deviceLogs.slice(-50);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [recent.length]);

  const LEVEL_COLORS: Record<string, string> = {
    DEBUG: "var(--text-muted)",
    INFO: "var(--accent)",
    WARNING: "#f59e0b",
    ERROR: "#ef4444",
  };

  return (
    <div
      ref={listRef}
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        overflow: "auto",
        maxHeight: 250,
        fontFamily: "var(--font-mono)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {recent.length === 0 ? (
        <div
          style={{
            padding: "var(--space-lg)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            textAlign: "center",
            fontFamily: "var(--font-primary, inherit)",
          }}
        >
          No log entries for this device yet.
        </div>
      ) : (
        recent.map((e, i) => {
          const time = new Date(e.timestamp * 1000);
          return (
            <div
              key={i}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                display: "flex",
                gap: "var(--space-sm)",
                alignItems: "baseline",
              }}
            >
              <span style={{ color: "var(--text-muted)", fontSize: 11, flexShrink: 0 }}>
                {time.toLocaleTimeString(undefined, { hour12: false })}
              </span>
              <span
                style={{
                  color: LEVEL_COLORS[e.level] ?? "var(--text-primary)",
                  fontWeight: e.level === "ERROR" ? 600 : 400,
                  fontSize: 11,
                  flexShrink: 0,
                  width: 40,
                }}
              >
                {e.level}
              </span>
              <span style={{ color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {String(e.message)}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}

function DeviceStateLog({ deviceId }: { deviceId: string }) {
  const liveState = useConnectionStore((s) => s.liveState);
  const prevStateRef = useRef<Record<string, unknown>>({});
  const [entries, setEntries] = useState<
    { key: string; oldValue: unknown; newValue: unknown; timestamp: number }[]
  >([]);
  const listRef = useRef<HTMLDivElement>(null);

  const devicePrefix = `device.${deviceId}.`;

  // Track live state changes for this device
  useEffect(() => {
    const prev = prevStateRef.current;
    const newEntries: typeof entries = [];
    for (const [key, value] of Object.entries(liveState)) {
      if (!key.startsWith(devicePrefix)) continue;
      if (prev[key] !== value && prev[key] !== undefined) {
        newEntries.push({
          key: key.slice(devicePrefix.length),
          oldValue: prev[key],
          newValue: value,
          timestamp: Date.now() / 1000,
        });
      }
    }
    prevStateRef.current = { ...liveState };
    if (newEntries.length > 0) {
      setEntries((prev) => [...prev, ...newEntries].slice(-100));
    }
  }, [liveState, devicePrefix]);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [entries.length]);

  const formatValue = (v: unknown) => {
    if (v === null || v === undefined) return "null";
    return String(v);
  };

  return (
    <div
      ref={listRef}
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        overflow: "auto",
        maxHeight: 250,
        fontFamily: "var(--font-mono)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {entries.length === 0 ? (
        <div
          style={{
            padding: "var(--space-lg)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            textAlign: "center",
            fontFamily: "var(--font-primary, inherit)",
          }}
        >
          No state changes yet. Interact with the device to see live updates.
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-color)", position: "sticky", top: 0, background: "var(--bg-surface)" }}>
              <th style={devLogThStyle}>Time</th>
              <th style={devLogThStyle}>Property</th>
              <th style={devLogThStyle}>Old</th>
              <th style={devLogThStyle}>New</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => {
              const time = new Date(e.timestamp * 1000);
              return (
                <tr key={i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td style={devLogTdStyle}>
                    {time.toLocaleTimeString(undefined, { hour12: false })}
                  </td>
                  <td style={{ ...devLogTdStyle, color: "var(--accent)" }}>{e.key}</td>
                  <td style={{ ...devLogTdStyle, color: "var(--text-muted)" }}>
                    {formatValue(e.oldValue)}
                  </td>
                  <td style={devLogTdStyle}>{formatValue(e.newValue)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

const devLogThStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--text-secondary)",
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};

const devLogTdStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 150,
};
