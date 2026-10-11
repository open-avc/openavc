// React-free helpers for the transport config panel (TransportPicker) and
// the driver export path.

/**
 * Canonical delimiter form = real control characters — what YAML decoding
 * produces for every installed driver and what the inline-protocol editor
 * already stores. Drafts saved by older Builder versions hold the escaped
 * text form ("\\r" as two characters), which the runtime also accepts, so
 * normalize on read before comparing against the dropdown options.
 */
export function normalizeDelimiter(delimiter: string): string {
  return delimiter.replace(/\\r/g, "\r").replace(/\\n/g, "\n");
}

/**
 * Visible text for a delimiter — real control characters are invisible in
 * an option label, so render them as escape sequences.
 */
export function displayDelimiter(delimiter: string): string {
  let out = "";
  for (const ch of delimiter) {
    const code = ch.charCodeAt(0);
    if (ch === "\r") out += "\\r";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\t") out += "\\t";
    else if (code < 0x20 || code === 0x7f) {
      out += "\\x" + code.toString(16).padStart(2, "0");
    } else {
      out += ch;
    }
  }
  return out;
}

/**
 * Interpret one keystroke in a numeric default-config field.
 *
 * Returns the parsed number to store, `null` when the field was cleared
 * (unset the key — the input's placeholder shows the effective default),
 * or `undefined` for an unparseable string (ignore the keystroke). This
 * lets the field hold blank and 0 while editing instead of snapping to a
 * magic default.
 */
export function parseNumericField(
  raw: string,
  float = false,
): number | null | undefined {
  if (raw.trim() === "") return null;
  const n = float ? parseFloat(raw) : parseInt(raw, 10);
  return Number.isNaN(n) ? undefined : n;
}

/** default_config fields that hold credentials. */
export const SECRET_CONFIG_FIELDS = ["token", "api_key"] as const;

/**
 * Which secret fields carry a non-empty value — used to mask the inputs'
 * context and to warn before exporting a driver file that would contain
 * the credential in cleartext. A field counts as secret by well-known name
 * (token/api_key) or when the driver's config_schema flags it `secret: true`
 * — the latter catches hand-authored/imported drivers whose secret fields
 * the Builder itself can't create defaults for.
 */
export function secretFieldsInConfig(
  config: Record<string, unknown> | undefined,
  schema?: Record<string, unknown>,
): string[] {
  if (!config) return [];
  const names = new Set<string>(SECRET_CONFIG_FIELDS);
  for (const [name, def] of Object.entries(schema ?? {})) {
    if ((def as { secret?: boolean } | undefined)?.secret === true) names.add(name);
  }
  return [...names].filter(
    (field) => typeof config[field] === "string" && (config[field] as string).length > 0,
  );
}

type FieldDef = Record<string, unknown>;

const ADDRESS_FIELD: FieldDef = { type: "string", required: true, label: "IP Address" };

/**
 * The connection fields a driver declares for its transport, so the Add
 * Device form asks for them: an address and port on the network, the port
 * on a serial line, nothing for a bridge device (it has no address of its
 * own). tcp, udp and osc have no port to fall back on, so theirs is
 * required; http dials 80 or 443 without one. The Builder keeps these out of
 * its Configuration Fields list, so it is the only thing that writes them.
 */
export function connectionFieldsFor(transport: string): Record<string, FieldDef> {
  switch (transport) {
    case "tcp":
    case "udp":
    case "osc":
      return {
        host: { ...ADDRESS_FIELD },
        port: { type: "integer", required: true, label: "Port" },
      };
    case "http":
      return { host: { ...ADDRESS_FIELD }, port: { type: "integer", label: "Port" } };
    case "serial":
      return { port: { type: "string", required: true, label: "Serial Port" } };
    default:
      return {};
  }
}

function sameField(a: unknown, b: FieldDef): boolean {
  if (!a || typeof a !== "object") return false;
  const x = a as FieldDef;
  const keys = new Set([...Object.keys(x), ...Object.keys(b)]);
  for (const k of keys) if (x[k] !== b[k]) return false;
  return true;
}

/**
 * config_schema after the transport changes from `from` to `to`. A
 * connection field still as the Builder wrote it for `from` gives way to the
 * one `to` needs; a field the author changed is kept. The connection fields
 * come first, so the Add Device form asks for the address before anything
 * else.
 */
export function switchConnectionFields(
  schema: Record<string, unknown> | undefined,
  from: string,
  to: string,
): Record<string, unknown> {
  const rest: Record<string, unknown> = { ...(schema ?? {}) };
  for (const [key, def] of Object.entries(connectionFieldsFor(from))) {
    if (sameField(rest[key], def)) delete rest[key];
  }
  const first: Record<string, unknown> = {};
  for (const [key, def] of Object.entries(connectionFieldsFor(to))) {
    if (key in rest) {
      first[key] = rest[key];
      delete rest[key];
    } else {
      first[key] = def;
    }
  }
  return { ...first, ...rest };
}
