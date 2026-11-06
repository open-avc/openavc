/**
 * Plugin configuration form — renders a plugin's CONFIG_SCHEMA as a form.
 *
 * Shared by the plugin detail page (PluginsView) and plugin surface views
 * (PluginExtensions), so a control surface's settings are editable from the
 * same view its layout is edited in.
 *
 * A login (a field `isSecretSetting` calls a secret, at the top level or in a
 * group) never shows a value: the server sends the "saved on this system"
 * marker in its place, shown as "Saved on this system" with Change. With
 * `onSaveLogin` it is a SavedLoginField that saves on its own button, so the
 * form's own saving-as-you-type never carries part of a password and `values`
 * only ever holds the marker for it.
 */
import { useState } from "react";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";
import { InlineColorPicker } from "../shared/InlineColorPicker";
import { MacroRefPicker, DeviceRefPicker, CommandRefPicker } from "../shared/RefPickers";
import { SearchableSelect } from "../shared/SearchableSelect";
import { isSecretSetting } from "../shared/secretSetting";
import {
  PASSWORD_MANAGER_IGNORE,
  SavedLoginField,
  type SavedLoginState,
} from "../shared/SavedLoginField";
import { SAVED_LOGIN } from "../../views/devices/deviceLogins";
import type { SchemaField } from "../../api/types";

/** Saves one login field at once: its path (["password"], or
 *  ["connection", "api_key"] for a field in a group) and the value typed.
 *  Resolves false when it was not saved. */
export type SaveLoginHandler = (path: string[], value: string) => Promise<boolean | void>;

/** Where a plugin's login field stands: `needsLogin` is the server's list of
 *  the logins the project marks saved that this system does not have, as
 *  dotted paths ("password", "connection.api_key"). */
export function pluginLoginState(
  value: unknown,
  path: string[],
  needsLogin: readonly string[] | undefined,
): SavedLoginState {
  if (needsLogin?.includes(path.join("."))) return "needs_login";
  return typeof value === "string" && value !== "" ? "saved" : "none";
}

/** `config` with `value` at `path`, groups copied on the way down. */
export function withConfigValueAt(
  config: Record<string, unknown>,
  path: string[],
  value: unknown,
): Record<string, unknown> {
  const [head, ...rest] = path;
  if (head === undefined) return config;
  if (rest.length === 0) return { ...config, [head]: value };
  const inner = config[head];
  const group = inner && typeof inner === "object" && !Array.isArray(inner)
    ? (inner as Record<string, unknown>)
    : {};
  return { ...config, [head]: withConfigValueAt(group, rest, value) };
}

/** The value at `path` in `config`, or undefined. */
export function configValueAt(config: Record<string, unknown>, path: string[]): unknown {
  let at: unknown = config;
  for (const key of path) {
    if (!at || typeof at !== "object" || Array.isArray(at)) return undefined;
    at = (at as Record<string, unknown>)[key];
  }
  return at;
}

export function SchemaFormRenderer({
  schema,
  values,
  onChange,
  needsLogin,
  onSaveLogin,
  path = [],
}: {
  schema: Record<string, SchemaField>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  needsLogin?: readonly string[];
  onSaveLogin?: SaveLoginHandler;
  /** The group this renderer draws, for a login's path. */
  path?: string[];
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
              needsLogin={needsLogin}
              onSaveLogin={onSaveLogin}
              path={[...path, key]}
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

        if (isSecretSetting(key, field)) {
          return (
            <SchemaLoginField
              key={key}
              field={field}
              fieldKey={key}
              value={values[key]}
              onChange={(v) => onChange(key, v)}
              state={pluginLoginState(values[key], [...path, key], needsLogin)}
              onSave={onSaveLogin ? (v) => onSaveLogin([...path, key], v) : undefined}
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
  needsLogin,
  onSaveLogin,
  path,
}: {
  field: SchemaField;
  fieldKey: string;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  needsLogin?: readonly string[];
  onSaveLogin?: SaveLoginHandler;
  path: string[];
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
            needsLogin={needsLogin}
            onSaveLogin={onSaveLogin}
            path={path}
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

  // A picker draws its own trigger, so it takes the cell's LAYOUT and none of
  // its skin -- handing it cellInputStyle put a border around a border.
  const cellPickerStyle: React.CSSProperties = { width: "100%", boxSizing: "border-box" };

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
          <SearchableSelect
            value={String(value ?? col.default ?? "")}
            onChange={(v) => updateCell(rowIndex, key, v)}
            options={(col.options ?? []).map((opt) => ({
              value: String(opt.value),
              label: opt.label,
            }))}
            allowEmpty={false}
            placeholder="Select..."
            searchPlaceholder="Search..."
            style={cellPickerStyle}
          />
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
            style={cellPickerStyle}
          />
        );
      case "device_ref":
        return (
          <DeviceRefPicker
            value={String(value ?? "")}
            onChange={(v) => updateCell(rowIndex, key, v)}
            style={cellPickerStyle}
          />
        );
      case "command_ref":
        return (
          <CommandRefPicker
            value={String(value ?? "")}
            deviceId={String(items[rowIndex]?.[col.device_field ?? "device_id"] ?? "")}
            onChange={(v) => updateCell(rowIndex, key, v)}
            style={cellPickerStyle}
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

function FieldLabel({ field }: { field: SchemaField }) {
  return (
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
  );
}

/** A login field. With `onSave`, "Saved on this system" with Change and a box
 *  that saves on its own button. Without one (a surface view's settings), a
 *  masked box that never shows the saved one: left alone it keeps it. */
function SchemaLoginField({
  field,
  fieldKey,
  value,
  onChange,
  state,
  onSave,
}: {
  field: SchemaField;
  fieldKey: string;
  value: unknown;
  onChange: (value: unknown) => void;
  state: SavedLoginState;
  onSave?: (value: string) => Promise<boolean | void>;
}) {
  const label = field.label || fieldKey;
  return (
    <div>
      <FieldLabel field={field} />
      {onSave ? (
        <SavedLoginField label={label} state={state} onSave={onSave} placeholder={field.placeholder} />
      ) : (
        <input
          type="password"
          autoComplete="new-password"
          {...PASSWORD_MANAGER_IGNORE}
          aria-label={label}
          value={value === SAVED_LOGIN || typeof value !== "string" ? "" : value}
          placeholder={value === SAVED_LOGIN ? "Saved on this system" : field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          style={{
            width: "100%",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
            fontSize: "var(--font-size-base)",
          }}
        />
      )}
      {field.description && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
          {field.description}
        </div>
      )}
    </div>
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
        <SearchableSelect
          value={String(value ?? field.default ?? "")}
          onChange={onChange}
          options={(field.options ?? []).map((opt) => ({
            value: String(opt.value),
            label: opt.label,
          }))}
          allowEmpty={false}
          placeholder="Select..."
          searchPlaceholder="Search..."
        />
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
