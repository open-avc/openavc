import { useEffect, useState, useCallback, useRef } from "react";
import { Search, Plug, AlertTriangle, RefreshCw, Power, PowerOff, Trash2 } from "lucide-react";
import { CopyButton } from "../components/shared/CopyButton";
import { ViewContainer } from "../components/layout/ViewContainer";
import { usePluginStore } from "../store/pluginStore";
import { useNavigationStore } from "../store/navigationStore";
import * as api from "../api/restClient";
import type { PluginInfo, SchemaField } from "../api/types";
import { SurfaceConfigurator } from "../components/plugins/SurfaceConfigurator";
import { BrowsePlugins } from "../components/plugins/BrowsePlugins";
import { VariableKeyPicker } from "../components/shared/VariableKeyPicker";
import { useProjectStore } from "../store/projectStore";

// ──── Status Dot ────

function PluginStatusDot({ status, size = 10 }: { status: string; size?: number }) {
  const isTriangle = status === "missing" || status === "incompatible";
  const color =
    status === "running"
      ? "var(--color-success)"
      : status === "error"
        ? "var(--color-error)"
        : status === "missing"
          ? "var(--color-warning, #f59e0b)"
          : status === "incompatible"
            ? "#f97316"
            : "var(--text-muted)";

  const title =
    status === "running"
      ? "Running"
      : status === "error"
        ? "Error"
        : status === "missing"
          ? "Not installed"
          : status === "incompatible"
            ? "Incompatible platform"
            : "Stopped";

  if (isTriangle) {
    return (
      <span
        style={{
          display: "inline-block",
          flexShrink: 0,
          width: 0,
          height: 0,
          borderLeft: `${size / 2}px solid transparent`,
          borderRight: `${size / 2}px solid transparent`,
          borderBottom: `${size}px solid ${color}`,
          backgroundColor: "transparent",
        }}
        title={title}
      />
    );
  }

  return (
    <span
      style={{
        display: "inline-block",
        flexShrink: 0,
        width: size,
        height: size,
        borderRadius: "50%",
        backgroundColor: color,
      }}
      title={title}
    />
  );
}

// ──── Plugin List Item ────

function PluginListItem({
  plugin,
  selected,
  onClick,
}: {
  plugin: PluginInfo;
  selected: boolean;
  onClick: () => void;
}) {
  const suffix =
    plugin.status === "missing"
      ? " (not installed)"
      : plugin.status === "incompatible"
        ? " (incompatible)"
        : "";

  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-md)",
        width: "100%",
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: selected ? "var(--accent-dim)" : "transparent",
        textAlign: "left",
        marginBottom: "var(--space-xs)",
        transition: "background var(--transition-fast)",
        opacity: plugin.status === "running" ? 1 : 0.6,
      }}
    >
      <PluginStatusDot status={plugin.status} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {plugin.name}
        </div>
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color:
              plugin.status === "missing" || plugin.status === "incompatible"
                ? "var(--color-warning, #f59e0b)"
                : "var(--text-muted)",
          }}
        >
          {plugin.version ? `v${plugin.version}` : plugin.plugin_id}
          {suffix}
        </div>
      </div>
    </button>
  );
}

// ──── Schema Form Renderer ────

function SchemaFormRenderer({
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

// ──── Missing Plugin Banner ────

function MissingPluginBanner({ plugin }: { plugin: PluginInfo }) {
  const navigateTo = useNavigationStore((s) => s.navigateTo);
  const isMissing = plugin.status === "missing";
  const isIncompat = plugin.status === "incompatible";

  if (!isMissing && !isIncompat) return null;

  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.12)",
        border: "1px solid rgba(245, 158, 11, 0.3)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          fontWeight: 600,
          marginBottom: "var(--space-sm)",
          color: "var(--color-warning, #f59e0b)",
        }}
      >
        <AlertTriangle size={16} />
        {isMissing ? "Plugin Required" : "Platform Incompatible"}
      </div>
      <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
        {isMissing
          ? `This project uses the plugin "${plugin.name || plugin.plugin_id}" which is not installed.`
          : `Plugin "${plugin.name || plugin.plugin_id}" is not compatible with the current platform.`}
      </div>
      {isMissing && (
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button
            onClick={() => navigateTo("plugins")}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--color-warning, #f59e0b)",
              color: "#000",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
            }}
          >
            Install from Community
          </button>
        </div>
      )}
    </div>
  );
}

