import type { CSSProperties, ReactNode } from "react";
import type { ListenMode } from "../../api/systemClient";

/** What the person changing who can connect is told when the change cuts off
 *  the browser they are using: before saving (the confirm dialog), while the
 *  server restarts, and once this page can no longer reach it. */
export interface CutOffFacts {
  /** This computer's address the browser reached. */
  connectedThrough: string;
  mode: ListenMode;
  adapterName: string;
  /** Where the Programmer opens under the new choice. */
  programmerUrls: string[];
  /** The Programmer on this computer's own screen. */
  localUrl: string;
  /** The choice it goes back to, as Settings lists it ("Any network"). */
  previousLabel: string;
  /** Minutes the safety net waits. */
  minutes: number;
  /** Remote access through OpenAVC Cloud is set up, so it is worth naming. */
  cloudPaired: boolean;
  /** What else stops working, in the server's sentences. */
  consequences: string[];
}

const urlStyle: CSSProperties = {
  display: "block",
  margin: "var(--space-xs) 0 var(--space-xs) var(--space-md)",
  fontFamily: "var(--font-mono)",
  fontSize: 13,
  wordBreak: "break-all",
};

const para: CSSProperties = { margin: "0 0 var(--space-sm)" };

function Urls({ urls }: { urls: string[] }) {
  return (
    <>
      {urls.map((u) => (
        <code key={u} style={urlStyle}>{u}</code>
      ))}
    </>
  );
}

/** "If nobody opens the Programmer that way within 10 minutes, OpenAVC goes
 *  back to Any network." */
export function safetyNetSentence(f: CutOffFacts): string {
  return `If nobody opens the Programmer that way within ${f.minutes} minutes, OpenAVC goes back to ${f.previousLabel}.`;
}

function where(f: CutOffFacts): ReactNode {
  if (f.mode === "adapter" && f.programmerUrls.length > 0) {
    return (
      <>
        open
        <Urls urls={f.programmerUrls} />
        from a device on the {f.adapterName || "chosen"} network, or use this computer's own
        screen (<code>{f.localUrl}</code>).
      </>
    );
  }
  return <>use this computer's own screen (<code>{f.localUrl}</code>).</>;
}

/** The confirm dialog's body. */
export function CutOffWarning({ facts }: { facts: CutOffFacts }) {
  return (
    <div data-testid="listen-cut-off-warning" style={{ lineHeight: 1.5 }}>
      <p style={para}>
        You're connected through {facts.connectedThrough}. After the restart, OpenAVC won't accept
        connections from that network, so this page will stop loading here.
      </p>
      <p style={para}>
        To keep working on it, {where(facts)}
        {facts.cloudPaired && <> Remote access through OpenAVC Cloud keeps working.</>}
      </p>
      {facts.consequences.length > 0 && (
        <ul style={{ ...para, paddingLeft: "var(--space-lg)" }}>
          {facts.consequences.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
      <p style={{ ...para, marginBottom: 0 }}>{safetyNetSentence(facts)}</p>
    </div>
  );
}

/** Shown while the server restarts. */
export function CutOffRestartLines({ facts }: { facts: CutOffFacts }) {
  return (
    <div style={{ lineHeight: 1.5, color: "var(--text-secondary)" }}>
      <p style={para}>This page will stop loading here. To keep working, {where(facts)}</p>
      <p style={{ ...para, marginBottom: 0 }}>{safetyNetSentence(facts)}</p>
    </div>
  );
}

/** Shown once this page can no longer reach the server. */
export function CutOffSaved({ facts }: { facts: CutOffFacts }) {
  const onNetwork = facts.mode === "adapter" && facts.programmerUrls.length > 0;
  return (
    <div style={{ lineHeight: 1.5 }}>
      <p style={para}>
        {onNetwork ? (
          <>
            Saved. Open the Programmer from
            <Urls urls={facts.programmerUrls} />
            on a device on the {facts.adapterName || "chosen"} network, or on this computer's own
            screen (<code>{facts.localUrl}</code>).
          </>
        ) : (
          <>
            Saved. Open the Programmer on this computer's own screen (<code>{facts.localUrl}</code>).
          </>
        )}
      </p>
      <p style={{ ...para, marginBottom: 0, color: "var(--text-secondary)" }}>{safetyNetSentence(facts)}</p>
    </div>
  );
}
