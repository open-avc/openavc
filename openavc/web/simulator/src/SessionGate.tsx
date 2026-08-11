/**
 * Holds the app back until this tab has a credential.
 *
 * Normally invisible: the Programmer opened us, we ask it for its session
 * token, and the app renders. The form below only appears when there is no
 * opener to ask AND this instance actually has a credential — a bookmarked
 * URL, or a reload after the server restarted. That is the same thing
 * `/programmer` does in a fresh tab, and it is the reason the browser's own
 * sign-in dialog never appears: the shell is served openly, so the 401 that
 * would trigger the dialog never happens on a top-level navigation.
 *
 * The "actually has a credential" half is not a detail. An open dev checkout
 * has no password, so the Programmer has no token to pass down, and without
 * the check we would show a password box that nothing can satisfy.
 */

import { useEffect, useState, type ReactNode } from "react";
import { requestTokenFromOpener, signIn, currentToken, credentialRequired } from "./store/session";
import { AUTH_REQUIRED } from "./store/api";

type Phase = "asking" | "ready" | "needs-password";

export function SessionGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>(currentToken() ? "ready" : "asking");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (phase !== "asking") return;
    let cancelled = false;
    requestTokenFromOpener().then(async (token) => {
      if (cancelled) return;
      if (token) { setPhase("ready"); return; }
      // No token to be had. Before asking for a password, find out whether
      // this instance even has one — on an open dev checkout it does not, and
      // the form would be a door with no key.
      const needed = await credentialRequired();
      if (cancelled) return;
      setPhase(needed ? "needs-password" : "ready");
    });
    return () => { cancelled = true; };
  }, [phase]);

  // The server can refuse this tab's credential later — most often a token
  // inherited from the opener that a restart invalidated. Go back to asking
  // rather than leaving a signed-out UI that just looks broken.
  useEffect(() => {
    const onAuthRequired = () => setPhase((p) => (p === "ready" ? "asking" : p));
    window.addEventListener(AUTH_REQUIRED, onAuthRequired);
    return () => window.removeEventListener(AUTH_REQUIRED, onAuthRequired);
  }, []);

  if (phase === "ready") return <>{children}</>;

  if (phase === "asking") {
    return (
      <div style={shell}>
        <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>Connecting…</p>
      </div>
    );
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(username, password);
      setPhase("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={shell}>
      <form onSubmit={submit} style={card}>
        <h2 style={{ margin: "0 0 4px", fontSize: 20, textAlign: "center" }}>Device Simulator</h2>
        <p style={{ margin: "0 0 20px", fontSize: 13, color: "var(--text-secondary)", textAlign: "center" }}>
          Sign in to continue
        </p>
        <label style={label}>Username</label>
        <input
          style={input}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
        />
        <label style={label}>Password</label>
        <input
          style={input}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          autoFocus
        />
        {error && (
          <p style={{ color: "#ef4444", fontSize: 13, margin: "12px 0 0" }}>{error}</p>
        )}
        <button type="submit" style={button} disabled={busy}>
          {busy ? "Signing in…" : "Sign In"}
        </button>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: "14px 0 0", textAlign: "center" }}>
          Opening the simulator from the Programmer signs you in automatically.
        </p>
      </form>
    </div>
  );
}

const shell: React.CSSProperties = {
  position: "fixed", inset: 0, display: "flex",
  alignItems: "center", justifyContent: "center", padding: 24,
};

const card: React.CSSProperties = {
  background: "var(--bg-surface)", border: "1px solid var(--border-color)",
  borderRadius: 8, padding: "32px 36px", width: "100%", maxWidth: 360,
  boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
};

const label: React.CSSProperties = {
  display: "block", fontSize: 13, margin: "12px 0 6px",
};

const input: React.CSSProperties = {
  width: "100%", padding: "9px 12px", borderRadius: 6, fontSize: 14,
  background: "var(--bg-base)", color: "var(--text-primary)",
  border: "1px solid var(--border-color)", boxSizing: "border-box",
};

const button: React.CSSProperties = {
  width: "100%", padding: "10px 12px", marginTop: 20, borderRadius: 6,
  fontSize: 14, fontWeight: 600, cursor: "pointer",
  background: "var(--accent, #6b8f71)", color: "#fff", border: "none",
};
