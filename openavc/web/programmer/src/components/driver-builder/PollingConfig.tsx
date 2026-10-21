import { Filter, Plus, Trash2 } from "lucide-react";
import type { DriverDefinition, DriverWhenState } from "../../api/types";
import {
  buildQueryEntry,
  carryQueryExtras,
  firstGateEntry,
  gateFieldNames,
  isEachChild,
  listReadOptions,
  queryPageSize,
  queryQueryFor,
  queryWhen,
  queryWhenState,
  querySend,
  stateGateVariables,
  withPageSize,
  withWhenState,
  type QueryEntry,
} from "./queryEntryHelpers";
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
  const gateFields = gateFieldNames(draft);
  const stateVarNames = Object.keys(draft.state_variables ?? {});
  const listReads = listReadOptions(draft);
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

  const setQueryGate = (index: number, gate: DriverWhenState | undefined) => {
    const next = [...queries];
    next[index] = withWhenState(queries[index], gate);
    updatePolling({ queries: next });
  };

  const addQuery = () => {
    updatePolling({ queries: [...queries, ""] });
  };

  const removeQuery = (index: number) => {
    updatePolling({ queries: queries.filter((_: unknown, i: number) => i !== index) });
  };

  const updateQuery = (index: number, value: PollQuery) => {
    const next = [...queries];
    // An edit of the send, scope, gate or pairing keeps the entry's other
    // keys (a page size) where the new shape can hold them.
    next[index] = carryQueryExtras(queries[index], value);
    updatePolling({ queries: next });
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

        {queries.map((query: PollQuery, i: number) => {
          const eachChild = isEachChild(query);
          const send = querySend(query);
          const when = queryWhen(query);
          const queryFor = queryQueryFor(query);
          const stateGate = queryWhenState(query);
          return (
            <div key={i} style={{ marginBottom: "var(--space-xs)" }}>
            <div
              style={{
                display: "flex",
                gap: "var(--space-sm)",
                alignItems: "center",
              }}
            >
              {childTypeNames.length > 0 && (
                <select
                  value={eachChild ? query.each_child : ""}
                  onChange={(e) =>
                    // Changing the scope invalidates a declared state pairing
                    // (device-level vs child-level variables) — drop it.
                    updateQuery(
                      i,
                      buildQueryEntry(send, e.target.value, when, undefined, ""),
                    )
                  }
                  title="Send once, or once per registered child of a type"
                  style={{ width: 130, fontSize: "var(--font-size-sm)" }}
                >
                  <option value="">Once</option>
                  {childTypeNames.map((t) => (
                    <option key={t} value={t}>
                      Per {draft.child_entity_types?.[t]?.label || t}
                    </option>
                  ))}
                </select>
              )}
              <input
                value={send}
                onChange={(e) =>
                  updateQuery(
                    i,
                    buildQueryEntry(
                      e.target.value,
                      eachChild ? query.each_child : "",
                      when,
                      undefined,
                      queryFor,
                    ),
                  )
                }
                placeholder={
                  eachChild
                    ? "e.g., ?VOUT{child_id}\\r"
                    : draft.transport === "osc"
                      ? "e.g., /xremote or get_status"
                      : "e.g., %1POWR ?\\r"
                }
                style={{
                  flex: 1,
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
              {gateFields.length > 0 && (
                <select
                  value={when}
                  onChange={(e) =>
                    updateQuery(
                      i,
                      buildQueryEntry(
                        send,
                        eachChild ? query.each_child : "",
                        e.target.value,
                        undefined,
                        queryFor,
                      ),
                    )
                  }
                  title="Only run this query while a config field is on, for example poll meters behind an 'Enable Meters' checkbox"
                  style={{ width: 150, fontSize: "var(--font-size-sm)" }}
                >
                  <option value="">Always</option>
                  {gateFields.map((f) => (
                    <option key={f} value={f}>
                      Only if {f}
                    </option>
                  ))}
                </select>
              )}
              {draft.transport !== "osc" && (() => {
                // Device-level variables on a plain query; the child type's
                // own variables on a per-child query (each child answers
                // from its own state).
                // A query sent once may also be a list read that a
                // child_list rule spreads across every child.
                const reportOptions = eachChild
                  ? Object.keys(
                      draft.child_entity_types?.[query.each_child]
                        ?.state_variables ?? {},
                    )
                  : [...stateVarNames, ...listReads];
                if (reportOptions.length === 0) return null;
                return (
                  <select
                    value={queryFor}
                    onChange={(e) =>
                      updateQuery(
                        i,
                        buildQueryEntry(
                          send,
                          eachChild ? query.each_child : "",
                          when,
                          undefined,
                          e.target.value,
                        ),
                      )
                    }
                    title="Which state variable the device's reply reports, which lets the simulator answer this query without guessing from command names"
                    style={{ width: 150, fontSize: "var(--font-size-sm)" }}
                  >
                    <option value="">Reports (auto)</option>
                    {reportOptions.map((v) => (
                      <option key={v} value={v}>
                        Reports {v}
                      </option>
                    ))}
                  </select>
                );
              })()}
              {eachChild && (
                <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                Children per query
                <input
                  type="number"
                  min={2}
                  value={queryPageSize(query) ?? ""}
                  onChange={(e) => {
                    const n = parseInt(e.target.value, 10);
                    const next = [...queries];
                    next[i] = withPageSize(query, Number.isNaN(n) ? undefined : n);
                    updatePolling({ queries: next });
                  }}
                  placeholder="1 each"
                  aria-label="Children per query"
                  title="Children one reply covers. Set it for a list read that answers a page of children starting at the one asked for: the query then goes out once per page. Leave blank to send it once per child."
                  style={{ width: 70, fontSize: "var(--font-size-sm)" }}
                />
                </label>
              )}
              {gateVariables.length > 0 && (
                <button
                  onClick={() =>
                    setQueryGate(
                      i,
                      stateGate
                        ? undefined
                        : Object.fromEntries([firstGateEntry(gateVariables)!]),
                    )
                  }
                  aria-label={stateGate ? "Remove the state condition" : "Send only while the device reports a state"}
                  title={stateGate ? "Remove the state condition" : "Send only while the device reports a state"}
                  style={{
                    padding: "2px",
                    color: stateGate ? "var(--accent)" : "var(--text-muted)",
                  }}
                >
                  <Filter size={14} />
                </button>
              )}
              <button
                onClick={() => removeQuery(i)}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </div>
            {stateGate && (
              <div style={{ marginLeft: childTypeNames.length > 0 ? 138 : 0 }}>
                <WhenStateEditor
                  draft={draft}
                  gate={stateGate}
                  onChange={(gate) => setQueryGate(i, gate)}
                  lead="Send only while"
                />
              </div>
            )}
            </div>
          );
        })}

        <button
          onClick={addQuery}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
            marginTop: "var(--space-sm)",
          }}
        >
          <Plus size={14} /> Add Query
        </button>
      </div>
    </div>
  );
}
