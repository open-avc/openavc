import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Wifi, Lock, Check } from "lucide-react";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { showError, showSuccess } from "../../store/toastStore";
import { parseApiError } from "../../api/errors";
import * as api from "../../api/restClient";
import type { HostNetworkInterface, HostNetworkStatus, WifiNetwork } from "../../api/restClient";

// This device's own network connection (IP, gateway, DNS, WiFi, hostname).
// Available only where OpenAVC owns the OS (Pi appliance, Linux with
// NetworkManager) — the GET 404s elsewhere and the card hides itself.
// The on-device counterpart of this UI lives on the /setup screen.

const cardStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-lg)",
  marginBottom: "var(--space-xl)",
};

const subTitle: React.CSSProperties = {
  fontSize: "var(--font-size-base)",
  fontWeight: 600,
  color: "var(--text-primary)",
  margin: 0,
  marginBottom: "var(--space-xs)",
};

const description: React.CSSProperties = {
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

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  background: "var(--bg-base)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
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
  background: "var(--accent-bg)",
  color: "var(--text-on-accent)",
  border: "1px solid var(--accent-bg)",
};

const secondaryBtn: React.CSSProperties = {
  ...btnStyle,
  background: "none",
  color: "var(--text-secondary)",
  border: "1px solid var(--border-color)",
};

const monoValue: React.CSSProperties = {
  fontFamily: "var(--font-mono, monospace)",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-primary)",
};

function newProgrammerUrl(addressCidr: string): string {
  const ip = addressCidr.split("/")[0];
  const port = window.location.port ? `:${window.location.port}` : "";
  return `${window.location.protocol}//${ip}${port}/programmer`;
}

