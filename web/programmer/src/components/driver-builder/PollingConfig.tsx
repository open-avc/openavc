import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition } from "../../api/types";

interface PollingConfigProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function PollingConfig({ draft, onUpdate }: PollingConfigProps) {
  const polling = draft.polling ?? {};
  const queries = polling.queries ?? [];

  const updatePolling = (partial: Record<string, unknown>) => {
    onUpdate({ polling: { ...polling, ...partial } });
  };

  const addQuery = () => {
    updatePolling({ queries: [...queries, ""] });
  };

  const removeQuery = (index: number) => {
    updatePolling({ queries: queries.filter((_: unknown, i: number) => i !== index) });
  };

  const updateQuery = (index: number, value: string) => {
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
          value={polling.interval ?? 0}
          onChange={(e) =>
            updatePolling({ interval: parseInt(e.target.value) || 0 })
          }
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
          Set to 0 to disable polling. Typical: 10–30 seconds.
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
          Command strings sent each poll cycle. Include the delimiter at the
          end (e.g., <code>\r</code> or <code>\r\n</code>). You can use
          config field placeholders like{" "}
          <code>{"{set_id}"}</code>.
        </div>

        {queries.map((query: string, i: number) => (
          <div
            key={i}
            style={{
              display: "flex",
              gap: "var(--space-sm)",
              marginBottom: "var(--space-xs)",
              alignItems: "center",
            }}
          >
            <input
              value={query}
              onChange={(e) => updateQuery(i, e.target.value)}
              placeholder="e.g., %1POWR ?\r"
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
        ))}

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
