import { useState, useEffect } from "react";
import { cloudLogin, getCloudEndpoint } from "../../api/cloudClient";
import { getCloudStatus } from "../../api/restClient";

interface CloudLoginDialogProps {
  onSuccess: () => void;
  onClose: () => void;
}

const inputStyle: React.CSSProperties = {
  padding: "6px 10px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  width: "100%",
  boxSizing: "border-box",
  marginBottom: "var(--space-md)",
};

export function CloudLoginDialog({ onSuccess, onClose }: CloudLoginDialogProps) {
  const [endpoint, setEndpoint] = useState(getCloudEndpoint() || "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [detecting, setDetecting] = useState(false);

  // Auto-detect endpoint from cloud pairing on dialog open
  useEffect(() => {
    let cancelled = false;
    getCloudStatus().then((status) => {
      if (cancelled) return;
      if (status.endpoint) {
        // Convert ws://host:port/agent/v1 → http://host:port
        let url = status.endpoint;
        url = url.replace(/^wss:/, "https:").replace(/^ws:/, "http:");
        url = url.replace(/\/agent\/.*$/, "");
        setEndpoint(url);
      }
    }).catch(console.error);
    return () => { cancelled = true; };
  }, []);

  const detectEndpoint = async () => {
    setDetecting(true);
    try {
      const status = await getCloudStatus();
      if (status.endpoint) {
        let url = status.endpoint;
        url = url.replace(/^wss:/, "https:").replace(/^ws:/, "http:");
        url = url.replace(/\/agent\/.*$/, "");
        setEndpoint(url);
      }
    } catch {
      // Ignore — user can type manually
    }
    setDetecting(false);
  };

  const handleLogin = async () => {
    if (!endpoint || !email || !password) return;
    setLoading(true);
    setError("");
    try {
      await cloudLogin(endpoint, email, password);
      onSuccess();
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xl)",
          minWidth: 380,
          maxWidth: 440,
          boxShadow: "var(--shadow-lg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3
          style={{
            marginBottom: "var(--space-lg)",
            fontSize: "var(--font-size-lg)",
          }}
        >
          Sign in to OpenAVC Cloud
        </h3>
        <p
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            marginBottom: "var(--space-lg)",
          }}
        >
          AI features require a cloud subscription. Sign in with your cloud
          account credentials.
        </p>

        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
          Cloud Server URL
        </label>
        <div style={{ display: "flex", gap: "var(--space-xs)", marginBottom: "var(--space-md)" }}>
          <input
            style={{ ...inputStyle, marginBottom: 0, flex: 1 }}
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="https://cloud.openavc.com"
          />
          <button
            onClick={detectEndpoint}
            disabled={detecting}
            style={{
              padding: "6px 10px",
              fontSize: "var(--font-size-sm)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-hover)",
              color: "var(--text-primary)",
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {detecting ? "..." : "Detect"}
          </button>
        </div>

        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
          Email
        </label>
        <input
          style={inputStyle}
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />

        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
          Password
        </label>
        <input
          style={inputStyle}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          onKeyDown={(e) => e.key === "Enter" && handleLogin()}
        />

        {error && (
          <p style={{ color: "var(--color-error, #e53e3e)", fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
            {error}
          </p>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
          <button
            onClick={onClose}
            style={{
              padding: "6px 16px",
              fontSize: "var(--font-size-sm)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-hover)",
              color: "var(--text-primary)",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleLogin}
            disabled={loading || !endpoint || !email || !password}
            style={{
              padding: "6px 16px",
              fontSize: "var(--font-size-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              cursor: loading ? "not-allowed" : "pointer",
              fontWeight: 500,
            }}
          >
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </div>
      </div>
    </div>
  );
}
