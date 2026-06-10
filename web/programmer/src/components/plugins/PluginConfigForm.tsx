/**
 * Plugin configuration form — renders a plugin's CONFIG_SCHEMA as a form.
 *
 * Shared by the plugin detail page (PluginsView) and plugin surface views
 * (PluginExtensions), so a control surface's settings are editable from the
 * same view its layout is edited in.
 */
import { useEffect, useState } from "react";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";
import { InlineColorPicker } from "../shared/InlineColorPicker";
import { useProjectStore } from "../../store/projectStore";
import * as api from "../../api/restClient";
import type { SchemaField } from "../../api/types";

export function SchemaFormRenderer({
  schema,
  values,
  onChange,
}: {
  schema: Record<string, SchemaField>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-lg)" }}>
      {Object.entries(schema).map(([key, field]) => {
        // Conditional visibility
        if (field.visible_when) {
          const match = Object.entries(field.visible_when).every(
            ([k, v]) => values[k] === v
          );
          if (!match) return null;
        }

        if (field.type === "group") {
          return (
            <SchemaFieldGroup
              key={key}
              field={field}
              fieldKey={key}
              values={values}
              onChange={onChange}
            />
          );
        }

        if (field.type === "mapping_list" && field.item_schema) {
          return (
            <SchemaFieldMappingList
              key={key}
              field={field}
              items={(values[key] as Record<string, unknown>[]) ?? []}
              onChange={(v) => onChange(key, v)}
            />
          );
        }

        return (
          <SchemaFieldInput
            key={key}
            field={field}
            value={values[key]}
            onChange={(v) => onChange(key, v)}
          />
        );
      })}
    </div>
  );
}

function SchemaFieldGroup({
  field,
  fieldKey,
  values,
  onChange,
}: {
  field: SchemaField;
  fieldKey: string;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  const [collapsed, setCollapsed] = useState(field.collapsed ?? false);
  const groupValues = (values[fieldKey] as Record<string, unknown>) ?? {};

  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        overflow: "hidden",
      }}
    >
      <button
        onClick={() => setCollapsed(!collapsed)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          width: "100%",
          padding: "var(--space-sm) var(--space-md)",
          background: "var(--bg-hover)",
          fontWeight: 500,
          fontSize: "var(--font-size-sm)",
          textAlign: "left",
        }}
      >
        <span style={{ transform: collapsed ? "rotate(-90deg)" : "rotate(0)", transition: "transform var(--transition-fast)" }}>
          ▾
        </span>
        {field.label}
      </button>
      {!collapsed && field.fields && (
        <div style={{ padding: "var(--space-md)" }}>
          <SchemaFormRenderer
            schema={field.fields}
            values={groupValues}
            onChange={(k, v) => {
              onChange(fieldKey, { ...groupValues, [k]: v });
            }}
          />
        </div>
      )}
    </div>
  );
}

