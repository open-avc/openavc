import { useMemo, useState } from "react";
import { Plus, X } from "lucide-react";
import { useProjectStore, syncDeviceConfig } from "../../store/projectStore";
import * as api from "../../api/restClient";

// Generic device-page editor for a `type: "table"` config field. A driver
// declares a repeatable typed-row config (the columns it wants) in its
// config_schema; this renders the friendly row editor and writes the resulting
// list of row objects back into the device config — the first-class replacement
// for hand-typing a structured list into a textarea. It is driven entirely by
// the field's column schema, so any driver that declares a table field gets the
// same editor with no bespoke component.
//
// The field's stored value is an array of row objects keyed by column id
// (e.g. Modbus register map: [{name, area, address, datatype, scale, access,
// unit}, ...]). The save path mirrors InlineProtocolEditor: PUT the whole
// config (device config is stored verbatim), then mirror into the project store.

interface ColumnDef {
  type?: string; // string | text | integer | number | float | boolean | enum
  label?: string;
  help?: string;
  required?: boolean;
  min?: number;
  max?: number;
  default?: unknown;
  values?: (string | { value?: unknown; label?: unknown })[];
}

// Each row holds raw strings per column while editing (so typing "-" or ""
// in a number cell doesn't churn types); coerced to typed values on save.
type Row = Record<string, string>;

const NUMERIC = new Set(["integer", "number", "float"]);

function optionValue(v: string | { value?: unknown; label?: unknown }): string {
  return v !== null && typeof v === "object" ? String(v.value ?? "") : String(v);
}
function optionLabel(v: string | { value?: unknown; label?: unknown }): string {
  if (v !== null && typeof v === "object") {
    return String(v.label ?? v.value ?? "");
  }
  return String(v);
}

// Stored typed value -> raw string for the input.
function toCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function existingRows(value: unknown, colKeys: string[]): Row[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => {
    const row: Row = {};
    const obj = (entry ?? {}) as Record<string, unknown>;
    for (const k of colKeys) row[k] = toCell(obj[k]);
    return row;
  });
}

function blankRow(columns: Record<string, ColumnDef>, colKeys: string[]): Row {
  const row: Row = {};
  for (const k of colKeys) {
    // Seed a declared column default so a new row starts sensible (e.g. an
    // enum's default option), not blank.
    row[k] = columns[k].default != null ? toCell(columns[k].default) : "";
  }
  return row;
}

