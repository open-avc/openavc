import { useEffect, useState } from "react";
import { ArrowUpCircle, CloudOff } from "lucide-react";
import { ViewContainer } from "../layout/ViewContainer";
import { getUpdateStatus } from "../../api/restClient";
import { useConnectionStore } from "../../store/connectionStore";
import { useNavigationStore } from "../../store/navigationStore";
import { requestInstall } from "../../views/requestedInstall";

/** What is in the way of the assistant (the store's `unavailableState`). */
export type AssistantBlock = "unpaired" | "disconnected" | "unavailable" | "outdated";

interface Props {
  state: AssistantBlock;
  /** The sentence the instance or the cloud gave for it. */
  reason: string;
  /** The release to install, while `outdated`. */
  neededRelease: string;
}

const textStyle: React.CSSProperties = { color: "var(--text-secondary)", maxWidth: 400 };
const noteStyle: React.CSSProperties = { color: "var(--text-muted)", fontSize: "var(--font-size-sm)" };

const primaryStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  textDecoration: "none",
  border: "none",
  cursor: "pointer",
};

const secondaryStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
};

/** The assistant pane when the assistant cannot answer. Which of the reasons
 *  is in the way decides what to say: telling somebody to pair a system they
 *  paired last month, when what is actually wrong is that their account's
 *  assistant is paused, leaves them re-pairing a working system. */
export function AssistantUnavailable({ state, reason, neededRelease }: Props) {
  const outdated = state === "outdated" && !!neededRelease;
  return (
    <ViewContainer title="AI Assistant">
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: "var(--space-lg)",
          textAlign: "center",
          padding: "var(--space-xl)",
        }}
      >
        {outdated ? (
          <ArrowUpCircle size={48} style={{ color: "var(--text-muted)" }} />
        ) : (
          <CloudOff size={48} style={{ color: "var(--text-muted)" }} />
        )}
        <h2 style={{ fontSize: "var(--font-size-lg)" }}>AI Assistant</h2>
        {outdated ? (
          <UpdateForAssistant neededRelease={neededRelease} />
        ) : (
          <CloudBlock state={state} reason={reason} />
        )}
      </div>
    </ViewContainer>
  );
}

/** Whether a click installs the update or only opens the Updates page, which
 *  then says how to update this deployment: null until the update settings
 *  are read, false when they cannot be. */
function useInstallsItself(): boolean | null {
  const [installs, setInstalls] = useState<boolean | null>(null);
  useEffect(() => {
    let left = false;
    const settle = (value: boolean) => { if (!left) setInstalls(value); };
    getUpdateStatus()
      .then((s) => settle(s.can_self_update && s.update_owner !== "firmware"))
      .catch(() => settle(false));
    return () => { left = true; };
  }, []);
  return installs;
}

/** The version this system runs, as the live state has it, or "". */
function useRunningVersion(): string {
  const running = useConnectionStore((s) => s.liveState["system.version"]);
  return typeof running === "string" ? running : "";
}

/** The system is behind the release the assistant needs: that release, and
 *  its install one click away through the Updates page. */
function UpdateForAssistant({ neededRelease }: { neededRelease: string }) {
  const running = useRunningVersion();
  const installs = useInstallsItself();
  return (
    <>
      <p style={textStyle}>{"Update to v" + neededRelease + " to use the assistant."}</p>
      {running && (
        <p style={{ ...noteStyle, marginTop: "calc(-1 * var(--space-sm))" }}>{"This system runs v" + running + "."}</p>
      )}
      {installs !== null && <UpdateButton neededRelease={neededRelease} installs={installs} />}
    </>
  );
}

function UpdateButton({ neededRelease, installs }: { neededRelease: string; installs: boolean }) {
  const open = () => requestInstall(neededRelease);
  if (!installs) {
    return <button style={secondaryStyle} onClick={open}>Open System Updates</button>;
  }
  return (
    <>
      <button style={primaryStyle} onClick={open}>{"Install v" + neededRelease}</button>
      <p style={noteStyle}>Installing restarts the server.</p>
    </>
  );
}

/** Not paired, not connected, or refused by the cloud. */
function CloudBlock({ state, reason }: { state: AssistantBlock; reason: string }) {
  const refused = state === "unavailable" || state === "outdated";
  const paired = state !== "unpaired";
  return (
    <>
      <CloudBlockText refused={refused} paired={paired} reason={reason} />
      <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap", justifyContent: "center" }}>
        {!paired && (
          <a href="https://cloud.openavc.com" target="_blank" rel="noopener noreferrer" style={primaryStyle}>
            Sign up at cloud.openavc.com
          </a>
        )}
        {refused && (
          <a href="https://cloud.openavc.com" target="_blank" rel="noopener noreferrer" style={primaryStyle}>
            Open OpenAVC Cloud
          </a>
        )}
        <button
          onClick={() => useNavigationStore.getState().navigateTo("cloud")}
          style={secondaryStyle}
        >
          Open Cloud Connection
        </button>
      </div>
      {!refused && reason && reason !== "Checking..." && <p style={noteStyle}>{reason}</p>}
    </>
  );
}

/** What is in the way, in words: the cloud's sentence for a refusal. */
function CloudBlockText({ refused, paired, reason }: { refused: boolean; paired: boolean; reason: string }) {
  if (refused) {
    return <p style={textStyle}>{reason || "The assistant is not available on this account."}</p>;
  }
  if (paired) {
    return (
      <p style={textStyle}>
        This system is paired but not connected to OpenAVC Cloud right
        now. Cloud Connection shows what the connection is doing.
      </p>
    );
  }
  return (
    <>
      <p style={textStyle}>
        AI features require a cloud connection. Pair this system with
        your OpenAVC Cloud account to get started.
      </p>
      <p style={{ ...noteStyle, maxWidth: 400, marginTop: "calc(-1 * var(--space-sm))" }}>
        A free account includes one paired system, so you can try the
        assistant and the other cloud features at no cost. On a free
        account the assistant comes with a usage allowance.
      </p>
    </>
  );
}