function SchemaFieldMappingList({
  field,
  items,
  onChange,
}: {
  field: SchemaField;
  items: Record<string, unknown>[];
  onChange: (value: Record<string, unknown>[]) => void;
}) {
  const schema = field.item_schema!;
  const columns = Object.entries(schema);

  const buildDefaultRow = (): Record<string, unknown> => {
    const row: Record<string, unknown> = {};
    for (const [key, col] of columns) {
      row[key] = col.default ?? "";
    }
    return row;
  };

  const addRow = () => {
    if (field.max_items != null && items.length >= field.max_items) return;
    onChange([...items, buildDefaultRow()]);
  };

  const removeRow = (index: number) => {
    if (field.min_items != null && items.length <= field.min_items) return;
    onChange(items.filter((_, i) => i !== index));
  };

  const updateCell = (rowIndex: number, key: string, value: unknown) => {
    const next = items.map((row, i) => (i === rowIndex ? { ...row, [key]: value } : row));
    onChange(next);
  };

  const cellStyle: React.CSSProperties = {
    padding: "2px 4px",
  };

  const cellInputStyle: React.CSSProperties = {
    width: "100%",
    padding: "var(--space-xs) var(--space-sm)",
    borderRadius: "var(--border-radius)",
    border: "1px solid var(--border-color)",
    background: "var(--bg-surface)",
    color: "var(--text-primary)",
    fontSize: "var(--font-size-sm)",
    boxSizing: "border-box",
  };

  const renderCell = (col: SchemaField, value: unknown, rowIndex: number, key: string) => {
    switch (col.type) {
      case "boolean":
        return (
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => updateCell(rowIndex, key, e.target.checked)}
            style={{ width: 16, height: 16, accentColor: "var(--accent)" }}
          />
        );
      case "select":
        return (
          <select
            value={String(value ?? col.default ?? "")}
            onChange={(e) => updateCell(rowIndex, key, e.target.value)}
            style={cellInputStyle}
          >
            {col.options?.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        );
      case "integer":
      case "float":
        return (
          <input
            type="number"
            value={value != null && value !== "" ? String(value) : ""}
            min={col.min}
            max={col.max}
            step={col.step ?? (col.type === "float" ? 0.1 : 1)}
            placeholder={col.placeholder}
            onChange={(e) => {
              const v = e.target.value;
              updateCell(rowIndex, key, v === "" ? null : col.type === "integer" ? parseInt(v, 10) : parseFloat(v));
            }}
            style={{ ...cellInputStyle, width: 70 }}
          />
        );
      case "color":
        return (
          <InlineColorPicker
            value={typeof value === "string" ? value : ""}
            onChange={(c) => updateCell(rowIndex, key, c)}
          />
        );
      case "state_key":
        return (
          <VariableKeyPicker
            value={String(value ?? "")}
            onChange={(k) => updateCell(rowIndex, key, k)}
            placeholder={col.placeholder ?? "Select state key..."}
            style={{ minWidth: 200 }}
          />
        );
      case "macro_ref":
        return (
          <MacroRefPicker
            value={String(value ?? "")}
            onChange={(v) => updateCell(rowIndex, key, v)}
            style={cellInputStyle}
          />
        );
      case "device_ref":
        return (
          <DeviceRefPicker
            value={String(value ?? "")}
            onChange={(v) => updateCell(rowIndex, key, v)}
            style={cellInputStyle}
          />
        );
      case "command_ref":
        return (
          <CommandRefPicker
            value={String(value ?? "")}
            deviceId={String(items[rowIndex]?.[col.device_field ?? "device_id"] ?? "")}
            onChange={(v) => updateCell(rowIndex, key, v)}
            style={cellInputStyle}
          />
        );
      default:
        return (
          <input
            type="text"
            value={String(value ?? "")}
            placeholder={col.placeholder}
            onChange={(e) => updateCell(rowIndex, key, e.target.value)}
            style={cellInputStyle}
          />
        );
    }
  };

  return (
    <div>
      <label
        style={{
          display: "block",
          fontSize: "var(--font-size-sm)",
          fontWeight: 500,
          marginBottom: "var(--space-xs)",
          color: "var(--text-secondary)",
        }}
      >
        {field.label}
      </label>
      {field.description && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          {field.description}
        </div>
      )}
      <div
        style={{
          border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)",
          overflow: "hidden",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--font-size-sm)" }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)" }}>
              {columns.map(([key, col]) => (
                <th
                  key={key}
                  style={{
                    padding: "var(--space-xs) var(--space-sm)",
                    textAlign: "left",
                    fontWeight: 500,
                    color: "var(--text-secondary)",
                    fontSize: 11,
                    borderBottom: "1px solid var(--border-color)",
                  }}
                >
                  {col.label}
                </th>
              ))}
              <th style={{ width: 32, borderBottom: "1px solid var(--border-color)" }} />
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr>
                <td
                  colSpan={columns.length + 1}
                  style={{ padding: "var(--space-md)", textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}
                >
                  No items. Click + to add one.
                </td>
              </tr>
            )}
            {items.map((row, rowIndex) => (
              <tr key={rowIndex} style={{ borderBottom: "1px solid var(--border-color)" }}>
                {columns.map(([key, col]) => {
                  // Check visible_when condition against this row's values
                  if (col.visible_when) {
                    const visible = Object.entries(col.visible_when).every(
                      ([k, v]) => row[k] === v
                    );
                    if (!visible) {
                      return <td key={key} style={cellStyle} />;
                    }
                  }
                  return (
                    <td key={key} style={cellStyle}>
                      {renderCell(col, row[key], rowIndex, key)}
                    </td>
                  );
                })}
                <td style={{ ...cellStyle, textAlign: "center" }}>
                  <button
                    onClick={() => removeRow(rowIndex)}
                    title="Remove row"
                    style={{
                      background: "none",
                      color: "var(--text-muted)",
                      fontSize: 14,
                      cursor: "pointer",
                      padding: "2px 6px",
                      borderRadius: "var(--border-radius)",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.color = "var(--color-error)")}
                    onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-muted)")}
                  >
                    &times;
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button
          onClick={addRow}
          disabled={field.max_items != null && items.length >= field.max_items}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "var(--space-xs)",
            width: "100%",
            padding: "var(--space-xs) var(--space-sm)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            cursor: "pointer",
            borderTop: "1px solid var(--border-color)",
          }}
        >
          + Add
        </button>
      </div>
    </div>
  );
}

// ──── Ref Pickers (macro, device, command) ────

function MacroRefPicker({
  value,
  onChange,
  style,
}: {
  value: string;
  onChange: (v: string) => void;
  style?: React.CSSProperties;
}) {
  const project = useProjectStore((s) => s.project);
  const macros = project?.macros ?? [];
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={style ?? { width: "100%", padding: "var(--space-sm) var(--space-md)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", background: "var(--bg-surface)", color: "var(--text-primary)", fontSize: "var(--font-size-base)" }}
    >
      <option value="">Select macro...</option>
      {macros.map((m) => (
        <option key={m.id} value={m.id}>{m.name}</option>
      ))}
    </select>
  );
}

