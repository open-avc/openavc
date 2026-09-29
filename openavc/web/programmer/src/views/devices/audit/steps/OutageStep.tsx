import { useEffect, useState } from "react";
import { Loader2, Power, Square } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import { useAuditStore } from "../../../../store/auditStore";
import { currentRun, outageProgress } from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { buttonStyle, headingStyle, hintStyle, labelStyle, panelStyle, spinStyle } from "../auditStyles";

type Call = () => Promise<{ session: audit.AuditSessionState }>;

/** What each test asks the person to do, and the marks they give. */
const TESTS: Record<
  audit.AuditOutageKind,
  { title: string; intro: string; start: string; off: string; on: string; after: string }
> = {
  power_cycle: {
    title: "Power cycle",
    intro:
      "Turn the device off at its power switch or unplug it, wait 10 seconds, then turn it back " +
      "on. OpenAVC times how long it takes to notice, and to reconnect once the device is back.",
    start: "Start the power cycle test",
    off: "I turned it off",
    on: "I turned it back on",
    after: "Now turn the device off. Press the button as you do.",
  },
  cable_pull: {
    title: "Cable pull",
    intro:
      "Unplug the device's network cable, wait for OpenAVC to notice, then plug it back in. " +
      "A device that simply goes quiet is noticed only if the driver checks.",
    start: "Start the cable pull test",
    off: "I unplugged it",
    on: "I plugged it back in",
    after: "Now unplug the network cable. Press the button as you do.",
  },
};

/** How each test starts. */
const START: Partial<Record<audit.AuditOutageKind, (sessionId: string) => Promise<{ session: audit.AuditSessionState }>>> = {
  power_cycle: audit.startPowerCycle,
};

/** Step 7: power cycle and cable pull, each optional. */
export function OutageStep() {
  const session = useAuditStore((s) => s.session);
  const run = currentRun(session);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = (run?.outages ?? []).find((o) => o.status === "running");

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!session || !run) return null;
  const sessionId = session.session_id;
  const connected = !!run.listen?.active;

  const act = async (key: string, call: Call) => {
    setError("");
    setBusy(key);
    try {
      const { session: next } = await call();
      useAuditStore.getState().setSession(next);
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const kinds: audit.AuditOutageKind[] = ["power_cycle"];

  return (
    <div style={{ maxWidth: 820 }}>
      <h2 style={headingStyle}>Power and cable</h2>
      <p style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-md)" }}>
        Optional. These show how the driver copes when the device goes away and comes back. Run
        them where you can reach the device.
      </p>
      {error && <ErrorLine text={error} />}

      {kinds.map((kind) => {
        const words = TESTS[kind];
        const tests = (run.outages ?? []).filter((o) => o.kind === kind);
        const last = tests[tests.length - 1];
        const live = last && last.status === "running" ? last : undefined;
        return (
          <div key={kind} style={{ ...panelStyle, marginBottom: "var(--space-md)", fontSize: "var(--font-size-sm)" }}>
            <div style={labelStyle}>{words.title}</div>
            <div>{words.intro}</div>

            {live ? (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginTop: "var(--space-sm)" }}>
                  <Loader2 size={14} style={spinStyle} />
                  <span>
                    {live.off_at === null && live.unreachable_at === null
                      ? words.after
                      : live.on_at === null && live.reachable_at === null
                        ? "Wait about 10 seconds, then bring it back and press the button."
                        : live.reconnected_at === null
                          ? "Waiting for the driver to reconnect."
                          : "Watching the status values come back."}
                  </span>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
                  <button
                    type="button"
                    onClick={() => void act("off", () => audit.markOutage(sessionId, "off"))}
                    disabled={busy !== "" || live.off_at !== null}
                    style={buttonStyle(live.off_at === null ? "primary" : "muted", busy !== "" || live.off_at !== null)}
                  >
                    {words.off}
                  </button>
                  <button
                    type="button"
                    onClick={() => void act("on", () => audit.markOutage(sessionId, "on"))}
                    disabled={
                      busy !== "" || live.on_at !== null || (live.off_at === null && live.unreachable_at === null)
                    }
                    style={buttonStyle(
                      "muted",
                      busy !== "" || live.on_at !== null || (live.off_at === null && live.unreachable_at === null),
                    )}
                  >
                    {words.on}
                  </button>
                  <button
                    type="button"
                    onClick={() => void act("stop", () => audit.stopOutage(sessionId))}
                    disabled={busy !== ""}
                    style={buttonStyle("muted", busy !== "")}
                  >
                    <Square size={12} /> Stop the test
                  </button>
                </div>
                <Progress outage={live} now={now} />
                {!live.ping.used && <div style={hintStyle}>{live.ping.why}</div>}
              </>
            ) : (
              <>
                {last && (
                  <div role="status" style={{ marginTop: "var(--space-sm)", overflowWrap: "anywhere" }}>
                    {last.summary}
                  </div>
                )}
                {last && <Progress outage={last} now={now} />}
                <div style={{ marginTop: "var(--space-sm)" }}>
                  <button
                    type="button"
                    onClick={() => void act(kind, () => START[kind]!(sessionId))}
                    disabled={busy !== "" || !connected || !!running}
                    style={buttonStyle("muted", busy !== "" || !connected || !!running)}
                  >
                    {busy === kind ? <Loader2 size={14} style={spinStyle} /> : <Power size={14} />}
                    {last ? "Run it again" : words.start}
                  </button>
                </div>
                {!connected && (
                  <div style={hintStyle}>The driver is not connected, so there is nothing to see drop.</div>
                )}
              </>
            )}
          </div>
        );
      })}

      <div style={{ marginTop: "var(--space-lg)" }}>
        <button
          type="button"
          onClick={() => useAuditStore.getState().setStep("report")}
          disabled={!!running}
          style={buttonStyle("primary", !!running)}
          title={running ? "Stop the test first." : undefined}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

function Progress({ outage, now }: { outage: audit.AuditOutage; now: number }) {
  const lines = outageProgress(outage, now);
  if (lines.length === 0) return null;
  return (
    <table style={{ borderCollapse: "collapse", marginTop: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
      <tbody>
        {lines.map((line) => (
          <tr key={line.label}>
            <th
              scope="row"
              style={{ textAlign: "left", fontWeight: 500, color: "var(--text-secondary)", padding: "1px var(--space-md) 1px 0" }}
            >
              {line.label}
            </th>
            <td style={{ padding: "1px 0", overflowWrap: "anywhere" }}>{line.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
