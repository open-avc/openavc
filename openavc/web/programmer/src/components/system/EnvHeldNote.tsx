import type { CSSProperties, ReactNode } from "react";
import { Lock } from "lucide-react";

/** The docs section that says how to set a value in the environment on each
 *  kind of install. */
export const ENV_SETTINGS_DOCS_URL =
  "https://docs.openavc.com/hardened-deployment/#setting-values-in-the-environment-instead";

/** Wraps a control the environment holds. A disabled fieldset disables every
 *  input, select and button inside it, the custom switches included. */
export function EnvLock({ held, children, style }: {
  held: string | undefined;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <fieldset
      disabled={!!held}
      style={{
        border: 0,
        margin: 0,
        padding: 0,
        minWidth: 0,
        ...(held ? { opacity: 0.55, cursor: "not-allowed" } : null),
        ...style,
      }}
    >
      {children}
    </fieldset>
  );
}

/** Where a held value is set on this kind of install, and how to give the
 *  choice back to this screen: one sentence that names a place the reader can
 *  find. */
function whereItIsSet(variable: string, deploymentType: string): ReactNode {
  const name = <code>{variable}</code>;
  switch (deploymentType) {
    case "linux_package":
      return (
        <>
          Set by the {name} line in a systemd override for this server
          (<code>sudo systemctl edit openavc</code>). Remove that line to change it here.
        </>
      );
    case "docker":
      return <>Set by {name} in the container's environment. Remove it there to change it here.</>;
    case "windows_installer":
      return (
        <>
          Set by {name} on the OpenAVC Windows service. Remove it from the service's
          environment to change it here.
        </>
      );
    case "macos_app":
      return <>Set by {name} in the OpenAVC launch settings. Remove it there to change it here.</>;
    case "git_dev":
      return <>Set by {name} where you start the server.</>;
    default:
      return <>Set by {name} in this server's environment, so it can't be changed here.</>;
  }
}

/** The line under a field the environment holds. Renders nothing for a field
 *  it does not hold. */
export function EnvHeldNote({ variable, deploymentType, configurationOwner, style }: {
  variable: string | undefined;
  deploymentType: string;
  configurationOwner?: "firmware";
  style?: CSSProperties;
}) {
  if (!variable) return null;
  return (
    <span
      data-testid="env-held-note"
      style={{
        display: "flex",
        gap: 6,
        alignItems: "flex-start",
        fontSize: 12,
        color: "var(--text-secondary)",
        marginTop: 4,
        lineHeight: 1.5,
        ...style,
      }}
    >
      <Lock size={12} style={{ flexShrink: 0, marginTop: 3 }} aria-hidden />
      <span>
        {configurationOwner === "firmware" ? <>
          This setting is managed by the product's firmware. Contact product support if it needs to change.
        </> : <>
        {whereItIsSet(variable, deploymentType)}{" "}
        <a href={ENV_SETTINGS_DOCS_URL} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Setting values in the environment
        </a>
        </>}
      </span>
    </span>
  );
}
