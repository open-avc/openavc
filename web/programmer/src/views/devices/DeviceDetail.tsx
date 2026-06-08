import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Send, Pencil, Trash2, Wifi, WifiOff, Power, RefreshCw, Copy, Settings, Check, X, Loader2, Search, ChevronDown } from "lucide-react";
import { CopyButton } from "../../components/shared/CopyButton";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { useLogStore } from "../../store/logStore";
import * as api from "../../api/restClient";
import type { DeviceConfig, DeviceInfo, DeviceSettingValue } from "../../api/types";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { findDeviceReferences } from "./deviceUtils";
import { ChildEntities } from "./ChildEntities";

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
  } | null>(null);
  const [testing, setTesting] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");

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

  // Drivers that manage child entities declare them in DRIVER_INFO; those
  // keys (device.<id>.<type>.<paddedId>.<prop>) belong to the Child Entities
  // tab, not this flat list — a fully-loaded controller has tens of thousands
  // of them. We hide them here only; the StateStore, scripts, macros,
  // triggers, and the cloud relay still see every key (presentation only).
  const childTypeNames = useMemo(() => {
    const cet = deviceInfo?.driver_info?.child_entity_types as
      | Record<string, unknown>
      | undefined;
    return cet ? new Set(Object.keys(cet)) : new Set<string>();
  }, [deviceInfo]);

  // Extract device state from flat liveState
  const prefix = `device.${deviceId}.`;
  const stateEntries: [string, string][] = [];
  let hiddenChildKeyCount = 0;
  const stateTerm = searchTerm.trim().toLowerCase();
  for (const [key, value] of Object.entries(liveState)) {
    if (!key.startsWith(prefix)) continue;
    const rest = key.slice(prefix.length);
    if (childTypeNames.size > 0) {
      const dot = rest.indexOf(".");
      if (dot > 0 && childTypeNames.has(rest.slice(0, dot))) {
        hiddenChildKeyCount++;
        continue;
      }
    }
    const valueStr = String(value ?? "");
    if (
      stateTerm &&
      !rest.toLowerCase().includes(stateTerm) &&
      !valueStr.toLowerCase().includes(stateTerm)
    ) {
      continue;
    }
    stateEntries.push([rest, valueStr]);
  }

  const deviceName = String(liveState[`device.${deviceId}.name`] ?? deviceId);
  const connected = Boolean(liveState[`device.${deviceId}.connected`]);
  const orphaned = Boolean(liveState[`device.${deviceId}.orphaned`]);
  // Server-built, human-readable offline reason (device.<id>.offline_detail).
  // The taxonomy lives server-side; this view only renders the message.
  const offlineDetail = String(liveState[`device.${deviceId}.offline_detail`] ?? "");
  const reconnectAttempt = Number(liveState[`device.${deviceId}.reconnect_attempt`]) || 0;
  const reconnectFailed = Boolean(liveState[`device.${deviceId}.reconnect_failed`]);

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
      {orphaned && (
        <OrphanBanner
          driverId={deviceConfig?.driver ?? ""}
          onReassign={() => deviceConfig && onEdit(deviceConfig)}
          onBrowseDrivers={onBrowseDrivers}
          onActivated={() => api.getDevice(deviceId).then(setDeviceInfo).catch(console.error)}
        />
      )}

      {/* Offline reason banner — actionable cause from device.<id>.offline_detail */}
      {!connected && isEnabled && !orphaned && offlineDetail && (
        <OfflineBanner
          detail={offlineDetail}
          attempt={reconnectAttempt}
          failed={reconnectFailed}
        />
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
            ? `Connected successfully (${testResult.latency_ms}ms)`
            : `Connection failed: ${testResult.error}`}
        </div>
      )}

      {/* Unified filter — one box narrows both the child-entity rows (across
          every value in each row, not just the summary columns) and the Live
          State list below. */}
      <div style={{ position: "relative", marginBottom: "var(--space-lg)" }}>
        <Search
          size={16}
          style={{
            position: "absolute",
            left: 10,
            top: "50%",
            transform: "translateY(-50%)",
            color: "var(--text-muted)",
            pointerEvents: "none",
          }}
        />
        <input
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          placeholder="Filter children and live state by id, label, name, or any value"
          data-testid="device-filter"
          style={{ width: "100%", padding: "var(--space-sm) 34px" }}
        />
        {searchTerm && (
          <button
            onClick={() => setSearchTerm("")}
            title="Clear filter"
            style={{
              position: "absolute",
              right: 8,
              top: "50%",
              transform: "translateY(-50%)",
              padding: 2,
              background: "transparent",
              color: "var(--text-muted)",
              border: "none",
              cursor: "pointer",
            }}
          >
            <X size={16} />
          </button>
        )}
      </div>

      {/* Child Entities (only renders when the driver declares any) */}
      <ChildEntities deviceId={deviceId} search={searchTerm} />

      {/* Live State */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Live State</h3>
        {hiddenChildKeyCount > 0 && (
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              marginTop: "-4px",
              marginBottom: "var(--space-sm)",
            }}
          >
            {hiddenChildKeyCount.toLocaleString()} child-entity state{" "}
            {hiddenChildKeyCount === 1 ? "key is" : "keys are"} shown in the
            Child Entities tab above.
          </div>
        )}
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
              {stateTerm ? `No state matches "${searchTerm}"` : "No state values yet"}
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

      {/* Send Command */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Send Command</h3>
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
                <CommandPicker
                  commands={commands}
                  value={selectedCommand}
                  onChange={(cmd) => {
                    setSelectedCommand(cmd);
                    // Seed defaults so enum params show a real selection (not a
                    // blank box) and booleans default to No.
                    const pdefs = (commands[cmd] as Record<string, unknown>)
                      ?.params as Record<string, Record<string, unknown>> | undefined;
                    const defaults: Record<string, string> = {};
                    for (const [name, d] of Object.entries(pdefs ?? {})) {
                      const t = String(d?.type ?? "string");
                      const vals = d?.values as string[] | undefined;
                      if (t === "enum" && vals && vals.length > 0) defaults[name] = vals[0];
                      else if (t === "boolean") defaults[name] = "false";
                      else defaults[name] = "";
                    }
                    setCommandParams(defaults);
                    setCommandResult(null);
                  }}
                />
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
                    const def = pDef?.[paramName];
                    const paramHelp = def?.help as string | undefined;
                    const paramType = String(def?.type ?? "string");
                    const paramValues = def?.values as string[] | undefined;
                    const paramMin = def?.min as number | undefined;
                    const paramMax = def?.max as number | undefined;
                    const required = def?.required === true;
                    const current = commandParams[paramName] ?? "";
                    const setParam = (val: string) =>
                      setCommandParams((p) => ({ ...p, [paramName]: val }));
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
                      {paramType === "enum" && paramValues ? (
                        <select
                          value={current}
                          onChange={(e) => setParam(e.target.value)}
                          style={{ flex: 1 }}
                        >
                          {!required && <option value="">(none)</option>}
                          {paramValues.map((v) => (
                            <option key={v} value={v}>{v}</option>
                          ))}
                        </select>
                      ) : paramType === "boolean" ? (
                        <select
                          value={current || "false"}
                          onChange={(e) => setParam(e.target.value)}
                          style={{ flex: 1 }}
                        >
                          <option value="true">Yes</option>
                          <option value="false">No</option>
                        </select>
                      ) : (
                        <input
                          type={paramType === "integer" || paramType === "number" ? "number" : "text"}
                          value={current}
                          min={paramMin}
                          max={paramMax}
                          onChange={(e) => setParam(e.target.value)}
                          placeholder={
                            paramMin !== undefined && paramMax !== undefined
                              ? `${paramMin}-${paramMax}`
                              : paramName
                          }
                          style={{ flex: 1 }}
                        />
                      )}
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

// --- Command Picker ---

// Searchable command dropdown. Devices like the Chazy Control Pro expose
// 200+ commands; a native <select> is unusable at that size. Filters by
// command id and human label, shows both, and closes on outside click.
function CommandPicker({
  commands,
  value,
  onChange,
}: {
  commands: Record<string, unknown>;
  value: string;
  onChange: (cmd: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  const names = useMemo(() => Object.keys(commands), [commands]);
  const labelOf = useCallback(
    (cmd: string): string => {
      const lbl = (commands[cmd] as Record<string, unknown> | undefined)?.label;
      return typeof lbl === "string" ? lbl : "";
    },
    [commands],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return names;
    return names.filter(
      (n) => n.toLowerCase().includes(q) || labelOf(n).toLowerCase().includes(q),
    );
  }, [names, query, labelOf]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const selectedDisplay = value
    ? labelOf(value)
      ? `${labelOf(value)} (${value})`
      : value
    : "";

  return (
    <div ref={ref} style={{ position: "relative", flex: 1 }}>
      <div style={{ position: "relative" }}>
        <Search
          size={14}
          style={{
            position: "absolute",
            left: 8,
            top: "50%",
            transform: "translateY(-50%)",
            color: "var(--text-muted)",
            pointerEvents: "none",
          }}
        />
        <input
          value={open ? query : selectedDisplay}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => {
            setOpen(true);
            setQuery("");
          }}
          placeholder="Search commands..."
          data-testid="command-search"
          style={{ width: "100%", padding: "var(--space-xs) 28px" }}
        />
        <ChevronDown
          size={14}
          style={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            color: "var(--text-muted)",
            pointerEvents: "none",
          }}
        />
      </div>
      {open && (
        <div
          data-testid="command-options"
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            zIndex: 30,
            marginTop: 2,
            maxHeight: 280,
            overflowY: "auto",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
          }}
        >
          {filtered.length === 0 ? (
            <div
              style={{
                padding: "var(--space-sm) var(--space-md)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              No commands match "{query}"
            </div>
          ) : (
            filtered.map((cmd) => {
              const lbl = labelOf(cmd);
              const isSelected = cmd === value;
              return (
                <button
                  key={cmd}
                  onClick={() => {
                    onChange(cmd);
                    setOpen(false);
                    setQuery("");
                  }}
                  data-testid={`command-option-${cmd}`}
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    gap: "var(--space-sm)",
                    width: "100%",
                    textAlign: "left",
                    padding: "var(--space-xs) var(--space-md)",
                    background: isSelected ? "var(--accent-bg)" : "transparent",
                    color: isSelected ? "var(--text-on-accent)" : "var(--text-primary)",
                    border: "none",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                  }}
                >
                  {lbl && <span>{lbl}</span>}
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: isSelected ? "var(--text-on-accent)" : "var(--text-muted)",
                      opacity: lbl ? 0.8 : 1,
                    }}
                  >
                    {cmd}
                  </span>
                </button>
              );
            })
          )}
        </div>
      )}
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

