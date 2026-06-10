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

export function coerceConfigValue(
  val: string,
  fieldType: string,
  secret = false,
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
  // Untyped: accept a JSON object if it happens to parse (back-compat with
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

// Connection-related config fields that belong in the project's connections
// table, not device.config. Mirrors CONNECTION_FIELDS in
// server/core/project_migration.py — keep the two in sync.
export const CONNECTION_FIELDS = new Set([
  "host",
  "port",
  "baudrate",
  "username",
  "password",
  "base_url",
  "ssl",
]);

// Split a flat config map the way the device-update API does
// (server/api/routes/devices.py): connection fields go to the connections
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
