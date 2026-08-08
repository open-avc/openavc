import type { DriverAuthDef, DriverDefinition } from "../../api/types";

interface AuthEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the `auth` block — declarative login handshake the runtime runs
 * after socket connect, before on_connect. Today only `type: telnet_login`
 * is implemented (prompt-driven Telnet / SSH banner login). Used by Lutron
 * HomeWorks QS and similar prompt-based devices.
 */
export function AuthEditor({ draft, onUpdate }: AuthEditorProps) {
  const auth = draft.auth;
  const enabled = !!auth;

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };
  const helpStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: "var(--space-xs)",
  };

  const setEnabled = (next: boolean) => {
    if (next) {
      // Sensible defaults the runtime uses anyway. Username/password fields
      // default to "username"/"password" which is what most config_schemas
      // already declare.
      onUpdate({
        auth: {
          type: "telnet_login",
          username_prompt: "login: ",
          password_prompt: "password: ",
          line_ending: "\r\n",
          timeout_seconds: 10,
          skip_if_empty: true,
        },
      });
    } else {
      onUpdate({ auth: undefined });
    }
  };

  const update = (partial: Partial<DriverAuthDef>) => {
    onUpdate({ auth: { ...(auth ?? {}), ...partial } });
  };

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginTop: 0,
          marginBottom: "var(--space-md)",
        }}
      >
        For devices that present a <code>login:</code> / <code>password:</code>{" "}
        prompt over Telnet or SSH after connect (Lutron HomeWorks QS, some
        Cisco gear, legacy serial-over-IP gateways). The runtime watches the
        incoming bytes for the configured prompts and types credentials in
        before any other traffic flows. Most modern AV gear authenticates a
        different way — leave disabled unless your device shows a banner.
      </p>

      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          fontSize: "var(--font-size-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Enable login handshake
      </label>

      {enabled && (
        <div
          style={{
            display: "grid",
            gap: "var(--space-md)",
            padding: "var(--space-md)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-surface)",
          }}
        >
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
            <div>
              <label style={labelStyle}>Username Prompt</label>
              <input
                value={auth?.username_prompt ?? ""}
                onChange={(e) => update({ username_prompt: e.target.value })}
                placeholder="login: "
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Substring the runtime watches for in incoming bytes before
                typing the username.
              </div>
            </div>
            <div>
              <label style={labelStyle}>Password Prompt</label>
              <input
                value={auth?.password_prompt ?? ""}
                onChange={(e) => update({ password_prompt: e.target.value })}
                placeholder="password: "
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Watched after the username is sent. Required.
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
            <div>
              <label style={labelStyle}>Success Pattern</label>
              <input
                value={auth?.success_pattern ?? ""}
                onChange={(e) => update({ success_pattern: e.target.value })}
                placeholder='e.g. "GNET> "'
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Optional. Substring that signals login succeeded — the runtime
                stops watching once it appears.
              </div>
            </div>
            <div>
              <label style={labelStyle}>Failure Pattern</label>
              <input
                value={auth?.failure_pattern ?? ""}
                onChange={(e) => update({ failure_pattern: e.target.value })}
                placeholder='e.g. "Authentication failed"'
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Optional. If seen, the runtime fails the connection
                immediately rather than timing out.
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
            <div>
              <label style={labelStyle}>Username Config Field</label>
              <input
                value={auth?.username_field ?? ""}
                onChange={(e) => update({ username_field: e.target.value })}
                placeholder="username"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Which config key holds the username. Defaults to{" "}
                <code>username</code>.
              </div>
            </div>
            <div>
              <label style={labelStyle}>Password Config Field</label>
              <input
                value={auth?.password_field ?? ""}
                onChange={(e) => update({ password_field: e.target.value })}
                placeholder="password"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Which config key holds the password. Defaults to{" "}
                <code>password</code>.
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-md)" }}>
            <div>
              <label style={labelStyle}>Line Ending</label>
              <select
                value={auth?.line_ending ?? "\r\n"}
                onChange={(e) => update({ line_ending: e.target.value })}
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              >
                <option value={"\r\n"}>CR LF (\r\n)</option>
                <option value={"\r"}>CR (\r)</option>
                <option value={"\n"}>LF (\n)</option>
              </select>
            </div>
            <div>
              <label style={labelStyle}>Timeout (sec)</label>
              <input
                type="number"
                value={auth?.timeout_seconds ?? 10}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  update({ timeout_seconds: Number.isFinite(n) && n > 0 ? n : 10 });
                }}
                min={1}
                style={{ width: "100%" }}
              />
            </div>
            <div>
              <label style={labelStyle}>Skip If Empty</label>
              <select
                value={(auth?.skip_if_empty ?? true) ? "yes" : "no"}
                onChange={(e) =>
                  update({ skip_if_empty: e.target.value === "yes" })
                }
                style={{ width: "100%" }}
              >
                <option value="yes">Yes (skip when no creds)</option>
                <option value="no">No (always run)</option>
              </select>
            </div>
          </div>
          <div style={helpStyle}>
            <strong>Skip If Empty:</strong> when the user hasn't entered a
            username, "Yes" connects without authenticating (useful when the
            same driver handles both authed and unauthed devices). "No" runs
            the handshake unconditionally.
          </div>
        </div>
      )}
    </div>
  );
}
