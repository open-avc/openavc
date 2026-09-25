import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Loader2, Minus, Plug } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import { useAuditStore } from "../../../../store/auditStore";
import {
  currentRun,
  displayBytes,
  secondsLeft,
  statusValue,
} from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { buttonStyle, headingStyle, hintStyle, labelStyle, panelStyle, spinStyle } from "../auditStyles";

const CONTRACT_LABELS: Record<string, string> = {
  unmatched_response: "Replies no response rule matched",
  undeclared_state: "Status values the driver does not declare",
  type_mismatch: "Values not of their declared type",
  coercion_failure: "Values that could not be converted",
  unknown_command: "Commands the driver does not have",
  child_unregistered: "Status for channels or zones never registered",
};

/** Step 5: connect as adding the device to a space would, and listen. */
export function ListenStep() {
  const session = useAuditStore((s) => s.session);
  const timeline = useAuditStore((s) => s.timeline);
  const traffic = useAuditStore((s) => s.traffic);
  const run = currentRun(session);
  const listen = run?.listen;
  const [busy, setBusy] = useState<"" | "connect" | "extend" | "showed" | "did_not">("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = listen?.status === "listening" || listen?.status === "not_connected" || listen?.status === "connecting";

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!session || !run) return null;
  const sessionId = session.session_id;

  const act = async (
    kind: "connect" | "extend" | "showed" | "did_not",
    call: () => Promise<{ session: audit.AuditSessionState }>,
  ) => {
    setError("");
    setBusy(kind);
    try {
      const { session: next } = await call();
      useAuditStore.getState().setSession(next);
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const left = secondsLeft(listen, now);
  const capped = !!listen && (listen.ends_at ?? 0) >= listen.max_ends_at - 1;
  const runTimeline = timeline.filter(
    (e) =>
      (e.kind.startsWith("listen.") || e.kind.startsWith("contract.")) &&
      (e.data?.run === undefined || e.data.run === run.index),
  );

  return (
    <div style={{ maxWidth: 820 }}>
      <h2 style={headingStyle}>Connect and listen</h2>
      <p style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-md)" }}>
        This does what adding the device to a space does: it connects, signs in if needed, runs
        the driver's start-up steps and asks the device for its status. It sends none of the
        driver's commands.
      </p>

      {!listen ? (
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button
            type="button"
            onClick={() => void act("connect", () => audit.connectAudit(sessionId))}
            disabled={busy !== ""}
            style={buttonStyle("primary", busy !== "")}
          >
            {busy === "connect" ? <Loader2 size={14} style={spinStyle} /> : <Plug size={14} />}
            Connect
          </button>
          <button
            type="button"
            onClick={() => useAuditStore.getState().setStep("connection")}
            style={buttonStyle("muted")}
          >
            Change the connection settings
          </button>
        </div>
      ) : (
        <>
          <StatusBanner listen={listen} left={left} />
          <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap", marginTop: "var(--space-sm)" }}>
            {(listen.status === "listening" || listen.status === "done" || listen.status === "not_connected" || listen.status === "failed") && (
              <button
                type="button"
                onClick={() => void act("extend", () => audit.keepListening(sessionId))}
                disabled={busy !== "" || capped}
                style={buttonStyle("muted", busy !== "" || capped)}
                title={capped ? "The audit listens for five minutes at most." : undefined}
              >
                {busy === "extend" && <Loader2 size={14} style={spinStyle} />}
                Keep listening
              </button>
            )}
            <button
              type="button"
              onClick={() => useAuditStore.getState().setStep("connection")}
              style={buttonStyle("muted")}
            >
              Change the connection settings
            </button>
            {listen.status !== "connecting" && (
              <button
                type="button"
                onClick={() => void act("connect", () => audit.connectAudit(sessionId))}
                disabled={busy !== ""}
                style={buttonStyle("muted", busy !== "")}
              >
                {busy === "connect" && <Loader2 size={14} style={spinStyle} />}
                Connect again
              </button>
            )}
          </div>
          {capped && (
            <div style={hintStyle}>The audit listens for five minutes at most.</div>
          )}

          <StatusTable listen={listen} />
          <ContractSummary listen={listen} />

          {listen.connected_at && (
            <div style={{ ...panelStyle, marginTop: "var(--space-lg)" }}>
              <div style={labelStyle}>Front-panel check (optional)</div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>
                Change something on the device itself, like the volume or the input. Did OpenAVC
                show the change?
              </div>
              {listen.front_panel ? (
                <div style={{ ...hintStyle, fontSize: "var(--font-size-sm)" }}>
                  Recorded: {listen.front_panel.answer === "showed" ? "it showed" : "it did not"}.
                </div>
              ) : null}
              <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
                <button
                  type="button"
                  onClick={() => void act("showed", () => audit.answerFrontPanel(sessionId, "showed"))}
                  disabled={busy !== ""}
                  style={buttonStyle("muted", busy !== "")}
                >
                  It showed
                </button>
                <button
                  type="button"
                  onClick={() => void act("did_not", () => audit.answerFrontPanel(sessionId, "did_not"))}
                  disabled={busy !== ""}
                  style={buttonStyle("muted", busy !== "")}
                >
                  It did not
                </button>
              </div>
            </div>
          )}

          <Timeline entries={runTimeline} />
          <TrafficPanel entries={traffic} listen={listen} />
        </>
      )}

      {error && (
        <div style={{ marginTop: "var(--space-md)" }}>
          <ErrorLine text={error} />
        </div>
      )}

      {listen && (
        <div style={{ marginTop: "var(--space-lg)" }}>
          <button
            type="button"
            onClick={() => useAuditStore.getState().setStep("report")}
            disabled={listen.status === "connecting"}
            style={buttonStyle("primary", listen.status === "connecting")}
          >
            Continue
          </button>
        </div>
      )}
    </div>
  );
}

function StatusBanner({ listen, left }: { listen: audit.AuditListen; left: number | null }) {
  const tone =
    listen.status === "done"
      ? { bg: "var(--color-success-bg)", border: "var(--color-success)" }
      : listen.status === "failed" || listen.status === "not_connected"
        ? { bg: "var(--color-warning-bg)", border: "var(--color-warning)" }
        : { bg: "var(--color-info-bg)", border: "var(--color-info)" };
  let text = "";
  if (listen.status === "connecting") text = "Connecting.";
  else if (listen.status === "listening") text = `Connected. Listening${left !== null ? `, ${left} s left` : ""}.`;
  else if (listen.status === "not_connected") text = `Not connected yet${left !== null ? `, ${left} s left` : ""}.`;
  else if (listen.status === "done") text = `Listening finished: ${listen.reported} of ${listen.declared} status values reported. The driver stays connected until you finish.`;
  else if (listen.status === "failed") text = listen.error || "The driver did not connect while the audit was listening.";
  else text = "This attempt was ended.";
  return (
    <div
      role="status"
      style={{
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        fontSize: "var(--font-size-sm)",
      }}
    >
      <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
        {(listen.status === "connecting" || listen.status === "listening") && (
          <Loader2 size={14} style={spinStyle} />
        )}
        <span>{text}</span>
      </div>
      {listen.offline && listen.status !== "listening" && listen.status !== "done" && (
        <div style={{ marginTop: "var(--space-xs)" }}>
          {listen.offline.detail} {listen.offline.next_step}
        </div>
      )}
      {listen.traffic.not_captured && (
        <div style={{ marginTop: "var(--space-xs)" }}>
          This driver manages its own connection, so its traffic was not captured.
        </div>
      )}
    </div>
  );
}

function StatusTable({ listen }: { listen: audit.AuditListen }) {
  const vars = listen.status_table.variables;
  const children = Object.entries(listen.status_table.children);
  const settings = listen.status_table.settings;
  if (vars.length === 0 && children.length === 0 && settings.length === 0) return null;
  const cell = { padding: "2px var(--space-sm)", borderBottom: "1px solid var(--border-color)", verticalAlign: "top" as const };
  return (
    <div style={{ marginTop: "var(--space-lg)" }}>
      <div style={labelStyle}>
        Status values ({listen.reported} of {listen.declared} reported)
      </div>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--font-size-sm)" }}>
        <tbody>
          {vars.map((v) => (
            <tr key={v.name}>
              <td style={{ ...cell, width: 24 }}>
                {v.problem ? (
                  <AlertTriangle size={12} style={{ color: "var(--color-warning)" }} aria-label="Problem" />
                ) : v.reported ? (
                  <Check size={12} style={{ color: "var(--color-success)" }} aria-label="Reported" />
                ) : (
                  <Minus size={12} style={{ color: "var(--text-muted)" }} aria-label="Not reported" />
                )}
              </td>
              <td style={{ ...cell, width: "35%" }}>
                {v.label} <span style={{ color: "var(--text-secondary)" }}>{v.name !== v.label ? v.name : ""}</span>
              </td>
              <td style={{ ...cell, overflowWrap: "anywhere" }}>
                <span style={{ color: v.reported ? "var(--text-primary)" : "var(--text-secondary)" }}>
                  {statusValue(v)}
                </span>
                {v.problem && <div style={hintStyle}>{v.problem}</div>}
                {!v.reported && v.sources.length > 0 && (
                  <div style={hintStyle}>Set by a {v.sources.join(", or a ")}.</div>
                )}
              </td>
            </tr>
          ))}
          {settings.map((s) => (
            <tr key={`setting-${s.key}`}>
              <td style={{ ...cell, width: 24 }}>
                {s.populated ? (
                  <Check size={12} style={{ color: "var(--color-success)" }} aria-label="Read back" />
                ) : (
                  <Minus size={12} style={{ color: "var(--text-muted)" }} aria-label="Not read back" />
                )}
              </td>
              <td style={cell}>Setting: {s.label}</td>
              <td style={cell}>{s.populated ? String(s.value) : "Not read back"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {children.map(([ctype, byId]) => (
        <div key={ctype} style={{ ...hintStyle, fontSize: "var(--font-size-sm)" }}>
          {ctype}: {Object.keys(byId).length} registered
        </div>
      ))}
    </div>
  );
}

function ContractSummary({ listen }: { listen: audit.AuditListen }) {
  const counts = Object.entries(listen.contract.counts).filter(([, n]) => n > 0);
  if (counts.length === 0) return null;
  return (
    <div style={{ ...panelStyle, marginTop: "var(--space-md)", fontSize: "var(--font-size-sm)" }}>
      <div style={labelStyle}>What the driver did not handle</div>
      <ul style={{ margin: 0, paddingLeft: "var(--space-lg)" }}>
        {counts.map(([kind, n]) => (
          <li key={kind}>
            {CONTRACT_LABELS[kind] ?? kind}: {n}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Timeline({ entries }: { entries: audit.AuditTimelineEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <div style={{ marginTop: "var(--space-lg)" }}>
      <div style={labelStyle}>Timeline</div>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, fontSize: "var(--font-size-sm)" }}>
        {entries.map((e, i) => (
          <li key={i} style={{ display: "flex", gap: "var(--space-sm)", padding: "1px 0" }}>
            <span style={{ color: "var(--text-secondary)", fontFamily: "var(--font-mono, monospace)", flexShrink: 0 }}>
              {new Date(e.t * 1000).toLocaleTimeString()}
            </span>
            <span style={{ overflowWrap: "anywhere" }}>{e.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function TrafficPanel({ entries, listen }: { entries: audit.AuditTrafficEntry[]; listen: audit.AuditListen }) {
  const box = useRef<HTMLDivElement | null>(null);
  const frames = entries.filter((e) => !e.chunk);
  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [frames.length]);
  return (
    <div style={{ marginTop: "var(--space-lg)" }}>
      <div style={labelStyle}>
        Traffic ({listen.traffic.sent} sent, {listen.traffic.received} received)
      </div>
      <div
        ref={box}
        role="log"
        aria-label="Traffic"
        style={{
          ...panelStyle,
          maxHeight: 240,
          overflowY: "auto",
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "var(--font-size-xs)",
          padding: "var(--space-sm)",
        }}
      >
        {frames.length === 0 ? (
          <div style={{ color: "var(--text-secondary)" }}>Nothing yet.</div>
        ) : (
          frames.map((e) => (
            <div key={e.seq} style={{ display: "flex", gap: "var(--space-sm)" }}>
              <span style={{ color: "var(--text-secondary)", flexShrink: 0 }}>
                {new Date(e.t * 1000).toLocaleTimeString()}
              </span>
              <span
                style={{ flexShrink: 0, color: e.direction === "tx" ? "var(--accent)" : "var(--color-success)" }}
                aria-label={e.direction === "tx" ? "Sent" : "Received"}
              >
                {e.direction === "tx" ? "→" : "←"}
              </span>
              <span style={{ overflowWrap: "anywhere" }}>
                {e.meta && typeof e.meta.method === "string"
                  ? `${e.meta.method} ${String(e.meta.target ?? "")}${e.meta.status ? ` ${String(e.meta.status)}` : ""} `
                  : ""}
                {displayBytes(e.text, e.hex)}
              </span>
            </div>
          ))
        )}
      </div>
      {listen.traffic.truncated && (
        <div style={hintStyle}>
          The audit stopped keeping traffic at 20 MB. The report says where it stopped.
        </div>
      )}
    </div>
  );
}