function DeviceRefPicker({
  value,
  onChange,
  style,
}: {
  value: string;
  onChange: (v: string) => void;
  style?: React.CSSProperties;
}) {
  const project = useProjectStore((s) => s.project);
  const devices = project?.devices ?? [];
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={style ?? { width: "100%", padding: "var(--space-sm) var(--space-md)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", background: "var(--bg-surface)", color: "var(--text-primary)", fontSize: "var(--font-size-base)" }}
    >
      <option value="">Select device...</option>
      {devices.map((d) => (
        <option key={d.id} value={d.id}>{d.name}</option>
      ))}
    </select>
  );
}

function CommandRefPicker({
  value,
  deviceId,
  onChange,
  style,
}: {
  value: string;
  deviceId: string;
  onChange: (v: string) => void;
  style?: React.CSSProperties;
}) {
  const [commands, setCommands] = useState<string[]>([]);
  useEffect(() => {
    if (!deviceId) {
      setCommands([]);
      return;
    }
    api.getDevice(deviceId)
      .then((info) => setCommands(Object.keys(info?.commands ?? {})))
      .catch(() => setCommands([]));
  }, [deviceId]);

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={style ?? { width: "100%", padding: "var(--space-sm) var(--space-md)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", background: "var(--bg-surface)", color: "var(--text-primary)", fontSize: "var(--font-size-base)" }}
    >
      <option value="">{deviceId ? "Select command..." : "Select device first"}</option>
      {commands.map((cmd) => (
        <option key={cmd} value={cmd}>{cmd}</option>
      ))}
    </select>
  );
}

function SchemaFieldInput({
  field,
  value,
  onChange,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "var(--space-sm) var(--space-md)",
    borderRadius: "var(--border-radius)",
    border: "1px solid var(--border-color)",
    background: "var(--bg-surface)",
    color: "var(--text-primary)",
    fontSize: "var(--font-size-base)",
  };

  let input: React.ReactNode;

  switch (field.type) {
    case "boolean":
      input = (
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
            style={{ width: 16, height: 16, accentColor: "var(--accent)" }}
          />
          <span style={{ fontSize: "var(--font-size-sm)" }}>{field.label}</span>
        </label>
      );
      return <div>{input}{field.description && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>{field.description}</div>}</div>;

    case "select":
      input = (
        <select
          value={String(value ?? field.default ?? "")}
          onChange={(e) => onChange(e.target.value)}
          style={inputStyle}
        >
          {field.options?.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      );
      break;

    case "integer":
    case "float":
      input = (
        <input
          type="number"
          value={value != null ? String(value) : ""}
          min={field.min}
          max={field.max}
          step={field.step ?? (field.type === "float" ? 0.1 : 1)}
          placeholder={field.placeholder}
          onChange={(e) => {
            const v = e.target.value;
            onChange(v === "" ? null : field.type === "integer" ? parseInt(v, 10) : parseFloat(v));
          }}
          style={inputStyle}
        />
      );
      break;

    case "color":
      input = (
        <InlineColorPicker
          value={typeof value === "string" ? value : String(field.default ?? "")}
          onChange={(c) => onChange(c)}
        />
      );
      break;

    case "state_key":
      input = (
        <VariableKeyPicker
          value={String(value ?? "")}
          onChange={(key) => onChange(key)}
          placeholder={field.placeholder ?? "Select state key..."}
        />
      );
      break;

    case "macro_ref":
      input = <MacroRefPicker value={String(value ?? "")} onChange={(v) => onChange(v)} />;
      break;

    case "device_ref":
      input = <DeviceRefPicker value={String(value ?? "")} onChange={(v) => onChange(v)} />;
      break;

    case "text":
      input = (
        <textarea
          value={String(value ?? "")}
          placeholder={field.placeholder}
          rows={6}
          onChange={(e) => onChange(e.target.value)}
          style={{
            ...inputStyle,
            fontFamily: "var(--font-mono, monospace)",
            fontSize: "var(--font-size-sm)",
            resize: "vertical",
            minHeight: "120px",
          }}
        />
      );
      break;

    case "string":
    default:
      input = (
        <input
          type="text"
          value={String(value ?? "")}
          placeholder={field.placeholder ?? (field.type === "macro_ref" ? "Macro ID" : field.type === "device_ref" ? "Device ID" : "")}
          maxLength={field.max_length}
          onChange={(e) => onChange(e.target.value)}
          style={inputStyle}
        />
      );
      break;
  }

  return (
    <div>
      <label
        style={{
          display: "block",
          fontSize: "var(--font-size-sm)",
          fontWeight: 500,
          marginBottom: "var(--space-xs)",
          color: "var(--text-secondary)",
        }}
      >
        {field.label}
        {field.required && <span style={{ color: "var(--color-error)", marginLeft: 2 }}>*</span>}
      </label>
      {input}
      {field.description && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
          {field.description}
        </div>
      )}
    </div>
  );
}
