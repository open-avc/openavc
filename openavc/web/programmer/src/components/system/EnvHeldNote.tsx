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

/** What changes a held value on this kind of install. */
function whereToChange(variable: string, deploymentType: string): ReactNode {
  switch (deploymentType) {
    case "linux_package":
      return (
        <>
          To change it, run <code>sudo systemctl edit openavc</code> and add an{" "}
          <code>Environment={variable}=</code> line under <code>[Service]</code>.
        </>
      );
    case "docker":
      return <>To change it, set it in the container's environment.</>;
    case "windows_installer":
      return <>The installer sets it, and sets it again on every update.</>;
    case "macos_app":
      return <>The installer sets it, and sets it again each time it runs.</>;
    case "git_dev":
      return <>To change it, set it where you start the server.</>;
    default:
      return null;
  }
}

/** The line under a field the environment holds. Renders nothing for a field
 *  it does not hold. */
export function EnvHeldNote({ variable, deploymentType, style }: {
  variable: string | undefined;
  deploymentType: string;
  style?: CSSProperties;
}) {
  if (!variable) return null;
  const where = whereToChange(variable, deploymentType);
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
        Set by <code>{variable}</code> in this server's environment, so it can't be changed here.
        {where && <> {where}</>}{" "}
        <a href={ENV_SETTINGS_DOCS_URL} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Setting values in the environment
        </a>
      </span>
    </span>
  );
}
