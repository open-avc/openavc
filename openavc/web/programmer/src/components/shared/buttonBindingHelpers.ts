// Pure helpers behind the shared button binding editor
// (ButtonBindingEditor.tsx — web UI Builder and Surface Configurator).
//
// A button's `press` binding is an array of action objects. The runtimes
// (panel/engine and control surfaces) fire EVERY entry in order in tap
// mode, and the mode/toggle/hold config always rides on press[0] next to
// its action fields. These helpers keep that shape intact through edits:
// splitting press[0] into config vs. action, and rebuilding the array when
// the primary action is set or removed — removing the primary promotes the
// next action instead of discarding the rest of the list.

/** The config keys that live on press[0] alongside the action fields. */
const PRESS_CONFIG_KEYS = [
  "mode",
  "off_action",
  "hold_action",
  "hold_repeat_ms",
  "hold_threshold_ms",
  "toggle_key",
  "toggle_value",
  "on_label",
  "off_label",
] as const;

/**
 * The mode/toggle/hold config carried by press[0], without its action
 * fields. Empty-ish values are dropped (toggle_value keeps false — "on
 * when false" is a valid toggle).
 */
export function pressConfigFields(
  press: Record<string, unknown>,
): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  for (const key of PRESS_CONFIG_KEYS) {
    if (key === "toggle_value") {
      if (press.toggle_value !== undefined) config.toggle_value = press.toggle_value;
    } else if (press[key]) {
      config[key] = press[key];
    }
  }
  return config;
}

/**
 * The action fields of press[0], without its mode/toggle/hold config.
 * Null when no action is configured (a config-only entry is valid: a
 * toggle can carry just its Off Action, which fires via toggle_off).
 */
export function pressActionFields(
  press: Record<string, unknown>,
): Record<string, unknown> | null {
  const action: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(press)) {
    if (!(PRESS_CONFIG_KEYS as readonly string[]).includes(key)) {
      action[key] = value;
    }
  }
  return Object.keys(action).length > 0 ? action : null;
}

/**
 * Rebuilds the press array after the primary action is edited (`value`)
 * or removed (`value === null`), preserving the press[0] config and the
 * additional actions. On remove, the first additional action is promoted
 * to primary — the runtimes fire the whole list, so dropping the extras
 * (or leaving them behind an action-less primary in a non-tap mode, where
 * the editor can't show them) silently changes what the button does.
 * Returns null only when nothing remains to keep.
 */
export function pressAfterActionEdit(
  press: Record<string, unknown>,
  extraActions: readonly Record<string, unknown>[],
  value: Record<string, unknown> | null,
): Record<string, unknown>[] | null {
  const config = pressConfigFields(press);
  if (value) {
    return [{ ...config, ...value }, ...extraActions];
  }
  if (extraActions.length > 0) {
    const [promoted, ...rest] = extraActions;
    return [{ ...config, ...promoted }, ...rest];
  }
  return Object.keys(config).length > 0 ? [config] : null;
}
