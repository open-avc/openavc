/** A device setting's HTTP write in the Driver Builder: the request line,
 *  body, headers and query parameters, each filled with {value} and the
 *  config as a command's request is. The runtime half is the YAML driver
 *  runtime's `setting_writes.py`, through the request builder a command
 *  uses (`http_commands.py`). */

import { Plus, Trash2 } from "lucide-react";
import type { DriverDeviceSettingDef } from "../../api/types";
import { KeyValueList } from "./KeyValueList";
import { valuesAsText } from "./fieldText";

type WriteDef = NonNullable<DriverDeviceSettingDef["write"]>;

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
  marginTop: "var(--space-sm)",
};

const helpStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
  marginTop: 2,
};

/** A map with rows in it, else undefined, so the YAML never carries an
 *  empty map. */
function unlessEmpty(map: Record<string, string>): Record<string, string> | undefined {
  return Object.keys(map).length ? map : undefined;
}

export function HttpWriteFields({
  write,
  onWrite,
}: {
  write: WriteDef | undefined;
  onWrite: (partial: Partial<WriteDef>) => void;
}) {
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-sm)" }}>
        <select
          value={write?.method ?? "POST"}
          onChange={(e) => onWrite({ method: e.target.value })}
          style={{ width: 90 }}
        >
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="GET">GET</option>
        </select>
        <input
          value={write?.path ?? ""}
          onChange={(e) => onWrite({ path: e.target.value })}
          placeholder="/api/settings"
          style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
        />
      </div>
      <label style={labelStyle}>
        Request body (optional)
      </label>
      <textarea
        value={write?.body ?? ""}
        onChange={(e) => onWrite({ body: e.target.value || undefined })}
        placeholder={'JSON, e.g. {"name": "{value}"}'}
        rows={2}
        style={{
          width: "100%",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--font-size-sm)",
          resize: "vertical",
        }}
      />
      <label style={labelStyle}>
        Headers (optional)
      </label>
      <HttpHeadersEditor
        headers={valuesAsText(write?.headers)}
        onChange={(headers) => onWrite({ headers: unlessEmpty(headers) })}
      />
      <label style={labelStyle}>
        Query parameters (optional)
      </label>
      <KeyValueList
        values={valuesAsText(write?.query_params)}
        onChange={(map) => onWrite({ query_params: unlessEmpty(map) })}
        keyPlaceholder="key"
        valuePlaceholder="e.g. {value}"
        monoValue
      />
      <div style={helpStyle}>
        Body, headers and query parameters support {"{value}"} and
        config placeholders. Query parameters are added to the
        address as ?key=value. Set Content-Type here for non-JSON
        bodies.
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// HTTP headers sub-editor — key/value pairs sent with the HTTP write.
// ──────────────────────────────────────────────────────────────────────────
function HttpHeadersEditor({
  headers,
  onChange,
}: {
  headers: Record<string, string>;
  onChange: (headers: Record<string, string>) => void;
}) {
  const rows = Object.entries(headers);

  const setRow = (index: number, name: string, value: string) => {
    const next: Record<string, string> = {};
    rows.forEach(([k, v], i) => {
      if (i === index) {
        if (name) next[name] = value;
      } else {
        next[k] = v;
      }
    });
    onChange(next);
  };
  const removeRow = (index: number) =>
    onChange(Object.fromEntries(rows.filter((_, i) => i !== index)));
  const add = () => onChange({ ...headers, "": "" });

  return (
    <div style={{ marginTop: "var(--space-xs)" }}>
      {rows.map(([name, value], i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr auto",
            gap: "var(--space-xs)",
            alignItems: "center",
            marginBottom: "var(--space-xs)",
          }}
        >
          <input
            value={name}
            onChange={(e) => setRow(i, e.target.value, value)}
            placeholder="Header"
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <input
            value={value}
            onChange={(e) => setRow(i, name, e.target.value)}
            placeholder="Value"
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <button
            type="button"
            onClick={() => removeRow(i)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "11px",
          color: "var(--accent)",
        }}
      >
        <Plus size={12} /> Add header
      </button>
    </div>
  );
}
