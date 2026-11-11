/** A table of text keys and values edited as rows, in the order they were
 *  added: an HTTP command's headers and query parameters, and the HTTP
 *  transport's default headers. A row whose key is cleared drops out; a new
 *  row gets the first free `key<N>` for the author to rename. */

import { Trash2 } from "lucide-react";

export function KeyValueList({
  values,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  monoValue,
}: {
  values: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  monoValue?: boolean;
}) {
  // Render insertion-ordered to keep the user's typing flow stable.
  const entries = Object.entries(values);

  const updateRow = (index: number, key: string, value: string) => {
    const next: Record<string, string> = {};
    entries.forEach(([k, v], i) => {
      if (i === index) {
        if (key) next[key] = value;
      } else {
        next[k] = v;
      }
    });
    onChange(next);
  };

  const removeRow = (index: number) => {
    const next: Record<string, string> = {};
    entries.forEach(([k, v], i) => {
      if (i !== index) next[k] = v;
    });
    onChange(next);
  };

  const addRow = () => {
    // Insert an empty placeholder key the user can fill in.
    let key = "";
    let counter = 1;
    while (key === "" || key in values) {
      key = `key${counter}`;
      counter++;
    }
    onChange({ ...values, [key]: "" });
  };

  return (
    <div>
      {entries.map(([k, v], i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 2fr auto",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
            alignItems: "center",
          }}
        >
          <input
            value={k}
            onChange={(e) => updateRow(i, e.target.value, v)}
            placeholder={keyPlaceholder}
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <input
            value={v}
            onChange={(e) => updateRow(i, k, e.target.value)}
            placeholder={valuePlaceholder}
            style={{
              fontFamily: monoValue ? "var(--font-mono)" : "inherit",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <button
            onClick={() => removeRow(i)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        onClick={addRow}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Add
      </button>
    </div>
  );
}
