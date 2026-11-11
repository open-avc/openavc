/**
 * A plugin panel element's settings: the form its `config_schema` declares,
 * or the raw JSON when it declares none (or the author asks for it).
 *
 * Each declared field gets the control its kind calls for
 * (`panelElementFieldKind`), the same pickers the plugin CONFIG_SCHEMA form
 * uses, so `state_key` / `device_ref` / `macro_ref` get a picker instead of a
 * raw text box, and `text` is a textarea while `string` is single-line.
 */
import { useState } from "react";
import { usePluginStore } from "../../../store/pluginStore";
import { OptionSourcePicker } from "../../shared/OptionSourcePicker";
import { DeviceRefPicker, MacroRefPicker } from "../../shared/RefPickers";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { FieldRow } from "./FieldRow";
import { intOrUndefined, numOrUndefined } from "./numericField";
import { panelElementFieldKind, type PanelFieldKind } from "./panelElementConfig";

type PanelConfigField = {
  key: string;
  label: string;
  type: string;
  default?: unknown;
  options?: string[];
  options_source?: string;
};

type Config = Record<string, unknown>;

export function PluginElementConfig({
  pluginId,
  pluginType,
  config,
  onChange,
}: {
  pluginId: string;
  pluginType: string;
  config: Config;
  onChange: (cfg: Config) => void;
}) {
  const panelElements = usePluginStore((s) => s.extensions.panel_elements);
  const ext = panelElements.find(
    (e) => e.plugin_id === pluginId && e.type === pluginType,
  );
  const schema = ext?.config_schema;
  const fields = schema && schema.length > 0 ? schema : null;

  const [jsonMode, setJsonMode] = useState(false);

  return (
    <>
      <FieldRow label="Plugin">
        <input value={pluginId} readOnly style={{ flex: 1, opacity: 0.6 }} />
      </FieldRow>
      <FieldRow label="Type">
        <input value={pluginType} readOnly style={{ flex: 1, opacity: 0.6 }} />
      </FieldRow>

      {fields && !jsonMode ? (
        <SchemaForm schema={fields} config={config} onChange={onChange} onEditJson={() => setJsonMode(true)} />
      ) : (
        <JsonConfig config={config} onChange={onChange} hasSchema={!!fields} onEditForm={() => setJsonMode(false)} />
      )}
    </>
  );
}

const SWITCH_LINK = {
  fontSize: 10, color: "var(--text-muted)", background: "transparent", border: "none", cursor: "pointer",
  alignSelf: "flex-start", textDecoration: "underline",
} as const;

/** One row per declared field, and the way out to the raw JSON. */
function SchemaForm({
  schema,
  config,
  onChange,
  onEditJson,
}: {
  schema: PanelConfigField[];
  config: Config;
  onChange: (cfg: Config) => void;
  onEditJson: () => void;
}) {
  return (
    <>
      {schema.map((field) => (
        <FieldRow key={field.key} label={field.label}>
          <PanelFieldControl field={field} config={config} onChange={onChange} />
        </FieldRow>
      ))}
      <button
        onClick={onEditJson}
        style={SWITCH_LINK}
      >
        Edit as JSON
      </button>
    </>
  );
}

/** The settings as JSON; text that does not parse is not applied. */
function JsonConfig({
  config,
  onChange,
  hasSchema,
  onEditForm,
}: {
  config: Config;
  onChange: (cfg: Config) => void;
  hasSchema: boolean;
  onEditForm: () => void;
}) {
  return (
    <>
      <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "2px 0" }}>
        {hasSchema ? "Raw JSON:" : "Plugin configuration (JSON):"}
      </div>
      <textarea
        value={JSON.stringify(config, null, 2)}
        onChange={(e) => {
          try { onChange(JSON.parse(e.target.value)); } catch { /* invalid JSON */ }
        }}
        rows={4}
        style={{ width: "100%", fontSize: 11, fontFamily: "monospace", resize: "vertical" }}
      />
      {hasSchema && (
        <button
          onClick={onEditForm}
          style={SWITCH_LINK}
        >
          Edit as form
        </button>
      )}
    </>
  );
}

interface ControlProps {
  field: PanelConfigField;
  config: Config;
  /** The field's value as text, or its default. */
  text: string;
  set: (value: unknown) => void;
}

/** The control each kind of declared field gets. */
const FIELD_CONTROLS: Record<PanelFieldKind, (props: ControlProps) => React.JSX.Element> = {
  boolean: ({ field, config, set }) => (
    <input
      type="checkbox"
      checked={config[field.key] != null ? !!config[field.key] : !!field.default}
      onChange={(e) => set(e.target.checked)}
    />
  ),
  select: ({ field, text, set }) => (
    <OptionSourcePicker
      value={text}
      staticOptions={field.options}
      optionsSource={field.options_source}
      onChange={set}
      emptyHint={
        field.options_source
          ? "Nothing has offered a source yet. A device whose driver publishes a preview shows up here on its own; anything else is added by hand under Video Streams."
          : undefined
      }
    />
  ),
  number: ({ field, config, set }) => (
    <input
      type="number"
      value={config[field.key] != null ? Number(config[field.key]) : ""}
      placeholder={field.default != null ? String(field.default) : undefined}
      onChange={(e) => set(field.type === "integer" ? intOrUndefined(e.target.value) : numOrUndefined(e.target.value))}
      step={field.type === "float" ? 0.1 : 1}
      style={{ flex: 1 }}
    />
  ),
  state_key: ({ text, set }) => <VariableKeyPicker value={text} onChange={set} style={{ flex: 1 }} />,
  macro_ref: ({ text, set }) => <MacroRefPicker value={text} onChange={set} style={{ flex: 1 }} />,
  device_ref: ({ text, set }) => <DeviceRefPicker value={text} onChange={set} style={{ flex: 1 }} />,
  textarea: ({ field, text, set }) => (
    <textarea
      value={text}
      onChange={(e) => set(e.target.value)}
      placeholder={field.default != null ? String(field.default) : ""}
      rows={4}
      style={{ flex: 1, resize: "vertical", fontFamily: "var(--font-mono, monospace)", fontSize: "var(--font-size-sm)" }}
    />
  ),
  text: ({ field, text, set }) => (
    <input
      value={text}
      onChange={(e) => set(e.target.value)}
      placeholder={field.default != null ? String(field.default) : ""}
      style={{ flex: 1 }}
    />
  ),
};

/** One declared field, as the control its kind calls for. */
function PanelFieldControl({
  field,
  config,
  onChange,
}: {
  field: PanelConfigField;
  config: Config;
  onChange: (cfg: Config) => void;
}) {
  const Control = FIELD_CONTROLS[panelElementFieldKind(field)];
  return (
    <Control
      field={field}
      config={config}
      text={String(config[field.key] ?? field.default ?? "")}
      set={(v: unknown) => onChange({ ...config, [field.key]: v })}
    />
  );
}
