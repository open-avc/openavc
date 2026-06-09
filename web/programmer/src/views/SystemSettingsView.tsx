import { useState, useEffect, useCallback, useRef } from "react";
import { Save, AlertTriangle, Eye, EyeOff, RefreshCw, Download, Lock, Power, Upload, FileCheck2, ChevronDown, ChevronRight, Copy, Smartphone } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { RestartProgressDialog } from "../components/shared/RestartProgressDialog";
import { showError, showSuccess } from "../store/toastStore";
import * as api from "../api/restClient";
import type { SystemConfig, NetworkAdapter, TlsStatus, TlsUploadResult, SshStatus } from "../api/restClient";

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
        autoComplete="new-password"
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

// --- CA install instructions (per-OS) ---
//
// The text below is hand-written from current OS docs (Apple Platform Security
// Guide, Microsoft "Manage trusted root certificates", Google Chrome Help, and
// Mozilla support). Re-check it when shipping a new major OS version.

type CaInstallOs =
  | "windows"
  | "macos"
  | "ios"
  | "android"
  | "linux-chrome"
  | "linux-firefox";

const CA_INSTALL_OS_LABELS: Array<{ id: CaInstallOs; label: string }> = [
  { id: "windows", label: "Windows" },
  { id: "macos", label: "macOS" },
  { id: "ios", label: "iOS (iPhone / iPad)" },
  { id: "android", label: "Android" },
  { id: "linux-chrome", label: "Linux (Chrome / Chromium / Edge)" },
  { id: "linux-firefox", label: "Linux (Firefox)" },
];

const CA_INSTALL_STEPS: Record<CaInstallOs, string[]> = {
  windows: [
    "Double-click the downloaded openavc-ca.crt file.",
    "Click \"Install Certificate…\" and choose \"Local Machine\" (or \"Current User\" if you can't elevate).",
    "Select \"Place all certificates in the following store\", click \"Browse…\", and pick \"Trusted Root Certification Authorities\".",
    "Finish the wizard. Restart any browsers that were already running.",
  ],
  macos: [
    "Double-click the downloaded openavc-ca.crt file to open it in Keychain Access.",
    "Pick the \"System\" keychain (or \"login\" if you only want to trust it for your own user) and add the certificate.",
    "In Keychain Access, find \"OpenAVC Root CA\", double-click it, expand \"Trust\", and set \"When using this certificate\" to \"Always Trust\".",
    "Close the window and authenticate. Restart Safari / Chrome to pick up the new trust setting.",
  ],
  ios: [
    "Email or AirDrop the openavc-ca.crt file to the device, then open it.",
    "iOS will say \"Profile Downloaded\". Open Settings → General → VPN & Device Management and tap the OpenAVC profile to install it.",
    "Open Settings → General → About → Certificate Trust Settings and enable full trust for the OpenAVC root.",
    "Reopen any browser tabs that were already showing a warning.",
  ],
  android: [
    "Copy the openavc-ca.crt file to the device (USB, Drive, or email).",
    "Open Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate → CA certificate.",
    "Acknowledge the warning, tap \"Install anyway\", browse to the file, and confirm.",
    "Some apps only trust user-installed CAs with explicit opt-in. Chrome works out of the box; the OpenAVC Panel app trusts the server automatically and does not need this step.",
  ],
  "linux-chrome": [
    "Open Settings → Privacy and security → Security → Manage certificates → \"Authorities\" tab.",
    "Click \"Import\", select openavc-ca.crt, and confirm.",
    "In the trust dialog, tick \"Trust this certificate for identifying websites\" and click OK.",
    "Restart Chrome / Chromium / Edge.",
  ],
  "linux-firefox": [
    "Open Settings → Privacy & Security, scroll to \"Certificates\", and click \"View Certificates…\".",
    "Switch to the \"Authorities\" tab and click \"Import…\".",
    "Pick openavc-ca.crt and tick \"Trust this CA to identify websites\".",
    "Close the dialog and reload any open OpenAVC tab.",
  ],
};

