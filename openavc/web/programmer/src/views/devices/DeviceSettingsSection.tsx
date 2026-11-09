/**
 * Device Settings on a device's page: the settings the driver can read and
 * write on the device itself (an input name, a volume, a network option), each
 * changed in place.
 *
 * A value is checked against the setting's definition before it is sent
 * (validateSettingValue), never coerced: a blank or mistyped number is refused
 * rather than written to the hardware as 0. A value sent to a device that has
 * not reported it back is marked pending; the server sends it again on the
 * next connection. The list is read again every five seconds and a second
 * after a save, and the value shown is the live one when the device reports
 * it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Loader2, Pencil, Settings, X } from "lucide-react";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import type { DeviceSettingValue } from "../../api/types";
import { BooleanOptions } from "../../components/shared/BooleanOptions";
import { normalizeOptionList, settingValueText } from "../../components/shared/paramOptions";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { validateSettingValue } from "./deviceUtils";
import { sectionTitleStyle, surfaceStyle } from "./deviceSectionStyles";

type SaveResult = { key: string; success: boolean; error?: string };

/** The device's settings as the server last read them, polled while shown.
 *  `reloadSoon` reads them again a second after a save. */
function useDeviceSettings(deviceId: string) {
  const [settings, setSettings] = useState<Record<string, DeviceSettingValue>>({});
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadSettings = useCallback(() => {
    if (!mounted.current) return;
    api.getDeviceSettings(deviceId).then((data) => {
      if (!mounted.current) return;
      setSettings(data.settings);
      setLoaded(true);
    }).catch(() => { if (mounted.current) setLoaded(true); });
  }, [deviceId]);

  useEffect(() => {
    mounted.current = true;
    loadSettings();
    const interval = setInterval(loadSettings, 5000);
    return () => {
      mounted.current = false;
      clearInterval(interval);
      if (refreshTimer.current !== null) clearTimeout(refreshTimer.current);
    };
  }, [loadSettings]);

  const reloadSoon = () => {
    if (mounted.current) refreshTimer.current = setTimeout(loadSettings, 1000);
  };

  return { settings, loaded, reloadSoon };
}

/** The value on the device now: the live state under the setting's state key
 *  when the device reports it, else what the last read of the settings said. */
function currentValueOf(
  liveState: Record<string, unknown>,
  deviceId: string,
  key: string,
  def: DeviceSettingValue | undefined,
): unknown {
  const stateKey = `device.${deviceId}.${String(def?.state_key ?? key)}`;
  return stateKey in liveState ? liveState[stateKey] : def?.current_value;
}

export function DeviceSettingsSection({ deviceId, connected }: { deviceId: string; connected: boolean }) {
  const liveState = useConnectionStore((s) => s.liveState);
  const devices = useProjectStore((s) => s.project?.devices);
  const pendingSettings = useMemo(() => {
    const dev = devices?.find((d) => d.id === deviceId);
    return dev?.pending_settings ?? {};
  }, [devices, deviceId]);
  const { settings, loaded, reloadSoon } = useDeviceSettings(deviceId);

  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [saveResult, setSaveResult] = useState<SaveResult | null>(null);

  const settingKeys = Object.keys(settings);
  if (!loaded || settingKeys.length === 0) return null;

  const handleStartEdit = (key: string) => {
    const current = currentValueOf(liveState, deviceId, key, settings[key]);
    setEditingKey(key);
    setEditValue(current != null ? String(current) : String(settings[key]?.default ?? ""));
    setSaveResult(null);
  };

  const handleSave = async (key: string) => {
    setSaving(key);
    setSaveResult(null);
    try {
      const validated = validateSettingValue(settings[key], editValue);
      if (!validated.ok) {
        setSaveResult({ key, success: false, error: validated.error });
        setSaving(null);
        return;
      }
      await api.setDeviceSetting(deviceId, key, validated.value);
      setSaveResult({ key, success: true });
      setEditingKey(null);
      reloadSoon();
    } catch (e) {
      setSaveResult({ key, success: false, error: parseApiError(e) });
    } finally {
      setSaving(null);
    }
  };

  const handleCancel = () => {
    setEditingKey(null);
    setSaveResult(null);
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3 style={{ ...sectionTitleStyle, display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
        <Settings size={14} /> Device Settings
      </h3>
      <div style={{ ...surfaceStyle, overflow: "hidden" }}>
        {settingKeys.map((key) => (
          <SettingRow
            key={key}
            settingKey={key}
            def={settings[key]}
            currentValue={currentValueOf(liveState, deviceId, key, settings[key])}
            pending={key in pendingSettings}
            pendingValue={pendingSettings[key]}
            connected={connected}
            editor={
              editingKey === key
                ? {
                    value: editValue,
                    onChange: setEditValue,
                    saving: saving === key,
                    onSave: () => handleSave(key),
                    onCancel: handleCancel,
                  }
                : null
            }
            result={saveResult?.key === key ? saveResult : null}
            onStartEdit={() => handleStartEdit(key)}
          />
        ))}
      </div>
    </div>
  );
}

interface EditorProps {
  value: string;
  onChange: (value: string) => void;
  saving: boolean;
  onSave: () => void;
  onCancel: () => void;
}

function SettingRow({
  settingKey,
  def,
  currentValue,
  pending,
  pendingValue,
  connected,
  editor,
  result,
  onStartEdit,
}: {
  settingKey: string;
  def: DeviceSettingValue | undefined;
  currentValue: unknown;
  pending: boolean;
  pendingValue: unknown;
  connected: boolean;
  /** The edit box's state while this row is being edited, else null. */
  editor: EditorProps | null;
  result: SaveResult | null;
  onStartEdit: () => void;
}) {
  const help = String(def?.help ?? "");
  return (
    <div style={{ padding: "var(--space-md)", borderBottom: "1px solid var(--border-color)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>{String(def?.label ?? settingKey)}</div>
          {help && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{help}</div>
          )}
        </div>
        {editor ? (
          <SettingEditor def={def} {...editor} />
        ) : (
          <SettingReading
            def={def}
            currentValue={currentValue}
            pending={pending}
            pendingValue={pendingValue}
            connected={connected}
            onStartEdit={onStartEdit}
          />
        )}
      </div>
      {result && <SaveResultLine result={result} />}
    </div>
  );
}

/** The value on the device, a pending mark, and the edit button, which waits
 *  for the device to be connected. */
function SettingReading({
  def,
  currentValue,
  pending,
  pendingValue,
  connected,
  onStartEdit,
}: {
  def: DeviceSettingValue | undefined;
  currentValue: unknown;
  pending: boolean;
  pendingValue: unknown;
  connected: boolean;
  onStartEdit: () => void;
}) {
  const known = currentValue != null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--font-size-sm)",
          color: known ? "var(--text-primary)" : "var(--text-muted)",
        }}
      >
        {known ? settingValueText(def, currentValue) : "(not set)"}
      </span>
      {pending && <PendingMark def={def} value={pendingValue} connected={connected} />}
      <button
        onClick={onStartEdit}
        disabled={!connected}
        title={connected ? "Edit setting" : "Device must be connected to change settings"}
        style={{
          padding: "2px 8px",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          fontSize: "var(--font-size-sm)",
          opacity: connected ? 1 : 0.4,
        }}
      >
        <Pencil size={12} />
      </button>
    </div>
  );
}

