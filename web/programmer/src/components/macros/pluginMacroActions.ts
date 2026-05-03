import { useEffect, useState, useCallback } from "react";
import type {
  PluginMacroAction,
  PluginMacroActionParam,
} from "../../api/pluginClient";
import { getPluginMacroActions } from "../../api/pluginClient";
import type { MacroStep } from "../../api/types";

// Module-level cache so multiple components share one fetch.
let _cache: PluginMacroAction[] | null = null;
let _inflight: Promise<PluginMacroAction[]> | null = null;
const _listeners = new Set<(actions: PluginMacroAction[]) => void>();

async function _fetch(): Promise<PluginMacroAction[]> {
  if (_inflight) return _inflight;
  _inflight = getPluginMacroActions()
    .then((res) => {
      _cache = res.actions ?? [];
      _listeners.forEach((fn) => fn(_cache!));
      return _cache;
    })
    .finally(() => {
      _inflight = null;
    });
  return _inflight;
}

/**
 * Hook returning the list of plugin macro actions plus a refresh function.
 * Sound libraries don't change while a user is editing a macro, so the
 * fetch happens once on mount and stays cached. Use `refresh()` after
 * actions like installing a plugin or uploading new audio assets.
 */
export function usePluginMacroActions(): {
  actions: PluginMacroAction[];
  loading: boolean;
  refresh: () => Promise<void>;
} {
  const [actions, setActions] = useState<PluginMacroAction[]>(_cache ?? []);
  const [loading, setLoading] = useState<boolean>(_cache === null);

  useEffect(() => {
    let cancelled = false;
    _listeners.add(setActions);
    if (_cache === null) {
      setLoading(true);
      _fetch()
        .catch(() => {
          if (!cancelled) setActions([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    return () => {
      cancelled = true;
      _listeners.delete(setActions);
    };
  }, []);

  const refresh = useCallback(async () => {
    _cache = null;
    setLoading(true);
    try {
      await _fetch();
    } finally {
      setLoading(false);
    }
  }, []);

  return { actions, loading, refresh };
}

/** Look up a plugin action by its action_type string. */
export function findPluginAction(
  actions: PluginMacroAction[],
  actionType: string,
): PluginMacroAction | undefined {
  return actions.find((a) => a.action_type === actionType);
}

/** Build the default params object for a plugin action from its schema. */
export function defaultPluginActionParams(action: PluginMacroAction): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of action.params) {
    if (p.default !== undefined) {
      out[p.key] = p.default;
    }
  }
  return out;
}

/** Build a new MacroStep for a plugin action with default params. */
export function newPluginActionStep(action: PluginMacroAction): MacroStep {
  return {
    action: action.action_type,
    params: defaultPluginActionParams(action),
  };
}

/** Build a short summary string for a plugin action step (used in step list). */
export function pluginActionSummary(
  step: MacroStep,
  action: PluginMacroAction | undefined,
): string {
  if (!action) {
    // Plugin not installed — show the raw action type so the user can
    // tell what's missing.
    return `(missing) ${step.action}`;
  }
  const params = step.params ?? {};
  // Show the first 1-2 params as a compact summary
  const parts: string[] = [];
  for (const p of action.params.slice(0, 2)) {
    const val = params[p.key];
    if (val !== undefined && val !== "") {
      parts.push(typeof val === "string" ? val : JSON.stringify(val));
    }
  }
  return parts.length > 0 ? parts.join(" · ") : action.label;
}

/** Re-export types for convenience */
export type { PluginMacroAction, PluginMacroActionParam };

/**
 * Create a new MacroStep for any action type — built-in or plugin.
 * Returns null if the action type is unknown.
 */
export function createStepForAction(
  actionType: string,
  pluginActions: PluginMacroAction[],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  builtinDefaults: (action: string) => any | undefined,
): MacroStep | null {
  const builtin = builtinDefaults(actionType);
  if (builtin) {
    return { action: actionType, ...builtin };
  }
  const plugin = findPluginAction(pluginActions, actionType);
  if (plugin) {
    return newPluginActionStep(plugin);
  }
  return null;
}
