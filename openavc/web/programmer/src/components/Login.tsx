import { useEffect, useRef, useState } from "react";
import { loginWithPassword } from "../api/auth";

interface LoginProps {
  onSuccess: () => void;
}

export function Login({ onSuccess }: LoginProps) {
  const [user, setUser] = useState("admin");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    passRef.current?.focus();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await loginWithPassword(user, pass);
      if (res.ok) {
        onSuccess();
        return;
      }
      if (res.status === 401) {
        setError("Wrong username or password.");
      } else if (res.status === 429) {
        setError("Too many attempts. Wait a minute and try again.");
      } else {
        setError(`Login failed (${res.status}).`);
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
          width: 320,
          padding: "var(--space-2xl)",
          borderRadius: "var(--radius-lg)",
          background: "var(--bg-surface)",
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-lg)",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: "var(--space-sm)" }}>
          <h2 style={{ margin: 0, fontSize: "var(--font-size-xl)" }}>OpenAVC Programmer</h2>
          <p style={{ marginTop: "var(--space-xs)", fontSize: "var(--font-size-base)", opacity: 0.7 }}>
            Sign in to continue
          </p>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", fontSize: "var(--font-size-base)" }}>
          Username
          <input
            type="text"
            value={user}
            onChange={(e) => setUser(e.target.value)}
            autoComplete="username"
            disabled={busy}
            style={inputStyle}
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", fontSize: "var(--font-size-base)" }}>
          Password
          <input
            ref={passRef}
            type="password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            autoComplete="current-password"
            disabled={busy}
            style={inputStyle}
          />
        </label>

        {error && (
          <div style={{ fontSize: "var(--font-size-base)", color: "#ef4444" }}>{error}</div>
        )}

        <button
          type="submit"
          disabled={busy || !pass}
          style={{
            padding: "var(--space-md) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            border: "none",
            background: busy || !pass ? "rgba(138,180,147,0.4)" : "#8AB493",
            color: "#000",
            fontSize: "var(--font-size-lg)",
            fontWeight: "var(--font-weight-semibold)",
            cursor: busy || !pass ? "not-allowed" : "pointer",
          }}
        >
          {busy ? "Signing in…" : "Sign In"}
        </button>

        <p style={{ fontSize: "var(--font-size-sm)", opacity: 0.55, margin: 0, textAlign: "center" }}>
          Your password is exchanged for a session key kept in this tab only.
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
