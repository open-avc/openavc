/**
 * A command's parameters as a form holds them: what it starts with, what it
 * sends, and when it cannot send yet. Shared by every form that sends a
 * command (the device page, the Driver Builder's Test tab, the device audit)
 * through `CommandParamForm`.
 *
 * A form holds each value as a string, the way the inputs give it; the
 * declared type is applied when it is sent.
 */
import type { DriverParamDef } from "../../api/types";
import { normalizeOptionList } from "./paramOptions";
import { hasInvalidParams, hasMissingRequiredParams } from "./paramValidation";

export type CommandParamDefs = Record<string, Partial<DriverParamDef>>;

/**
 * What the form starts with when a command is chosen.
 *
 * By default a parameter starts at the value its driver declares, and
 * otherwise EMPTY: a form that sends to real hardware over a connection it
 * opened itself (the Driver Builder's Test tab, a device audit) must not put a
 * value in the form nobody picked. An unchosen required parameter is supposed
 * to stop the send.
 *
 * `firstOption` is the device page's form: an enum starts on its first
 * option, a flag on false, anything else empty.
 */
export function seedCommandParams(
  params: CommandParamDefs,
  opts: { firstOption?: boolean } = {},
): Record<string, string> {
  const seeded: Record<string, string> = {};
  for (const [name, def] of Object.entries(params)) {
    if (opts.firstOption) {
      const options = Array.isArray(def.values) ? normalizeOptionList(def.values) : [];
      if (def.type === "enum" && options.length > 0) seeded[name] = options[0].value;
      else if (def.type === "boolean") seeded[name] = "false";
      else seeded[name] = "";
    } else {
      seeded[name] = def.default !== undefined && def.default !== null ? String(def.default) : "";
    }
  }
  return seeded;
}

/**
 * The values to send, each as its declared type: a whole number, a number, a
 * flag. An empty field is left out, and so is a number that does not parse
 * (the form's own check has already said so). A value with no declaration is
 * sent as typed.
 */
export function coerceCommandParams(
  params: CommandParamDefs,
  values: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, val] of Object.entries(values)) {
    if (val === "") continue;
    const def = params[name];
    if (!def) {
      out[name] = val;
      continue;
    }
    if (def.type === "integer") {
      const n = parseInt(val, 10);
      if (!Number.isNaN(n)) out[name] = n;
    } else if (def.type === "number" || def.type === "float") {
      const n = parseFloat(val);
      if (!Number.isNaN(n)) out[name] = n;
    } else if (def.type === "boolean") {
      out[name] = val === "true";
    } else {
      out[name] = val;
    }
  }
  return out;
}

/** True while the form cannot send: a value is out of range or the wrong
 *  shape, or a required one is empty. Each field says which. */
export function commandParamsBlocked(
  params: CommandParamDefs,
  values: Record<string, string>,
): boolean {
  return hasInvalidParams(params, values) || hasMissingRequiredParams(params, values);
}

/** What to ask before a person sends this command by hand, or null to send at
 *  once: the driver's own sentence, or a plain question for `confirm: true`. */
export function commandConfirmMessage(
  command: { confirm?: unknown } | null | undefined,
  label: string,
): string | null {
  const confirm = command?.confirm;
  if (typeof confirm === "string" && confirm.trim()) return confirm;
  if (confirm === true) return `Send ${label}?`;
  return null;
}
