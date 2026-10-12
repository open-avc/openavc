import { useState, useEffect, useRef, useMemo, useId } from "react";
import { useProjectStore } from "../../store/projectStore";
import * as api from "../../api/restClient";
import { getSerialPorts, type SerialPortInfo } from "../../api/systemClient";
import type { DeviceConfig, DriverInfo } from "../../api/types";
import { DeviceSettingsSetupDialog, hasDriverSetupSettings } from "../../components/shared/DeviceSettingsSetupDialog";
import { Modal } from "../../components/shared/Modal";
import { normalizeOptionList } from "../../components/shared/paramOptions";
import { buildConfigFromForm, configFieldKind, splitConnectionFields, CONNECTION_FIELDS, SERIAL_PICKER_FIELDS, IR_PICKER_FIELDS, prefillConfigValues, type ConnectionMode } from "./deviceConfigCoerce";
import { parseApiError } from "../../api/errors";
import { booleanWord } from "../../components/shared/booleanWords";

// --- Typed Config Fields ---

// Shared with the Device Audit's connection step, which asks for the same
// fields the same way.
export function ConfigFieldInputs({
  configKeys,
  driverInfo,
  configValues,
  setConfigValues,
}: {
  configKeys: string[];
  driverInfo: DriverInfo | undefined;
  configValues: Record<string, string>;
  setConfigValues: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}) {
  const schemaFor = (key: string) =>
    (driverInfo?.config_schema as Record<string, Record<string, unknown>>)?.[key] ?? {};
  // A driver marks a field `advanced` when its answer is not known at the
  // moment the device is added: it depends on something configured later, or
  // on a decision nobody has made yet. Asking anyway trains people to skim
  // past the whole form, and the field they DO need is in there too. Still
  // present, one click away, and pre-filled with its default.
  const plainKeys = configKeys.filter((k) => schemaFor(k).advanced !== true);
  const advancedKeys = configKeys.filter((k) => schemaFor(k).advanced === true);
  // Each label names its field: a screen reader reads it, and a click on it
  // focuses the input.
  const idPrefix = useId();

  const renderKey = (key: string) => {
        const schema = schemaFor(key);
        const label = String(schema.label || key);
        const description = schema.description ? String(schema.description) : "";
        const fieldType = String(schema.type || "string");
        // Enum options may be plain strings OR {value,label} objects — both are
        // valid in config_schema, so normalize before rendering (a raw object
        // rendered as an <option> child is React error #31 and crashes the form).
        const values = schema.values as unknown[] | undefined;
        const isRequired = schema.required === true;
        const defaultVal = schema.default;
        const isObjectField = fieldType === "object" || fieldType === "json";
        // Widget choice lives in configFieldKind so secret fields reliably
        // mask and the dialogs can't drift from the coercion rules.
        const kind = configFieldKind(schema);
        // Build helpful placeholder from key name conventions
        const placeholder = isObjectField
          ? (defaultVal && typeof defaultVal === "object" && Object.keys(defaultVal).length > 0
              ? JSON.stringify(defaultVal)
              : '{"key": "value"}')
          : key === "host" ? "192.168.1.100"
          : key === "port" ? "1-65535"
          : key === "username" ? "admin"
          : key === "password" ? "password"
          : key === "community" ? "public"
          : key === "baud_rate" || key === "baudrate" ? "9600"
          : defaultVal != null && defaultVal !== "" ? String(defaultVal)
          : label;
        // A field the driver fills in from what the device reports, once it
        // connects to the real equipment: marked beside its label. The
        // driver's own description says what it says about it.
        const learned = Boolean(schema.learned_from);
        const selectOptions = normalizeOptionList(values ?? []);
        // An enum that lists its own blank entry ("Detect automatically")
        // needs no "Select..." placeholder beside it.
        const listsBlank = selectOptions.some((o) => o.value === "");
        const fieldId = `${idPrefix}-${key}`;
        const helpId = description ? `${fieldId}-help` : undefined;
        const learnedId = learned ? `${fieldId}-learned` : undefined;
        const describedBy = [helpId, learnedId].filter(Boolean).join(" ") || undefined;
        const labelled = { id: fieldId, "aria-describedby": describedBy, "aria-required": isRequired || undefined };

        return (
          <div key={key} style={{ marginBottom: "var(--space-sm)" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                marginBottom: "var(--space-xs)",
              }}
            >
              <label
                htmlFor={kind === "table" ? undefined : fieldId}
                style={{
                  display: "block",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                }}
              >
                {label}
                {isRequired && <RequiredMark />}
              </label>
              {learned && (
                <span
                  id={learnedId}
                  title="Filled in from the device when it connects."
                  style={{
                    display: "inline-block",
                    padding: "0 6px",
                    borderRadius: 999,
                    fontSize: "var(--font-size-xs)",
                    lineHeight: "16px",
                    color: "var(--accent)",
                    border: "1px solid var(--accent)",
                  }}
                >
                  From the device
                </span>
              )}
            </div>
            {kind === "boolean" ? (
              <button
                type="button"
                id={fieldId}
                aria-pressed={configValues[key] === "true"}
                aria-describedby={describedBy}
                onClick={() =>
                  setConfigValues((v) => ({
                    ...v,
                    [key]: v[key] === "true" ? "false" : "true",
                  }))
                }
                style={{
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background:
                    configValues[key] === "true"
                      ? "var(--color-success-bg)"
                      : "var(--bg-hover)",
                  color:
                    configValues[key] === "true" ? "var(--color-success)" : "var(--text-secondary)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {booleanWord(configValues[key] === "true")}
              </button>
            ) : kind === "password" ? (
              <input
                {...labelled}
                type="password"
                autoComplete="new-password"
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            ) : kind === "select" ? (
              <select
                {...labelled}
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                style={{ width: "100%" }}
              >
                {!listsBlank && <option value="">Select...</option>}
                {selectOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : kind === "number" ? (
              <input
                {...labelled}
                type="number"
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            ) : kind === "table" ? (
              <div
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-muted)",
                  border: "1px dashed var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  padding: "var(--space-sm)",
                }}
              >
                Set this up on the device page after adding. It opens a table
                editor.
              </div>
            ) : kind === "textarea" ? (
              <textarea
                {...labelled}
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                rows={6}
                spellCheck={false}
                style={{
                  width: "100%",
                  fontFamily: "var(--font-mono, monospace)",
                  fontSize: "var(--font-size-sm)",
                  resize: "vertical",
                  minHeight: "120px",
                }}
              />
            ) : (
              <input
                {...labelled}
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            )}
            {description && (
              <div id={helpId} style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                {description}
              </div>
            )}
          </div>
        );
  };

  return (
    <>
      {plainKeys.map(renderKey)}
      {advancedKeys.length > 0 && (
        <details style={{ marginBottom: "var(--space-sm)" }}>
          <summary
            style={{
              cursor: "pointer",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-muted)",
              userSelect: "none",
            }}
          >
            Advanced ({advancedKeys.length})
          </summary>
          <div style={{ marginTop: "var(--space-sm)" }}>
            {advancedKeys.map(renderKey)}
          </div>
        </details>
      )}
    </>
  );
}

