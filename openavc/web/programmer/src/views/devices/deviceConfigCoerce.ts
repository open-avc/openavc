// Shared logic for the Add/Edit Device dialogs' config form: which input a
// schema field renders as, how its string form coerces back into the typed
// value stored in the project, and which fields belong in the project's
// connections table rather than device.config.
//
// The Add and Edit dialogs both use this so they can't drift — the drift was
// the bug: the Add dialog stored an object-typed field (e.g. the generic_tcp
// `commands` map) as a raw string, which then broke command sending at runtime
// with an AttributeError. An object field that isn't valid JSON now reports a
// clear error instead of being silently stored as a string.

import { CONNECTION_FIELDS } from "../../api/types.gen";

export type CoerceResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

const SIMPLE_NUMBER = /^-?\d+(\.\d+)?$/;

// A password/secret config field must never be pre-filled — render it blank so
// a masked default (or stored value) can't be re-saved by accident.
export function isSecretConfigField(
  schema: Record<string, unknown> | undefined,
  key: string,
): boolean {
  const f = schema?.[key] as { type?: string; secret?: boolean } | undefined;
  return f?.type === "password" || f?.secret === true;
}

// The dialog form is a flat string map, so a driver's default_config is
// flattened into it when a driver is picked. A scalar becomes its string. An
// array or object (a `table` field's default rows, an object field's default
// map) becomes JSON, which coerceConfigValue parses straight back into the
// typed value on save: String() on an array gave "[object Object],..." which
// the table coercion could not parse, so a driver's declared default roster
// (the Tesira block list, for one) was saved as an empty list. Secrets are
// never pre-filled, and an empty default is left unset so the driver's own
// default applies.
export function prefillConfigValues(
  defaults: Record<string, unknown> | undefined,
  schema: Record<string, unknown> | undefined,
): Record<string, string> {
  const prefilled: Record<string, string> = {};
  for (const [k, v] of Object.entries(defaults ?? {})) {
    if (isSecretConfigField(schema, k)) continue;
    if (v === "" || v == null) continue;
    prefilled[k] = typeof v === "object" ? JSON.stringify(v) : String(v);
  }
  return prefilled;
}

// Which input widget a config field renders as. Centralised so the dialogs
// can't disagree with the coercion rules below — and so `secret: true`
// (the Driver Builder's Secret checkbox, e.g. generic_http's passwords and
// API keys) reliably gets a masked input instead of falling through to the
// plaintext fallback.
export type ConfigFieldKind =
  | "boolean"
  | "password"
  | "select"
  | "number"
  | "textarea"
  | "table"
  | "plain";

export function configFieldKind(
  field: Record<string, unknown> | undefined,
): ConfigFieldKind {
  const f = (field ?? {}) as {
    type?: unknown;
    secret?: unknown;
    values?: unknown;
  };
  const fieldType = String(f.type || "string");
  if (fieldType === "boolean") return "boolean";
  // Secret wins over every other widget: a dropdown or number input would
  // show the credential on screen.
  if (fieldType === "password" || f.secret === true) return "password";
  // A table field is a repeatable typed-row list, edited on the device page by
  // ConfigTableEditor — the Add/Edit dialog just shows a pointer to it (the
  // dialog form is a flat string map that can't hold a row array).
  if (fieldType === "table") return "table";
  const values = f.values as unknown[] | undefined;
  if (Array.isArray(values) && values.length > 0) return "select";
  if (fieldType === "integer" || fieldType === "number" || fieldType === "float") {
    return "number";
  }
  if (fieldType === "text" || fieldType === "object" || fieldType === "json") {
    return "textarea";
  }
  return "plain";
}

