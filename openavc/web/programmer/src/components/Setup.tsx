import { useEffect, useRef, useState } from "react";
import { loginWithPassword } from "../api/auth";
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
  const [user, setUser] = useState("admin");
  const [pass, setPass] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // Username is prefilled with "admin", so land the cursor on the password —
    // most people keep the default username and just pick a password.
    passRef.current?.focus();
  }, []);

  // Empty username falls back to "admin" so the login screen's default always
  // matches a click-through setup. Set explicitly so it's never a mystery later.
  const username = user.trim() || "admin";
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
        body: JSON.stringify({ username, password: pass }),
      });
      if (res.ok) {
        // Exchange the just-created credential for a session token. If the
        // mint fails we continue anyway — the first 401 drops the user on
        // the login screen, which is the correct fallback.
        await loginWithPassword(username, pass).catch(() => undefined);
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
          padding: "var(--space-2xl)",
          borderRadius: "var(--radius-lg)",
          background: "var(--bg-surface)",
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-lg)",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: "var(--space-xs)" }}>
          <h2 style={{ margin: 0, fontSize: "var(--font-size-xl)" }}>Set up OpenAVC</h2>
          <p style={{ marginTop: "var(--space-sm)", fontSize: "var(--font-size-base)", opacity: 0.7, lineHeight: "var(--line-tight)" }}>
            Choose an admin username and password. You'll use these to open the
            Programmer. The room panel stays open and never asks for a login.
          </p>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", fontSize: "var(--font-size-base)" }}>
          Admin username
          <input
            type="text"
            value={user}
            onChange={(e) => setUser(e.target.value)}
            autoComplete="username"
            disabled={busy}
            style={inputStyle}
          />
          <span style={{ fontSize: "var(--font-size-sm)", opacity: 0.55 }}>
            Keep "admin" or pick your own. You'll enter this to sign in.
          </span>
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", fontSize: "var(--font-size-base)" }}>
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

        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", fontSize: "var(--font-size-base)" }}>
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
          <div style={{ fontSize: "var(--font-size-sm)", opacity: 0.7 }}>Use at least 8 characters.</div>
        )}
        {mismatch && (
          <div style={{ fontSize: "var(--font-size-base)", color: "#ef4444" }}>Passwords don't match.</div>
        )}
        {error && <div style={{ fontSize: "var(--font-size-base)", color: "#ef4444" }}>{error}</div>}

        <button
          type="submit"
          disabled={!canSubmit}
          style={{
            padding: "var(--space-md) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            border: "none",
            background: canSubmit ? "#8AB493" : "rgba(138,180,147,0.4)",
            color: "#000",
            fontSize: "var(--font-size-lg)",
            fontWeight: "var(--font-weight-semibold)",
            cursor: canSubmit ? "pointer" : "not-allowed",
          }}
        >
          {busy ? "Creating…" : "Create & Continue"}
        </button>

        <p style={{ fontSize: "var(--font-size-sm)", opacity: 0.55, margin: 0, textAlign: "center" }}>
          You can change this later in Settings.
        </p>
      </form>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color, #444)",
  background: "var(--bg-primary, #1a1a2e)",
  color: "inherit",
  fontSize: "var(--font-size-lg)",
  outline: "none",
};
