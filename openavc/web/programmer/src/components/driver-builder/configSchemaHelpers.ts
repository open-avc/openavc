// Pure logic for the config-schema editor, split out so it can be unit
// tested without React (see openavc/tests/test_config_schema_helpers.py).
// The editor component imports these; keep this file free of React/DOM
// imports. Mirrors stateVariableHelpers.ts, which backs the state-variable
// editor the same way.

export interface ConfigFieldDef {
  type: string;
  label: string;
  default?: unknown;
  description?: string;
  secret?: boolean;
  required?: boolean;
  min?: number;
  max?: number;
  values?: string[];
}

/** Config-field types whose values are numbers. `float` is the runtime's
 *  alias for `number` (driver_loader.py accepts both), so it renders and
 *  coerces the same way. */
export const NUMERIC_CONFIG_TYPES: ReadonlySet<string> = new Set([
  "integer",
  "number",
  "float",
]);

/**
 * Coerce the Default Value widget's string form into the typed value stored
 * in default_config, per the field's declared type. `undefined` means "no
 * default" — the caller drops the key. An empty input always means unset,
 * and a value that can't represent the declared type (e.g. "hdmi1" as an
 * integer) is dropped rather than stored as the wrong primitive. Strings
 * (string/text/enum) stay exactly as typed — number-sniffing here would
 * corrupt all-numeric values like a "0123" device ID.
 */
export function coerceConfigDefault(
  fieldType: string,
  raw: string,
): string | number | boolean | undefined {
  if (raw === "") return undefined;
  if (fieldType === "boolean") {
    // Only the two literal spellings are booleans — anything else (e.g. an
    // old enum default surviving a type switch) is dropped, never invented.
    if (raw === "true") return true;
    if (raw === "false") return false;
    return undefined;
  }
  if (NUMERIC_CONFIG_TYPES.has(fieldType)) {
    const n = fieldType === "integer" ? parseInt(raw, 10) : parseFloat(raw);
    return Number.isFinite(n) ? n : undefined;
  }
  return raw;
}

/**
 * Compute the field def and its stored default when the field's `type`
 * changes, as ONE atomic pair so a single store write applies both. Strips
 * enum values when leaving enum, and re-coerces the existing default into
 * the new type (dropping it when it can't represent one) so a type switch
 * never leaves a wrong-typed default behind in default_config or in the
 * schema entry's own `default`.
 */
export function applyConfigFieldTypeChange(
  field: ConfigFieldDef,
  currentDefault: unknown,
  newType: string,
): { field: ConfigFieldDef; defaultValue: string | number | boolean | undefined } {
  const next = { ...field, type: newType } as Record<string, unknown>;
  if (newType !== "enum") delete next.values;
  const defaultValue = coerceConfigDefault(
    newType,
    currentDefault === undefined || currentDefault === null
      ? ""
      : String(currentDefault),
  );
  if ("default" in next) {
    if (defaultValue === undefined) delete next.default;
    else next.default = defaultValue;
  }
  return { field: next as unknown as ConfigFieldDef, defaultValue };
}

/**
 * Toggle `secret` on a config field as one atomic update to both maps.
 * Marking a field secret PURGES its default — the schema entry's `default`
 * and the default_config value, including one imported from a hand-authored
 * file — because a secret default exports plaintext credentials into the
 * shareable .avcdriver. Keys match the draft's field names so the result
 * feeds the editor's onUpdate directly.
 */
export function applyConfigSecretToggle(
  schema: Record<string, ConfigFieldDef>,
  defaultConfig: Record<string, unknown>,
  name: string,
  isSecret: boolean,
): {
  config_schema: Record<string, ConfigFieldDef>;
  default_config: Record<string, unknown>;
} {
  const field = { ...schema[name], secret: isSecret } as Record<string, unknown>;
  const nextDefaults = { ...defaultConfig };
  if (isSecret) {
    delete field.default;
    delete nextDefaults[name];
  }
  return {
    config_schema: { ...schema, [name]: field as unknown as ConfigFieldDef },
    default_config: nextDefaults,
  };
}
