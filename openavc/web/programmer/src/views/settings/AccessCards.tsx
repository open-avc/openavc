/** Settings > Access: who can sign in and open what. The Programmer login, SSH
 *  on a controller that offers it, the API key for integrations, and who can
 *  open the panel; and the warning at the top of the page while nothing asks
 *  for a credential. The rules that refuse Save over these fields are
 *  saveChecks.ts's; these cards say them. SSH applies at once, with no Save. */
import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { AlertTriangle, Copy } from "lucide-react";
import * as api from "../../api/restClient";
import { panelAccessMode } from "../../api/panelDevicesClient";
import type { SshStatus, SystemConfig } from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import { PASSWORD_TOO_SHORT } from "../../api/auth";
import { copyToClipboard } from "../../components/shared/clipboard";
import { EnvLock, HeldNote, heldSetting, type EnvHold } from "../../components/system/EnvHeldNote";
import {
  btnStyle,
  cardStyle,
  helpText,
  inputStyle,
  radioInput,
  radioRow,
  subCardDescription,
  subCardTitle,
  warningBox,
} from "../../components/system/settingsStyles";
import { showError, showSuccess } from "../../store/toastStore";
import { FieldRow, PasswordField, REDACTED, SwitchRow } from "./settingsControls";
import type { SettingsDraft } from "./settingsDraft";

type Auth = SystemConfig["auth"];

/** Nothing asks for a credential: no password and no API key, on screen or
 *  stored (a stored one comes back as REDACTED, which is set). */
function hasNoCredential(auth: Auth): boolean {
  return !auth.programmer_password && !auth.api_key;
}

/** Shown while something on the network can connect and nothing asks for a
 *  credential. */
export function NoCredentialWarning({ auth, reachesNetwork }: { auth: Auth; reachesNetwork: boolean }) {
  if (!hasNoCredential(auth) || !reachesNetwork) return null;
  return (
    <div style={warningBox}>
      <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
      <span>The server is accessible on the network with no authentication. Anyone on your network can open the Programmer IDE and modify your project. Set a <strong>programmer login</strong> below to require credentials.</span>
    </div>
  );
}

/** SSH on this controller: whether it can be changed here, whether it is on,
 *  and turning it on or off. Read when Settings opens; a change applies at
 *  once and says how it went. */