function IfaceEditor({
  iface,
  applyMode,
  onApplied,
}: {
  iface: HostNetworkInterface;
  applyMode: "live" | "reboot";
  onApplied: () => void;
}) {
  const cfg = iface.config;
  const [method, setMethod] = useState<"auto" | "manual">(
    cfg?.method === "manual" ? "manual" : "auto"
  );
  const [address, setAddress] = useState(
    cfg?.addresses[0] ?? iface.ip4.addresses[0] ?? ""
  );
  const [gateway, setGateway] = useState(cfg?.gateway ?? iface.ip4.gateway ?? "");
  const [dns, setDns] = useState(
    (cfg?.dns?.length ? cfg.dns : iface.ip4.dns).join(", ")
  );
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<{ warnings: string[] } | null>(null);

  const body = () => ({
    connection: iface.connection as string,
    method,
    address: address.trim() || null,
    gateway: gateway.trim() || null,
    dns: dns.split(",").map((s) => s.trim()).filter(Boolean),
  });

  const apply = async () => {
    setBusy(true);
    try {
      const dryRun = await api.setHostIpv4({ ...body(), confirmed: false });
      setConfirm({ warnings: dryRun.warnings ?? [] });
    } catch (e) {
      showError(parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const applyConfirmed = async () => {
    setConfirm(null);
    setBusy(true);
    try {
      const result = await api.setHostIpv4({ ...body(), confirmed: true });
      if (result.success && result.reboot) {
        // Saved to the device's boot configuration; it is now restarting.
        // Skip the status refresh — the server is going down with it.
        showSuccess(
          `${iface.device}: settings saved. The device is restarting to apply them.`
        );
        return;
      }
      if (result.success) {
        showSuccess(`${iface.device}: network change applied`);
      } else if (result.rolled_back) {
        showError(
          `${iface.device}: the change failed to activate and the previous settings were restored. ${result.error ?? ""}`
        );
      } else {
        showError(`${iface.device}: ${result.error ?? "failed to apply"}`);
      }
      onApplied();
    } catch {
      // The response never arrived — expected when the change moved the
      // address this browser session is connected through.
      showError(
        method === "manual"
          ? `No response from the server. If the address changed, reconnect at ${newProgrammerUrl(address)}`
          : "No response from the server. The address may have changed. Find the device at its new address (or openavc.local)."
      );
    } finally {
      setBusy(false);
    }
  };

  if (!iface.connection) {
    // No editable profile. Say why honestly: a live link without a profile
    // is not a cabling problem.
    const linkUp =
      iface.state === "connected" ||
      iface.state.startsWith("connecting") ||
      iface.ip4.addresses.length > 0;
    return (
      <div style={{ ...description, marginBottom: "var(--space-md)" }}>
        {linkUp
          ? `${iface.device} is connected, but its settings aren't editable from here.`
          : `No connection profile on ${iface.device}. Connect a cable and it will appear here.`}
      </div>
    );
  }

  return (
    <>
      <div style={fieldRow}>
        <label style={labelStyle}>Address mode</label>
        <select
          style={{ ...inputStyle, cursor: "pointer" }}
          value={method}
          onChange={(e) => setMethod(e.target.value as "auto" | "manual")}
        >
          <option value="auto">Automatic (DHCP)</option>
          <option value="manual">Static IP</option>
        </select>
      </div>
      {method === "manual" && (
        <>
          <div style={fieldRow}>
            <label style={labelStyle}>Address</label>
            <input
              style={inputStyle}
              value={address}
              placeholder="192.168.1.50/24"
              onChange={(e) => setAddress(e.target.value)}
            />
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>Gateway</label>
            <input
              style={inputStyle}
              value={gateway}
              placeholder="192.168.1.1"
              onChange={(e) => setGateway(e.target.value)}
            />
          </div>
          <div style={fieldRow}>
            <label style={labelStyle}>DNS servers</label>
            <input
              style={inputStyle}
              value={dns}
              placeholder="8.8.8.8, 1.1.1.1"
              onChange={(e) => setDns(e.target.value)}
            />
          </div>
        </>
      )}
      <button style={btnStyle} disabled={busy} onClick={apply}>
        <Check size={14} />
        Apply to {iface.device}
      </button>
      {applyMode === "reboot" && (
        <div style={{ ...labelStyle, marginTop: "var(--space-sm)" }}>
          Applying a change restarts the device.
        </div>
      )}

      {confirm && (
        <ConfirmDialog
          title={`Change network settings on ${iface.device}?`}
          destructive
          confirmLabel="Apply"
          onCancel={() => setConfirm(null)}
          onConfirm={applyConfirmed}
          message={
            <div>
              {confirm.warnings.map((w) => (
                <p key={w} style={{ marginBottom: "var(--space-sm)" }}>⚠ {w}</p>
              ))}
              <p style={{ marginBottom: "var(--space-sm)" }}>
                If this changes the address you are connected through, the
                Programmer becomes unreachable here.
                {method === "manual" && address.trim() && (
                  <> Reconnect at <code>{newProgrammerUrl(address)}</code>{" "}
                  {applyMode === "reboot"
                    ? "after it restarts (about a minute)."
                    : "after about 10 seconds."}</>
                )}
              </p>
              <p>
                {applyMode === "reboot"
                  ? "The device restarts to apply the change. If the new settings are wrong, fix them from the device's own screen."
                  : "If the new settings fail to activate, the previous configuration is restored automatically."}
              </p>
            </div>
          }
        />
      )}
    </>
  );
}

function WifiPane({ onChanged }: { onChanged: () => void }) {
  const [networks, setNetworks] = useState<WifiNetwork[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [pick, setPick] = useState<WifiNetwork | null>(null);
  const [psk, setPsk] = useState("");
  const [connecting, setConnecting] = useState(false);

  const scan = async () => {
    setScanning(true);
    try {
      const result = await api.scanHostWifi();
      setNetworks(result.networks);
    } catch (e) {
      showError(parseApiError(e));
    } finally {
      setScanning(false);
    }
  };

  const connect = async (network: WifiNetwork, password: string | null) => {
    setConnecting(true);
    try {
      const result = await api.connectHostWifi(network.ssid, password);
      if (result.success) {
        showSuccess(`Connected to ${network.ssid}`);
        setPick(null);
        setPsk("");
        onChanged();
      } else {
        showError(result.error ?? "Connection failed");
      }
    } catch (e) {
      showError(parseApiError(e));
    } finally {
      setConnecting(false);
    }
  };

  return (
    <div style={{ marginTop: "var(--space-lg)" }}>
      <button style={secondaryBtn} disabled={scanning} onClick={scan}>
        <RefreshCw
          size={14}
          style={scanning ? { animation: "spin 1s linear infinite" } : undefined}
        />
        {scanning ? "Scanning…" : "Scan for WiFi networks"}
      </button>
      {networks !== null && networks.length === 0 && (
        <div style={{ ...description, marginTop: "var(--space-sm)" }}>
          No networks found.
        </div>
      )}
      {networks !== null && networks.length > 0 && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          {networks.map((n) => (
            <div
              key={n.ssid}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "var(--space-sm) var(--space-xs)",
                borderBottom: "1px solid var(--border-color)",
                cursor: "pointer",
              }}
              onClick={() => {
                setPick(n);
                setPsk("");
                if (!n.secured) connect(n, null);
              }}
            >
              <span style={monoValue}>
                {n.in_use ? "✓ " : ""}
                {n.ssid}
              </span>
              <span style={{ ...labelStyle, display: "inline-flex", alignItems: "center", gap: 6 }}>
                <Wifi size={14} />
                {n.signal}%
                {n.secured && <Lock size={12} />}
              </span>
            </div>
          ))}
        </div>
      )}
      {pick?.secured && (
        <div style={{ ...fieldRow, marginTop: "var(--space-md)" }}>
          <label style={labelStyle}>Password for {pick.ssid}</label>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <input
              type="password"
              style={{ ...inputStyle, flex: 1 }}
              value={psk}
              autoComplete="new-password"
              onChange={(e) => setPsk(e.target.value)}
            />
            <button
              style={btnStyle}
              disabled={connecting || !psk}
              onClick={() => connect(pick, psk)}
            >
              {connecting ? "Connecting…" : "Connect"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function WifiSection({
  enabled,
  onChanged,
}: {
  enabled: boolean | null | undefined;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const on = enabled === true;

  const toggle = async () => {
    setBusy(true);
    try {
      const result = await api.setHostWifiRadio(!on);
      if (result.success) {
        onChanged();
      } else {
        showError(result.error ?? "Could not change WiFi");
      }
    } catch (e) {
      showError(parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: "var(--space-lg)" }}>
      <div
        style={{
          ...subTitle,
          fontSize: "var(--font-size-sm)",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
        }}
      >
        <Wifi size={14} /> WiFi
        <label
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            marginLeft: "auto",
            fontWeight: 400,
            color: "var(--text-secondary)",
            cursor: busy ? "default" : "pointer",
          }}
        >
          <input type="checkbox" checked={on} disabled={busy} onChange={toggle} />
          {on ? "On" : "Off"}
        </label>
      </div>
      {on ? (
        <WifiPane onChanged={onChanged} />
      ) : (
        <div style={{ ...description, marginTop: "var(--space-sm)" }}>
          WiFi is off. Turn it on to scan for and join networks.
        </div>
      )}
    </div>
  );
}

export function HostNetworkCard() {
  const [status, setStatus] = useState<HostNetworkStatus | null>(null);
  const [hidden, setHidden] = useState(false);
  const [needsPassword, setNeedsPassword] = useState(false);
  const [hostname, setHostname] = useState("");
  const [hostnameBusy, setHostnameBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.getHostNetwork();
      setStatus(result);
      setHostname(result.hostname ?? "");
    } catch (e) {
      // 404 = no backend on this deployment; anything else also hides the
      // card rather than presenting a broken control surface.
      const message = e instanceof Error ? e.message : String(e);
      // 403 is different from missing: the settings are here, this session
      // just cannot change them. Vanishing would read as "not supported".
      if (/^API 403/.test(message)) {
        setNeedsPassword(true);
        return;
      }
      setHidden(true);
      if (!/^API 404/.test(message)) {
        console.warn("Host network status unavailable:", e);
      }
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (needsPassword) {
    return (
      <div style={cardStyle}>
        <h4 style={subTitle}>This Device's Network</h4>
        <p style={{ ...description, marginBottom: 0 }}>
          Changing these settings needs this device's own password. Open the
          Programmer directly and sign in with it, or change them on the
          device's own screen.
        </p>
      </div>
    );
  }

  if (hidden || status === null) return null;

  const saveHostname = async () => {
    setHostnameBusy(true);
    try {
      const result = await api.setHostHostname(hostname.trim());
      if (result.success) {
        showSuccess(
          `Hostname changed. This device is now ${hostname.trim()}.local`
        );
        load();
      } else {
        showError(result.error ?? "Failed to set hostname");
      }
    } catch (e) {
      showError(parseApiError(e));
    } finally {
      setHostnameBusy(false);
    }
  };

  return (
    <div style={cardStyle}>
      <h4 style={subTitle}>This Device's Network</h4>
      <p style={description}>
        The network configuration of the machine OpenAVC runs on. Changing the
        IP address here changes where the Programmer and Panel are reached.
      </p>

      {status.capabilities.hostname && (
        <div style={fieldRow}>
          <label style={labelStyle}>Hostname</label>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <input
              style={{ ...inputStyle, flex: 1 }}
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
            />
            <button
              style={secondaryBtn}
              disabled={hostnameBusy || !hostname.trim() || hostname.trim() === status.hostname}
              onClick={saveHostname}
            >
              Save
            </button>
          </div>
          <span
            style={{
              fontSize: 12,
              color: "var(--text-muted)",
              gridColumn: "2",
              marginTop: -4,
            }}
          >
            Browsers on the network reach this device at{" "}
            <code>{(hostname.trim() || "openavc") + ".local"}</code>.
          </span>
        </div>
      )}

      {status.interfaces.map((iface) => (
        <div
          key={iface.device}
          style={{
            borderTop: "1px solid var(--border-color)",
            paddingTop: "var(--space-md)",
            marginTop: "var(--space-md)",
          }}
        >
          <div style={{ ...subTitle, fontSize: "var(--font-size-sm)" }}>
            {iface.device} ({iface.type}){" "}
            <span style={{ color: "var(--text-secondary)", fontWeight: 400 }}>
              {iface.state}
              {iface.ip4.addresses.length > 0 && ` · ${iface.ip4.addresses.join(", ")}`}
            </span>
          </div>
          {iface.type === "ethernet" && status.capabilities.ipv4 && (
            <div style={{ marginTop: "var(--space-sm)" }}>
              <IfaceEditor
                iface={iface}
                applyMode={status.capabilities.ipv4_apply === "reboot" ? "reboot" : "live"}
                onApplied={load}
              />
            </div>
          )}
        </div>
      ))}

      {status.capabilities.wifi && (
        <WifiSection enabled={status.wifi_enabled} onChanged={load} />
      )}
    </div>
  );
}