// --- Connection Mode Picker (serial-capable drivers) ---
//
// For drivers that can speak serial (transport "serial" or `transports`
// includes "serial"), the connection can be made three ways: over the network
// (IP), directly to a serial port on this server, or *through a bridge* device
// (e.g. an iTach) that vends a serial pass-through. A segmented control picks
// the mode and reveals the matching fields. The Add/Edit dialogs exclude these
// connection fields (SERIAL_PICKER_FIELDS) from the generic schema section so
// they aren't rendered twice. Non-serial drivers don't render this at all —
// their connection UI is unchanged.

const NETWORK_TRANSPORTS = ["tcp", "udp", "http", "osc"];
const BAUD_RATES = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"];
const PARITY_OPTS: [string, string][] = [["N", "None"], ["E", "Even"], ["O", "Odd"]];
const DATABITS_OPTS = ["5", "6", "7", "8"];
const STOPBITS_OPTS = ["1", "1.5", "2"];
const FLOW_OPTS: [string, string][] = [["none", "None"], ["hardware", "Hardware (RTS/CTS)"]];

export function driverSerialCapable(d: DriverInfo | undefined): boolean {
  if (!d) return false;
  const t = (d.transport || "").toLowerCase();
  const ts = (d.transports || []).map((x) => String(x).toLowerCase());
  return t === "serial" || ts.includes("serial");
}

// An IR device emits through an IR bridge (no address of its own): transport
// "bridge", or the ir_codes opt-in flag. Such a device is ALWAYS bridged, so
// the connection picker shows only "Through a bridge" and filters to ir ports.
export function driverIrCapable(d: DriverInfo | undefined): boolean {
  if (!d) return false;
  if ((d.transport || "").toLowerCase() === "bridge") return true;
  return (d as unknown as { ir_codes?: boolean }).ir_codes === true;
}

// Config keys a dedicated picker/editor owns, so the generic schema-driven
// section never renders them as raw text inputs. Serial and IR each have their
// own connection picker; `ir_codes` is owned by the device-page IR Codes editor
// and is never a raw field. The Edit dialog falls back to a device's existing
// config keys when a driver has no config_schema (e.g. generic_ir), so without
// this an IR device would leak ir_codes/bridge/bridge_port as editable text.
export function hiddenRawConfigKeys(driverInfo: DriverInfo | undefined): Set<string> {
  if (driverIrCapable(driverInfo)) return IR_PICKER_FIELDS;
  if (driverSerialCapable(driverInfo)) return SERIAL_PICKER_FIELDS;
  return new Set(["ir_codes"]);
}

function driverNetworkCapable(d: DriverInfo | undefined): boolean {
  if (!d) return false;
  const t = (d.transport || "").toLowerCase();
  const ts = (d.transports || []).map((x) => String(x).toLowerCase());
  return NETWORK_TRANSPORTS.includes(t) || ts.some((x) => NETWORK_TRANSPORTS.includes(x));
}

export function primaryNetworkTransport(d: DriverInfo | undefined): string {
  const t = (d?.transport || "").toLowerCase();
  if (NETWORK_TRANSPORTS.includes(t)) return t;
  const ts = (d?.transports || []).map((x) => String(x).toLowerCase());
  return ts.find((x) => NETWORK_TRANSPORTS.includes(x)) || "tcp";
}

type ConnMode = ConnectionMode;

function inferConnMode(cv: Record<string, string>, d: DriverInfo | undefined): ConnMode {
  // An IR device is always bridged — there's no network/serial mode for it.
  if (driverIrCapable(d)) return "bridge";
  if (cv.bridge && cv.bridge_port) return "bridge";
  if ((cv.transport || "").toLowerCase() === "serial") return "serial";
  if ((cv.host ?? "") !== "") return "network";
  return driverNetworkCapable(d) ? "network" : "serial";
}

// Rebuild the connection-owned keys when the user switches mode so no stale
// field from the previous mode is saved (a TCP port left as a COM path, a host
// left under a bridge binding, ...). Serial line params carry across
// serial<->bridge.
function applyConnMode(
  cv: Record<string, string>,
  next: ConnMode,
  d: DriverInfo | undefined,
): Record<string, string> {
  const v = { ...cv };
  if (next === "network") {
    delete v.bridge;
    delete v.bridge_port;
    delete v.baudrate;
    delete v.bytesize;
    delete v.parity;
    delete v.stopbits;
    delete v.flow_control;
    delete v.usb_serial;
    v.transport = primaryNetworkTransport(d);
    if (v.host == null) v.host = "";
    // A COM path left in `port` is meaningless as a TCP port → reset to the
    // driver's default network port.
    if (v.port && /[A-Za-z]/.test(v.port)) {
      const dp = d?.default_config?.port;
      v.port = dp != null ? String(dp) : "";
    }
  } else if (next === "serial") {
    delete v.host;
    delete v.bridge;
    delete v.bridge_port;
    v.transport = "serial";
    // A numeric TCP port is meaningless as a COM path → clear it.
    if (v.port && /^\d+$/.test(v.port)) v.port = "";
    seedLineSettings(v, d);
  } else {
    // bridge: the resolver computes the effective transport, so we store only
    // the binding. A serial device also keeps its line params; an IR device has
    // none (it emits codes through the port), so drop them.
    delete v.host;
    delete v.transport;
    delete v.port;
    delete v.usb_serial;
    if (driverIrCapable(d)) {
      delete v.baudrate;
      delete v.bytesize;
      delete v.parity;
      delete v.stopbits;
      delete v.flow_control;
    } else {
      seedLineSettings(v, d);
    }
  }
  return v;
}

