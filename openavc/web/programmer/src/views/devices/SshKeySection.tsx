/**
 * OpenAVC's own SSH public key, shown on the page of a device that connects
 * over SSH and can sign in with a key. Key sign-in uses this key whenever the
 * device's key path is blank, so the integrator adds it to the device's account
 * and never has to place a key file on the server.
 */
import { useEffect, useState } from "react";
import { CopyButton } from "../../components/shared/CopyButton";
import { getSshPublicKey, type SshPublicKey } from "../../api/deviceClient";
import { parseApiError } from "../../api/errors";

export const SSH_KEY_TITLE = "OpenAVC SSH key";
export const SSH_KEY_HELP =
  "Add this key to the device's user account to sign in without a password. " +
  "In Edit, choose key sign-in and leave the private key path blank.";

/**
 * Whether the key card belongs on this device's page: its driver offers key
 * sign-in, or the device signs in with a key now. `config` is the device's
 * config with its connections row laid over it. Like the platform, a device
 * that names no sign-in method uses the key; only "password" does not. A
 * password-only driver (it offers no `ssh_auth_method` choice) gets no card,
 * since its Edit dialog has no key sign-in to choose.
 */
export function canUseSshKey(
  driver: { config_schema?: unknown; default_config?: unknown } | undefined,
  config: Record<string, unknown> | undefined,
): boolean {
  const field = (driver?.config_schema as Record<string, { values?: unknown }> | undefined)
    ?.ssh_auth_method;
  if (Array.isArray(field?.values)) {
    const offered = field.values.map((v) =>
      String(v && typeof v === "object" ? (v as { value?: unknown }).value : v).toLowerCase(),
    );
    if (offered.includes("key")) return true;
  }
  const defaults = (driver?.default_config ?? {}) as Record<string, unknown>;
  const method = config?.ssh_auth_method ?? defaults.ssh_auth_method;
  return String(method || "key").toLowerCase() !== "password";
}

interface SshKeySectionProps {
  sectionStyle: React.CSSProperties;
  sectionTitleStyle: React.CSSProperties;
}

export function SshKeySection({ sectionStyle, sectionTitleStyle }: SshKeySectionProps) {
  const [key, setKey] = useState<SshPublicKey | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getSshPublicKey()
      .then((k) => {
        if (!cancelled) {
          setKey(k);
          setError("");
        }
      })
      .catch((e) => {
        if (!cancelled) setError(parseApiError(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div style={sectionStyle} data-testid="ssh-key-section">
      <h3 style={sectionTitleStyle}>{SSH_KEY_TITLE}</h3>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        {SSH_KEY_HELP}
      </div>
      {error ? (
        <div role="alert" style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>
          {error}
        </div>
      ) : !key ? (
        <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>Loading...</div>
      ) : (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "var(--space-xs)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-sm) var(--space-md)",
              background: "var(--bg-surface)",
            }}
          >
            <code
              aria-label="OpenAVC SSH public key"
              style={{
                flex: 1,
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                wordBreak: "break-all",
                userSelect: "all",
              }}
            >
              {key.public_key}
            </code>
            <CopyButton value={key.public_key} size={14} title="Copy the SSH key" />
          </div>
          <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-muted)" }}>
            Fingerprint {key.fingerprint}
          </div>
        </>
      )}
    </div>
  );
}
