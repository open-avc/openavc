import type { DriverDefinition, DriverWhenState } from "../../api/types";
import { QueryListEditor } from "./QueryListEditor";
import { stateGateVariables, type QueryEntry } from "./queryEntryHelpers";
import { WhenStateEditor } from "./WhenStateEditor";

interface PollingConfigProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

type PollQuery = QueryEntry;

export function PollingConfig({ draft, onUpdate }: PollingConfigProps) {
  const polling = draft.polling ?? {};
  const queries = (polling.queries ?? []) as PollQuery[];
  const childTypeNames = Object.keys(draft.child_entity_types ?? {});
  const defaultConfig = (draft.default_config ?? {}) as Record<string, unknown>;
  const pollIntervalRaw = defaultConfig.poll_interval;
  const pollInterval =
    typeof pollIntervalRaw === "number"
      ? pollIntervalRaw
      : typeof pollIntervalRaw === "string"
        ? parseInt(pollIntervalRaw) || 0
        : 0;

  const updatePollInterval = (value: number) => {
    onUpdate({
      default_config: { ...defaultConfig, poll_interval: value },
    });
  };

  const updatePolling = (partial: Record<string, unknown>) => {
    onUpdate({ polling: { ...polling, ...partial } });
  };

  const gateVariables = stateGateVariables(draft);

  const updatePollGate = (gate: DriverWhenState | undefined) => {
    const next: Record<string, unknown> = { ...polling };
    if (gate) {
      next.when_state = gate;
    } else {
      delete next.when_state;
    }
    onUpdate({ polling: next as DriverDefinition["polling"] });
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Configure periodic polling to query the device for status updates.
        These are the command strings sent at each interval to ask the device
        for its current state.
      </p>

      <div style={{ marginBottom: "var(--space-lg)" }}>
        <label style={labelStyle}>Poll Interval (seconds)</label>
        <input
          type="number"
          value={pollInterval}
          onChange={(e) => updatePollInterval(parseInt(e.target.value) || 0)}
          min={0}
          style={{ width: 120 }}
        />
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          Set to 0 to disable polling. Typical: 10–30 seconds. Stored as
          <code> default_config.poll_interval</code> so device config can
          override it per-instance.
        </div>
      </div>

      {gateVariables.length > 0 && (
        <div style={{ marginBottom: "var(--space-lg)" }}>
          <label style={labelStyle}>Pause Polling</label>
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Poll only while the device reports a state, for a unit that any
            command wakes from standby. A response rule must set the variable
            again when the device leaves that state, or polling stays paused.
            Nothing is sent while paused, so a device that goes offline
            meanwhile is not noticed.
          </div>
          <WhenStateEditor
            draft={draft}
            gate={polling.when_state}
            onChange={updatePollGate}
            lead="Poll only while"
          />
        </div>
      )}

      <div>
        <label style={labelStyle}>Poll Queries</label>
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginBottom: "var(--space-sm)",
          }}
        >
          {draft.transport === "osc"
            ? <>OSC addresses or command names sent each poll cycle. Bare addresses
              are sent with no arguments (as queries). Command names execute the
              full command definition.</>
            : <>Command strings sent each poll cycle. Include the delimiter at the
              end (e.g., <code>\r</code> or <code>\r\n</code>). You can use
              config field placeholders like{" "}
              <code>{"{set_id}"}</code>.</>
          }
          {childTypeNames.length > 0 && (
            <>
              {" "}A <b>per-child</b> query is sent once for each registered
              child, where <code>{"{child_id}"}</code> inserts its ID.
            </>
          )}
        </div>

        <QueryListEditor
          draft={draft}
          queries={queries}
          onChange={(next) => updatePolling({ queries: next })}
          addLabel="Add Query"
        />
      </div>
    </div>
  );
}