function OfflineBanner({
  detail,
  attempt,
  failed,
}: {
  detail: string;
  attempt: number;
  failed: boolean;
}) {
  const accent = "var(--color-warning, #f59e0b)";
  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.1)",
        border: "2px solid rgba(245, 158, 11, 0.4)",
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
      }}
    >
      <WifiOff size={18} style={{ color: accent, flexShrink: 0, marginTop: 2 }} />
      <div>
        <div style={{ fontWeight: 600, color: accent, fontSize: "var(--font-size-md)" }}>
          Offline
        </div>
        <div style={{ fontSize: "var(--font-size-sm)", marginTop: 2 }}>{detail}</div>
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          {failed
            ? "Automatic reconnection gave up. Use the Reconnect button above to try again."
            : attempt > 0
              ? `Reconnecting automatically… (attempt ${attempt})`
              : "Reconnecting automatically…"}
        </div>
      </div>
    </div>
  );
}

function OrphanBanner({
  driverId,
  onReassign,
  onBrowseDrivers,
  onActivated,
}: {
  driverId: string;
  onReassign: () => void;
  onBrowseDrivers?: () => void;
  onActivated: () => void;
}) {
  // Look up the missing driver in the catalog so the button can install it
  // directly (the previous behavior of switching tabs left the user stranded
  // — they had to find it manually). When the driver isn't in the catalog,
  // surface that fact inline rather than silently failing.
  const [match, setMatch] = useState<{ file_url: string; min_platform_version: string | null } | null>(null);
  const [lookupDone, setLookupDone] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!driverId) return;
    let cancelled = false;
    api
      .listMissingDrivers()
      .then((items) => {
        if (cancelled) return;
        const entry = items.find((m) => m.driver_id === driverId);
        if (entry?.community_match) {
          setMatch({
            file_url: entry.community_match.file_url,
            min_platform_version: entry.community_match.min_platform_version,
          });
        }
        setLookupDone(true);
      })
      .catch(() => setLookupDone(true));
    return () => {
      cancelled = true;
    };
  }, [driverId]);

  const handleInstall = async () => {
    if (!match) return;
    setInstalling(true);
    setError(null);
    try {
      await api.installCommunityDriver(driverId, match.file_url, match.min_platform_version || undefined);
      // Server retries orphans automatically; refresh device info to pick
      // up the now-active state.
      onActivated();
    } catch (e) {
      setError(String(e));
    } finally {
      setInstalling(false);
    }
  };

  return (
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
        This device needs the driver "{driverId}" which is not installed.
        {lookupDone && !match && (
          <div style={{ marginTop: "var(--space-xs)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
            This driver isn't in the community catalog. Reassign the device to a different
            driver, or upload the driver file from the Drivers tab.
          </div>
        )}
        {error && (
          <div style={{ marginTop: "var(--space-xs)", color: "#ef4444", fontSize: "var(--font-size-sm)" }}>
            Install failed: {error}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        {match && (
          <button
            onClick={handleInstall}
            disabled={installing}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--color-warning, #f59e0b)",
              color: "#000",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
              cursor: installing ? "not-allowed" : "pointer",
              opacity: installing ? 0.7 : 1,
            }}
          >
            {installing ? "Installing..." : "Install from Community"}
          </button>
        )}
        {lookupDone && !match && onBrowseDrivers && (
          <button
            onClick={onBrowseDrivers}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            Browse Drivers
          </button>
        )}
        <button
          onClick={onReassign}
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
  );
}
