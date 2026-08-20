import { useState, useEffect, useCallback } from "react";
import { ShieldCheck } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { CopyButton } from "../components/shared/CopyButton";
import { useNavigationStore } from "../store/navigationStore";
import {
  getCloudStatus,
  cloudPair,
  cloudUnpair,
  type CloudStatus,
} from "../api/restClient";

function formatUptime(s: number): string {
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  const h = Math.floor(s / 3600);
  const mn = Math.floor((s % 3600) / 60);
  return `${h}h ${mn}m`;
}

const cardStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-lg)",
  marginBottom: "var(--space-lg)",
};

const labelStyle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

const valueStyle: React.CSSProperties = {
  fontSize: "var(--font-size-base)",
  color: "var(--text-primary)",
  marginBottom: "var(--space-md)",
};

const inputStyle: React.CSSProperties = {
  padding: "6px 10px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  width: "100%",
  boxSizing: "border-box",
};

const btnStyle: React.CSSProperties = {
  padding: "6px 16px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--accent-bg)",
  color: "#fff",
  cursor: "pointer",
  fontWeight: 500,
};

const btnDangerStyle: React.CSSProperties = {
  ...btnStyle,
  background: "#c0392b",
  borderColor: "#c0392b",
};

const statusDotStyle = (connected: boolean): React.CSSProperties => ({
  display: "inline-block",
  width: 10,
  height: 10,
  borderRadius: "50%",
  background: connected ? "#2ecc71" : "#e74c3c",
  marginRight: "var(--space-sm)",
});

const helpTextStyle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-muted)",
  marginTop: "var(--space-xs)",
  lineHeight: 1.5,
};

