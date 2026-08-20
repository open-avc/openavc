import type { DriverDefinition, DriverLivenessDef } from "../../api/types";
import { OscArgsEditor } from "./OscArgsEditor";

interface LivenessEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the `liveness` block — a declarative connection watchdog for links
 * that die without closing the connection (UDP/OSC are connectionless; a
 * push-style TCP device that vanishes looks connected forever). The runtime
 * sends the probe every `interval` seconds, awaits a reply within `timeout`,
 * and reconnects after `max_failures` consecutive misses. Supported on
 * tcp/serial/udp/osc transports.
 */
export function LivenessEditor({ draft, onUpdate }: LivenessEditorProps) {
  const liveness = draft.liveness;
  const enabled = !!liveness;

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };
  const helpStyle: React.CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: "var(--text-muted)",
    marginTop: "var(--space-xs)",
  };

  const setEnabled = (next: boolean) => {
    if (next) {
      onUpdate({
        liveness: { send: "", interval: 30, timeout: 5, max_failures: 2 },
      });
    } else {
      onUpdate({ liveness: undefined });
    }
  };

  const update = (partial: Partial<DriverLivenessDef>) => {
    onUpdate({ liveness: { ...(liveness ?? {}), ...partial } });
  };

  // Optional numeric fields: an empty input removes the key so the runtime
  // default applies; anything parseable is stored and range-checked by
  // validation (mirroring the loader's minimums).
  const parseOptional = (raw: string, integer: boolean): number | undefined => {
    if (raw === "") return undefined;
    const n = integer ? parseInt(raw, 10) : parseFloat(raw);
    return Number.isNaN(n) ? undefined : n;
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
        Sends a cheap probe on an interval and reconnects after consecutive
        unanswered probes, for devices and transports that go quiet without
        closing the connection. UDP and OSC queries are fire-and-forget (a
        dead host answers nothing and nothing errors), and a push-style TCP
        device that vanishes without closing the socket looks connected
        forever. Leave disabled for devices whose regular polling already
        detects failures.
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
        Enable connection watchdog
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
              <label style={labelStyle}>Probe Command</label>
              <input
                value={liveness?.send ?? ""}
                onChange={(e) => update({ send: e.target.value })}
                placeholder="e.g. PING \r"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Raw protocol string sent as the probe (include the terminator,
                e.g. <code>\r</code>). On an OSC transport this is the OSC
                address. Required.
              </div>
            </div>
            <div>
              <label style={labelStyle}>Expect Pattern</label>
              <input
                value={liveness?.expect ?? ""}
                onChange={(e) =>
                  update({ expect: e.target.value || undefined })
                }
                placeholder="e.g. PONG"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Optional regex a reply must match. Leave blank to count any
                inbound frame as a reply, which is right for chatty devices.
              </div>
            </div>
          </div>

          {/* OSC probes carry typed arguments alongside the address, the same
              way an OSC command does. Only an OSC transport has them. */}
          {draft.transport === "osc" && (
            <div style={{ marginTop: "var(--space-md)" }}>
              <label style={labelStyle}>Probe Arguments</label>
              <OscArgsEditor
                args={
                  (liveness?.args ?? []) as { type: string; value: string }[]
                }
                onChange={(args) =>
                  update({
                    args: args.length
                      ? (args as DriverLivenessDef["args"])
                      : undefined,
                  })
                }
              />
              <div style={helpStyle}>
                Optional typed arguments sent with the probe address.
              </div>
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-md)" }}>
            <div>
              <label style={labelStyle}>Interval (sec)</label>
              <input
                type="number"
                value={liveness?.interval ?? ""}
                onChange={(e) =>
                  update({ interval: parseOptional(e.target.value, false) })
                }
                min={1}
                placeholder="30"
                style={{ width: "100%" }}
              />
              <div style={helpStyle}>Seconds between probes.</div>
            </div>
            <div>
              <label style={labelStyle}>Reply Timeout (sec)</label>
              <input
                type="number"
                value={liveness?.timeout ?? ""}
                onChange={(e) =>
                  update({ timeout: parseOptional(e.target.value, false) })
                }
                min={0.1}
                step={0.1}
                placeholder="5"
                style={{ width: "100%" }}
              />
              <div style={helpStyle}>How long to await a reply.</div>
            </div>
            <div>
              <label style={labelStyle}>Max Failures</label>
              <input
                type="number"
                value={liveness?.max_failures ?? ""}
                onChange={(e) =>
                  update({ max_failures: parseOptional(e.target.value, true) })
                }
                min={1}
                placeholder="2"
                style={{ width: "100%" }}
              />
              <div style={helpStyle}>
                Consecutive misses before reconnecting.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