export function useSshAccess() {
  const [status, setStatus] = useState<SshStatus | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getSshStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  const toggle = async (enabled: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      await settleSshChange(await api.setSsh(enabled), enabled, setStatus);
    } catch (e) {
      showError("SSH change failed: " + parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return { status, busy, toggle };
}

/** The server's answer to turning SSH on or off: shown, and said. A change
 *  still being confirmed is read again; a failed read keeps what is shown. */
async function settleSshChange(
  answer: Awaited<ReturnType<typeof api.setSsh>>,
  enabled: boolean,
  setStatus: Dispatch<SetStateAction<SshStatus | null>>,
) {
  if (answer.success) {
    setStatus((s) => (s ? { ...s, enabled } : s));
    showSuccess(enabled ? "SSH enabled." : "SSH disabled.");
  } else if (answer.pending) {
    showSuccess("SSH change submitted. Confirming...");
    const fresh = await api.getSshStatus().catch(() => null);
    if (fresh) setStatus(fresh);
  } else {
    showError("SSH change failed: " + (answer.error || "unknown error"));
  }
}

export type SshAccess = ReturnType<typeof useSshAccess>;

/** What Save would refuse over these fields (saveChecks.ts), said under them. */
export interface AccessProblems {
  passwordTooShort: boolean;
  apiKeyNeedsPassword: boolean;
}

export function AccessCards({ draft, hold, ssh, problems }: {
  draft: SettingsDraft;
  hold: EnvHold;
  ssh: SshAccess;
  problems: AccessProblems;
}) {
  return (
    <>
      <div style={{
        fontSize: "var(--font-size-sm)",
        color: "var(--text-secondary)",
        marginBottom: "var(--space-md)",
        lineHeight: 1.5,
      }}>
        A system installed from a package always asks for the admin password before the Programmer opens.
        A system run from source opens without one until a password is set below.
        Who can open the panel is set below under Panel access.
      </div>
      <ProgrammerLoginCard draft={draft} hold={hold} sshSupported={!!ssh.status?.supported} passwordTooShort={problems.passwordTooShort} />
      {ssh.status?.supported && <SshCard ssh={ssh} />}
      <ApiKeyCard draft={draft} hold={hold} needsPassword={problems.apiKeyNeedsPassword} />
      <PanelAccessCard draft={draft} hold={hold} />
    </>
  );
}

function ProgrammerLoginCard({ draft, hold, sshSupported, passwordTooShort }: {
  draft: SettingsDraft;
  hold: EnvHold;
  sshSupported: boolean;
  passwordTooShort: boolean;
}) {
  const auth = draft.merged("auth");
  return (
    <div style={cardStyle}>
      <h4 style={subCardTitle}>Programmer login</h4>
      <div style={subCardDescription}>
        For humans opening the Programmer IDE in a browser. When a password is set, the Programmer shows its own sign-in screen first. Who can open the panel is set below under Panel access.
      </div>
      <FieldRow
        label="Username"
        held={heldSetting(hold, "auth", "programmer_username")}
        help="Paired with the password on the Programmer sign-in screen. Defaults to admin; leave blank to accept any username."
      >
        <input
          style={inputStyle}
          type="text"
          autoComplete="off"
          spellCheck={false}
          value={auth.programmer_username}
          placeholder="No username set"
          onChange={(e) => draft.update("auth", "programmer_username", e.target.value)}
        />
      </FieldRow>
      <FieldRow
        label="Password"
        held={heldSetting(hold, "auth", "programmer_password")}
        help={<>
          Set this if anyone else on your network could open the Programmer IDE.
          {sshSupported && " On this controller it is also the SSH and console login for the openavc user."}
        </>}
        after={passwordTooShort && (
          <span style={{ ...helpText, color: "rgb(244, 67, 54)" }}>
            {PASSWORD_TOO_SHORT}
          </span>
        )}
      >
        <PasswordField
          value={auth.programmer_password}
          placeholder="No password set"
          onChange={(v) => draft.update("auth", "programmer_password", v)}
        />
      </FieldRow>
    </div>
  );
}

function SshCard({ ssh }: { ssh: SshAccess }) {
  const enabled = ssh.status?.enabled;
  return (
    <div style={cardStyle}>
      <h4 style={subCardTitle}>SSH access</h4>
      <div style={subCardDescription}>
        Remote command-line login to this controller. Off by default. When on, log in as user <code>openavc</code> with your admin password.
      </div>
      <SwitchRow
        title="Enable SSH"
        help={sshLine(ssh.busy, enabled)}
        checked={!!enabled}
        onChange={ssh.toggle}
      />
    </div>
  );
}

function sshLine(busy: boolean, enabled: boolean | null | undefined): string {
  if (busy) return "Applying...";
  if (enabled === null) return "Current state unknown";
  return enabled ? "SSH is on" : "SSH is off";
}

/** A new API key: 32 random bytes, url-safe, the same shape the server would
 *  produce. The server stores only a digest of whatever is saved here, so this
 *  is the one moment the key exists in readable form -- and its length is what
 *  makes a key safe to store that way. */
function generateApiKey(): string {
  const raw = new Uint8Array(32);
  crypto.getRandomValues(raw);
  return btoa(String.fromCharCode(...raw))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function ApiKeyCard({ draft, hold, needsPassword }: { draft: SettingsDraft; hold: EnvHold; needsPassword: boolean }) {
  const key = draft.merged("auth").api_key;
  return (
    <div style={cardStyle}>
      <h4 style={subCardTitle}>API key (for integrations)</h4>
      <div style={subCardDescription}>
        For external systems: control scripts, middleware, or other software that connects to the OpenAVC REST API or WebSocket. Not needed unless you are building a custom integration.
      </div>
      <FieldRow
        label="API key"
        held={heldSetting(hold, "auth", "api_key")}
        lockStyle={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}
        help={<>
          Provide this to external systems via the <code>X-API-Key</code> header.
          Copy it before you save. It is not shown again afterwards.
        </>}
        after={needsPassword && (
          <span style={{ ...helpText, color: "rgb(244, 67, 54)" }}>
            An API key can't sign in to the Programmer. Set a Programmer
            password as well, or clear the API key, so this system can still
            be opened in a browser.
          </span>
        )}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <PasswordField
            value={key}
            placeholder="No API key set"
            onChange={(v) => draft.update("auth", "api_key", v)}
          />
        </div>
        {key && key !== REDACTED && (
          <button
            type="button"
            title="Copy API key"
            onClick={() => { copyToClipboard(key).then((ok) => { if (ok) showSuccess("Copied."); }); }}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-secondary)", padding: 2, display: "inline-flex" }}
          >
            <Copy size={14} />
          </button>
        )}
        <button
          type="button"
          onClick={() => draft.update("auth", "api_key", generateApiKey())}
          style={{ ...btnStyle, background: "none", color: "var(--text-secondary)", border: "1px solid var(--border)", flexShrink: 0 }}
        >
          Generate
        </button>
      </FieldRow>
    </div>
  );
}

const PANEL_ACCESS_CHOICES = [
  {
    mode: "approved",
    title: "Approved panels only",
    help: "A new tablet or browser waits until you approve it here. Approved panels stay approved.",
  },
  {
    mode: "open",
    title: "Anyone on the network",
    help: "Any device that can reach this system can open the panel and control the space.",
  },
] as const;

function PanelAccessCard({ draft, hold }: { draft: SettingsDraft; hold: EnvHold }) {
  const mode = panelAccessMode(draft.merged("panels")?.access);
  const held = hold.by("panels", "access");
  return (
    <div style={cardStyle}>
      <h4 style={subCardTitle}>Panel access</h4>
      <div style={subCardDescription}>
        Who can open the panel from a tablet, a phone or a browser on the network. Applies as soon as it is saved.
      </div>
      <HeldNote hold={hold} variable={held} style={{ marginBottom: "var(--space-sm)" }} />
      <EnvLock held={held}>
        {PANEL_ACCESS_CHOICES.map((choice) => (
          <label key={choice.mode} style={radioRow}>
            <input
              type="radio"
              name="cfg-panel-access"
              value={choice.mode}
              checked={mode === choice.mode}
              onChange={() => draft.update("panels", "access", choice.mode)}
              style={radioInput}
            />
            <div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>{choice.title}</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{choice.help}</div>
            </div>
          </label>
        ))}
      </EnvLock>
    </div>
  );
}
