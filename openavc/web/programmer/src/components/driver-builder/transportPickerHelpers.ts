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
