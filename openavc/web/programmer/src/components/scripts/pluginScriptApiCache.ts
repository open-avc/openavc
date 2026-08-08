/**
 * Module-level cache of plugin SCRIPT_API methods, refreshed on demand.
 *
 * The script editor's Monaco completion provider runs in a synchronous
 * callback context, so we cache the fetched method list and let the provider
 * read it without awaiting. Callers can call refresh() to re-fetch (e.g.
 * after installing or enabling a plugin) and get notified via the optional
 * subscription callback.
 */

import type { PluginScriptMethod } from "../../api/pluginClient";
import { getPluginScriptApi } from "../../api/pluginClient";

let _cache: PluginScriptMethod[] | null = null;
let _inflight: Promise<PluginScriptMethod[]> | null = null;

async function _fetch(): Promise<PluginScriptMethod[]> {
  if (_inflight) return _inflight;
  _inflight = getPluginScriptApi()
    .then((res) => {
      _cache = res.methods ?? [];
      return _cache;
    })
    .catch(() => {
      _cache = [];
      return _cache!;
    })
    .finally(() => {
      _inflight = null;
    });
  return _inflight;
}

/** Kick off a fetch if we don't have a cached value yet. Non-blocking. */
export function ensurePluginScriptApiLoaded(): void {
  if (_cache === null && _inflight === null) {
    void _fetch();
  }
}

/** Force a refresh and return the new value. */
export async function refreshPluginScriptApi(): Promise<PluginScriptMethod[]> {
  _cache = null;
  return _fetch();
}

/** Synchronous read of the cached methods. Returns [] if not yet loaded. */
export function getCachedPluginScriptApi(): PluginScriptMethod[] {
  return _cache ?? [];
}

/** Group the flat list by plugin id. */
export function groupByPlugin(
  methods: PluginScriptMethod[],
): Map<string, { plugin_name: string; methods: PluginScriptMethod[] }> {
  const grouped = new Map<string, { plugin_name: string; methods: PluginScriptMethod[] }>();
  for (const m of methods) {
    const entry = grouped.get(m.plugin_id);
    if (entry) {
      entry.methods.push(m);
    } else {
      grouped.set(m.plugin_id, { plugin_name: m.plugin_name, methods: [m] });
    }
  }
  return grouped;
}