function SaveResultLine({ result }: { result: SaveResult }) {
  return (
    <div
      style={{
        marginTop: "var(--space-xs)",
        fontSize: 11,
        color: result.success ? "var(--color-success)" : "var(--color-error)",
      }}
    >
      {result.success ? "Setting saved successfully" : `Error: ${result.error}`}
    </div>
  );
}

/** A value sent but not yet reported back by the device. */
function PendingMark({ def, value, connected }: { def: DeviceSettingValue | undefined; value: unknown; connected: boolean }) {
  const text = settingValueText(def, value);
  return (
    <span
      style={{
        fontSize: 10,
        color: "var(--accent)",
        padding: "1px 6px",
        borderRadius: "var(--border-radius)",
        background: "var(--accent-dim)",
      }}
      title={
        connected
          ? `Pending: ${text}. Sent, but the device has not reported it back yet. It will be sent again on the next connection.`
          : `Pending: ${text}, will be applied when device connects`
      }
    >
      pending
    </span>
  );
}

const editorButtonStyle = {
  padding: "2px 6px",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  display: "flex",
  alignItems: "center",
} as const;

function SettingEditor({ def, value, onChange, saving, onSave, onCancel }: EditorProps & { def: DeviceSettingValue | undefined }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
      <SettingInput def={def} value={value} onChange={onChange} onSave={onSave} onCancel={onCancel} />
      <button
        onClick={onSave}
        disabled={saving}
        title="Save"
        style={{ ...editorButtonStyle, background: "var(--color-success-bg)", color: "var(--color-success)" }}
      >
        {saving ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Check size={14} />}
      </button>
      <button onClick={onCancel} title="Cancel" style={{ ...editorButtonStyle, background: "var(--bg-hover)" }}>
        <X size={14} />
      </button>
    </div>
  );
}

const NUMBER_TYPES = new Set(["integer", "number", "float"]);
const selectStyle = { fontSize: "var(--font-size-sm)", padding: "2px 6px" } as const;

type InputProps = {
  def: DeviceSettingValue | undefined;
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
};

/** The box for one setting's type: Yes / No for a switch, the declared
 *  choices for a choice, else a text or number box. */
function SettingInput(props: InputProps) {
  const fieldType = String(props.def?.type ?? "string");
  const values = props.def?.values as unknown[] | undefined;
  if (fieldType === "boolean") {
    return (
      <select value={props.value} onChange={(e) => props.onChange(e.target.value)} style={selectStyle}>
        <BooleanOptions />
      </select>
    );
  }
  if (fieldType === "enum" && values) {
    return (
      <select value={props.value} onChange={(e) => props.onChange(e.target.value)} style={selectStyle}>
        {normalizeOptionList(values).map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  }
  return <SettingTextBox {...props} fieldType={fieldType} />;
}

/** A number box carries the setting's range (its placeholder says it) and
 *  whole steps for an integer; anything else is a text box. */
function boxAttributes(def: DeviceSettingValue | undefined, fieldType: string) {
  const min = def?.min;
  const max = def?.max;
  const ranged = min !== undefined && max !== undefined;
  return {
    type: NUMBER_TYPES.has(fieldType) ? "number" : "text",
    min,
    max,
    step: fieldType === "integer" ? 1 : undefined,
    placeholder: ranged ? `${min}-${max}` : undefined,
  };
}

/** Enter saves, Escape cancels. */
function SettingTextBox({ def, value, onChange, onSave, onCancel, fieldType }: InputProps & { fieldType: string }) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      {...boxAttributes(def, fieldType)}
      style={{ ...selectStyle, width: 180 }}
      autoFocus
      onKeyDown={(e) => {
        if (e.key === "Enter") onSave();
        if (e.key === "Escape") onCancel();
      }}
    />
  );
}
