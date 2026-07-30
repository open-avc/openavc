import type { ActionParam, DriverParamDef } from "../../api/types";

/** Either param-schema shape — the authoring surfaces use both. */
type ParamLike = Partial<DriverParamDef> & Partial<ActionParam>;

/**
 * Validate a single command/action param value against its declared schema —
 * the authoring-time mirror of the runtime gate in ConfigurableDriver
 * (`_normalize_and_validate_command_params`). Returns a short, user-facing
 * error string, or null when the value is acceptable (or can't be checked
 * here).
 *
 * Deliberately skipped (returns null):
 *   - a dynamic `$var/$state` reference — the runtime resolves it, so the
 *     literal text can't be checked at authoring time;
 *   - an empty value — a missing *required* field is reported separately by
 *     `hasMissingRequired`, and an empty optional is fine.
 * Values are trimmed before checking, matching the runtime's normalization, so
 * `" 5 "` validates like `"5"`.
 *
 * This is an aid only — it never becomes the gate. The runtime re-validates
 * every value regardless of what the client sent; this just catches mistakes
 * before the user sends or saves.
 */
export function validateParam(def: ParamLike, value: string): string | null {
  if (typeof value !== "string") return null;
  if (value.startsWith("$")) return null; // dynamic ref — resolved at runtime
  const v = value.trim();
  if (v === "") return null;
  const type = def.type || "string";

  if (type === "integer" || type === "number" || type === "float") {
    const n = Number(v);
    if (!Number.isFinite(n)) {
      return type === "integer" ? "Must be a whole number." : "Must be a number.";
    }
    if (type === "integer" && !Number.isInteger(n)) {
      return "Must be a whole number.";
    }
    if (typeof def.min === "number" && n < def.min) {
      return `Must be at least ${def.min}.`;
    }
    if (typeof def.max === "number" && n > def.max) {
      return `Must be at most ${def.max}.`;
    }
    return null;
  }

  if (def.pattern) {
    let re: RegExp;
    try {
      // Full-match, mirroring the runtime's re.fullmatch.
      re = new RegExp(`^(?:${def.pattern})$`);
    } catch {
      return null; // malformed pattern — load-time validation owns this
    }
    if (!re.test(v)) return "Value doesn't match the required format.";
  }
  return null;
}

/**
 * True when any param's current value fails `validateParam` — for disabling a
 * send/save button on a dialog surface. The per-param inline error (rendered by
 * ParamInput) tells the user *which* one; this aggregate just gates the action.
 */
export function hasInvalidParams(
  params: Record<string, ParamLike>,
  values: Record<string, string>,
): boolean {
  return Object.keys(params).some(
    (k) => validateParam(params[k], values[k] ?? "") !== null,
  );
}

/**
 * True when a param declaring `required` has been left blank.
 *
 * Separate from `hasInvalidParams` because `validateParam` deliberately passes
 * an empty value (it can't tell an optional left blank from a required one).
 * The Quick Action strip and the setup wizard have always checked this through
 * their own `hasMissingRequired`; the plain Send Command form did not, so a
 * required param left blank was simply dropped from the request and the user
 * got whatever the driver did next. The runtime now refuses it outright, and
 * this is the same answer one step earlier, inline, without a round trip.
 */
export function hasMissingRequiredParams(
  params: Record<string, ParamLike>,
  values: Record<string, string>,
): boolean {
  return Object.keys(params).some(
    (k) => params[k]?.required && (values[k] ?? "").trim() === "",
  );
}
