/**
 * Reference pickers (macro / device / command) shared by schema-driven forms.
 *
 * Used by the plugin CONFIG_SCHEMA form (PluginConfigForm) and the panel-element
 * config_schema form (UI Builder Properties panel), so a `macro_ref` /
 * `device_ref` / `command_ref` field renders the same picker on both surfaces
 * instead of a bare text box.
 *
 * `style` is the picker's LAYOUT — width, flex — not an input skin. The picker
 * draws its own trigger, so handing it a form field's padding and border here
 * would draw a second box around the first.
 */
import { useEffect, useState } from "react";
import { useProjectStore } from "../../store/projectStore";
import * as api from "../../api/restClient";
import { SearchableSelect } from "./SearchableSelect";
import { commandOptions, deviceOptions, macroOptions } from "./pickerOptions";

const defaultStyle: React.CSSProperties = { width: "100%" };

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
    <SearchableSelect
      value={value}
      onChange={onChange}
      options={macroOptions(macros)}
      placeholder="Select macro..."
      searchPlaceholder="Search macros..."
      emptyHint="No macros yet. Create one in the Macros view."
      style={style ?? defaultStyle}
    />
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
    <SearchableSelect
      value={value}
      onChange={onChange}
      options={deviceOptions(devices)}
      placeholder="Select device..."
      searchPlaceholder="Search devices..."
      emptyHint="No devices yet. Add one in the Devices view."
      style={style ?? defaultStyle}
    />
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
  const [commands, setCommands] = useState<Record<string, unknown>>({});
  useEffect(() => {
    if (!deviceId) {
      setCommands({});
      return;
    }
    // A late answer for the device picked a moment ago would fill this
    // dropdown with that device's commands beside the new device's name.
    let stale = false;
    api.getDevice(deviceId)
      .then((info) => { if (!stale) setCommands((info?.commands ?? {}) as Record<string, unknown>); })
      .catch(() => { if (!stale) setCommands({}); });
    return () => { stale = true; };
  }, [deviceId]);

  return (
    <SearchableSelect
      value={value}
      onChange={onChange}
      options={commandOptions(commands)}
      placeholder={deviceId ? "Select command..." : "Select device first"}
      searchPlaceholder="Search commands..."
      emptyHint={
        deviceId
          ? "This device's driver declares no commands."
          : "Pick a device first."
      }
      style={style ?? defaultStyle}
    />
  );
}
