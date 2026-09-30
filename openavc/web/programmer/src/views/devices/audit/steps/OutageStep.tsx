import { useEffect, useState } from "react";
import { Loader2, Power, Square } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import { useAuditStore } from "../../../../store/auditStore";
import { currentRun, outageNextMark, outageNowText, outageProgress } from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { buttonStyle, headingStyle, hintStyle, labelStyle, panelStyle, spinStyle } from "../auditStyles";

type Call = () => Promise<{ session: audit.AuditSessionState }>;

/** What each test checks, the steps the person takes, and the marks they give. */
const TESTS: Record<
  audit.AuditOutageKind,
  { title: string; intro: string; steps: string[]; start: string; off: string; on: string }
> = {
  power_cycle: {
    title: "Power cycle",
    intro:
      "Checks how long OpenAVC takes to notice that the device went off, and to reconnect once " +
      "it is back on.",
    steps: [
      "Press Start the power cycle test.",
      "Turn the device off at its power switch or unplug its power, and press I turned it off.",
      "Leave it off until the step says to turn it back on, then turn it on and press I turned it back on.",
      "Wait while the driver reconnects. The test ends on its own.",
    ],
    start: "Start the power cycle test",
    off: "I turned it off",
    on: "I turned it back on",
  },
  cable_pull: {
    title: "Cable pull",
    intro:
      "Checks whether OpenAVC notices when the device stops answering without warning, as when " +
      "its network cable is pulled.",
    steps: [
      "Press Start the cable pull test.",
      "Unplug the device's network cable and press I unplugged it.",
      // The limit is NOTICE_CEILING_SECONDS in openavc/audit/outage.py.
      "Leave it unplugged until OpenAVC notices. The table shows it. This can take up to 5 minutes.",
      "Plug it back in and press I plugged it back in. The test ends on its own once the driver reconnects.",
    ],
    start: "Start the cable pull test",
    off: "I unplugged it",
    on: "I plugged it back in",
  },
};

/** How each test starts. */
const START: Record<audit.AuditOutageKind, (sessionId: string) => Promise<{ session: audit.AuditSessionState }>> = {
  power_cycle: audit.startPowerCycle,
  cable_pull: audit.startCablePull,
};

const KINDS: audit.AuditOutageKind[] = ["power_cycle", "cable_pull"];

/** Step 7: power cycle and cable pull, each optional. */
export function OutageStep() {
  const session = useAuditStore((s) => s.session);
  const run = currentRun(session);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = (run?.outages ?? []).find((o) => o.status === "running");
  // A boolean, not the test: each live update is a new object, and a timer
  // reset twice a second never ticks.
  const ticking = !!running;

  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now() / 1000);
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [ticking]);

  if (!session || !run) return null;
  const sessionId = session.session_id;
  // Connected right now: a driver that dropped and has not reconnected has
  // nothing left to see drop.
  const connected = !!run.listen?.active && run.listen.connected !== false;

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

  return (
    <div style={{ maxWidth: 820 }}>
      <h2 style={headingStyle}>Power and cable</h2>
      <p style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-md)" }}>
        These show how the driver copes when the device goes away and comes back. Each needs
        someone at the device and takes a few minutes. Run one at a time. This step is optional.
      </p>
      {error && <ErrorLine text={error} />}

      {!connected && !running && (
        <div style={{ ...panelStyle, marginBottom: "var(--space-md)", fontSize: "var(--font-size-sm)" }}>
          The driver is not connected to the device, so there is nothing to see drop.{" "}
          <button
            type="button"
            onClick={() => useAuditStore.getState().setStep("listen")}
            style={{ ...buttonStyle("muted"), display: "inline-flex", marginLeft: "var(--space-sm)" }}
          >
            Back to Connect and listen
          </button>
        </div>
      )}

      {KINDS.map((kind) => {
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
                <div role="status" style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-xs)", marginTop: "var(--space-sm)", fontWeight: 600 }}>
                  <Loader2 size={14} style={{ ...spinStyle, flexShrink: 0, marginTop: 2 }} />
                  <span>{outageNowText(live)}</span>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
                  <button
                    type="button"
                    onClick={() => void act("off", () => audit.markOutage(sessionId, "off"))}
                    disabled={busy !== "" || live.off_at !== null}
                    style={buttonStyle(outageNextMark(live) === "off" ? "primary" : "muted", busy !== "" || live.off_at !== null)}
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
                      outageNextMark(live) === "on" ? "primary" : "muted",
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
                {!last && (
                  <ol style={{ margin: "var(--space-sm) 0 0", paddingLeft: "var(--space-lg)" }}>
                    {words.steps.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ol>
                )}
                {last && (
                  <div role="status" style={{ marginTop: "var(--space-sm)", overflowWrap: "anywhere" }}>
                    {last.summary}
                  </div>
                )}
                {last && <Progress outage={last} now={now} />}
                <div style={{ marginTop: "var(--space-sm)" }}>
                  <button
                    type="button"
                    onClick={() => void act(kind, () => START[kind](sessionId))}
                    disabled={busy !== "" || !connected || !!running}
                    style={buttonStyle("muted", busy !== "" || !connected || !!running)}
                  >
                    {busy === kind ? <Loader2 size={14} style={spinStyle} /> : <Power size={14} />}
                    {last ? "Run it again" : words.start}
                  </button>
                </div>
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
