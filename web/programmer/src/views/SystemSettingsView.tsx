import { useState, useEffect, useCallback } from "react";
import { Save, AlertTriangle, Eye, EyeOff, RefreshCw } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { showError, showSuccess } from "../store/toastStore";
import * as api from "../api/restClient";
import type { SystemConfig, NetworkAdapter } from "../api/restClient";

const REDACTED = "***";

const cardStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-lg)",
  marginBottom: "var(--space-xl)",
};

const sectionTitle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  fontWeight: 600,
  marginBottom: "var(--space-md)",
};

const fieldRow: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "200px 1fr",
  gap: "var(--space-sm) var(--space-lg)",
  alignItems: "center",
  marginBottom: "var(--space-md)",
};

const labelStyle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
};

const helpText: React.CSSProperties = {
  fontSize: 12,
  color: "var(--text-muted)",
  gridColumn: "2",
  marginTop: -4,
  marginBottom: "var(--space-xs)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  background: "var(--bg-input, var(--bg-elevated))",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
};

const selectStyle: React.CSSProperties = {
  ...inputStyle,
  cursor: "pointer",
};

const toggleRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "var(--space-sm) 0",
  marginBottom: "var(--space-sm)",
};

const toggleStyle: React.CSSProperties = {
  position: "relative",
  width: 40,
  height: 22,
  borderRadius: 11,
  cursor: "pointer",
  transition: "background var(--transition-fast)",
  border: "none",
  flexShrink: 0,
};

const btnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-sm) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  fontWeight: 500,
  cursor: "pointer",
  transition: "all var(--transition-fast)",
  background: "var(--accent)",
  color: "#fff",
  border: "1px solid var(--accent)",
};

const warningBox: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  padding: "var(--space-md)",
  background: "rgba(255, 152, 0, 0.08)",
  border: "1px solid rgba(255, 152, 0, 0.3)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-primary)",
  marginBottom: "var(--space-md)",
};

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      style={{
        ...toggleStyle,
        background: checked ? "var(--accent)" : "var(--bg-hover, #555)",
      }}
      onClick={() => onChange(!checked)}
    >
      <div
        style={{
          position: "absolute",
          top: 3,
          left: checked ? 21 : 3,
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: "#fff",
          transition: "left var(--transition-fast)",
        }}
      />
    </button>
  );
}