// `original` is the typed value the form's text came from, when known (the
// device's saved config, or the driver's default_config). It only matters for
// a key the schema does not describe, where the text alone cannot say whether
// "true" was a boolean or "1" was text.
export function coerceConfigValue(
  val: string,
  fieldType: string,
  secret = false,
  original?: unknown,
): CoerceResult {
  if (fieldType === "boolean") {
    return { ok: true, value: val === "true" };
  }
  if (fieldType === "integer" || fieldType === "number" || fieldType === "float") {
    return { ok: true, value: SIMPLE_NUMBER.test(val) ? Number(val) : val };
  }
  if (fieldType === "text") {
    // Multi-line free text — preserve the raw string, no coercion.
    return { ok: true, value: val };
  }
  if (fieldType === "table") {
    // A table field is authored on the device page (ConfigTableEditor), never
    // in the dialog's flat string form. But the Edit dialog still loads the
    // whole config into that form (an array value gets JSON-stringified) and
    // re-coerces on save, so parse it straight back to the row array to
    // preserve it. Tolerate a blank/invalid value as an empty list rather than
    // failing a save the user can't fix here.
    if (val.trim() === "") return { ok: true, value: [] };
    try {
      const parsed = JSON.parse(val);
      return { ok: true, value: Array.isArray(parsed) ? parsed : [] };
    } catch {
      return { ok: true, value: [] };
    }
  }
  if (fieldType === "object" || fieldType === "json") {
    let parsed: unknown;
    try {
      parsed = JSON.parse(val);
    } catch {
      return { ok: false, error: "must be valid JSON" };
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { ok: false, error: "must be a JSON object" };
    }
    return { ok: true, value: parsed };
  }
  if (fieldType === "string" || fieldType === "password" || secret) {
    // A declared string/password (or any secret) stays exactly as typed.
    // Number-sniffing here corrupted all-numeric credentials: a PIN of
    // "0123" became the number 123, and codes past 2^53 lost digits.
    return { ok: true, value: val };
  }
  // Untyped. A value the form still shows as it came keeps its type, and a
  // true/false that came as a boolean stays one: a hidden default of `true`
  // saved as the text "true" made a saved "false" read as on.
  if (original != null && typeof original !== "object") {
    if (String(original) === val) return { ok: true, value: original };
    if (typeof original === "boolean" && (val === "true" || val === "false")) {
      return { ok: true, value: val === "true" };
    }
    if (typeof original === "string") return { ok: true, value: val };
  }
  // Otherwise accept a JSON object if it happens to parse (back-compat with
  // schema-less edits), else a number when the round-trip is lossless, else
  // the raw string. The lossless check keeps schema-less edits from
  // corrupting values that merely look numeric ("0123", 19-digit codes).
  try {
    const parsed = JSON.parse(val);
    if (typeof parsed === "object" && parsed !== null) {
      return { ok: true, value: parsed };
    }
  } catch {
    /* not JSON — fall through to number / string */
  }
  if (SIMPLE_NUMBER.test(val) && String(Number(val)) === val) {
    return { ok: true, value: Number(val) };
  }
  return { ok: true, value: val };
}

// The listed value of an enum field whose text is `val`, so the field keeps
// the type its driver lists ("4" stays text, 4 stays a number): the rule the
// server applies to a value the device fills in (core/learned_config.py).
function listedEnumValue(
  field: Record<string, unknown> | undefined,
  val: string,
): { found: boolean; value?: unknown } {
  if (field?.type !== "enum" || !Array.isArray(field.values)) return { found: false };
  for (const option of field.values as unknown[]) {
    const value =
      option !== null && typeof option === "object"
        ? (option as { value?: unknown }).value
        : option;
    if (value == null || value === "") continue;
    if (String(value) === val.trim()) return { found: true, value };
  }
  return { found: false };
}

// How the device connects, as the connection picker shows it. A driver
// without the picker connects over the network.
export type ConnectionMode = "network" | "serial" | "bridge";

// The connection picker's fields a driver may require, and the connections
// each applies to: no IP address is asked of a device wired by serial or
// reached through a bridge. The picker's other fields (serial line settings,
// the bridge binding) are never refused, and a bridge picked before its port
// is still a bridged device. Mirrors
// PICKER_FIELD_CONNECTIONS in openavc/core/required_config.py, the server's
// half of this rule; keep the two in sync.
const PICKER_FIELD_CONNECTIONS: Record<string, ConnectionMode[]> = {
  host: ["network"],
  port: ["network", "serial"],
};

const isBlank = (v: unknown) => v == null || (typeof v === "string" && v.trim() === "");