export function CloudSettingsView() {
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [pairingToken, setPairingToken] = useState("");
  const [cloudApiUrl, setCloudApiUrl] = useState("https://cloud.openavc.com");
  const [pairing, setPairing] = useState(false);
  const [unpairing, setUnpairing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showUnpairConfirm, setShowUnpairConfirm] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const s = await getCloudStatus();
      setStatus(s);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const handlePair = useCallback(async () => {
    if (!pairingToken.trim()) return;
    setPairing(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await cloudPair(pairingToken.trim(), cloudApiUrl.trim());
      setPairingToken("");
      if (result.agent_started === false) {
        // Pairing persisted but the agent didn't connect — show the server's
        // warning instead of an unqualified success.
        setError(result.warning ?? "Paired, but the cloud connection did not start. Check the server logs.");
      } else {
        setSuccess(`Paired successfully! System ID: ${result.system_id}`);
      }
      await fetchStatus();
    } catch (e) {
      setError(String(e));
    } finally {
      setPairing(false);
    }
  }, [pairingToken, cloudApiUrl, fetchStatus]);

  const handleUnpair = useCallback(async () => {
    setShowUnpairConfirm(false);
    setUnpairing(true);
    setError(null);
    setSuccess(null);
    try {
      await cloudUnpair();
      setSuccess("Unpaired successfully.");
      await fetchStatus();
    } catch (e) {
      setError(String(e));
    } finally {
      setUnpairing(false);
    }
  }, [fetchStatus]);

  if (loading) {
    return (
      <ViewContainer title="Cloud Connection">
        <div style={{ color: "var(--text-muted)", padding: "var(--space-lg)" }}>
          Loading...
        </div>
      </ViewContainer>
    );
  }

  const isPaired = status?.enabled && status?.system_id;

  return (
    <ViewContainer title="Cloud Connection">
      <div style={{ maxWidth: 600 }}>
        {/* Status Card */}
        <div style={cardStyle}>
          <div style={labelStyle}>Status</div>
          <div style={{ ...valueStyle, display: "flex", alignItems: "center" }}>
            <span style={statusDotStyle(status?.connected ?? false)} />
            {!isPaired
              ? "Not Configured"
              : status?.connected
                ? "Connected"
                : status?.stop_reason === "version_mismatch"
                  ? "Stopped: needs update"
                  : status?.stop_reason === "auth_failed"
                    ? "Stopped: needs pairing"
                    : "Disconnected"}
          </div>

          {/* The agent only stops when retrying is pointless. Say what happened
              and what fixes it, right where someone would come looking. */}
          {isPaired && !status?.connected && !!status?.stop_detail && (
            <div
              style={{
                marginTop: "var(--space-sm)",
                padding: "var(--space-sm)",
                borderRadius: "var(--radius-sm)",
                background: "rgba(231,76,60,0.08)",
                border: "1px solid rgba(231,76,60,0.25)",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
              }}
            >
              {status.stop_detail}
            </div>
          )}

          {isPaired && (
            <>
              <div style={labelStyle}>System ID</div>
              <div style={{ ...valueStyle, display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                <code style={{ fontSize: "var(--font-size-sm)" }}>
                  {status?.system_id}
                </code>
                {status?.system_id && <CopyButton value={status.system_id} />}
              </div>

              <div style={labelStyle}>Cloud Endpoint</div>
              <div style={{ ...valueStyle, display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                <code style={{ fontSize: "var(--font-size-sm)" }}>
                  {status?.endpoint}
                </code>
                {status?.endpoint && <CopyButton value={status.endpoint} />}
              </div>

              {status?.connected && status?.uptime != null && (
                <>
                  <div style={labelStyle}>Connection Uptime</div>
                  <div style={valueStyle}>
                    {formatUptime(status.uptime)}
                  </div>
                </>
              )}

              {status?.session_id && (
                <>
                  <div style={labelStyle}>Session ID</div>
                  <div style={{ ...valueStyle, display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                    <code style={{ fontSize: "var(--font-size-sm)" }}>
                      {status.session_id}
                    </code>
                    <CopyButton value={status.session_id} />
                  </div>
                </>
              )}
              {status?.last_heartbeat && (
                <>
                  <div style={labelStyle}>Last Heartbeat</div>
                  <div style={valueStyle}>
                    {new Date(status.last_heartbeat).toLocaleTimeString()}
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {/* Messages */}
        {error && (
          <div
            style={{
              ...cardStyle,
              borderColor: "#c0392b",
              color: "#e74c3c",
              background: "rgba(192, 57, 43, 0.1)",
            }}
          >
            {error}
          </div>
        )}
        {success && (
          <div
            style={{
              ...cardStyle,
              borderColor: "#27ae60",
              color: "#2ecc71",
              background: "rgba(39, 174, 96, 0.1)",
            }}
          >
            {success}
          </div>
        )}
        {/* Pairing-success next step: the trusted certificate is the thing a
            fresh pairing unlocks — point at it right at the success moment. */}
        {success?.startsWith("Paired successfully") && (
          <div
            style={{
              ...cardStyle,
              borderColor: "rgba(76, 175, 80, 0.45)",
              background: "rgba(76, 175, 80, 0.06)",
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: "var(--space-xs)", display: "flex", alignItems: "center", gap: 6 }}>
              <ShieldCheck size={16} style={{ color: "rgb(76, 175, 80)" }} />
              Next step: get a trusted certificate
            </div>
            <p style={{ ...helpTextStyle, marginTop: 0 }}>
              Your cloud pairing can now issue this system a real, browser-trusted HTTPS
              certificate, with no more security warnings for anyone on this network, nothing to
              install on devices, and it renews automatically.
            </p>
            <button
              style={{ ...btnStyle, marginTop: "var(--space-sm)" }}
              onClick={() => useNavigationStore.getState().navigateTo("settings")}
            >
              Open Security settings
            </button>
          </div>
        )}

        {/* Pair / Unpair */}
        {!isPaired ? (
          <div style={cardStyle}>
            <h3
              style={{
                fontSize: "var(--font-size-base)",
                fontWeight: 600,
                marginBottom: "var(--space-md)",
              }}
            >
              Pair with OpenAVC Cloud
            </h3>
            <p style={helpTextStyle}>
              Enter the pairing token from your{" "}
              <a
                href="https://cloud.openavc.com"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--accent)" }}
              >
                OpenAVC Cloud
              </a>{" "}
              account. You can generate one at Settings &gt; Systems &gt; Add System.
              Don't have an account?{" "}
              <a
                href="https://cloud.openavc.com"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--accent)" }}
              >
                Sign up free
              </a>.
            </p>

            <div style={{ marginTop: "var(--space-md)" }}>
              <div style={labelStyle}>Pairing Token</div>
              <input
                style={inputStyle}
                type="text"
                placeholder="Paste your pairing token here"
                value={pairingToken}
                onChange={(e) => setPairingToken(e.target.value)}
                disabled={pairing}
              />
            </div>

            <div style={{ marginTop: "var(--space-md)" }}>
              <div style={labelStyle}>Cloud API URL</div>
              <input
                style={inputStyle}
                type="text"
                value={cloudApiUrl}
                onChange={(e) => setCloudApiUrl(e.target.value)}
                disabled={pairing}
              />
              <p style={helpTextStyle}>
                Default is fine for most users. Only change this if you're
                running a self-hosted cloud instance.
              </p>
            </div>

            <div style={{ marginTop: "var(--space-lg)" }}>
              <button
                style={{
                  ...btnStyle,
                  opacity: pairing || !pairingToken.trim() ? 0.5 : 1,
                }}
                onClick={handlePair}
                disabled={pairing || !pairingToken.trim()}
              >
                {pairing ? "Pairing..." : "Pair"}
              </button>
            </div>
          </div>
        ) : (
          <div style={cardStyle}>
            <h3
              style={{
                fontSize: "var(--font-size-base)",
                fontWeight: 600,
                marginBottom: "var(--space-md)",
              }}
            >
              Manage Connection
            </h3>
            <p style={helpTextStyle}>
              This system is paired with OpenAVC Cloud. Unpairing will
              disconnect from the cloud platform and remove the stored
              credentials.
            </p>
            <div style={{ marginTop: "var(--space-lg)" }}>
              <button
                style={{ ...btnDangerStyle, opacity: unpairing ? 0.5 : 1 }}
                onClick={() => setShowUnpairConfirm(true)}
                disabled={unpairing}
              >
                {unpairing ? "Unpairing..." : "Unpair"}
              </button>
            </div>
          </div>
        )}
      </div>
      {showUnpairConfirm && (
        <ConfirmDialog
          title="Unpair from Cloud"
          message="Unpair from cloud? You will need a new pairing token to reconnect."
          confirmLabel="Unpair"
          onConfirm={handleUnpair}
          onCancel={() => setShowUnpairConfirm(false)}
        />
      )}
    </ViewContainer>
  );
}