function detectOs(): CaInstallOs {
  if (typeof navigator === "undefined") return "windows";
  const ua = navigator.userAgent;
  if (/iPad|iPhone|iPod/i.test(ua)) return "ios";
  if (/Android/i.test(ua)) return "android";
  if (/Macintosh|Mac OS X/i.test(ua)) return "macos";
  if (/Linux/i.test(ua)) {
    if (/Firefox/i.test(ua)) return "linux-firefox";
    return "linux-chrome";
  }
  return "windows";
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
  const [showRestartDialog, setShowRestartDialog] = useState(false);
  const [showRestartPrompt, setShowRestartPrompt] = useState(false);
  // TLS provided-mode cert upload state.
  const [pickedCert, setPickedCert] = useState<File | null>(null);
  const [pickedKey, setPickedKey] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<TlsUploadResult | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteCert, setPasteCert] = useState("");
  const [pasteKey, setPasteKey] = useState("");
  const certInputRef = useRef<HTMLInputElement>(null);
  const keyInputRef = useRef<HTMLInputElement>(null);
  const [kioskAvailable, setKioskAvailable] = useState(false);
  const [ssh, setSsh] = useState<SshStatus | null>(null);
  const [sshBusy, setSshBusy] = useState(false);
  const [adapters, setAdapters] = useState<NetworkAdapter[]>([]);
  const [adaptersLoading, setAdaptersLoading] = useState(false);
  const [tlsStatus, setTlsStatus] = useState<TlsStatus | null>(null);
  const [installOs, setInstallOs] = useState<CaInstallOs>(() => detectOs());
  const [installStepsOpen, setInstallStepsOpen] = useState(false);
  const [fingerprintCopied, setFingerprintCopied] = useState(false);

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
    api.getSshStatus().then(setSsh).catch(() => setSsh(null));
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

    // Capture the restart flag before the post-save reset, so we can decide
    // whether to pop the confirm prompt after the request returns.
    const needsRestart = restartNeeded;

    setSaving(true);
    try {
      await api.updateSystemConfig(payload as Partial<SystemConfig>);
      showSuccess("Settings saved" + (needsRestart ? "." : "."));
      // Reload config + tls status to get fresh state
      const fresh = await api.getSystemConfig();
      setConfig(fresh);
      setDirty({});
      loadTlsStatus();
      if (hasKioskChanges && kioskAvailable) {
        // Kiosk-reboot dialog handles its own flow; don't stack a second prompt.
        setShowRebootDialog(true);
      } else if (needsRestart) {
        setShowRestartPrompt(true);
      }
    } catch (e) {
      showError("Failed to save: " + String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleSshToggle = async (enabled: boolean) => {
    if (sshBusy) return;
    setSshBusy(true);
    try {
      const r = await api.setSsh(enabled);
      if (r.ok) {
        setSsh((s) => (s ? { ...s, enabled } : s));
        showSuccess(enabled ? "SSH enabled." : "SSH disabled.");
      } else if (r.pending) {
        showSuccess("SSH change submitted. Confirming...");
        const fresh = await api.getSshStatus().catch(() => null);
        if (fresh) setSsh(fresh);
      } else {
        showError("SSH change failed: " + (r.error || "unknown error"));
      }
    } catch (e) {
      showError("SSH change failed: " + String(e));
    } finally {
      setSshBusy(false);
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

  // Upload either the picked files (file-picker path) or the pasted PEM text
  // (paste-expander path). Server writes them to data_dir/tls/, returns the
  // paths, and we wire those into the unsaved config so the next Save points
  // tls.cert_file / tls.key_file at the new files.
  const handleUploadCert = async (cert: File, key: File) => {
    setUploading(true);
    try {
      const result = await api.uploadTlsCert(cert, key);
      setUploadResult(result);
      // Auto-fill the path fields. Marks dirty so the save button enables.
      update("tls", "cert_file", result.cert_path);
      update("tls", "key_file", result.key_path);
      // Reset picker state.
      setPickedCert(null);
      setPickedKey(null);
      setPasteCert("");
      setPasteKey("");
      setPasteOpen(false);
      if (certInputRef.current) certInputRef.current.value = "";
      if (keyInputRef.current) keyInputRef.current.value = "";
      showSuccess(
        "Certificate uploaded. Save and restart to apply." +
          (result.warnings.includes("is-ca-cert")
            ? " Warning: this looks like a CA cert, not a server cert — browsers may not trust it."
            : ""),
      );
    } catch (e) {
      showError(String(e instanceof Error ? e.message : e));
    } finally {
      setUploading(false);
    }
  };

  const handleUploadFromPickers = () => {
    if (!pickedCert || !pickedKey) return;
    void handleUploadCert(pickedCert, pickedKey);
  };

  const handleUploadFromPaste = () => {
    if (!pasteCert.trim() || !pasteKey.trim()) return;
    const certFile = new File([pasteCert], "cert.pem", { type: "application/x-pem-file" });
    const keyFile = new File([pasteKey], "key.pem", { type: "application/x-pem-file" });
    void handleUploadCert(certFile, keyFile);
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
        {/* Restart warning + action.
            - hasDirty: settings unsaved; user has to save first.
            - !hasDirty: settings saved; offer to restart in-app. */}
        {restartNeeded && (
          <div style={warningBox}>
            <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)", flexWrap: "wrap", flex: 1 }}>
              <span style={{ flex: 1 }}>
                {hasDirty
                  ? "Network or security changes need a restart. Save first, then restart from here."
                  : "Saved. Restart the server to apply network or security changes."}
              </span>
              {!hasDirty && (
                <button
                  onClick={() => setShowRestartDialog(true)}
                  style={{
                    ...btnStyle,
                    background: "rgb(255, 152, 0)",
                    color: "#fff",
                    border: "none",
                  }}
                >
                  <Power size={14} />
                  <span>Restart now</span>
                </button>
              )}
            </div>
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
                  {/* Panel-app banner — only relevant when the server is actually
                      running in auto-generate TLS mode. The browser-side card
                      below is what desktop / iOS users need to install on their
                      device. */}
                  {tlsStatus?.enabled && tlsStatus.mode === "auto" && (
                    <div style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "var(--space-sm)",
                      padding: "var(--space-md)",
                      marginBottom: "var(--space-md)",
                      background: "rgba(76, 175, 80, 0.08)",
                      border: "1px solid rgba(76, 175, 80, 0.3)",
                      borderRadius: "var(--border-radius)",
                      fontSize: "var(--font-size-sm)",
                      color: "var(--text-primary)",
                    }}>
                      <Smartphone size={16} style={{ color: "rgb(76, 175, 80)", flexShrink: 0, marginTop: 2 }} />
                      <span>
                        The OpenAVC Panel app (Android v0.1.0-rc6 or newer) trusts this server automatically — no
                        certificate install needed. The instructions below are for web browsers and the iOS panel app.
                      </span>
                    </div>
                  )}

                  {/* Fingerprint verification — lets a paranoid integrator confirm
                      the CA being downloaded matches the one the server is actually
                      serving, before they install it on devices. */}
                  {tlsStatus?.enabled && tlsStatus.cert && (
                    <div style={{
                      padding: "var(--space-md)",
                      marginBottom: "var(--space-md)",
                      background: "var(--bg-elevated)",
                      border: "1px solid var(--border-color)",
                      borderRadius: "var(--border-radius)",
                    }}>
                      <div style={{ ...subCardTitle, marginBottom: "var(--space-xs)" }}>Verify fingerprint</div>
                      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-sm)", lineHeight: 1.5 }}>
                        When you install this certificate on a device, the device will show this same SHA-256
                        fingerprint. They should match exactly.
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
                        <code style={{
                          fontFamily: "var(--font-mono, monospace)",
                          fontSize: 12,
                          padding: "var(--space-xs) var(--space-sm)",
                          background: "var(--bg-surface)",
                          border: "1px solid var(--border-color)",
                          borderRadius: 4,
                          wordBreak: "break-all",
                          flex: 1,
                          minWidth: 240,
                        }}>
                          {tlsStatus.cert.fingerprint}
                        </code>
                        <button
                          type="button"
                          onClick={() => {
                            navigator.clipboard.writeText(tlsStatus.cert!.fingerprint).then(() => {
                              setFingerprintCopied(true);
                              setTimeout(() => setFingerprintCopied(false), 1500);
                            }).catch(() => showError("Couldn't copy to clipboard"));
                          }}
                          style={{
                            ...btnStyle,
                            background: "var(--bg-surface)",
                            color: "var(--text-primary)",
                            border: "1px solid var(--border-color)",
                          }}
                        >
                          <Copy size={14} />
                          <span>{fingerprintCopied ? "Copied" : "Copy"}</span>
                        </button>
                      </div>
                    </div>
                  )}

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
                  </div>

                  {/* OS picker + collapsible step-by-step. Lets browser / iOS users
                      see the right install path for their device without us trying
                      to pretend the whole flow is one-click. */}
                  {tlsStatus?.enabled && tlsStatus.mode === "auto" && (
                    <div style={fieldRow}>
                      <label style={labelStyle} htmlFor="ca-install-os">Install on</label>
                      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
                        <select
                          id="ca-install-os"
                          value={installOs}
                          onChange={(e) => setInstallOs(e.target.value as CaInstallOs)}
                          style={selectStyle}
                        >
                          {CA_INSTALL_OS_LABELS.map((opt) => (
                            <option key={opt.id} value={opt.id}>{opt.label}</option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={() => setInstallStepsOpen((v) => !v)}
                          style={{
                            ...btnStyle,
                            background: "transparent",
                            color: "var(--text-primary)",
                            border: "1px solid var(--border-color)",
                            justifyContent: "flex-start",
                            marginTop: "var(--space-xs)",
                          }}
                        >
                          {installStepsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          <span>How to install</span>
                        </button>
                        {installStepsOpen && (
                          <ol style={{
                            marginTop: "var(--space-sm)",
                            padding: "var(--space-md) var(--space-md) var(--space-md) var(--space-xl)",
                            background: "var(--bg-elevated)",
                            border: "1px solid var(--border-color)",
                            borderRadius: "var(--border-radius)",
                            fontSize: "var(--font-size-sm)",
                            lineHeight: 1.6,
                            color: "var(--text-primary)",
                          }}>
                            {CA_INSTALL_STEPS[installOs].map((step, i) => (
                              <li key={i} style={{ marginBottom: "var(--space-xs)" }}>{step}</li>
                            ))}
                          </ol>
                        )}
                      </div>
                    </div>
                  )}
                </>
              )}

              {tlsCertMode === "provided" && (
                <>
                  {/* Active-cert card: shows either the just-uploaded result or
                      the previously-saved provided cert (from tls-status). */}
                  {(uploadResult || (tlsStatus?.enabled && tlsStatus.mode === "provided" && tlsStatus.cert)) && (
                    <div style={{
                      padding: "var(--space-md)",
                      background: "var(--bg-elevated)",
                      borderRadius: "var(--border-radius)",
                      marginBottom: "var(--space-md)",
                      border: "1px solid var(--border-color)",
                    }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: "var(--space-xs)" }}>
                        <FileCheck2 size={16} style={{ color: "rgb(76, 175, 80)" }} />
                        <strong style={{ fontSize: "var(--font-size-sm)" }}>
                          {uploadResult ? "Certificate uploaded" : "Active certificate"}
                        </strong>
                      </div>
                      <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6 }}>
                        <div>
                          Valid until <strong>
                            {new Date((uploadResult?.expires_at) ?? (tlsStatus?.cert?.expires_at ?? "")).toLocaleDateString()}
                          </strong>
                          {" "}
                          <ExpiryBadge days={(uploadResult?.days_until_expiry) ?? (tlsStatus?.cert?.days_until_expiry ?? 0)} />
                        </div>
                        <div><strong>Subject:</strong> <code>{uploadResult?.subject ?? tlsStatus?.cert?.subject}</code></div>
                        <div style={{ wordBreak: "break-all" }}>
                          <strong>SHA-256:</strong> <code>{uploadResult?.fingerprint ?? tlsStatus?.cert?.fingerprint}</code>
                        </div>
                        <div><strong>Valid for:</strong> {(uploadResult?.sans ?? tlsStatus?.cert?.sans ?? []).join(", ") || "—"}</div>
                        {uploadResult?.warnings.includes("is-ca-cert") && (
                          <div style={{ marginTop: "var(--space-xs)", color: "rgb(255, 152, 0)" }}>
                            <AlertTriangle size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
                            This looks like a CA certificate, not a server certificate. Most browsers won't trust it.
                          </div>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setUploadResult(null);
                          update("tls", "cert_file", "");
                          update("tls", "key_file", "");
                          setPickedCert(null);
                          setPickedKey(null);
                        }}
                        style={{
                          ...btnStyle,
                          background: "transparent",
                          border: "1px solid var(--border-color)",
                          color: "var(--text-primary)",
                          marginTop: "var(--space-sm)",
                        }}
                      >
                        Replace certificate
                      </button>
                    </div>
                  )}

                  {/* Picker UI: hidden when an upload result is showing.
                      "Replace certificate" above puts us back here. */}
                  {!uploadResult && !(tls.cert_file && tls.key_file) && (
                    <>
                      <div style={fieldRow}>
                        <label style={labelStyle}>Certificate</label>
                        <div>
                          <input
                            ref={certInputRef}
                            type="file"
                            accept=".pem,.crt,.cer,application/x-pem-file,application/x-x509-ca-cert"
                            onChange={(e) => setPickedCert(e.target.files?.[0] ?? null)}
                            style={{ fontSize: "var(--font-size-sm)" }}
                          />
                          {pickedCert && (
                            <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
                              Selected: <code>{pickedCert.name}</code> ({Math.ceil(pickedCert.size / 100) / 10} KB)
                            </div>
                          )}
                        </div>
                      </div>
                      <div style={fieldRow}>
                        <label style={labelStyle}>Private key</label>
                        <div>
                          <input
                            ref={keyInputRef}
                            type="file"
                            accept=".pem,.key,application/x-pem-file"
                            onChange={(e) => setPickedKey(e.target.files?.[0] ?? null)}
                            style={{ fontSize: "var(--font-size-sm)" }}
                          />
                          {pickedKey && (
                            <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
                              Selected: <code>{pickedKey.name}</code> ({Math.ceil(pickedKey.size / 100) / 10} KB)
                            </div>
                          )}
                        </div>
                        <span style={helpText}>
                          The private key matching the certificate above. PEM format, no passphrase.
                          Files are written to the server's data directory; you don't need filesystem access.
                        </span>
                      </div>
                      <div style={fieldRow}>
                        <div />
                        <button
                          type="button"
                          onClick={handleUploadFromPickers}
                          disabled={!pickedCert || !pickedKey || uploading}
                          style={{
                            ...btnStyle,
                            opacity: !pickedCert || !pickedKey || uploading ? 0.5 : 1,
                            alignSelf: "flex-start",
                          }}
                        >
                          <Upload size={14} />
                          <span>{uploading ? "Uploading..." : "Upload certificate"}</span>
                        </button>
                      </div>

                      {/* Paste-PEM expander for users whose cert arrived in an email. */}
                      <div style={{ marginTop: "var(--space-md)" }}>
                        <button
                          type="button"
                          onClick={() => setPasteOpen((v) => !v)}
                          style={{
                            background: "none",
                            border: "none",
                            color: "var(--text-secondary)",
                            cursor: "pointer",
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            padding: 0,
                            fontSize: "var(--font-size-sm)",
                          }}
                        >
                          {pasteOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          <span>Or paste PEM contents</span>
                        </button>
                        {pasteOpen && (
                          <div style={{ marginTop: "var(--space-sm)", display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
                            <textarea
                              placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                              value={pasteCert}
                              onChange={(e) => setPasteCert(e.target.value)}
                              rows={6}
                              style={{ ...inputStyle, fontFamily: "monospace", fontSize: 12, resize: "vertical" }}
                            />
                            <textarea
                              placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
                              value={pasteKey}
                              onChange={(e) => setPasteKey(e.target.value)}
                              rows={6}
                              style={{ ...inputStyle, fontFamily: "monospace", fontSize: 12, resize: "vertical" }}
                            />
                            <button
                              type="button"
                              onClick={handleUploadFromPaste}
                              disabled={!pasteCert.trim() || !pasteKey.trim() || uploading}
                              style={{
                                ...btnStyle,
                                opacity: !pasteCert.trim() || !pasteKey.trim() || uploading ? 0.5 : 1,
                                alignSelf: "flex-start",
                              }}
                            >
                              <Upload size={14} />
                              <span>{uploading ? "Uploading..." : "Use pasted certificate"}</span>
                            </button>
                          </div>
                        )}
                      </div>
                    </>
                  )}

                  {tlsProvidedBlank && !uploadResult && (
                    <div style={{ ...helpText, color: "rgb(244, 67, 54)", marginTop: "var(--space-sm)" }}>
                      Upload a certificate before saving.
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
            For humans opening the Programmer IDE in a browser. When a password is set, the Programmer shows its own sign-in screen first. The room panel stays open and is never protected.
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
              Paired with the password on the Programmer sign-in screen. Defaults to admin; leave blank to accept any username.
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
              {ssh?.supported && " On this controller it is also the SSH and console login for the openavc user."}
            </span>
          </div>
        </div>

        {ssh?.supported && (
          <div style={cardStyle}>
            <h4 style={subCardTitle}>SSH access</h4>
            <div style={subCardDescription}>
              Remote command-line login to this controller. Off by default. When on, log in as user <code>openavc</code> with your admin password.
            </div>
            <div style={toggleRow}>
              <div>
                <div style={{ fontSize: "var(--font-size-sm)" }}>Enable SSH</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {sshBusy
                    ? "Applying..."
                    : ssh.enabled === null
                    ? "Current state unknown"
                    : ssh.enabled
                    ? "SSH is on"
                    : "SSH is off"}
                </div>
              </div>
              <Toggle checked={!!ssh.enabled} onChange={handleSshToggle} />
            </div>
          </div>
        )}

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

    {showRestartPrompt && (
      <ConfirmDialog
        title="Restart to apply changes?"
        message={
          <>
            Your network or security changes need a server restart to take effect.
            The Programmer will reconnect automatically once the server is back —
            usually about ten seconds.
            <br /><br />
            You can also keep working and restart later from the banner at the top of this page.
          </>
        }
        confirmLabel="Restart now"
        cancelLabel="Later"
        onConfirm={() => {
          setShowRestartPrompt(false);
          setShowRestartDialog(true);
        }}
        onCancel={() => setShowRestartPrompt(false)}
      />
    )}

    {showRestartDialog && (() => {
      // Target URL is derived from the SAVED config (not the unsaved `dirty`
      // state). The Restart button is only enabled when !hasDirty, so config
      // reflects what the post-restart server will actually serve.
      const targetScheme = config?.tls?.enabled ? "https" : "http";
      const targetPort = config?.tls?.enabled
        ? (config.tls.port ?? 8443)
        : (config?.network?.http_port ?? 8080);
      const targetUrl = `${targetScheme}://${window.location.hostname}:${targetPort}/programmer`;
      const currentScheme = window.location.protocol === "https:" ? "https" : "http";
      const isProtocolSwitch = currentScheme !== targetScheme;
      const expectsNewCert = currentScheme === "http" && targetScheme === "https";
      return (
        <RestartProgressDialog
          targetUrl={targetUrl}
          isProtocolSwitch={isProtocolSwitch}
          expectsNewCert={expectsNewCert}
          onClose={() => {
            setShowRestartDialog(false);
            // Clear restartNeeded only if the dialog reported success — but
            // since the success path navigates away before the user can close,
            // a manual close means the user is bailing out. Leave the banner
            // up so they can try again.
          }}
        />
      );
    })()}
    </>
  );
}
