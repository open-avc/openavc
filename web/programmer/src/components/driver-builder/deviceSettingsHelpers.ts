// Pure logic for the Device Settings editor + setup dialog, split out so it can
// be unit tested without React (see test_device_settings_helpers.py). Keep this
// file free of React/DOM imports.
import type { DriverDeviceSettingDef } from "../../api/types";

export interface RenameResult {
  ok: boolean;
  reason?: string;
}

type WriteDef = NonNullable<DriverDeviceSettingDef["write"]>;

/** Sanitize raw input into a legal setting key (lowercase alnum + underscore). */
export function sanitizeSettingKey(raw: string): string {
  return raw.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();
}

/** Validate renaming `current` to `cleaned` against the sibling `existing` keys. */
export function checkSettingRename(
  cleaned: string,
  current: string,
  existing: string[],
): RenameResult {
  if (!cleaned) return { ok: false, reason: "Key can't be empty." };
  if (cleaned === current) return { ok: true };
  if (existing.includes(cleaned)) {
    return { ok: false, reason: `"${cleaned}" already exists.` };
  }
  return { ok: true };
}

/** Smallest `setting_N` not already present. */
export function nextSettingKey(existing: string[]): string {
  let counter = existing.length + 1;
  let key = `setting_${counter}`;
  while (existing.includes(key)) {
    counter++;
    key = `setting_${counter}`;
  }
  return key;
}

// Which write keys belong to which transport. The runtime (set_device_setting)
// dispatches on address -> path/method -> send in that order, so a leftover
// `address` from a previous transport shadows an HTTP/TCP write and mis-routes.
const WRITE_KEYS_BY_TRANSPORT: Record<string, (keyof WriteDef)[]> = {
  osc: ["address", "args"],
  http: ["method", "path", "body", "headers"],
};
const TCP_WRITE_KEYS: (keyof WriteDef)[] = ["send"];

function writeKeysFor(transport: string | undefined): (keyof WriteDef)[] {
  return WRITE_KEYS_BY_TRANSPORT[transport ?? ""] ?? TCP_WRITE_KEYS;
}

/**
 * Strip a setting's write down to only the keys valid for `transport`, so
 * switching a driver's transport can't leave a stale OSC/HTTP field that the
 * runtime would dispatch on instead of the intended protocol.
 */
export function normalizeWriteForTransport(
  write: WriteDef | undefined,
  transport: string | undefined,
): WriteDef {
  const allowed = new Set<string>(writeKeysFor(transport) as string[]);
  const next: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(write ?? {})) {
    if (allowed.has(k)) next[k] = v;
  }
  return next as WriteDef;
}

/** True if `write` carries keys that don't belong to `transport` (stale shape). */
export function writeHasForeignKeys(
  write: WriteDef | undefined,
  transport: string | undefined,
): boolean {
  const allowed = new Set<string>(writeKeysFor(transport) as string[]);
  return Object.keys(write ?? {}).some((k) => !allowed.has(k));
}

/**
 * For OSC writes, true when neither the address nor any arg references {value},
 * i.e. the write would send an empty message that silently no-ops the value on
 * hardware. Drives the inline "this write sends no value" warning.
 */
export function oscWriteOmitsValue(write: WriteDef | undefined): boolean {
  if (!write) return true;
  const addrHasValue = (write.address ?? "").includes("{value}");
  const argHasValue = (write.args ?? []).some((a) =>
    (a?.value ?? "").includes("{value}"),
  );
  return !addrHasValue && !argHasValue;
}

/** OSC type tags that carry no value (true/false/nil) — their value input is N/A. */
export const OSC_VALUELESS_TAGS = new Set(["T", "F", "N"]);

export interface SettingValueCheck {
  ok: boolean;
  error?: string;
}

/**
 * Validate a raw string value entered for a setting against the def's type and
 * its min/max (numeric) / regex (string) constraints. Empty is allowed (the
 * field isn't required here); enum membership is enforced by the select.
 */
export function validateSettingValue(
  raw: string,
  def: Pick<DriverDeviceSettingDef, "type" | "min" | "max" | "regex"> | undefined,
): SettingValueCheck {
  if (!def) return { ok: true };
  const type = def.type ?? "string";

  if (raw === "") {
    // A numeric setting can't be pushed blank — it would silently coerce to 0
    // (e.g. a cleared port/channel/gain field). Require a value. String /
    // password / enum settings may legitimately be left blank.
    if (type === "integer" || type === "number" || type === "float") {
      return { ok: false, error: "Enter a value." };
    }
    return { ok: true };
  }

  if (type === "integer" || type === "number" || type === "float") {
    const n = type === "integer" ? parseInt(raw, 10) : parseFloat(raw);
    if (!Number.isFinite(n)) {
      return { ok: false, error: `Must be ${type === "integer" ? "a whole number" : "a number"}.` };
    }
    if (def.min !== undefined && n < def.min) {
      return { ok: false, error: `Must be at least ${def.min}.` };
    }
    if (def.max !== undefined && n > def.max) {
      return { ok: false, error: `Must be at most ${def.max}.` };
    }
    return { ok: true };
  }

  if (typeof def.regex === "string" && def.regex) {
    let re: RegExp;
    try {
      re = new RegExp(def.regex);
    } catch {
      return { ok: true }; // a malformed author regex shouldn't block the user
    }
    if (!re.test(raw)) {
      return { ok: false, error: "Doesn't match the required format." };
    }
  }
  return { ok: true };
}