export function ConfigTableEditor({
  deviceId,
  fieldKey,
  fieldSchema,
  onSaved,
}: {
  deviceId: string;
  fieldKey: string;
  fieldSchema: Record<string, unknown>;
  connected: boolean;
  onSaved: () => void;
}) {
  const project = useProjectStore((s) => s.project);
  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const savedConfig = useMemo(
    () => (deviceConfig?.config ?? {}) as Record<string, unknown>,
    [deviceConfig],
  );

  const columns = (fieldSchema.columns ?? {}) as Record<string, ColumnDef>;
  const colKeys = useMemo(() => Object.keys(columns), [columns]);
  const label = String(fieldSchema.label || fieldKey);
  const help = fieldSchema.help ? String(fieldSchema.help) : "";
  const rowLabel = String(fieldSchema.row_label || "row");

  const [rows, setRows] = useState<Row[]>(() =>
    existingRows(savedConfig[fieldKey], colKeys),
  );
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = (next: Row[]) => {
    setRows(next);
    setDirty(true);
    setSaved(false);
  };
  const setCell = (i: number, col: string, val: string) =>
    apply(rows.map((r, idx) => (idx === i ? { ...r, [col]: val } : r)));
  const removeRow = (i: number) => apply(rows.filter((_, idx) => idx !== i));
  const addRow = () => apply([...rows, blankRow(columns, colKeys)]);

  // Build the cleaned, typed array and validate. Fully-empty rows are dropped;
  // everything else must satisfy required / numeric / min-max per its column.
  const buildAndValidate = (): { rows: Record<string, unknown>[] } | { error: string } => {
    const out: Record<string, unknown>[] = [];
    for (let i = 0; i < rows.length; i++) {
      const raw = rows[i];
      const nonEmpty = colKeys.some((k) => (raw[k] ?? "").trim() !== "");
      if (!nonEmpty) continue; // drop a blank row silently
      const obj: Record<string, unknown> = {};
      for (const k of colKeys) {
        const col = columns[k];
        const type = String(col.type || "string");
        const s = (raw[k] ?? "").trim();
        if (s === "") {
          if (col.required) {
            return { error: `${rowLabel} ${i + 1}: "${col.label || k}" is required.` };
          }
          continue; // omit empty optional cell; driver applies its default
        }
        if (type === "boolean") {
          obj[k] = s === "true";
        } else if (NUMERIC.has(type)) {
          const n = Number(s);
          if (!Number.isFinite(n)) {
            return { error: `${rowLabel} ${i + 1}: "${col.label || k}" must be a number.` };
          }
          if (col.min != null && n < col.min) {
            return { error: `${rowLabel} ${i + 1}: "${col.label || k}" must be ≥ ${col.min}.` };
          }
          if (col.max != null && n > col.max) {
            return { error: `${rowLabel} ${i + 1}: "${col.label || k}" must be ≤ ${col.max}.` };
          }
          obj[k] = type === "integer" ? Math.trunc(n) : n;
        } else {
          obj[k] = s; // string / text / enum
        }
      }
      out.push(obj);
    }
    return { rows: out };
  };

  const handleSave = async () => {
    const result = buildAndValidate();
    if ("error" in result) {
      setError(result.error);
      return;
    }
    const newConfig: Record<string, unknown> = {
      ...savedConfig,
      [fieldKey]: result.rows,
    };
    setSaving(true);
    setError(null);
    try {
      await api.updateDevice(deviceId, { config: newConfig });
      // The endpoint persisted + bumped the revision — re-sync the store
      // (fresh ETag when clean, mirror when unsaved edits are in flight).
      await syncDeviceConfig(deviceId, newConfig);
      setDirty(false);
      setSaved(true);
      onSaved();
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const cellStyle: React.CSSProperties = {
    width: "100%",
    fontSize: "var(--font-size-sm)",
    fontFamily: "var(--font-mono)",
  };
  // A minimum width per column so many columns scroll horizontally rather than
  // crushing (a register map is wide). The trailing 28px is the remove button.
  const gridTemplate = `${colKeys
    .map((k) => (NUMERIC.has(String(columns[k].type)) ? "minmax(90px, 0.7fr)" : "minmax(120px, 1fr)"))
    .join(" ")} 28px`;

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-lg)",
        marginBottom: "var(--space-md)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-sm)",
        }}
      >
        <h3
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
          }}
        >
          {label}
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          {saved && (
            <span style={{ fontSize: "11px", color: "var(--color-success)" }}>Saved</span>
          )}
          <button
            type="button"
            onClick={handleSave}
            disabled={!dirty || saving}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: dirty ? "var(--accent)" : "var(--bg-hover)",
              color: dirty ? "var(--accent-contrast, #fff)" : "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
              cursor: dirty && !saving ? "pointer" : "default",
            }}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {help && (
        <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          {help}
        </div>
      )}

      {error && (
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--error, #f44336)",
            marginBottom: "var(--space-sm)",
          }}
        >
          {error}
        </div>
      )}

      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: "min-content" }}>
          {/* Header */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: gridTemplate,
              gap: "var(--space-xs)",
              marginBottom: 4,
              fontSize: "11px",
              color: "var(--text-muted)",
            }}
          >
            {colKeys.map((k) => (
              <span key={k} title={columns[k].help ? String(columns[k].help) : undefined}>
                {String(columns[k].label || k)}
                {columns[k].required && (
                  <span style={{ color: "var(--error, #f44336)", marginLeft: 2 }}>*</span>
                )}
              </span>
            ))}
            <span />
          </div>

          {rows.length === 0 && (
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-muted)",
                padding: "var(--space-sm) 0",
              }}
            >
              No {rowLabel}s yet. Add one below.
            </div>
          )}

          {rows.map((row, i) => (
            <div
              key={i}
              style={{
                display: "grid",
                gridTemplateColumns: gridTemplate,
                gap: "var(--space-xs)",
                alignItems: "center",
                marginBottom: "var(--space-xs)",
              }}
            >
              {colKeys.map((k) => {
                const col = columns[k];
                const type = String(col.type || "string");
                if (type === "enum") {
                  return (
                    <select
                      key={k}
                      value={row[k] ?? ""}
                      onChange={(e) => setCell(i, k, e.target.value)}
                      style={{ ...cellStyle, fontFamily: "inherit" }}
                    >
                      <option value="">—</option>
                      {(col.values ?? []).map((v) => {
                        const val = optionValue(v);
                        return (
                          <option key={val} value={val}>
                            {optionLabel(v)}
                          </option>
                        );
                      })}
                    </select>
                  );
                }
                if (type === "boolean") {
                  return (
                    <input
                      key={k}
                      type="checkbox"
                      checked={row[k] === "true"}
                      onChange={(e) => setCell(i, k, e.target.checked ? "true" : "false")}
                      style={{ justifySelf: "start" }}
                    />
                  );
                }
                return (
                  <input
                    key={k}
                    type={NUMERIC.has(type) ? "number" : "text"}
                    value={row[k] ?? ""}
                    onChange={(e) => setCell(i, k, e.target.value)}
                    placeholder={col.default != null ? String(col.default) : ""}
                    style={cellStyle}
                  />
                );
              })}
              <button
                type="button"
                onClick={() => removeRow(i)}
                title={`Remove ${rowLabel}`}
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
        </div>
      </div>

      <button
        type="button"
        onClick={addRow}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          fontSize: "11px",
          color: "var(--accent)",
          padding: "4px 0",
          marginTop: 2,
        }}
      >
        <Plus size={12} /> Add {rowLabel}
      </button>
    </div>
  );
}
