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
          padding: 32,
          borderRadius: 8,
          background: "var(--bg-secondary, #232342)",
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 8 }}>
          <h2 style={{ margin: 0, fontSize: 20 }}>OpenAVC Programmer</h2>
          <p style={{ marginTop: 4, fontSize: 13, opacity: 0.7 }}>
            Sign in to continue
          </p>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
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

        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
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
          <div style={{ fontSize: 13, color: "#ef4444" }}>{error}</div>
        )}

        <button
          type="submit"
          disabled={busy || !pass}
          style={{
            padding: "10px 16px",
            borderRadius: 4,
            border: "none",
            background: busy || !pass ? "rgba(138,180,147,0.4)" : "#8AB493",
            color: "#000",
            fontSize: 14,
            fontWeight: 600,
            cursor: busy || !pass ? "not-allowed" : "pointer",
          }}
        >
          {busy ? "Signing in…" : "Sign In"}
        </button>

        <p style={{ fontSize: 12, opacity: 0.55, margin: 0, textAlign: "center" }}>
          Your password is exchanged for a session key kept in this tab only.
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
