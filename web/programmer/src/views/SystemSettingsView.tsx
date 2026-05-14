import { useState, useEffect, useCallback } from "react";
import { Save, AlertTriangle, Eye, EyeOff, RefreshCw, Download, Lock } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { showError, showSuccess } from "../store/toastStore";
import * as api from "../api/restClient";
import type { SystemConfig, NetworkAdapter, TlsStatus } from "../api/restClient";

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

const subCardTitle: React.CSSProperties = {
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
  color: "var(--text-primary)",
  margin: 0,
  marginBottom: "var(--space-xs)",
};

const subCardDescription: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-lg)",
  lineHeight: 1.5,
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
  background: "var(--accent-bg)",
  color: "var(--text-on-accent)",
  border: "1px solid var(--accent-bg)",
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
        background: checked ? "var(--accent-bg)" : "var(--bg-hover, #555)",
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
      {!isRedacted && (
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
      )}
    </div>
  );
}

function ExpiryBadge({ days }: { days: number }) {
  let bg = "rgba(76, 175, 80, 0.15)";
  let color = "rgb(76, 175, 80)";
  let label = `${days} days`;
  if (days < 0) {
    bg = "rgba(244, 67, 54, 0.15)";
    color = "rgb(244, 67, 54)";
    label = "Expired";
  } else if (days < 30) {
    bg = "rgba(255, 152, 0, 0.15)";
    color = "rgb(255, 152, 0)";
    label = `${days} days left`;
  }
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      background: bg,
      color,
    }}>
      {label}
    </span>
  );
}

function warningLabel(w: string): string {
  switch (w) {
    case "expired": return "Certificate is expired — generate a new one";
    case "expiring-soon": return "Certificate expires in under 30 days";
    case "hostname-mismatch": return "Certificate does not cover this server's current hostname/IP — regenerate after IP change";
    default: return w;
  }
}

function getTheme(): "dark" | "light" {
  return (localStorage.getItem("openavc-theme") as "dark" | "light") || "dark";
}

function setTheme(theme: "dark" | "light") {
  localStorage.setItem("openavc-theme", theme);
  document.documentElement.dataset.theme = theme;
}