// The label of each required field the form leaves blank, in schema order. A
// field counts as filled when the form holds a value or the driver has a
// default for it, which is what the device would run with. A table field is
// set up on the device page, never in the dialog, so it is not asked here.
export function missingRequiredFields(
  values: Record<string, string>,
  schema: Record<string, Record<string, unknown>>,
  defaults: Record<string, unknown> | undefined,
  connection: ConnectionMode,
): string[] {
  const missing: string[] = [];
  for (const [key, field] of Object.entries(schema)) {
    if (field?.required !== true || field.type === "table") continue;
    if (SERIAL_PICKER_FIELDS.has(key) || IR_PICKER_FIELDS.has(key)) {
      if (!PICKER_FIELD_CONNECTIONS[key]?.includes(connection)) continue;
    }
    if (isBlank(values[key]) && isBlank(defaults?.[key])) {
      missing.push(String(field.label || key));
    }
  }
  return missing;
}

// "Model is required." / "IP Address and Model are required." The server
// words its refusal the same way (required_sentence).
export function requiredFieldsMessage(labels: string[]): string {
  if (labels.length === 1) return `${labels[0]} is required.`;
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]} are required.`;
}

// The Add and Edit dialogs' form back into a typed config: every key coerced
// by its schema field, or, for a key the schema does not describe, by the
// typed value it came from (`originals`). Empty normally means "unset, leave
// alone", but an empty usb_serial is a deliberate clear (the user re-picked a
// manual or no-serial port) and is kept so it overwrites a previously bound
// adapter serial in the merge. A required field left blank refuses the save
// (`required`: the driver's default_config and the picker's connection).
export function buildConfigFromForm(
  values: Record<string, string>,
  schema: Record<string, Record<string, unknown>>,
  originals: Record<string, unknown>,
  required: { defaults: Record<string, unknown> | undefined; connection: ConnectionMode },
): { ok: true; config: Record<string, unknown> } | { ok: false; error: string } {
  const missing = missingRequiredFields(values, schema, required.defaults, required.connection);
  if (missing.length > 0) return { ok: false, error: requiredFieldsMessage(missing) };
  const config: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(values)) {
    if (val === "" && key !== "usb_serial") continue;
    const field = schema[key];
    const listed = listedEnumValue(field, val);
    if (listed.found) {
      config[key] = listed.value;
      continue;
    }
    const result = coerceConfigValue(
      val, String(field?.type || ""), field?.secret === true, originals[key],
    );
    if (!result.ok) {
      return { ok: false, error: `${String(field?.label || key)}: ${result.error}` };
    }
    config[key] = result.value;
  }
  return { ok: true, config };
}

// Connection-related config fields that belong in the project's connections
// table, not device.config. Generated from the driver contract (spec.py), the
// list the server splits a device's config by.
export { CONNECTION_FIELDS };

// Connection fields the segmented connection-mode picker owns and renders
// itself (for serial-capable drivers). The Add/Edit dialogs exclude these from
// the generic schema-driven section so they aren't rendered twice. `transport`
// is here too: the picker sets it ("serial" for Direct serial; cleared for
// bridge mode, where the resolver forces tcp) even though it lives in
// device.config, not the connections table.
export const SERIAL_PICKER_FIELDS = new Set([
  "transport",
  "host",
  "port",
  "bridge",
  "bridge_port",
  "usb_serial",
  "baudrate",
  "bytesize",
  "parity",
  "stopbits",
  "flow_control",
]);

// The IR counterpart to SERIAL_PICKER_FIELDS. An IR device is always bridged, so
// the connection picker owns `bridge`/`bridge_port` (and `transport` is the
// "bridge" sentinel), and its code set lives in `ir_codes` — owned by the
// device-page IR Codes editor. None of these belong in the generic
// schema-driven config section (an IR driver has no config_schema, so without
// this filter the Edit dialog would dump them as raw text fields).
export const IR_PICKER_FIELDS = new Set([
  "transport",
  "bridge",
  "bridge_port",
  "ir_codes",
]);

// Split a flat config map the way the device-update API does
// (openavc/api/routes/devices.py): connection fields go to the connections
// table, the rest stays in device.config. The Add dialog persists via the
// whole-project save, so it has to apply the same split itself or freshly
// added devices land with host/port/password in device.config — violating
// the v0.5.0 project schema and hiding them from anything that reads
// project.connections (cloud config push, migrations, diffing).
export function splitConnectionFields(config: Record<string, unknown>): {
  config: Record<string, unknown>;
  connection: Record<string, unknown>;
} {
  const protocol: Record<string, unknown> = {};
  const connection: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config)) {
    if (CONNECTION_FIELDS.has(key)) {
      connection[key] = value;
    } else {
      protocol[key] = value;
    }
  }
  return { config: protocol, connection };
}
