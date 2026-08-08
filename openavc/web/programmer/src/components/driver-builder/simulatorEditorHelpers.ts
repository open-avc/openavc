// Pure logic for the Simulation tab editor, split out so it can be unit
// tested without React (see openavc/tests/test_simulator_editor_helpers.py).
// The editor component imports these; keep this file free of React/DOM
// imports. Mirrors configSchemaHelpers.ts / responseBuilderHelpers.ts.

export interface SimErrorMode {
  behavior?: string;
  description?: string;
  set_state?: Record<string, unknown>;
}

/** The wire-level behaviors the simulator transports actually read
 *  (has_error_behavior in the TCP/UDP/HTTP/OSC/MQTT servers). "" is the
 *  state-change-only form: the mode carries no behavior key and only
 *  applies its set_state values when injected. */
export const SIM_ERROR_BEHAVIORS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "State Change Only" },
  { value: "no_response", label: "No Response" },
  { value: "corrupt_response", label: "Corrupt Response" },
];

/**
 * Parse the Response Delay input into the stored delays map. 0 is a valid
 * authored value (an instantaneous response) — the old `|| 0.05` fallback
 * silently snapped it back. Empty/unparseable/negative input means "unset":
 * the key is removed and the simulator uses its runtime default (no delay).
 * Returns undefined when no delays remain so the YAML stays clean.
 */
export function setCommandResponseDelay(
  delays: Record<string, number> | undefined,
  raw: string,
): Record<string, number> | undefined {
  const next = { ...delays };
  const n = parseFloat(raw);
  if (Number.isFinite(n) && n >= 0) {
    next.command_response = n;
  } else {
    delete next.command_response;
  }
  return Object.keys(next).length > 0 ? next : undefined;
}

/**
 * Set or clear an error mode's behavior. "" (State Change Only) removes the
 * key entirely — the runtime treats a behavior-less mode as set_state-only,
 * and writing behavior: "" would export a meaningless field.
 */
export function applyErrorModeBehavior(
  mode: SimErrorMode,
  behavior: string,
): SimErrorMode {
  const next = { ...mode };
  if (behavior === "") delete next.behavior;
  else next.behavior = behavior;
  return next;
}

/** Coerce a set_state value input by the state variable's declared type,
 *  mirroring the Initial State editor's parsing. */
export function coerceSimStateValue(varType: string | undefined, raw: string): unknown {
  if (varType === "boolean") return raw.toLowerCase() === "true";
  if (varType === "integer") return parseInt(raw) || 0;
  if (varType === "number" || varType === "float") return parseFloat(raw) || 0;
  return raw;
}

/** Add a set_state entry for the first state variable the mode doesn't
 *  already change. Returns the mode unchanged when every variable is used
 *  (or none exist). */
export function addErrorModeStateEntry(
  mode: SimErrorMode,
  stateVarNames: string[],
): SimErrorMode {
  const used = mode.set_state ?? {};
  const unused = stateVarNames.find((v) => !(v in used));
  if (unused === undefined) return mode;
  return { ...mode, set_state: { ...used, [unused]: "" } };
}

/** Point a set_state entry at a different state variable, keeping its value
 *  and entry order. The editor's dropdown excludes variables the mode
 *  already changes, so a rename can't merge two entries. */
export function renameErrorModeStateVar(
  mode: SimErrorMode,
  oldVar: string,
  newVar: string,
): SimErrorMode {
  const next: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(mode.set_state ?? {})) {
    next[k === oldVar ? newVar : k] = v;
  }
  return { ...mode, set_state: next };
}

/** Remove a set_state entry; drops the set_state key when the last entry
 *  goes so the YAML stays clean. */
export function removeErrorModeStateEntry(
  mode: SimErrorMode,
  varName: string,
): SimErrorMode {
  const next = { ...(mode.set_state ?? {}) };
  delete next[varName];
  const out = { ...mode };
  if (Object.keys(next).length > 0) out.set_state = next;
  else delete out.set_state;
  return out;
}
