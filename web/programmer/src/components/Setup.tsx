import { useEffect, useRef, useState } from "react";
import { setStoredAuth } from "../api/auth";
import { getTunnelPrefix } from "../api/base";

interface SetupProps {
  /** Called after the admin password is created and the SPA is authenticated. */
  onComplete: () => void;
}

/**
 * First-run claim screen. A fresh shipped controller has no admin credential
 * and is "unclaimed"; this lets the first person set one. The room panel stays
 * open the whole time — only the Programmer needs this.
 */
export function Setup({ onComplete }: SetupProps) {
  const [pass, setPass] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    passRef.current?.focus();
  }, []);

  const tooShort = pass.length > 0 && pass.length < 8;
  const mismatch = confirm.length > 0 && pass !== confirm;
  const canSubmit = pass.length >= 8 && pass === confirm && !busy;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${getTunnelPrefix()}/api/auth/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pass }),
      });
      if (res.ok) {
        // No username is configured, so any username pairs with the password.
        setStoredAuth("admin", pass);
        onComplete();
        return;
      }
      if (res.status === 409) {
        setError("This controller was just set up by someone else. Reload to log in.");
      } else if (res.status === 400) {
        setError("Password must be at least 8 characters.");
      } else {
        setError(`Setup failed (${res.status}).`);
      }
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        height: "100vh",
        width: "100vw",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-primary, #1a1a2e)",
        color: "var(--text-primary, #fff)",
      }}
    >
      <form
        onSubmit={submit}
        style={{
          width: 340,
          padding: 32,
          borderRadius: 8,
          background: "var(--bg-secondary, #232342)",
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 4 }}>
          <h2 style={{ margin: 0, fontSize: 20 }}>Set up OpenAVC</h2>
          <p style={{ marginTop: 6, fontSize: 13, opacity: 0.7, lineHeight: 1.4 }}>
            Create an admin password to protect this controller. The room panel
            stays open — only the Programmer needs this.
          </p>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          New password
          <input
            ref={passRef}
            type="password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            autoComplete="new-password"
            disabled={busy}
            style={inputStyle}
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          Confirm password
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            disabled={busy}
            style={inputStyle}
          />
        </label>

        {tooShort && (
          <div style={{ fontSize: 12, opacity: 0.7 }}>Use at least 8 characters.</div>
        )}
        {mismatch && (
          <div style={{ fontSize: 13, color: "#ef4444" }}>Passwords don't match.</div>
        )}
        {error && <div style={{ fontSize: 13, color: "#ef4444" }}>{error}</div>}

        <button
          type="submit"
          disabled={!canSubmit}
          style={{
            padding: "10px 16px",
            borderRadius: 4,
            border: "none",
            background: canSubmit ? "#8AB493" : "rgba(138,180,147,0.4)",
            color: "#000",
            fontSize: 14,
            fontWeight: 600,
            cursor: canSubmit ? "pointer" : "not-allowed",
          }}
        >
          {busy ? "Creating…" : "Create & Continue"}
        </button>

        <p style={{ fontSize: 12, opacity: 0.55, margin: 0, textAlign: "center" }}>
          You can change this later in Settings.
        </p>
      </form>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 4,
  border: "1px solid var(--border-color, #444)",
  background: "var(--bg-primary, #1a1a2e)",
  color: "inherit",
  fontSize: 14,
  outline: "none",
};
