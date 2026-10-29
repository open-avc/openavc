import { useEffect, useRef, useState } from "react";
import { loginWithPassword } from "../api/auth";

interface LoginProps {
  onSuccess: () => void;
  /** The session ended while the app was open, rather than this being a
   *  first sign-in. */
  expired?: boolean;
  /** The instance started requiring a password while this tab, which never
   *  signed in, was open (a password was just set in Settings). */
  nowRequired?: boolean;
  /** There were unsaved edits in the tab when the sign-in screen came up. */
  unsavedWork?: boolean;
}

/** Where the per-install reset steps are written up. */
export const RESET_GUIDE_URL =
  "https://docs.openavc.com/getting-started/#forgot-the-programmer-password";

export function Login({ onSuccess, expired, nowRequired, unsavedWork }: LoginProps) {
  // Empty, not "admin". The username is whatever the person who claimed this
  // controller chose, and the client has no way to learn it -- a prefill is
  // only ever right for the default, and when it is wrong it is invisible:
  // the correct password typed over the wrong name answers "Wrong username or
  // password" with nothing pointing at the name. An instance claimed without
  // a username accepts any value here, blank included.
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showReset, setShowReset] = useState(false);
  const userRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    userRef.current?.focus();
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
        // An email address in the Username box is almost always an OpenAVC
        // Cloud account typed into the wrong sign-in, so that is the one case
        // that names the cloud.
        setError(
          user.includes("@")
            ? "Wrong username or password. This is the Programmer's own login. Your OpenAVC Cloud account doesn't work here."
            : user.trim()
              ? "Wrong username or password."
              : "Wrong username or password. If a username was set up, enter it too.",
        );
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
            {expired
              ? "Your session ended. Sign in to continue."
              : nowRequired
                ? "A password is now required. Sign in to continue."
                : "Sign in to continue"}
          </p>
          {(expired || nowRequired) && unsavedWork && (
            <p style={{ marginTop: 8, fontSize: 13, color: "#8AB493" }}>
              Your unsaved changes are still open in this tab.
            </p>
          )}
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          Username
          <input
            ref={userRef}
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
          Use the username and password set up for this system.
        </p>
        <button
          type="button"
          onClick={() => setShowReset((v) => !v)}
          aria-expanded={showReset}
          style={{
            background: "none",
            border: "none",
            padding: 0,
            color: "#8AB493",
            fontSize: 12,
            cursor: "pointer",
            alignSelf: "center",
          }}
        >
          Forgot your password?
        </button>
        {showReset && (
          <div style={{ fontSize: 12, lineHeight: 1.5, opacity: 0.85 }}>
            <p style={{ margin: "0 0 6px" }}>On the computer running OpenAVC:</p>
            <p style={{ margin: "0 0 4px" }}>
              <strong>Windows:</strong> right-click the OpenAVC icon by the clock
              and choose Reset Programmer Password.
            </p>
            <p style={{ margin: "0 0 4px" }}>
              <strong>Mac:</strong> click the OpenAVC icon in the menu bar and
              choose Reset Programmer Password.
            </p>
            <p style={{ margin: 0 }}>
              <strong>Raspberry Pi, Linux or Docker:</strong>{" "}
              <a
                href={RESET_GUIDE_URL}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "#8AB493" }}
              >
                follow the steps in the guide
              </a>
              .
            </p>
          </div>
        )}
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
