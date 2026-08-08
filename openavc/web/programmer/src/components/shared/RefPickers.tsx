/**
 * Reference pickers (macro / device / command) shared by schema-driven forms.
 *
 * Used by the plugin CONFIG_SCHEMA form (PluginConfigForm) and the panel-element
 * config_schema form (UI Builder Properties panel), so a `macro_ref` /
 * `device_ref` / `command_ref` field renders the same picker on both surfaces
 * instead of a bare text box.
 */
import { useEffect, useState } from "react";
import { useProjectStore } from "../../store/projectStore";
import * as api from "../../api/restClient";

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-base)",
};

export function MacroRefPicker({
  value,
  onChange,
  style,
}: {
  value: string;
  onChange: (v: string) => void;
  style?: React.CSSProperties;
}) {
  const macros = useProjectStore((s) => s.project?.macros) ?? [];
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={style ?? selectStyle}>
      <option value="">Select macro...</option>
      {macros.map((m) => (
        <option key={m.id} value={m.id}>{m.name}</option>
      ))}
    </select>
  );
}

export function DeviceRefPicker({
  value,
  onChange,
  style,
}: {
  value: string;
  onChange: (v: string) => void;
  style?: React.CSSProperties;
}) {
  const devices = useProjectStore((s) => s.project?.devices) ?? [];
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={style ?? selectStyle}>
      <option value="">Select device...</option>
      {devices.map((d) => (
        <option key={d.id} value={d.id}>{d.name}</option>
      ))}
    </select>
  );
}

export function CommandRefPicker({
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
    <select value={value} onChange={(e) => onChange(e.target.value)} style={style ?? selectStyle}>
      <option value="">{deviceId ? "Select command..." : "Select device first"}</option>
      {commands.map((cmd) => (
        <option key={cmd} value={cmd}>{cmd}</option>
      ))}
    </select>
  );
}
