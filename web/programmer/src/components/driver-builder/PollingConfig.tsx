import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition, DriverEachChildQuery } from "../../api/types";

interface PollingConfigProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

type PollQuery = string | DriverEachChildQuery;

function isEachChild(q: PollQuery): q is DriverEachChildQuery {
  return typeof q === "object" && q !== null && "each_child" in q;
}

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

  const addQuery = () => {
    updatePolling({ queries: [...queries, ""] });
  };

  const removeQuery = (index: number) => {
    updatePolling({ queries: queries.filter((_: unknown, i: number) => i !== index) });
  };

  const updateQuery = (index: number, value: PollQuery) => {
    const next = [...queries];
    next[index] = value;
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
              child — <code>{"{child_id}"}</code> inserts its ID.
            </>
          )}
        </div>

        {queries.map((query: PollQuery, i: number) => {
          const eachChild = isEachChild(query);
          return (
            <div
              key={i}
              style={{
                display: "flex",
                gap: "var(--space-sm)",
                marginBottom: "var(--space-xs)",
                alignItems: "center",
              }}
            >
              {childTypeNames.length > 0 && (
                <select
                  value={eachChild ? query.each_child : ""}
                  onChange={(e) => {
                    const t = e.target.value;
                    if (!t) {
                      // Back to a plain query; keep the template text.
                      updateQuery(i, eachChild ? query.send : (query as string));
                    } else {
                      const send = eachChild
                        ? query.send
                        : (query as string) || "";
                      updateQuery(i, { each_child: t, send });
                    }
                  }}
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
                value={eachChild ? query.send : (query as string)}
                onChange={(e) =>
                  updateQuery(
                    i,
                    eachChild
                      ? { each_child: query.each_child, send: e.target.value }
                      : e.target.value,
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
              <button
                onClick={() => removeQuery(i)}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
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
