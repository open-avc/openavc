import { useState } from "react";
import { Plus, X } from "lucide-react";
import type { EnumOption } from "../../api/types";

/** Editor for an enum param/setting's option list. Each row is a wire value
 *  plus an optional human label; a labeled row stores `{value, label}`, an
 *  unlabeled one stores the bare value string (so a driver that never wants
 *  labels round-trips byte-identically). Shared by the Command Builder command
 *  params and the Device Settings editor so both authoring surfaces offer the
 *  same "value + label" table instead of a bare comma-separated list. */

interface Row {
  value: string;
  label: string;
}

function toRows(values: EnumOption[] | undefined): Row[] {
  if (!values || values.length === 0) return [];
  return values.map((v) =>
    v !== null && typeof v === "object"
      ? { value: String(v.value ?? ""), label: String(v.label ?? "") }
      : { value: String(v), label: "" },
  );
}

function fromRows(rows: Row[]): EnumOption[] | undefined {
  const out: EnumOption[] = [];
  for (const r of rows) {
    const value = r.value.trim();
    if (!value) continue;
    const label = r.label.trim();
    out.push(label && label !== value ? { value, label } : value);
  }
  return out.length ? out : undefined;
}

const inputStyle = {
  width: "100%",
  fontSize: "var(--font-size-sm)",
  fontFamily: "var(--font-mono)",
} as const;

export function EnumValuesEditor({
  values,
  onChange,
}: {
  values: EnumOption[] | undefined;
  onChange: (values: EnumOption[] | undefined) => void;
}) {
  // Local row state so a row stays visible while its value field is being
  // typed/cleared (emitting through fromRows would otherwise drop an
  // in-progress empty row). Seeded once from props — this is the sole editor
  // of the field.
  const [rows, setRows] = useState<Row[]>(() => {
    const r = toRows(values);
    return r.length ? r : [{ value: "", label: "" }];
  });

  const apply = (next: Row[]) => {
    setRows(next);
    onChange(fromRows(next));
  };

  const setRow = (i: number, patch: Partial<Row>) =>
    apply(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  const removeRow = (i: number) => {
    const next = rows.filter((_, idx) => idx !== i);
    apply(next.length ? next : [{ value: "", label: "" }]);
  };

  const addRow = () => apply([...rows, { value: "", label: "" }]);

  return (
    <div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 24px",
          gap: "var(--space-xs)",
          marginBottom: 2,
          fontSize: "11px",
          color: "var(--text-muted)",
        }}
      >
        <span>Value (on the wire)</span>
        <span>Label (shown to users, optional)</span>
        <span />
      </div>
      {rows.map((row, i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 24px",
            gap: "var(--space-xs)",
            alignItems: "center",
            marginBottom: "var(--space-xs)",
          }}
        >
          <input
            value={row.value}
            onChange={(e) => setRow(i, { value: e.target.value })}
            placeholder="e.g. 0f"
            style={inputStyle}
          />
          <input
            value={row.label}
            onChange={(e) => setRow(i, { label: e.target.value })}
            placeholder="e.g. Multi Channel Stereo"
            style={{ ...inputStyle, fontFamily: "inherit" }}
          />
          <button
            type="button"
            onClick={() => removeRow(i)}
            title="Remove value"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 2,
              color: "var(--text-muted)",
            }}
          >
            <X size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={addRow}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          fontSize: "11px",
          color: "var(--accent)",
          padding: "2px 0",
        }}
      >
        <Plus size={12} /> Add value
      </button>
      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 2 }}>
        Leave the label blank to send the value as-is. Pickers show the label
        and send the value.
      </div>
    </div>
  );
}
