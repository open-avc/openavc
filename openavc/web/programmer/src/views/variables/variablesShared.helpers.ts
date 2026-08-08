// Pure cross-reference helpers for the Variables / Device States sub-tabs.
// Extracted from variablesShared.tsx so the binding-scan + glob logic can be
// unit-tested without React/lucide. See tests/test_variables_shared_helpers.py.

// Characters that make a pattern a glob, mirroring the runtime state store's
// _GLOB_CHARS (server/core/state_store.py). A pattern without any of these is an
// exact-match key.
const GLOB_CHARS = /[*?[]/;

export function hasGlobChars(s: string): boolean {
  return GLOB_CHARS.test(s);
}

/**
 * Glob matcher for state-key patterns, mirroring the runtime's Python `fnmatch`
 * (server/core/state_store.py, server/core/event_bus.py) so the IDE "Used By"
 * cross-reference reports exactly what the runtime subscribes to: "*" matches any
 * run of characters INCLUDING dots, "?" matches one character, and "[...]" is a
 * character class. Every other character is matched literally, so a script-derived
 * key carrying regex metacharacters can never crash the view (SyntaxError) or hang
 * it (ReDoS).
 */
export function globMatch(pattern: string, key: string): boolean {
  if (pattern === key) return true;
  if (!hasGlobChars(pattern)) return false;
  return fnmatchToRegExp(pattern).test(key);
}

/** Translate a glob pattern to an anchored RegExp the way Python's
 *  fnmatch.translate does (see globMatch). Every pattern yields a valid regex —
 *  an unbalanced "[" is treated as a literal — so this can never throw. */
function fnmatchToRegExp(pattern: string): RegExp {
  let res = "";
  let i = 0;
  const n = pattern.length;
  while (i < n) {
    const c = pattern[i++];
    if (c === "*") {
      while (i < n && pattern[i] === "*") i++; // collapse runs of "*"
      res += ".*";
    } else if (c === "?") {
      res += ".";
    } else if (c === "[") {
      let j = i;
      if (j < n && (pattern[j] === "!" || pattern[j] === "^")) j++;
      if (j < n && pattern[j] === "]") j++;
      while (j < n && pattern[j] !== "]") j++;
      if (j >= n) {
        res += "\\["; // no closing "]" — treat "[" as a literal
      } else {
        let stuff = pattern.slice(i, j).replace(/\\/g, "\\\\");
        let cls = "";
        if (stuff[0] === "!") { cls = "^"; stuff = stuff.slice(1); }
        else if (stuff[0] === "^") { cls = "\\^"; stuff = stuff.slice(1); }
        res += "[" + cls + stuff + "]";
        i = j + 1;
      }
    } else {
      res += c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp("^" + res + "$", "s");
}

/**
 * Normalize an event-binding slot (press/release/change) to a list of action
 * objects. The UI authors these as arrays of actions (PressBindingEditor), but
 * legacy projects and the panel runtime also accept a single action object, so
 * we accept both shapes.
 */
function asActionList(binding: unknown): Record<string, any>[] {
  if (Array.isArray(binding)) return binding as Record<string, any>[];
  if (binding && typeof binding === "object") return [binding as Record<string, any>];
  return [];
}

// Every do.<interaction> that holds an action list (matches the runtime).
const DO_INTERACTIONS = [
  "press", "release", "hold", "change", "submit", "select",
  "route", "audio_route", "mute_route", "audio_mute_route",
];

/** Scan an element's bindings for var.* references. */
export function scanBindingForVars(
  bindings: Record<string, unknown>,
  onFound: (varId: string, detail: string) => void,
) {
  if (!bindings) return;
  const show = (bindings.show || {}) as Record<string, any>;
  const doMap = (bindings.do || {}) as Record<string, any>;

  const checkKey = (obj: any, context: string) => {
    if (!obj || typeof obj !== "object") return;
    const key = obj.key as string | undefined;
    if (key?.startsWith("var.")) {
      onFound(key.slice(4), context);
    }
  };

  checkKey(show.value, "Value source");
  checkKey(show.look, "Appearance binding");

  for (const eventType of DO_INTERACTIONS) {
    for (const action of asActionList(doMap[eventType])) {
      if (action.action === "state.set" && typeof action.key === "string" && action.key.startsWith("var.")) {
        onFound(action.key.slice(4), `${eventType} → Set Variable`);
      }
      if (action.action === "value_map" && action.map) {
        const actionMap = action.map as Record<string, any>;
        for (const [optVal, subAction] of Object.entries(actionMap)) {
          if (subAction?.action === "state.set" && typeof subAction.key === "string" && subAction.key.startsWith("var.")) {
            onFound(subAction.key.slice(4), `${eventType} → ${optVal} → Set Variable`);
          }
        }
      }
    }
  }
}

/** Scan an element's bindings for ALL state key references (not just var.*). */
export function scanBindingForAllKeys(
  bindings: Record<string, unknown>,
  onFound: (key: string, detail: string) => void,
) {
  if (!bindings) return;
  const show = (bindings.show || {}) as Record<string, any>;
  const doMap = (bindings.do || {}) as Record<string, any>;

  const checkKey = (obj: any, context: string) => {
    if (!obj || typeof obj !== "object") return;
    const key = obj.key as string | undefined;
    if (key) onFound(key, context);
  };

  checkKey(show.value, "Value source");
  checkKey(show.look, "Appearance binding");

  for (const eventType of DO_INTERACTIONS) {
    for (const action of asActionList(doMap[eventType])) {
      if (action.action === "state.set" && typeof action.key === "string") {
        onFound(action.key, `${eventType} → Set state`);
      }
      if (action.action === "value_map" && action.map) {
        const actionMap = action.map as Record<string, any>;
        for (const [optVal, subAction] of Object.entries(actionMap)) {
          if (subAction?.action === "state.set" && typeof subAction.key === "string") {
            onFound(subAction.key, `${eventType} → ${optVal} → Set state`);
          }
        }
      }
    }
  }
}

/**
 * Return every candidate key matched by a wildcard pattern (e.g. "device.*").
 * Used so a wildcard script reference can annotate device state keys that only
 * it touches — those keys aren't seeded into the usage map by macros/UI, so they
 * must be matched against the known device-key set, not just the seeded keys.
 */
export function collectWildcardMatches(pattern: string, candidateKeys: Iterable<string>): string[] {
  const out: string[] = [];
  for (const key of candidateKeys) {
    if (globMatch(pattern, key)) out.push(key);
  }
  return out;
}

// Built-in macro action types whose step `params` the runtime does NOT resolve
// `$var` references in. Every OTHER action — `device.command`, `group.command`,
// and any plugin-registered action — has its `params` passed through the macro
// engine's `_resolve_params` (server/core/macro_engine.py: device.command,
// group.command, and the plugin-action `else` branch), so a `$var.<name>` inside
// those params is live at runtime.
const NON_PARAM_MACRO_ACTIONS = new Set([
  "delay",
  "state.set",
  "event.emit",
  "macro",
  "conditional",
  "wait_until",
  "ui.navigate",
]);

/**
 * True when a macro step's `params` object carries runtime-resolved `$var`
 * references — i.e. device/group commands and plugin-action steps. The variable
 * rename rewrite and the "Used By" scan both gate on this, so a variable
 * referenced only from a plugin-action param is rewritten/counted instead of
 * being silently left dangling (rename) or under-counted (usage panel).
 */
export function stepParamsResolveVars(action: string | undefined | null): boolean {
  return !!action && !NON_PARAM_MACRO_ACTIONS.has(action);
}
