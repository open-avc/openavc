import { useState } from "react";
import { Trash2 } from "lucide-react";

// The wire value map editors: rows of "value -> what is sent". A command
// parameter's map takes any value (WireValueMapEditor); a boolean or enum
// device setting's map is keyed by the values the setting can take
// (FixedWireValueMapEditor). Both look the same.

export type WireValueMap = Record<string, string | number>;

const addButtonStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--accent)",
  padding: "2px 0",
  display: "block",
  marginTop: "var(--space-xs)",
};

const headerStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  gap: "var(--space-xs)",
  alignItems: "center",
  marginBottom: 2,
};

const cellStyle: React.CSSProperties = {
  width: 90,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-sm)",
};

const fixedKeyStyle: React.CSSProperties = {
  ...cellStyle,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

function Arrow() {
  return <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>→</span>;
}

/** Optional wire-value translation for a param: rows of "value" -> "wire
 *  value" applied by the runtime after validation, before the value is
 *  substituted into the send template. Use when the value the integrator
 *  picks differs from what the protocol wants on the wire — a 1-based child
 *  ID on a 0-based protocol, a named preset that sends a code. Unmapped
 *  values pass through unchanged. */
export function WireValueMapEditor({
  map,
  onChange,
  addTitle = "Translate the picked value to a different value on the wire (e.g. child ID 1 sends channel 0)",
}: {
  map: WireValueMap | undefined;
  onChange: (map: WireValueMap | undefined) => void;
  addTitle?: string;
}) {
  const rows = Object.entries(map ?? {});
  if (rows.length === 0) {
    return (
      <button
        onClick={() => onChange({ "": "" })}
        title={addTitle}
        style={addButtonStyle}
      >
        + Wire value map
      </button>
    );
  }
  const rebuild = (
    mutate: (next: WireValueMap) => void,
  ) => {
    const next: WireValueMap = { ...(map ?? {}) };
    mutate(next);
    onChange(Object.keys(next).length > 0 ? next : undefined);
  };
  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <div style={headerStyle}>
        Wire value map (value → what is sent)
      </div>
      {rows.map(([from, to], ri) => (
        <div key={ri} style={rowStyle}>
          <input
            value={from}
            onChange={(e) =>
              rebuild((next) => {
                const rebuilt: WireValueMap = {};
                for (const [k, v] of Object.entries(next)) {
                  rebuilt[k === from ? e.target.value : k] = v;
                }
                for (const k of Object.keys(next)) delete next[k];
                Object.assign(next, rebuilt);
              })
            }
            placeholder="value"
            style={cellStyle}
          />
          <Arrow />
          <input
            value={String(to)}
            onChange={(e) =>
              rebuild((next) => {
                next[from] = e.target.value;
              })
            }
            placeholder="wire value"
            style={cellStyle}
          />
          <button
            onClick={() => rebuild((next) => delete next[from])}
            style={{ padding: 1, color: "var(--text-muted)" }}
          >
            <Trash2 size={10} />
          </button>
        </div>
      ))}
      <button
        onClick={() =>
          rebuild((next) => {
            if (!("" in next)) next[""] = "";
          })
        }
        style={{ fontSize: "11px", color: "var(--accent)", padding: "2px 0" }}
      >
        + Add
      </button>
    </div>
  );
}

export interface FixedWireValueRow {
  /** The map key: the value the setting takes. */
  key: string;
  /** What the row is called on screen. */
  label: string;
}

/** A wire value map whose values are fixed: one row per value the setting
 *  can take, each with the word sent in its place. A blank word removes the
 *  row's entry. A key the rows do not name (left over from another type or
 *  a renamed value) is listed after them with a delete button. */
export function FixedWireValueMapEditor({
  map,
  rows,
  onSetWord,
  addTitle,
}: {
  map: WireValueMap | undefined;
  rows: FixedWireValueRow[];
  onSetWord: (key: string, word: string) => void;
  addTitle?: string;
}) {
  // Open from the start when there are words, and kept open once opened, so
  // clearing the last word leaves the rows where they were.
  const [open, setOpen] = useState(() => Object.keys(map ?? {}).length > 0);
  const entries = map ?? {};
  const named = new Set(rows.map((r) => r.key));
  const others = Object.keys(entries).filter((k) => !named.has(k));
  if (!open && Object.keys(entries).length === 0) {
    return (
      <button onClick={() => setOpen(true)} title={addTitle} style={addButtonStyle}>
        + Wire value map
      </button>
    );
  }
  const wordRow = (key: string, label: string, removable: boolean) => (
    <div key={key} style={rowStyle}>
      <span style={fixedKeyStyle} title={key}>
        {label}
      </span>
      <Arrow />
      <input
        value={entries[key] === undefined ? "" : String(entries[key])}
        onChange={(e) => onSetWord(key, e.target.value)}
        placeholder="as is"
        aria-label={`What is sent for ${label}`}
        style={cellStyle}
      />
      {removable && (
        <button
          onClick={() => onSetWord(key, "")}
          title="Remove this entry"
          style={{ padding: 1, color: "var(--text-muted)" }}
        >
          <Trash2 size={10} />
        </button>
      )}
    </div>
  );
  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <div style={headerStyle}>
        Wire value map (value → what is sent)
      </div>
      {rows.map((r) => wordRow(r.key, r.label, false))}
      {others.map((k) => wordRow(k, k, true))}
    </div>
  );
}
