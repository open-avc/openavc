/**
 * Whether a plugin is incompatible with the current platform.
 *
 * The backend always computes the truthful `compatible` flag for every
 * discovered plugin (server/core/plugin_loader.py `is_platform_compatible`),
 * but it only sets `status === "incompatible"` for plugins listed in the
 * project that the loader actually tried to start. A discovered-but-unstarted
 * incompatible plugin therefore carries `compatible: false` with some other
 * status, so the badge / banner / Enable gating must read `compatible` —
 * falling back to the status string only when the flag is somehow absent.
 *
 * Callers still check `status === "missing"` first, so a not-installed plugin
 * keeps its own "missing" treatment even when this also returns true.
 */
export function isPluginIncompatible(plugin: {
  compatible?: boolean;
  status?: string;
}): boolean {
  if (typeof plugin.compatible === "boolean") return !plugin.compatible;
  return plugin.status === "incompatible";
}