const LINE_SETTINGS = ["baudrate", "bytesize", "parity", "stopbits", "flow_control"];

// A line setting the form holds no value for: the driver's own default, which
// is what the device connects at when nothing is saved.
function lineDefault(d: DriverInfo | undefined, key: string, fallback: string): string {
  const dv = d?.default_config?.[key];
  return dv != null && dv !== "" ? String(dv) : fallback;
}

// A saved line setting outranks the driver's default, so a form switched to
// serial starts each one at the driver's value: a device that runs at 115200
// is not saved at 9600. Baud rate always gets one; the others only when the
// driver names one (8N1 with no flow control applies otherwise).
function seedLineSettings(v: Record<string, string>, d: DriverInfo | undefined): void {
  for (const key of LINE_SETTINGS) {
    if (v[key] != null) continue;
    if (key === "baudrate") v[key] = lineDefault(d, key, "9600");
    else if (d?.default_config?.[key] != null && d.default_config[key] !== "") v[key] = String(d.default_config[key]);
  }
}

// The form a device starts from when Edit Device picks another driver: the new
// driver's defaults, keeping what belongs to the device rather than to its
// driver. That is where it is (its IP address, the serial port it is plugged
// into and that line's settings, the bridge port it is wired to) when the new
// driver offers the same connection, and a username or password the new
// driver also asks for. The new driver's own default wins for the rest, the
// network port included: another driver often speaks another protocol on
// another port.
export function carryOnDriverChange(
  form: Record<string, string>,
  connection: ConnMode,
  oldInfo: DriverInfo | undefined,
  newInfo: DriverInfo | undefined,
): Record<string, string> {
  let next = prefillConfigValues(newInfo?.default_config, newInfo?.config_schema);
  const keep = (key: string) => {
    if ((form[key] ?? "").trim() !== "") next[key] = form[key];
  };
  const newSchema = (newInfo?.config_schema ?? {}) as Record<string, unknown>;
  const newIr = driverIrCapable(newInfo);
  const newSerial = !newIr && driverSerialCapable(newInfo);
  if (connection === "network" && !newIr && (driverNetworkCapable(newInfo) || "host" in newSchema)) {
    keep("host");
  } else if (connection === "serial" && newSerial) {
    next = applyConnMode(next, "serial", newInfo);
    ["port", "usb_serial", ...LINE_SETTINGS].forEach(keep);
  } else if (connection === "bridge" && (newIr ? driverIrCapable(oldInfo) : newSerial && !driverIrCapable(oldInfo))) {
    next = applyConnMode(next, "bridge", newInfo);
    ["bridge", "bridge_port", ...(newIr ? [] : LINE_SETTINGS)].forEach(keep);
  }
  ["username", "password"].filter((key) => key in newSchema).forEach(keep);
  return next;
}

// The picker's mode, held by the dialog: the required check asks it which
// connection fields apply, and a form switched to "Through a bridge" before a
// bridge is picked cannot be told apart from a network one by its values.
// Re-inferred when the driver changes (the dialog resets configValues then);
// keyed on the id alone so per-keystroke edits don't reset the mode.
function useConnectionMode(
  configValues: Record<string, string>,
  driverInfo: DriverInfo | undefined,
): [ConnMode, (mode: ConnMode) => void] {
  const [mode, setMode] = useState<ConnMode>(() => inferConnMode(configValues, driverInfo));
  useEffect(() => {
    setMode(inferConnMode(configValues, driverInfo));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driverInfo?.id]);
  return [mode, setMode];
}

// The connection the required check reads: the picker's mode when the driver
// has the picker, else the network.
function requiredConnection(driverInfo: DriverInfo | undefined, mode: ConnMode): ConnMode {
  return driverSerialCapable(driverInfo) || driverIrCapable(driverInfo) ? mode : "network";
}

// The dialogs' error line sits at the top of a form that scrolls, and the
// button that is refused sits at the bottom: each refusal brings the line into
// view, the same message pressed twice included.
function useDialogError(): [string, (message: string) => void, React.RefObject<HTMLDivElement | null>] {
  const [error, setErrorText] = useState("");
  const [shown, setShown] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (shown > 0) ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [shown]);
  const setError = (message: string) => {
    setErrorText(message);
    if (message) setShown((n) => n + 1);
  };
  return [error, setError, ref];
}

function RequiredMark() {
  return <span aria-hidden="true" style={{ color: "var(--error, #f44336)", marginLeft: 2 }}>*</span>;
}

const pickerLabelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

