// Whether a setting holds a secret: the one rule every form asks before it
// shows, pre-fills, trims or number-reads a value.
//
// A secret is the connection `password`, whatever its definition says, or any
// field whose definition says `secret: true` (a config field, a command or
// action parameter, a device setting) or `type: password`, an older spelling
// of `secret: true` that drivers still carry. `secret` counts only when it is
// a real `true`. `key` is the field's name, or null where only the definition
// is at hand.
//
// Mirrors is_secret_setting in openavc/drivers/spec.py, which the server's
// readers ask (the logs, reports, the login file, the space trimming, the
// driver validator); tests/test_secret_setting_parity.py runs one corpus
// through both.

export const PASSWORD_FIELD = "password";

export function isSecretSetting(key: string | null | undefined, def: unknown): boolean {
  if (key === PASSWORD_FIELD) return true;
  if (def === null || typeof def !== "object" || Array.isArray(def)) return false;
  const d = def as { secret?: unknown; type?: unknown };
  return d.secret === true || d.type === "password";
}
