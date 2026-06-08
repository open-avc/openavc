import type { ActionParam } from "../../api/types";

/** Default string value to seed a param field with. */
export function defaultFor(def: ActionParam): string {
  if (def.default !== undefined && def.default !== null) return String(def.default);
  if (def.type === "enum" && def.values && def.values.length > 0 && def.required) {
    return def.values[0];
  }
  if (def.type === "boolean") return "false";
  return "";
}

/** Seed a values map (name -> string) from a param schema. */
export function seedParamValues(
  params: Record<string, ActionParam>,
): Record<string, string> {
  const seed: Record<string, string> = {};
  for (const [name, def] of Object.entries(params)) seed[name] = defaultFor(def);
  return seed;
}

/** Coerce a string field value to the param's declared type. */
export function coerceParam(value: string, type?: string): unknown {
  if (type === "integer") {
    const n = parseInt(value, 10);
    return Number.isNaN(n) ? value : n;
  }
  if (type === "number" || type === "float") {
    const n = parseFloat(value);
    return Number.isNaN(n) ? value : n;
  }
  if (type === "boolean") return value === "true";
  return value;
}

/** Build the params object to send: coerce by type, drop empty optionals. */
export function buildParams(
  params: Record<string, ActionParam>,
  values: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, def] of Object.entries(params)) {
    const raw = values[name] ?? "";
    if (raw === "" && !def.required) continue;
    out[name] = coerceParam(raw, def.type);
  }
  return out;
}

/** True when a required field is still blank. */
export function hasMissingRequired(
  params: Record<string, ActionParam>,
  values: Record<string, string>,
): boolean {
  return Object.keys(params).some(
    (k) => params[k].required && (values[k] ?? "").trim() === "",
  );
}

/** Renders the input fields for an action's params (enum / boolean / number /
 *  text / password), mirroring the Send Command param form. */
export function ActionParamFields({
  params,
  values,
  onChange,
}: {
  params: Record<string, ActionParam>;
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
}) {
  return (
    <>
      {Object.keys(params).map((name) => {
        const def = params[name];
        const label = def.label || name;
        const type = def.type || "string";
        const current = values[name] ?? "";
        return (
          <div key={name} style={{ marginBottom: "var(--space-md)" }}>
            <label
              style={{
                display: "block",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: 4,
              }}
            >
              {label}
              {def.required && <span style={{ color: "var(--color-error)" }}> *</span>}
            </label>
            {type === "enum" && def.values ? (
              <select
                value={current}
                onChange={(e) => onChange(name, e.target.value)}
                style={{ width: "100%" }}
              >
                {!def.required && <option value="">(none)</option>}
                {def.values.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            ) : type === "boolean" ? (
              <select
                value={current || "false"}
                onChange={(e) => onChange(name, e.target.value)}
                style={{ width: "100%" }}
              >
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            ) : (
              <input
                type={
                  def.secret || type === "password"
                    ? "password"
                    : type === "integer" || type === "number"
                      ? "number"
                      : "text"
                }
                value={current}
                min={def.min}
                max={def.max}
                onChange={(e) => onChange(name, e.target.value)}
                placeholder={
                  def.min !== undefined && def.max !== undefined
                    ? `${def.min}-${def.max}`
                    : name
                }
                style={{ width: "100%" }}
              />
            )}
            {def.help && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                {def.help}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