// ──── Plugin Detail View ────

function PluginDetail({ plugin }: { plugin: PluginInfo }) {
  const enablePlugin = usePluginStore((s) => s.enablePlugin);
  const disablePlugin = usePluginStore((s) => s.disablePlugin);
  const updateConfig = usePluginStore((s) => s.updateConfig);
  const activatePlugin = usePluginStore((s) => s.activatePlugin);
  const load = usePluginStore((s) => s.load);
  const setSelectedId = usePluginStore((s) => s.setSelectedId);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [detailInfo, setDetailInfo] = useState<PluginInfo | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [uninstallError, setUninstallError] = useState<string | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Fetch full detail (including config_schema) on mount
  useEffect(() => {
    api.getPlugin(plugin.plugin_id).then(setDetailInfo).catch(console.error);
  }, [plugin.plugin_id, plugin.status]);

  // Load config values
  useEffect(() => {
    if (plugin.status !== "missing" && plugin.status !== "incompatible") {
      api
        .getPluginConfig(plugin.plugin_id)
        .then((r) => setConfigValues(r.config))
        .catch(console.error);
    }
  }, [plugin.plugin_id, plugin.status]);

  const handleConfigChange = useCallback(
    (key: string, value: unknown) => {
      setConfigValues((prev) => {
        const next = { ...prev, [key]: value };
        clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(async () => {
          setSaving(true);
          await updateConfig(plugin.plugin_id, next);
          setSaving(false);
        }, 1500);
        return next;
      });
    },
    [plugin.plugin_id, updateConfig]
  );

  const info = detailInfo ?? plugin;
  const isRunning = info.status === "running";
  const isMissing = info.status === "missing";
  const isIncompat = info.status === "incompatible";

  const categoryLabels: Record<string, string> = {
    control_surface: "Control Surface",
    integration: "Integration",
    sensor: "Sensor",
    utility: "Utility",
  };

  return (
    <div
      style={{
        flex: 1,
        overflow: "auto",
        padding: "var(--space-lg)",
      }}
    >
      {/* Banner for missing/incompatible */}
      <MissingPluginBanner plugin={info} />

      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: "var(--space-lg)",
        }}
      >
        <div>
          <h2 style={{ fontSize: "var(--font-size-xl)", fontWeight: 600, marginBottom: "var(--space-xs)" }}>
            {info.name}
          </h2>
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            {info.version && `v${info.version}`}
            {info.author && ` by ${info.author}`}
            {info.category && ` · ${categoryLabels[info.category] ?? info.category}`}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
            <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {plugin.plugin_id}
            </code>
            <CopyButton value={plugin.plugin_id} title="Copy plugin ID" />
          </div>
        </div>
        {!isMissing && !isIncompat && (
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            {isRunning ? (
              <button
                onClick={() => disablePlugin(plugin.plugin_id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <PowerOff size={14} />
                Disable
              </button>
            ) : (
              <button
                onClick={() => enablePlugin(plugin.plugin_id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--accent-bg)",
                  color: "var(--text-on-accent)",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 500,
                }}
              >
                <Power size={14} />
                Enable
              </button>
            )}
          </div>
        )}
        {isMissing && (
          <button
            onClick={() => activatePlugin(plugin.plugin_id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
            }}
          >
            <RefreshCw size={14} />
            Activate
          </button>
        )}
      </div>

      {/* Description */}
      {info.description && (
        <p style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: "var(--space-lg)" }}>
          {info.description}
        </p>
      )}

      {/* Error message */}
      {info.status === "error" && info.error && (
        <div
          style={{
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            background: "rgba(244, 67, 54, 0.12)",
            border: "1px solid rgba(244, 67, 54, 0.3)",
            fontSize: "var(--font-size-sm)",
            color: "var(--color-error)",
          }}
        >
          <strong>Error:</strong> {info.error}
        </div>
      )}

      {/* Capabilities */}
      {info.capabilities && info.capabilities.length > 0 && (
        <div style={{ marginBottom: "var(--space-lg)" }}>
          <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: "var(--space-sm)", color: "var(--text-secondary)" }}>
            Capabilities
          </h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)" }}>
            {info.capabilities.map((cap) => (
              <span
                key={cap}
                style={{
                  padding: "2px var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                }}
              >
                {cap}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Platforms */}
      {info.platforms && info.platforms.length > 0 && (
        <div style={{ marginBottom: "var(--space-lg)" }}>
          <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: "var(--space-sm)", color: "var(--text-secondary)" }}>
            Platforms
          </h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)" }}>
            {info.platforms.map((p) => (
              <span
                key={p}
                style={{
                  padding: "2px var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                }}
              >
                {p}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Surface Configurator */}
      {(detailInfo as any)?.surface_layout && (
        <div style={{ marginBottom: "var(--space-lg)" }}>
          <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "var(--space-md)" }}>
            Surface Layout
          </h3>
          <SurfaceConfigurator
            layout={(detailInfo as any).surface_layout}
            pluginId={plugin.plugin_id}
            config={configValues}
            onConfigChange={(newConfig) => {
              setConfigValues(newConfig);
              clearTimeout(saveTimer.current);
              saveTimer.current = setTimeout(async () => {
                setSaving(true);
                await updateConfig(plugin.plugin_id, newConfig);
                setSaving(false);
              }, 1500);
            }}
            onRequestConfigRefresh={async () => {
              try {
                const r = await api.getPluginConfig(plugin.plugin_id);
                setConfigValues(r.config);
              } catch (e) {
                console.error("Failed to refresh config:", e);
              }
            }}
          />
        </div>
      )}

      {/* Configuration */}
      {detailInfo?.config_schema && Object.keys(detailInfo.config_schema).length > 0 && (
        <div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-md)" }}>
            <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, color: "var(--text-secondary)" }}>
              Configuration
            </h3>
            {saving && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Saving...</span>
            )}
          </div>
          <SchemaFormRenderer
            schema={detailInfo.config_schema}
            values={configValues}
            onChange={handleConfigChange}
          />
        </div>
      )}

      {/* Uninstall */}
      {!isMissing && (
        <div style={{ marginTop: "var(--space-xl)", paddingTop: "var(--space-md)", borderTop: "1px solid var(--border-color)" }}>
          {uninstallError && (
            <div
              style={{
                padding: "var(--space-sm) var(--space-md)",
                marginBottom: "var(--space-md)",
                background: "rgba(220,38,38,0.1)",
                border: "1px solid rgba(220,38,38,0.3)",
                borderRadius: "var(--border-radius)",
                color: "var(--color-error)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              {uninstallError}
            </div>
          )}
          {!confirmUninstall ? (
            <div>
              <button
                onClick={() => {
                  if (isRunning) {
                    setUninstallError("Disable the plugin before uninstalling.");
                    return;
                  }
                  setUninstallError(null);
                  setConfirmUninstall(true);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "rgba(220,38,38,0.1)",
                  color: "var(--color-error, #dc2626)",
                  fontSize: "var(--font-size-sm)",
                  cursor: "pointer",
                }}
              >
                <Trash2 size={14} /> Uninstall Plugin
              </button>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                Removes the plugin files. You can reinstall from Browse.
              </div>
            </div>
          ) : (
            <div
              style={{
                padding: "var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "rgba(220,38,38,0.08)",
                border: "1px solid rgba(220,38,38,0.2)",
              }}
            >
              <div style={{ fontWeight: 500, marginBottom: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
                Uninstall &quot;{info.name}&quot;?
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-md)" }}>
                Plugin files will be removed. You can reinstall from the community repository later.
              </div>
              <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <button
                  onClick={async () => {
                    try {
                      await api.uninstallPlugin(plugin.plugin_id);
                      setConfirmUninstall(false);
                      setSelectedId(null);
                      load();
                    } catch (e) {
                      setUninstallError(String(e));
                      setConfirmUninstall(false);
                    }
                  }}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--color-error, #dc2626)",
                    color: "#fff",
                    fontSize: "var(--font-size-sm)",
                    fontWeight: 500,
                    cursor: "pointer",
                  }}
                >
                  Uninstall
                </button>
                <button
                  onClick={() => setConfirmUninstall(false)}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                    cursor: "pointer",
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ──── Main Plugins View ────

export function PluginsView() {
  const plugins = usePluginStore((s) => s.plugins);
  const loading = usePluginStore((s) => s.loading);
  const selectedId = usePluginStore((s) => s.selectedId);
  const setSelectedId = usePluginStore((s) => s.setSelectedId);
  const load = usePluginStore((s) => s.load);

  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"installed" | "browse">("installed");

  // Load on mount + consume focus
  useEffect(() => {
    load();
    const focus = useNavigationStore.getState().consumeFocus();
    if (focus?.type === "plugin" && focus.id) {
      setSelectedId(focus.id);
    }
  }, [load, setSelectedId]);

  const filtered = plugins.filter(
    (p) =>
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.plugin_id.toLowerCase().includes(search.toLowerCase())
  );

  const selected = plugins.find((p) => p.plugin_id === selectedId) ?? null;

  return (
    <ViewContainer
      title="Plugins"
      actions={
        <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
          {/* Tab toggle */}
          <div style={{ display: "flex", borderRadius: "var(--border-radius)", overflow: "hidden", border: "1px solid var(--border-color)" }}>
            <button
              onClick={() => setTab("installed")}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                fontSize: "var(--font-size-sm)",
                background: tab === "installed" ? "var(--accent-bg)" : "transparent",
                color: tab === "installed" ? "var(--text-on-accent)" : "var(--text-secondary)",
                fontWeight: tab === "installed" ? 600 : 400,
              }}
            >
              Installed
            </button>
            <button
              onClick={() => setTab("browse")}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                fontSize: "var(--font-size-sm)",
                background: tab === "browse" ? "var(--accent-bg)" : "transparent",
                color: tab === "browse" ? "var(--text-on-accent)" : "var(--text-secondary)",
                fontWeight: tab === "browse" ? 600 : 400,
              }}
            >
              Browse
            </button>
          </div>
          <button
            onClick={() => load()}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
            title="Refresh"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      }
    >
      {tab === "browse" ? (
        <BrowsePlugins />
      ) : (
      <div style={{ display: "flex", height: "100%", minHeight: 0 }}>
        {/* Left: Plugin List */}
        <div
          style={{
            width: 280,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            borderRight: "1px solid var(--border-color)",
          }}
        >
          {/* Search */}
          <div style={{ padding: "var(--space-sm)", borderBottom: "1px solid var(--border-color)" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-surface)",
                border: "1px solid var(--border-color)",
              }}
            >
              <Search size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
              <input
                type="text"
                placeholder="Search plugins..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
            </div>
          </div>

          {/* Plugin List */}
          <div style={{ flex: 1, overflow: "auto", padding: "var(--space-sm)" }}>
            {loading && plugins.length === 0 && (
              <div style={{ padding: "var(--space-lg)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
                Loading...
              </div>
            )}
            {!loading && filtered.length === 0 && (
              <div style={{ padding: "var(--space-lg)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
                {plugins.length === 0
                  ? <>
                      No plugins installed. Click the <strong>Browse</strong> tab to find and install plugins.
                      <br /><br />
                      <a href="https://docs.openavc.com/plugins" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                        Learn about plugins
                      </a>
                    </>
                  : "No matching plugins."}
              </div>
            )}
            {filtered.map((p) => (
              <PluginListItem
                key={p.plugin_id}
                plugin={p}
                selected={selectedId === p.plugin_id}
                onClick={() => setSelectedId(p.plugin_id)}
              />
            ))}
          </div>

          {/* Count */}
          <div
            style={{
              padding: "var(--space-sm) var(--space-md)",
              borderTop: "1px solid var(--border-color)",
              fontSize: 11,
              color: "var(--text-muted)",
            }}
          >
            {plugins.length} plugin{plugins.length !== 1 ? "s" : ""}
          </div>
        </div>

        {/* Right: Detail or Empty */}
        {selected ? (
          <PluginDetail plugin={selected} />
        ) : (
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-muted)",
              gap: "var(--space-md)",
            }}
          >
            <Plug size={48} strokeWidth={1} />
            <div style={{ fontSize: "var(--font-size-sm)" }}>
              {plugins.length === 0
                ? "No plugins installed"
                : "Select a plugin to view details"}
            </div>
          </div>
        )}
      </div>
      )}
    </ViewContainer>
  );
}