function PasswordField({
  value,
  placeholder,
  onChange,
}: {
  value: string;
  placeholder: string;
  onChange: (v: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const isRedacted = value === REDACTED;
  return (
    <div style={{ position: "relative" }}>
      <input
        type={visible ? "text" : "password"}
        style={inputStyle}
        value={isRedacted ? "" : value}
        placeholder={isRedacted ? "Set (hidden)" : placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        style={{
          position: "absolute",
          right: 8,
          top: "50%",
          transform: "translateY(-50%)",
          background: "none",
          border: "none",
          color: "var(--text-muted)",
          cursor: "pointer",
          padding: 4,
        }}
      >
        {visible ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

export function SystemSettingsView() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [dirty, setDirty] = useState<Partial<SystemConfig>>({});
  const [saving, setSaving] = useState(false);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [adapters, setAdapters] = useState<NetworkAdapter[]>([]);
  const [adaptersLoading, setAdaptersLoading] = useState(false);

  const loadAdapters = useCallback(() => {
    setAdaptersLoading(true);
    api.getNetworkAdapters()
      .then((r) => setAdapters(r.adapters))
      .catch(() => {})
      .finally(() => setAdaptersLoading(false));
  }, []);

  useEffect(() => {
    api.getSystemConfig().then(setConfig).catch((e) => showError("Failed to load config: " + e));
    loadAdapters();
  }, [loadAdapters]);

  // Track which fields the user has changed
  const update = useCallback(
    <S extends keyof SystemConfig>(section: S, key: keyof SystemConfig[S], value: SystemConfig[S][keyof SystemConfig[S]]) => {
      setDirty((prev) => ({
        ...prev,
        [section]: { ...(prev[section] as Record<string, unknown> ?? {}), [key]: value },
      }));
      // Track restart-required changes (bind_address/port need restart, control_interface does not)
      if (section === "network" && (key === "bind_address" || key === "http_port")) setRestartNeeded(true);
    },
    [],
  );

  // Merged view: base config + unsaved changes
  const merged = useCallback(
    <S extends keyof SystemConfig>(section: S): SystemConfig[S] => {
      if (!config) return {} as SystemConfig[S];
      return { ...config[section], ...(dirty[section] as Partial<SystemConfig[S]> ?? {}) };
    },
    [config, dirty],
  );

  const handleSave = async () => {
    if (Object.keys(dirty).length === 0) return;

    // Don't send redacted values back
    const payload: Record<string, Record<string, unknown>> = {};
    for (const [section, fields] of Object.entries(dirty)) {
      const clean: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(fields as Record<string, unknown>)) {
        if (value !== REDACTED) clean[key] = value;
      }
      if (Object.keys(clean).length > 0) payload[section] = clean;
    }

    if (Object.keys(payload).length === 0) return;

    setSaving(true);
    try {
      await api.updateSystemConfig(payload as Partial<SystemConfig>);
      showSuccess("Settings saved" + (restartNeeded ? ". Restart required for network changes." : "."));
      // Reload config to get fresh state
      const fresh = await api.getSystemConfig();
      setConfig(fresh);
      setDirty({});
    } catch (e) {
      showError("Failed to save: " + String(e));
    } finally {
      setSaving(false);
    }
  };

  const hasDirty = Object.keys(dirty).length > 0;
  const net = merged("network");
  const auth = merged("auth");
  const log = merged("logging");
  const upd = merged("updates");
  const kiosk = merged("kiosk");

  // Warning: no auth + public bind
  const noAuth = !auth.programmer_password && auth.programmer_password !== REDACTED && !auth.api_key && auth.api_key !== REDACTED;
  const publicBind = net.bind_address === "0.0.0.0";

  if (!config) {
    return (
      <ViewContainer title="System Settings">
        <div style={{ padding: "var(--space-xl)", color: "var(--text-muted)" }}>Loading...</div>
      </ViewContainer>
    );
  }

  return (
    <ViewContainer
      title="System Settings"
      actions={
        <button
          style={{ ...btnStyle, opacity: hasDirty && !saving ? 1 : 0.5 }}
          onClick={handleSave}
          disabled={!hasDirty || saving}
        >
          <Save size={14} />
          <span>{saving ? "Saving..." : "Save"}</span>
        </button>
      }
    >
      <div style={{ maxWidth: 700 }}>
        {/* Restart warning */}
        {restartNeeded && hasDirty && (
          <div style={warningBox}>
            <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
            <span>Network changes require a server restart to take effect.</span>
          </div>
        )}

        {/* Security warning */}
        {noAuth && publicBind && (
          <div style={warningBox}>
            <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
            <span>The server is accessible on the network with no authentication. Anyone on your network can open the Programmer IDE and modify your project. Set a <strong>programmer password</strong> below to require a login.</span>
          </div>
        )}

        {/* Network */}
        <h3 style={sectionTitle}>Network</h3>
        <div style={cardStyle}>
          <div style={fieldRow}>
            <label style={labelStyle} htmlFor="cfg-bind-address">Bind address</label>
            <input
              id="cfg-bind-address"
              style={inputStyle}
              value={net.bind_address}
              onChange={(e) => update("network", "bind_address", e.target.value)}
            />
            <span style={helpText}>
              Controls whether other devices on the network can reach the server. Set to <code>0.0.0.0</code> to allow tablets, phones, and other computers to access the Panel UI. Use <code>127.0.0.1</code> to restrict access to this machine only.
            </span>
          </div>
          <div style={fieldRow}>
            <label style={labelStyle} htmlFor="cfg-http-port">HTTP port</label>
            <input
              id="cfg-http-port"
              type="number"
              style={inputStyle}
              value={net.http_port}
              onChange={(e) => update("network", "http_port", parseInt(e.target.value) || 8080)}
            />
          </div>
          <div style={fieldRow}>
            <label style={labelStyle} htmlFor="cfg-control-interface">Control interface</label>
            <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
              <select
                id="cfg-control-interface"
                style={{ ...selectStyle, flex: 1 }}
                value={net.control_interface ?? ""}
                onChange={(e) => update("network", "control_interface", e.target.value)}
              >
                <option value="">Auto (use default route)</option>
                {adapters.map((a) => (
                  <option key={a.ip} value={a.ip}>
                    {a.name} — {a.ip} ({a.subnet})
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={loadAdapters}
                disabled={adaptersLoading}
                title="Refresh adapter list"
                style={{
                  background: "none",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                  padding: "var(--space-sm)",
                  display: "flex",
                  alignItems: "center",
                }}
              >
                <RefreshCw size={14} style={adaptersLoading ? { animation: "spin 1s linear infinite" } : undefined} />
              </button>
            </div>
            <span style={helpText}>
              Which network adapter OpenAVC uses to communicate with AV devices and run discovery scans. Changes take effect on the next device connection or scan. Does not require a restart.
            </span>
          </div>
        </div>

        {/* Authentication */}
        <h3 style={sectionTitle}>Authentication</h3>
        <div style={cardStyle}>
          <div style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            marginBottom: "var(--space-lg)",
            lineHeight: 1.5,
          }}>
            Authentication is optional. When the server is only accessible locally (bind address <code>127.0.0.1</code>), no credentials are needed.
            When the server is accessible on the network (<code>0.0.0.0</code>), set at least one of the options below to prevent unauthorized access to the Programmer IDE and API.
            The Panel UI is never password-protected so end users can always reach it.
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Programmer password</label>
            <PasswordField
              value={auth.programmer_password}
              placeholder="No password set"
              onChange={(v) => update("auth", "programmer_password", v)}
            />
            <span style={helpText}>
              Protects the Programmer IDE with a browser login prompt. Set this if anyone else on your network could access the server and you want to prevent them from modifying the project. This is for humans logging in via a browser.
            </span>
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>API key</label>
            <PasswordField
              value={auth.api_key}
              placeholder="No API key set"
              onChange={(v) => update("auth", "api_key", v)}
            />
            <span style={helpText}>
              For third-party integrations. If you have external systems (control scripts, middleware, or other software) that connect to the OpenAVC REST API or WebSocket, set an API key here and provide it to those systems. Not needed unless you are building custom integrations.
            </span>
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Panel lock code</label>
            <PasswordField
              value={auth.panel_lock_code}
              placeholder="No lock code set"
              onChange={(v) => update("auth", "panel_lock_code", v)}
            />
            <span style={helpText}>
              Prevents users from navigating away from the touch panel. Set a PIN here if the panel runs on a shared or public-facing display and you don't want people exiting it.
            </span>
          </div>
        </div>

        {/* Logging */}
        <h3 style={sectionTitle}>Logging</h3>
        <div style={cardStyle}>
          <div style={fieldRow}>
            <label style={labelStyle} htmlFor="cfg-log-level">Log level</label>
            <select
              id="cfg-log-level"
              style={selectStyle}
              value={log.level}
              onChange={(e) => update("logging", "level", e.target.value)}
            >
              <option value="debug">Debug</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="error">Error</option>
            </select>
          </div>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>File logging</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Write logs to disk in the data directory.</div>
            </div>
            <Toggle checked={log.file_enabled} onChange={(v) => update("logging", "file_enabled", v)} />
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Max file size (MB)</label>
            <input
              type="number"
              style={inputStyle}
              value={log.max_size_mb}
              onChange={(e) => update("logging", "max_size_mb", parseInt(e.target.value) || 50)}
            />
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Max log files</label>
            <input
              type="number"
              style={inputStyle}
              value={log.max_files}
              onChange={(e) => update("logging", "max_files", parseInt(e.target.value) || 5)}
            />
          </div>
        </div>

        {/* Updates */}
        <h3 style={sectionTitle}>Updates</h3>
        <div style={cardStyle}>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>Check for updates</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Periodically check GitHub for new releases.</div>
            </div>
            <Toggle checked={upd.check_enabled} onChange={(v) => update("updates", "check_enabled", v)} />
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Channel</label>
            <select
              style={selectStyle}
              value={upd.channel}
              onChange={(e) => update("updates", "channel", e.target.value)}
            >
              <option value="stable">Stable</option>
              <option value="beta">Beta</option>
            </select>
            <span style={helpText}>Beta includes pre-release versions.</span>
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Check interval (hours)</label>
            <input
              type="number"
              style={inputStyle}
              value={upd.auto_check_interval_hours}
              onChange={(e) => update("updates", "auto_check_interval_hours", parseInt(e.target.value) || 24)}
            />
            <span style={helpText}>How often to check for new versions.</span>
          </div>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>Auto-backup before update</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Automatically back up projects before applying updates.</div>
            </div>
            <Toggle checked={upd.auto_backup_before_update} onChange={(v) => update("updates", "auto_backup_before_update", v)} />
          </div>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>Notify only</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Show update notifications without applying automatically.</div>
            </div>
            <Toggle checked={upd.notify_only} onChange={(v) => update("updates", "notify_only", v)} />
          </div>
        </div>

        {/* Kiosk */}
        <h3 style={sectionTitle}>Kiosk</h3>
        <div style={cardStyle}>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>Kiosk mode</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Launch the Panel UI fullscreen on an attached display (e.g. Raspberry Pi with HDMI touchscreen). Requires a reboot of the device to take effect.</div>
            </div>
            <Toggle checked={kiosk.enabled} onChange={(v) => update("kiosk", "enabled", v)} />
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Target URL</label>
            <input
              style={inputStyle}
              value={kiosk.target_url}
              onChange={(e) => update("kiosk", "target_url", e.target.value)}
            />
            <span style={helpText}>The URL loaded in the kiosk browser. Defaults to the local Panel UI.</span>
          </div>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>Show cursor</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Show the mouse cursor on the kiosk display. Disable for touch-only panels.</div>
            </div>
            <Toggle checked={kiosk.cursor_visible} onChange={(v) => update("kiosk", "cursor_visible", v)} />
          </div>
        </div>
      </div>
    </ViewContainer>
  );
}