export function SystemSettingsView() {
  const [theme, setThemeState] = useState<"dark" | "light">(getTheme);
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [dirty, setDirty] = useState<Partial<SystemConfig>>({});
  const [saving, setSaving] = useState(false);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [showRebootDialog, setShowRebootDialog] = useState(false);
  const [kioskAvailable, setKioskAvailable] = useState(false);
  const [adapters, setAdapters] = useState<NetworkAdapter[]>([]);
  const [adaptersLoading, setAdaptersLoading] = useState(false);
  const [tlsStatus, setTlsStatus] = useState<TlsStatus | null>(null);

  const loadAdapters = useCallback(() => {
    setAdaptersLoading(true);
    api.getNetworkAdapters()
      .then((r) => setAdapters(r.adapters))
      .catch(() => {})
      .finally(() => setAdaptersLoading(false));
  }, []);

  const loadTlsStatus = useCallback(() => {
    api.getTlsStatus().then(setTlsStatus).catch(() => setTlsStatus(null));
  }, []);

  useEffect(() => {
    api.getSystemConfig().then(setConfig).catch((e) => showError("Failed to load config: " + e));
    api.getSystemVersion().then((v) => setKioskAvailable(v.kiosk_available)).catch(() => {});
    loadAdapters();
    loadTlsStatus();
  }, [loadAdapters, loadTlsStatus]);

  // Track which fields the user has changed
  const update = useCallback(
    <S extends keyof SystemConfig>(section: S, key: keyof SystemConfig[S], value: SystemConfig[S][keyof SystemConfig[S]]) => {
      setDirty((prev) => ({
        ...prev,
        [section]: { ...(prev[section] as Record<string, unknown> ?? {}), [key]: value },
      }));
      // Track restart-required changes (bind_address/port need restart, control_interface does not)
      if (section === "network" && (key === "bind_address" || key === "http_port")) setRestartNeeded(true);
      // Any TLS change requires a restart — uvicorn only reads cert + ports at startup.
      if (section === "tls") setRestartNeeded(true);
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

    const hasKioskChanges = "kiosk" in payload;

    setSaving(true);
    try {
      await api.updateSystemConfig(payload as Partial<SystemConfig>);
      showSuccess("Settings saved" + (restartNeeded ? ". Restart required for network/security changes." : "."));
      // Reload config + tls status to get fresh state
      const fresh = await api.getSystemConfig();
      setConfig(fresh);
      setDirty({});
      loadTlsStatus();
      if (hasKioskChanges && kioskAvailable) setShowRebootDialog(true);
    } catch (e) {
      showError("Failed to save: " + String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDownloadCert = async () => {
    try {
      const blob = await api.downloadCertificate();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "openavc-ca.crt";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showSuccess("CA certificate downloaded. Install it on your panel devices to skip the security warning.");
    } catch (e) {
      showError("Could not download CA certificate: " + String(e));
    }
  };

  const hasDirty = Object.keys(dirty).length > 0;
  const net = merged("network");
  const auth = merged("auth");
  const log = merged("logging");
  const upd = merged("updates");
  const kiosk = merged("kiosk");
  const tls = merged("tls");

  // Warning: no auth + public bind
  const noAuth = !auth.programmer_password && auth.programmer_password !== REDACTED && !auth.api_key && auth.api_key !== REDACTED;
  const publicBind = net.bind_address === "0.0.0.0";

  // Validation for TLS fields. The cert mode is driven by tls.auto_generate
  // (true => auto self-sign, false => user-supplied paths), not by whether the
  // path fields happen to be populated.
  const tlsCertMode: "auto" | "provided" = tls?.auto_generate ? "auto" : "provided";
  const tlsPortInvalid =
    tls && (tls.port < 1 || tls.port > 65535 || tls.port === net.http_port);
  const tlsPortError = tls && tls.port === net.http_port
    ? "HTTPS port must differ from HTTP port"
    : tls && (tls.port < 1 || tls.port > 65535)
    ? "Port must be between 1 and 65535"
    : null;
  const tlsProvidedBlank =
    tlsCertMode === "provided" &&
    tls?.enabled &&
    (!tls?.cert_file?.trim() || !tls?.key_file?.trim());
  const saveBlocked = !!(tls?.enabled && (tlsPortInvalid || tlsProvidedBlank));

  // Cross-protocol switch warning (page loaded over one scheme, switching to the other)
  const pageIsHttps = typeof window !== "undefined" && window.location.protocol === "https:";
  const switchingOff = pageIsHttps && tls && tls.enabled === false && config?.tls?.enabled === true;
  const switchingOn = !pageIsHttps && tls && tls.enabled === true && config?.tls?.enabled === false;

  if (!config) {
    return (
      <ViewContainer title="System Settings">
        <div style={{ padding: "var(--space-xl)", color: "var(--text-muted)" }}>Loading...</div>
      </ViewContainer>
    );
  }

  return (
    <>
    <ViewContainer
      title="System Settings"
      actions={
        <button
          style={{ ...btnStyle, opacity: hasDirty && !saving && !saveBlocked ? 1 : 0.5 }}
          onClick={handleSave}
          disabled={!hasDirty || saving || saveBlocked}
          title={saveBlocked ? "Fix the validation errors below before saving" : undefined}
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
            <span>The server is accessible on the network with no authentication. Anyone on your network can open the Programmer IDE and modify your project. Set a <strong>programmer login</strong> below to require credentials.</span>
          </div>
        )}

        {/* Appearance */}
        <h3 style={sectionTitle}>Appearance</h3>
        <div style={cardStyle}>
          <div style={fieldRow}>
            <label style={labelStyle}>Theme</label>
            <select
              style={selectStyle}
              value={theme}
              onChange={(e) => {
                const t = e.target.value as "dark" | "light";
                setThemeState(t);
                setTheme(t);
              }}
            >
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </div>
        </div>

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

        {/* Security (HTTPS / TLS) */}
        <h3 style={sectionTitle}>Security</h3>
        <div style={cardStyle}>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>Enable HTTPS</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Encrypt traffic between this server and panels / browsers. Off by default.
              </div>
            </div>
            <Toggle
              checked={!!tls?.enabled}
              onChange={(v) => update("tls", "enabled", v)}
            />
          </div>

          {/* URL preview always visible — shows the user where they'll reach the IDE */}
          <div style={{ ...helpText, gridColumn: "1 / -1", marginTop: 0, marginBottom: "var(--space-md)" }}>
            After restart, the Programmer IDE will be at{" "}
            <code>
              {tls?.enabled ? "https" : "http"}://&lt;server&gt;:
              {tls?.enabled ? tls.port : net.http_port}/programmer
            </code>
          </div>

          {switchingOff && (
            <div style={warningBox}>
              <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
              <span>
                You're disabling HTTPS while connected over <code>https://</code>. After restart, this page will be at{" "}
                <code>http://&lt;server&gt;:{net.http_port}/programmer</code>. Update any bookmarks pointing to{" "}
                <code>https://</code>.
              </span>
            </div>
          )}
          {switchingOn && (
            <div style={warningBox}>
              <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
              <span>
                You're enabling HTTPS while connected over <code>http://</code>. After restart, this page will be at{" "}
                <code>https://&lt;server&gt;:{tls?.port ?? 8443}/programmer</code>. Your browser will show a warning
                until you install the CA certificate (button appears below after restart).
              </span>
            </div>
          )}

          {tls?.enabled && (
            <>
              {/* Cert source */}
              <div style={{ ...fieldRow, gridTemplateColumns: "200px 1fr" }}>
                <label style={labelStyle}>Certificate source</label>
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", cursor: "pointer" }}>
                    <input
                      type="radio"
                      name="tls-cert-source"
                      checked={tlsCertMode === "auto"}
                      onChange={() => {
                        update("tls", "auto_generate", true);
                        update("tls", "cert_file", "");
                        update("tls", "key_file", "");
                      }}
                    />
                    <span>Auto-generate (recommended)</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", cursor: "pointer" }}>
                    <input
                      type="radio"
                      name="tls-cert-source"
                      checked={tlsCertMode === "provided"}
                      onChange={() => update("tls", "auto_generate", false)}
                    />
                    <span>Use my own certificate</span>
                  </label>
                </div>
              </div>

              {tlsCertMode === "auto" && (
                <>
                  <div style={fieldRow}>
                    <label style={labelStyle}>Certificate</label>
                    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
                      {tlsStatus?.enabled && tlsStatus.cert ? (
                        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
                          <span style={{ fontSize: "var(--font-size-sm)" }}>
                            Valid until{" "}
                            <strong>{new Date(tlsStatus.cert.expires_at).toLocaleDateString()}</strong>
                          </span>
                          <ExpiryBadge days={tlsStatus.cert.days_until_expiry} />
                        </div>
                      ) : (
                        <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                          The self-signed certificate will be generated on the next restart.
                        </span>
                      )}
                      {tlsStatus?.enabled && tlsStatus.mode === "auto" && (
                        <button
                          type="button"
                          onClick={handleDownloadCert}
                          style={{
                            ...btnStyle,
                            background: "var(--bg-elevated)",
                            color: "var(--text-primary)",
                            border: "1px solid var(--border-color)",
                            alignSelf: "flex-start",
                            marginTop: "var(--space-xs)",
                          }}
                        >
                          <Download size={14} />
                          <span>Download CA certificate</span>
                        </button>
                      )}
                    </div>
                    <span style={helpText}>
                      Install the CA on your panel devices (iOS Settings &rarr; Profile, or Android Security &rarr; Install certificate)
                      so they trust this server without a warning.
                    </span>
                  </div>
                </>
              )}

              {tlsCertMode === "provided" && (
                <>
                  <div style={fieldRow}>
                    <label style={labelStyle}>Certificate file</label>
                    <input
                      style={inputStyle}
                      value={tls.cert_file}
                      placeholder="/etc/openavc/tls/cert.pem"
                      onChange={(e) => update("tls", "cert_file", e.target.value)}
                    />
                    <span style={helpText}>
                      Absolute path on this server's filesystem. The server reads this file on each restart.
                    </span>
                  </div>
                  <div style={fieldRow}>
                    <label style={labelStyle}>Key file</label>
                    <input
                      style={inputStyle}
                      value={tls.key_file}
                      placeholder="/etc/openavc/tls/key.pem"
                      onChange={(e) => update("tls", "key_file", e.target.value)}
                    />
                    <span style={helpText}>
                      The private key matching the certificate above. Keep this file readable only by the OpenAVC service user.
                    </span>
                  </div>
                  {tlsProvidedBlank && (
                    <div style={{ ...helpText, color: "rgb(244, 67, 54)" }}>
                      Both certificate and key paths are required.
                    </div>
                  )}
                </>
              )}

              <div style={fieldRow}>
                <label style={labelStyle} htmlFor="cfg-tls-port">HTTPS port</label>
                <input
                  id="cfg-tls-port"
                  type="number"
                  min={1}
                  max={65535}
                  style={{
                    ...inputStyle,
                    borderColor: tlsPortError ? "rgb(244, 67, 54)" : (inputStyle.borderColor as string),
                  }}
                  value={tls.port}
                  onChange={(e) => update("tls", "port", parseInt(e.target.value) || 8443)}
                />
                {tlsPortError && (
                  <span style={{ ...helpText, color: "rgb(244, 67, 54)" }}>
                    {tlsPortError}
                  </span>
                )}
              </div>

              <div style={toggleRow}>
                <div>
                  <div style={{ fontSize: "var(--font-size-sm)" }}>Redirect HTTP to HTTPS</div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    Old links to <code>http://&lt;server&gt;:{net.http_port}</code> redirect automatically. Turn off only
                    if a reverse proxy in front of OpenAVC handles HTTP.
                  </div>
                </div>
                <Toggle
                  checked={!!tls.redirect_http}
                  onChange={(v) => update("tls", "redirect_http", v)}
                />
              </div>

              {/* Status block — read-only, populated after restart */}
              {tlsStatus?.enabled && tlsStatus.cert && (
                <div style={{
                  marginTop: "var(--space-md)",
                  padding: "var(--space-md)",
                  background: "var(--bg-elevated)",
                  borderRadius: "var(--border-radius)",
                  fontSize: 12,
                  lineHeight: 1.6,
                }}>
                  <div style={{ ...sectionTitle, marginBottom: "var(--space-sm)" }}>Current certificate</div>
                  <div><strong>Subject:</strong> <code>{tlsStatus.cert.subject}</code></div>
                  <div><strong>Issuer:</strong> <code>{tlsStatus.cert.issuer}</code></div>
                  <div><strong>SHA-256 fingerprint:</strong>{" "}
                    <code style={{ wordBreak: "break-all" }}>{tlsStatus.cert.fingerprint}</code>
                  </div>
                  <div><strong>Valid for:</strong> {tlsStatus.cert.sans.join(", ")}</div>
                  {tlsStatus.cert.warnings.length > 0 && (
                    <div style={{ marginTop: "var(--space-sm)", color: "rgb(255, 152, 0)" }}>
                      <Lock size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
                      {tlsStatus.cert.warnings.map((w) => warningLabel(w)).join("; ")}
                    </div>
                  )}
                </div>
              )}
              {tlsStatus?.enabled && tlsStatus.error && (
                <div style={{ ...helpText, color: "rgb(244, 67, 54)", marginTop: "var(--space-sm)" }}>
                  <Lock size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
                  {tlsStatus.error}
                </div>
              )}

              {/* Panel-device install hint */}
              <div style={{ ...helpText, gridColumn: "1 / -1", marginTop: "var(--space-md)" }}>
                <strong>Panel apps:</strong> the OpenAVC Panel app detects HTTPS automatically via mDNS once both the
                server and the app are updated to a release with HTTPS support. To suppress the browser warning on each
                device, download the CA certificate (button above) and install it on the device.
              </div>
            </>
          )}
        </div>

        {/* Access */}
        <h3 style={sectionTitle}>Access</h3>
        <div style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-md)",
          lineHeight: 1.5,
        }}>
          Access controls are optional. When the server is only accessible locally (bind address <code>127.0.0.1</code>), no credentials are needed.
          When the server is accessible on the network (<code>0.0.0.0</code>), set at least one of the options below to prevent unauthorized access.
          The Panel UI is never protected so end users can always reach it.
        </div>

        <div style={cardStyle}>
          <h4 style={subCardTitle}>Programmer login</h4>
          <div style={subCardDescription}>
            For humans opening the Programmer IDE in a browser. When set, the browser prompts for username and password before showing the IDE.
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Username</label>
            <input
              style={inputStyle}
              type="text"
              autoComplete="off"
              spellCheck={false}
              value={auth.programmer_username}
              placeholder="No username set"
              onChange={(e) => update("auth", "programmer_username", e.target.value)}
            />
            <span style={helpText}>
              Required if a password is set. The browser asks for both username and password.
            </span>
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Password</label>
            <PasswordField
              value={auth.programmer_password}
              placeholder="No password set"
              onChange={(v) => update("auth", "programmer_password", v)}
            />
            <span style={helpText}>
              Set this if anyone else on your network could open the Programmer IDE.
            </span>
          </div>
        </div>

        <div style={cardStyle}>
          <h4 style={subCardTitle}>API key (for integrations)</h4>
          <div style={subCardDescription}>
            For external systems — control scripts, middleware, or other software that connects to the OpenAVC REST API or WebSocket. Not needed unless you are building a custom integration.
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>API key</label>
            <PasswordField
              value={auth.api_key}
              placeholder="No API key set"
              onChange={(v) => update("auth", "api_key", v)}
            />
            <span style={helpText}>
              Provide this to external systems via the <code>X-API-Key</code> header.
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

        {/* Kiosk — only shown on Pi/kiosk-capable deployments */}
        {kioskAvailable && <>
        <h3 style={sectionTitle}>Kiosk</h3>
        <div style={cardStyle}>
          <div style={toggleRow}>
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>Kiosk mode</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Launch the Panel UI fullscreen on an attached display (e.g. Raspberry Pi with HDMI touchscreen).</div>
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
        </>}
      </div>
    </ViewContainer>

    {showRebootDialog && (
      <ConfirmDialog
        title="Reboot Required"
        message="Kiosk display settings require a reboot to take effect. Reboot now?"
        confirmLabel="Reboot Now"
        cancelLabel="Later"
        onConfirm={async () => {
          setShowRebootDialog(false);
          try {
            await api.rebootSystem();
            showSuccess("Device is rebooting...");
          } catch {
            showError("Reboot not available. Restart the device manually.");
          }
        }}
        onCancel={() => {
          setShowRebootDialog(false);
          showSuccess("Settings saved. Reboot the device for changes to take effect.");
        }}
      />
    )}
    </>
  );
}