// Direct-serial port chooser: a dropdown of the serial ports detected on the
// server (USB-to-serial adapters present to the OS as plain serial ports), with
// a manual-entry escape hatch for a port that isn't attached yet. Picking a
// detected adapter also stores its USB serial number (`usb_serial`) so the
// binding survives the OS port name moving across reboot/replug; manual entry
// stores no serial (the path is taken literally).
function SerialPortPicker({
  value,
  onPick,
  required = false,
}: {
  value: string;
  onPick: (device: string, usbSerial: string) => void;
  required?: boolean;
}) {
  const [ports, setPorts] = useState<SerialPortInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [manual, setManual] = useState(false);

  const load = () => {
    setLoading(true);
    getSerialPorts()
      .then((r) => setPorts(r.ports))
      .catch(() => setPorts([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const MANUAL = "__manual__";
  // A stored port that isn't currently attached still has to show as the
  // selection (editing a device whose adapter is unplugged).
  const detected = ports.some((p) => p.device === value);

  const onSelect = (next: string) => {
    if (next === MANUAL) {
      setManual(true);
      return;
    }
    setManual(false);
    const match = ports.find((p) => p.device === next);
    onPick(next, match?.serial_number ?? "");
  };

  return (
    <div style={{ marginBottom: "var(--space-sm)" }}>
      <label style={pickerLabelStyle}>Serial Port{required && <RequiredMark />}</label>
      <div style={{ display: "flex", gap: "var(--space-xs)" }}>
        {manual ? (
          <input
            value={value}
            onChange={(e) => onPick(e.target.value, "")}
            placeholder="COM3 or /dev/ttyUSB0"
            style={{ flex: 1 }}
            autoFocus
          />
        ) : (
          <select value={value} onChange={(e) => onSelect(e.target.value)} style={{ flex: 1 }}>
            <option value="">{loading ? "Detecting ports..." : "Select a serial port..."}</option>
            {value && !detected && <option value={value}>{value} (not detected)</option>}
            {ports.map((p) => (
              <option key={p.device} value={p.device}>{p.label}</option>
            ))}
            <option value={MANUAL}>Enter path manually...</option>
          </select>
        )}
        <button
          type="button"
          onClick={manual ? () => setManual(false) : load}
          title={manual ? "Back to the detected-port list" : "Refresh the port list"}
          style={{ whiteSpace: "nowrap" }}
        >
          {manual ? "Back" : "Refresh"}
        </button>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
        Ports on the OpenAVC server. The USB adapter must be plugged into that machine.
      </div>
    </div>
  );
}

export function ConnectionModePicker({
  driverInfo,
  configValues,
  setConfigValues,
  mode,
  onModeChange,
  devices,
  drivers,
  selfId,
  allowBridge = true,
}: {
  driverInfo: DriverInfo | undefined;
  configValues: Record<string, string>;
  setConfigValues: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  /** The dialog's mode (useConnectionMode). */
  mode: ConnMode;
  onModeChange: (mode: ConnMode) => void;
  devices: DeviceConfig[];
  drivers: DriverInfo[];
  selfId?: string;
  /** Offer "Through a bridge" (the Device Audit's connection step does not
   *  yet reach a device through a bridge). */
  allowBridge?: boolean;
}) {
  const isIr = driverIrCapable(driverInfo);
  if (!driverSerialCapable(driverInfo) && !isIr) return null;

  const set = (key: string, value: string) =>
    setConfigValues((v) => ({ ...v, [key]: value }));

  const switchMode = (next: ConnMode) => {
    if (next === mode) return;
    onModeChange(next);
    setConfigValues((v) => applyConnMode(v, next, driverInfo));
  };

  // An IR device is always bridged: only the "Through a bridge" mode.
  const netCapable = !isIr && driverNetworkCapable(driverInfo);
  const modes: { id: ConnMode; label: string }[] = [];
  if (!isIr) {
    if (netCapable) modes.push({ id: "network", label: "Network (IP)" });
    modes.push({ id: "serial", label: "Direct serial" });
  }
  if (allowBridge || isIr) modes.push({ id: "bridge", label: "Through a bridge" });

  const modeHelp: Record<ConnMode, string> = {
    network: "Reach the device over the network by IP address.",
    serial: "A serial (RS-232) port on this server.",
    bridge: isIr
      ? "Bind this IR device to an emitter port on an IR bridge (e.g. an iTach IP2IR)."
      : "Route this device's serial line through a bridge device (e.g. an iTach).",
  };

  // The port kind this device binds to: an IR device wants ir ports, everything
  // else wants serial pass-through ports.
  const wantKind = isIr ? "ir" : "serial";

  // Bridge devices = project devices whose driver advertises a port of the kind
  // this device needs (excluding this device itself).
  const bridges = devices.filter((dev) => {
    if (selfId && dev.id === selfId) return false;
    const di = drivers.find((x) => x.id === dev.driver);
    return (di?.bridge?.ports ?? []).some((p) => p.kind === wantKind);
  });
  const selectedBridge = bridges.find((b) => b.id === configValues.bridge);
  const selectedBridgeDriver = selectedBridge
    ? drivers.find((x) => x.id === selectedBridge.driver)
    : undefined;
  const bridgePorts = (selectedBridgeDriver?.bridge?.ports ?? []).filter(
    (p) => p.kind === wantKind,
  );

  const onBridgeChange = (bridgeId: string) => {
    setConfigValues((v) => {
      const bdrv = drivers.find(
        (x) => x.id === devices.find((d) => d.id === bridgeId)?.driver,
      );
      const ports = (bdrv?.bridge?.ports ?? []).filter((p) => p.kind === wantKind);
      // Auto-select when the bridge has exactly one port of the wanted kind.
      return {
        ...v,
        bridge: bridgeId,
        bridge_port: ports.length === 1 ? ports[0].id : "",
      };
    });
  };

  const field = (label: string, node: React.ReactNode, required = false) => (
    <div style={{ marginBottom: "var(--space-sm)" }}>
      <label style={pickerLabelStyle}>{label}{required && <RequiredMark />}</label>
      {node}
    </div>
  );
  const schemaRequires = (key: string) =>
    (driverInfo?.config_schema as Record<string, { required?: unknown }> | undefined)?.[key]
      ?.required === true;

  const serialParams = (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-sm)" }}>
        {field(
          "Baud rate",
          <select value={configValues.baudrate ?? lineDefault(driverInfo, "baudrate", "9600")} onChange={(e) => set("baudrate", e.target.value)} style={{ width: "100%" }}>
            {BAUD_RATES.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>,
        )}
        {field(
          "Parity",
          <select value={configValues.parity ?? lineDefault(driverInfo, "parity", "N")} onChange={(e) => set("parity", e.target.value)} style={{ width: "100%" }}>
            {PARITY_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>,
        )}
        {field(
          "Data bits",
          <select value={configValues.bytesize ?? lineDefault(driverInfo, "bytesize", "8")} onChange={(e) => set("bytesize", e.target.value)} style={{ width: "100%" }}>
            {DATABITS_OPTS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>,
        )}
        {field(
          "Stop bits",
          <select value={configValues.stopbits ?? lineDefault(driverInfo, "stopbits", "1")} onChange={(e) => set("stopbits", e.target.value)} style={{ width: "100%" }}>
            {STOPBITS_OPTS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>,
        )}
      </div>
      {field(
        "Flow control",
        <select value={configValues.flow_control ?? lineDefault(driverInfo, "flow_control", "none")} onChange={(e) => set("flow_control", e.target.value)} style={{ width: "100%" }}>
          {FLOW_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>,
      )}
    </>
  );

  return (
    <div style={{ marginBottom: "var(--space-sm)" }}>
      {/* Segmented mode control */}
      <div style={{ display: "flex", gap: 2, marginBottom: "var(--space-xs)", background: "var(--bg-hover)", borderRadius: "var(--border-radius)", padding: 2 }}>
        {modes.map((m) => (
          <button
            key={m.id}
            onClick={() => switchMode(m.id)}
            style={{
              flex: 1,
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: mode === m.id ? "var(--accent-bg)" : "transparent",
              color: mode === m.id ? "var(--text-on-accent)" : "var(--text-secondary)",
              fontSize: "var(--font-size-sm)",
              border: "none",
              cursor: "pointer",
            }}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        {modeHelp[mode]}
      </div>

      {mode === "network" && (
        <>
          {field("IP Address", <input value={configValues.host ?? ""} onChange={(e) => set("host", e.target.value)} placeholder="192.168.1.100" aria-required={schemaRequires("host") || undefined} style={{ width: "100%" }} />, schemaRequires("host"))}
          {field("Port", <input type="number" value={configValues.port ?? ""} onChange={(e) => set("port", e.target.value)} placeholder={String(driverInfo?.default_config?.port ?? "1-65535")} aria-required={schemaRequires("port") || undefined} style={{ width: "100%" }} />, schemaRequires("port"))}
        </>
      )}

      {mode === "serial" && (
        <>
          <SerialPortPicker
            value={configValues.port ?? ""}
            onPick={(device, usbSerial) =>
              setConfigValues((v) => ({ ...v, port: device, usb_serial: usbSerial }))
            }
            required={schemaRequires("port")}
          />
          {serialParams}
        </>
      )}

      {mode === "bridge" && (
        bridges.length === 0 ? (
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", padding: "var(--space-sm)", background: "var(--bg-base)", borderRadius: "var(--border-radius)" }}>
            {isIr
              ? "No IR bridge in this project yet. Add an IR bridge (such as a Global Cache iTach IP2IR) first, then bind this device to one of its emitter ports."
              : "No bridge devices in this project yet. Add a bridge (such as a Global Cache iTach) first, then bind this device to one of its ports."}
          </div>
        ) : (
          <>
            {field(
              isIr ? "IR bridge" : "Bridge",
              <select value={configValues.bridge ?? ""} onChange={(e) => onBridgeChange(e.target.value)} style={{ width: "100%" }}>
                <option value="">Select a bridge...</option>
                {bridges.map((b) => <option key={b.id} value={b.id}>{b.name || b.id}</option>)}
              </select>,
            )}
            {configValues.bridge && field(
              isIr ? "IR emitter port" : "Bridge port",
              <select value={configValues.bridge_port ?? ""} onChange={(e) => set("bridge_port", e.target.value)} style={{ width: "100%" }}>
                <option value="">Select a port...</option>
                {bridgePorts.map((p) => <option key={p.id} value={p.id}>{p.label || p.id}</option>)}
              </select>,
            )}
            {/* Serial devices set line params here; IR devices have none. */}
            {configValues.bridge && !isIr && serialParams}
          </>
        )
      )}
    </div>
  );
}

// --- Searchable Driver Dropdown ---

const CATEGORY_ORDER = ["projector", "display", "audio", "switcher", "camera", "lighting", "control", "utility", "other"];

function DriverSearchSelect({
  id,
  drivers,
  value,
  onChange,
}: {
  id?: string;
  drivers: DriverInfo[];
  value: string;
  onChange: (driverId: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return drivers.filter(
      (d) =>
        !q ||
        (d.name || d.id).toLowerCase().includes(q) ||
        (d.manufacturer || "").toLowerCase().includes(q) ||
        (d.category || "").toLowerCase().includes(q)
    );
  }, [drivers, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, DriverInfo[]>();
    for (const d of filtered) {
      const cat = d.category || "other";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(d);
    }
    const sorted = [...map.entries()].sort(
      (a, b) => (CATEGORY_ORDER.indexOf(a[0]) === -1 ? 99 : CATEGORY_ORDER.indexOf(a[0]))
        - (CATEGORY_ORDER.indexOf(b[0]) === -1 ? 99 : CATEGORY_ORDER.indexOf(b[0]))
    );
    return sorted;
  }, [filtered]);

  const selected = drivers.find((d) => d.id === value);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <input
        id={id}
        value={open ? search : (selected ? (selected.name || selected.id) : "")}
        onChange={(e) => { setSearch(e.target.value); if (!open) setOpen(true); }}
        onFocus={() => { setOpen(true); setSearch(""); }}
        placeholder="Search drivers..."
        style={{ width: "100%" }}
      />
      {open && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            maxHeight: 260,
            overflow: "auto",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            zIndex: 10,
            boxShadow: "var(--shadow-md)",
          }}
        >
          {grouped.length === 0 && (
            <div style={{ padding: "var(--space-sm) var(--space-md)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              No drivers found
            </div>
          )}
          {grouped.map(([cat, items]) => (
            <div key={cat}>
              <div
                style={{
                  padding: "var(--space-xs) var(--space-md)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  background: "var(--bg-surface)",
                  position: "sticky",
                  top: 0,
                }}
              >
                {cat}
              </div>
              {items.map((d) => (
                <div
                  key={d.id}
                  onClick={() => { onChange(d.id); setOpen(false); setSearch(""); }}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                    background: d.id === value ? "var(--accent-dim)" : "transparent",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = d.id === value ? "var(--accent-dim)" : "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = d.id === value ? "var(--accent-dim)" : "transparent")}
                >
                  <span>{d.name || d.id}</span>
                  {d.manufacturer && (
                    <span style={{ color: "var(--text-muted)", fontSize: 11 }}>{d.manufacturer}</span>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Add Device Dialog ---

function generateDeviceDefaults(
  driver: DriverInfo,
  existingDevices: DeviceConfig[],
): { id: string; name: string } {
  const existingIds = new Set(existingDevices.map((d) => d.id));
  const existingNames = new Set(existingDevices.map((d) => d.name));

  // ID: category-based like "projector_1", "display_2"
  const base = driver.category || "device";
  let idNum = 1;
  while (existingIds.has(`${base}_${idNum}`)) idNum++;
  const id = `${base}_${idNum}`;

  // Name: driver name like "PJLink Class 1", append " 2" if taken
  let name = driver.name;
  if (existingNames.has(name)) {
    let nameNum = 2;
    while (existingNames.has(`${driver.name} ${nameNum}`)) nameNum++;
    name = `${driver.name} ${nameNum}`;
  }

  return { id, name };
}

export function AddDeviceDialog({
  onClose,
  prefill,
}: {
  onClose: () => void;
  prefill?: DeviceConfig;
}) {
  const devices = useProjectStore((s) => s.project?.devices);
  const update = useProjectStore((s) => s.update);
  const save = useProjectStore((s) => s.save);
  const formId = useId();

  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [deviceId, setDeviceId] = useState(prefill ? "" : "");
  const [deviceName, setDeviceName] = useState(prefill?.name ? `${prefill.name} (Copy)` : "");
  const [selectedDriver, setSelectedDriver] = useState(prefill?.driver ?? "");
  const [idTouchedByUser, setIdTouchedByUser] = useState(!!prefill);
  const [nameTouchedByUser, setNameTouchedByUser] = useState(!!prefill?.name);
  // The typed values the form's text came from, so a key the driver's schema
  // does not describe keeps its type on save (buildConfigFromForm).
  const [configOriginals, setConfigOriginals] = useState<Record<string, unknown>>(() => {
    if (!prefill) return {};
    // Merge device.config with connection table overrides (host, port, etc.)
    const conn = useProjectStore.getState().project?.connections?.[prefill.id] ?? {};
    return { ...prefill.config, ...conn };
  });
  const [configValues, setConfigValues] = useState<Record<string, string>>(() => {
    const vals: Record<string, string> = {};
    for (const [k, v] of Object.entries(configOriginals)) {
      if (v != null && typeof v === "object") {
        // Pretty-print so object fields (e.g. a command map) are editable as
        // readable JSON in the multi-line textarea.
        vals[k] = JSON.stringify(v, null, 2);
      } else {
        vals[k] = String(v ?? "");
      }
    }
    return vals;
  });
  const [error, setError, errorRef] = useDialogError();
  const [isAdding, setIsAdding] = useState(false);
  const [setupDeviceId, setSetupDeviceId] = useState<string | null>(null);

  useEffect(() => {
    api.listDrivers().then((items) => setDrivers(items.filter((item) => !item.manufacturer_installed))).catch(console.error);
  }, []);

  const driverInfo = drivers.find((d) => d.id === selectedDriver);
  const [connMode, setConnMode] = useConnectionMode(configValues, driverInfo);
  const configKeys = Object.keys((driverInfo?.config_schema ?? {}) as Record<string, unknown>);
  const serialCapable = driverSerialCapable(driverInfo);
  // The connection picker / IR Codes editor own these fields; keep them out of
  // the generic schema section so they aren't rendered twice (or, for ir_codes,
  // as raw text at all).
  const visibleConfigKeys = configKeys.filter(
    (k) => !hiddenRawConfigKeys(driverInfo).has(k),
  );

  // Check if driver has setup settings
  const hasSetupSettings = useMemo(() => hasDriverSetupSettings(driverInfo), [driverInfo]);

  const handleAdd = async () => {
    if (!deviceId || !selectedDriver) {
      setError("Device ID and driver are required");
      return;
    }
    if (devices?.some((d) => d.id === deviceId)) {
      setError("A device with this ID already exists");
      return;
    }

    const schema = (driverInfo?.config_schema ?? {}) as Record<string, Record<string, unknown>>;
    const built = buildConfigFromForm(configValues, schema, configOriginals, {
      defaults: driverInfo?.default_config,
      connection: requiredConnection(driverInfo, connMode),
    });
    if (!built.ok) {
      setError(built.error);
      return;
    }
    const config = built.config;

    // Same split the device-update API applies: connection fields go to the
    // connections table (v0.5.0 schema), the rest stays in device.config.
    const { config: protocolConfig, connection } = splitConnectionFields(config);

    const newDevice: DeviceConfig = {
      id: deviceId,
      driver: selectedDriver,
      name: deviceName || deviceId,
      config: protocolConfig,
    };

    // Read devices + connections from the same store snapshot so the two
    // halves of the patch can't disagree.
    const current = useProjectStore.getState().project;
    update({
      devices: [...(current?.devices ?? []), newDevice],
      ...(Object.keys(connection).length > 0
        ? {
            connections: {
              ...(current?.connections ?? {}),
              [deviceId]: connection,
            },
          }
        : {}),
    });

    // Show setup dialog if driver has setup settings
    if (hasSetupSettings) {
      setIsAdding(true);
      // The setup dialog reads the device's settings from the engine as soon
      // as it opens, so the save (persist + reconcile) must land first —
      // un-awaited, opening it raced the device's instantiation.
      await save();
      const store = useProjectStore.getState();
      if (store.conflictDetected || store.dirty) {
        // The save didn't land (conflict, or retries exhausted — the global
        // banner explains); don't open setup for a device that isn't there.
        setIsAdding(false);
        return;
      }
      setSetupDeviceId(deviceId);
    } else {
      save();
      onClose();
    }
  };

  return (
    <Modal
      onClose={onClose}
      label="Add Device"
      panelStyle={{
        padding: "var(--space-xl)",
        width: 480,
        maxHeight: "80vh",
        overflow: "auto",
      }}
    >
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>
          {prefill ? "Duplicate Device" : "Add Device"}
        </h3>

        {error && (
          <div
            ref={errorRef}
            style={{
              background: "var(--color-error-bg)",
              color: "var(--color-error)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-md)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {error}
          </div>
        )}

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            htmlFor={`${formId}-driver`}
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Driver <span aria-hidden="true" style={{ color: "var(--color-error, #ef4444)" }}>*</span>
          </label>
          <DriverSearchSelect
            id={`${formId}-driver`}
            drivers={drivers}
            value={selectedDriver}
            onChange={(newDriverId) => {
              setSelectedDriver(newDriverId);
              const newDriver = drivers.find((d) => d.id === newDriverId);
              setConfigOriginals(newDriver?.default_config ?? {});
              setConfigValues(
                prefillConfigValues(newDriver?.default_config, newDriver?.config_schema),
              );
              if (newDriver) {
                const generated = generateDeviceDefaults(newDriver, devices ?? []);
                if (!idTouchedByUser) setDeviceId(generated.id);
                if (!nameTouchedByUser) setDeviceName(generated.name);
              }
            }}
          />
          {driverInfo?.help?.overview && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              {driverInfo.help.overview}
            </div>
          )}
          {driverInfo?.help?.setup && (
            <div style={{
              fontSize: 11,
              color: "var(--text-secondary)",
              marginTop: 4,
              padding: "var(--space-sm)",
              background: "var(--bg-base)",
              borderRadius: "var(--border-radius)",
              whiteSpace: "pre-line",
            }}>
              {driverInfo.help.setup}
            </div>
          )}
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            htmlFor={`${formId}-device-id`}
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Device ID <span aria-hidden="true" style={{ color: "var(--color-error, #ef4444)" }}>*</span>
          </label>
          <input
            id={`${formId}-device-id`}
            aria-required
            aria-describedby={`${formId}-device-id-help`}
            value={deviceId}
            onChange={(e) => {
              setIdTouchedByUser(true);
              setDeviceId(e.target.value.replace(/[^a-z0-9_]/gi, "").toLowerCase());
            }}
            placeholder="e.g., projector_room_1"
            style={{
              width: "100%",
              borderColor: deviceId && !isAdding && devices?.some((d) => d.id === deviceId)
                ? "var(--color-error, #ef4444)" : undefined,
            }}
          />
          <div id={`${formId}-device-id-help`} style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3 }}>
            Lowercase letters, numbers, and underscores only.
            {deviceId && (
              <span style={{ marginLeft: 6 }}>
                Your ID: <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{deviceId}</code>
                {!isAdding && devices?.some((d) => d.id === deviceId) && (
                  <span style={{ color: "var(--color-error, #ef4444)", marginLeft: 6 }}>Already exists</span>
                )}
              </span>
            )}
          </div>
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            htmlFor={`${formId}-name`}
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Display Name
          </label>
          <input
            id={`${formId}-name`}
            value={deviceName}
            onChange={(e) => {
              setNameTouchedByUser(true);
              setDeviceName(e.target.value);
            }}
            placeholder="e.g., Main Projector"
            maxLength={128}
            style={{ width: "100%" }}
          />
        </div>


        {(serialCapable || driverIrCapable(driverInfo) || visibleConfigKeys.length > 0) && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-sm)",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              Connection Settings
            </div>
            {(serialCapable || driverIrCapable(driverInfo)) && (
              <ConnectionModePicker
                driverInfo={driverInfo}
                configValues={configValues}
                setConfigValues={setConfigValues}
                mode={connMode}
                onModeChange={setConnMode}
                devices={devices ?? []}
                drivers={drivers}
                selfId={deviceId || undefined}
              />
            )}
            {visibleConfigKeys.length > 0 && (
              <ConfigFieldInputs
                configKeys={visibleConfigKeys}
                driverInfo={driverInfo}
                configValues={configValues}
                setConfigValues={setConfigValues}
              />
            )}
          </div>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-sm)",
            marginTop: "var(--space-lg)",
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleAdd}
            disabled={isAdding}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              opacity: isAdding ? 0.6 : 1,
            }}
          >
            {isAdding ? "Adding..." : prefill ? "Duplicate Device" : "Add Device"}
          </button>
        </div>

      {setupDeviceId && driverInfo && (
        <DeviceSettingsSetupDialog
          deviceId={setupDeviceId}
          driverInfo={driverInfo}
          existingDeviceIds={(devices ?? []).map((d) => d.id)}
          onClose={onClose}
        />
      )}
    </Modal>
  );
}

// --- Edit Device Dialog ---

export function EditDeviceDialog({
  device,
  onClose,
  onSaved,
}: {
  device: DeviceConfig;
  onClose: () => void;
  onSaved: () => void;
}) {
  const devices = useProjectStore((s) => s.project?.devices);
  const formId = useId();
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [deviceName, setDeviceName] = useState(device.name);
  const [selectedDriver, setSelectedDriver] = useState(device.driver);
  // The device as saved: what the form opens with, and returns to when the
  // device's own driver is picked again. `connection` is its connections row.
  const [stored] = useState(() => {
    // Merge device.config with connection table overrides (host, port, etc.)
    const connection = useProjectStore.getState().project?.connections?.[device.id] ?? {};
    const originals: Record<string, unknown> = { ...device.config, ...connection };
    const values: Record<string, string> = {};
    for (const [k, v] of Object.entries(originals)) {
      if (v != null && typeof v === "object") {
        // Pretty-print so object fields (e.g. a command map) are editable as
        // readable JSON in the multi-line textarea.
        values[k] = JSON.stringify(v, null, 2);
      } else {
        values[k] = String(v ?? "");
      }
    }
    return { connection, originals, values };
  });
  // The typed values the form's text came from, so a key the driver's schema
  // does not describe keeps its type on save (buildConfigFromForm).
  const [configOriginals, setConfigOriginals] = useState<Record<string, unknown>>(stored.originals);
  const [configValues, setConfigValues] = useState<Record<string, string>>(stored.values);
  const [error, setError, errorRef] = useDialogError();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.listDrivers().then(setDrivers).catch(console.error);
  }, []);

  const driverInfo = drivers.find((d) => d.id === selectedDriver);
  const [connMode, setConnMode] = useConnectionMode(configValues, driverInfo);
  // Show config fields from driver schema if available, otherwise from the device's existing config
  const schemaKeys = Object.keys((driverInfo?.config_schema ?? {}) as Record<string, unknown>);
  const existingKeys = Object.keys(configValues);
  const configKeys = schemaKeys.length > 0 ? schemaKeys : existingKeys;
  const serialCapable = driverSerialCapable(driverInfo);
  // The connection picker / IR Codes editor own these fields; keep them out of
  // the generic schema section. This matters most on the existingKeys fallback
  // above: a schema-less IR device (generic_ir) would otherwise dump its raw
  // ir_codes map + bridge/bridge_port as editable text next to the picker.
  const visibleConfigKeys = configKeys.filter(
    (k) => !hiddenRawConfigKeys(driverInfo).has(k),
  );

  // Another driver starts from its own defaults, keeping the device's address
  // and login (carryOnDriverChange); the device's own driver brings back what
  // is saved.
  const handleDriverChange = (newDriver: string) => {
    if (newDriver === selectedDriver) return;
    setSelectedDriver(newDriver);
    if (newDriver === device.driver) {
      setConfigOriginals(stored.originals);
      setConfigValues(stored.values);
      return;
    }
    const newDriverInfo = drivers.find((d) => d.id === newDriver);
    setConfigOriginals(newDriverInfo?.default_config ?? {});
    if (newDriverInfo?.manufacturer_installed) {
      setConfigValues({});
      return;
    }
    setConfigValues(
      carryOnDriverChange(
        configValues, requiredConnection(driverInfo, connMode), driverInfo, newDriverInfo,
      ),
    );
  };

  const handleSave = async () => {
    if (!selectedDriver) {
      setError("Driver is required");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const schema = (driverInfo?.config_schema ?? {}) as Record<string, Record<string, unknown>>;
      const built = driverInfo?.manufacturer_installed
        ? { ok: true as const, config: {} as Record<string, unknown> }
        : buildConfigFromForm(configValues, schema, configOriginals, {
        defaults: driverInfo?.default_config,
        connection: requiredConnection(driverInfo, connMode),
      });
      if (!built.ok) {
        setError(built.error);
        setSaving(false);
        return;
      }
      const config = built.config;
      // The server merges a device's connection settings into what it has,
      // so one the form no longer holds (another connection picked, a field
      // cleared, another driver chosen) is sent as null, which removes it.
      // What is saved is what the form shows.
      for (const key of Object.keys(stored.connection)) {
        if (!driverInfo?.manufacturer_installed && CONNECTION_FIELDS.has(key) && !(key in config)) config[key] = null;
      }

      const updateData: Record<string, unknown> = {
        name: deviceName || device.id,
        driver: selectedDriver,
        config,
      };

      await api.updateDevice(device.id, updateData as {
        name?: string;
        driver?: string;
        config?: Record<string, unknown>;
      });
      // The endpoint persisted + bumped the revision — refetch project +
      // ETag now rather than waiting for the debounced WS reload. With
      // unsaved edits in flight load() no-ops and the 409 guard covers.
      await useProjectStore.getState().load();
      onSaved();
      onClose();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      onClose={onClose}
      label="Edit Device"
      panelStyle={{
        padding: "var(--space-xl)",
        width: 480,
        maxHeight: "80vh",
        overflow: "auto",
      }}
    >
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>
          Edit Device
        </h3>

        {error && (
          <div
            ref={errorRef}
            style={{
              background: "var(--color-error-bg)",
              color: "var(--color-error)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-md)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {error}
          </div>
        )}

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            htmlFor={`${formId}-device-id`}
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Device ID
          </label>
          <input
            id={`${formId}-device-id`}
            aria-describedby={`${formId}-device-id-help`}
            value={device.id}
            disabled
            style={{ width: "100%", opacity: 0.6 }}
          />
          <div
            id={`${formId}-device-id-help`}
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginTop: "var(--space-xs)",
            }}
          >
            Device ID cannot be changed after creation
          </div>
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            htmlFor={`${formId}-driver`}
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Driver
          </label>
          <DriverSearchSelect
            id={`${formId}-driver`}
            drivers={
              // Include current driver if not in the loaded list
              selectedDriver && !drivers.some(d => d.id === selectedDriver)
                ? [...drivers, { id: selectedDriver, name: selectedDriver + (drivers.length === 0 ? " (loading...)" : " (not installed)"), manufacturer: "", category: "other", commands: {}, config_schema: {} }]
                : drivers
            }
            value={selectedDriver}
            onChange={handleDriverChange}
          />
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            htmlFor={`${formId}-name`}
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Display Name
          </label>
          <input
            id={`${formId}-name`}
            value={deviceName}
            onChange={(e) => setDeviceName(e.target.value)}
            placeholder="e.g., Main Projector"
            maxLength={128}
            style={{ width: "100%" }}
          />
        </div>


        {driverInfo?.manufacturer_installed && (
          <p style={{ color: "var(--text-secondary)", marginBottom: "var(--space-md)" }}>
            Use this product's hardware for this device. Its device ID and existing programming
            stay in the project. Review the controls for the new equipment before running the system.
          </p>
        )}
        {!driverInfo?.manufacturer_installed && (serialCapable || driverIrCapable(driverInfo) || visibleConfigKeys.length > 0) && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-sm)",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              Connection Settings
            </div>
            {(serialCapable || driverIrCapable(driverInfo)) && (
              <ConnectionModePicker
                driverInfo={driverInfo}
                configValues={configValues}
                setConfigValues={setConfigValues}
                mode={connMode}
                onModeChange={setConnMode}
                devices={devices ?? []}
                drivers={drivers}
                selfId={device.id}
              />
            )}
            {visibleConfigKeys.length > 0 && (
              <ConfigFieldInputs
                configKeys={visibleConfigKeys}
                driverInfo={driverInfo}
                configValues={configValues}
                setConfigValues={setConfigValues}
              />
            )}
          </div>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-sm)",
            marginTop: "var(--space-lg)",
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? "Saving..." : "Save Changes"}
          </button>
        </div>
    </Modal>
  );
}
