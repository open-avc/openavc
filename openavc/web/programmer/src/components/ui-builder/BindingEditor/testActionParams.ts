// Pure helpers behind the binding editor's "Test this action now" button.
//
// Command params can hold $-references. At runtime the engine resolves
// them per param (server/core/value_resolver.py): the UI-event tokens
// ($value, $input, $output, $mute) come from the firing interaction, and
// any other $ref reads the state store. A Test click has no firing
// interaction, so sending params raw would put the literal "$value" string
// on the wire — for a text-protocol driver that's a malformed command
// hitting real AV hardware. Instead: resolve state refs from the IDE's
// live state mirror, and refuse to send when a param needs an event token
// (no value exists to send) or names a state key with no current value
// (the runtime would send None) — with a message saying which param and
// why.

/** The interaction-scoped tokens resolve_ref takes from the UI event. */
const EVENT_TOKENS = new Set(["$value", "$input", "$output", "$mute"]);

export type TestParamsResult =
  | { ok: true; params: Record<string, unknown> }
  | { ok: false; param: string; token: string; reason: "event" | "no_value" };

export function resolveTestParams(
  params: Record<string, unknown>,
  liveState: Record<string, unknown>,
): TestParamsResult {
  const resolved: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(params)) {
    if (typeof value !== "string" || !value.startsWith("$")) {
      resolved[name] = value;
      continue;
    }
    if (EVENT_TOKENS.has(value)) {
      return { ok: false, param: name, token: value, reason: "event" };
    }
    const key = value.slice(1);
    if (!(key in liveState) || liveState[key] === undefined) {
      return { ok: false, param: name, token: value, reason: "no_value" };
    }
    resolved[name] = liveState[key];
  }
  return { ok: true, params: resolved };
}

/** Human message for a refused test, naming the param and the fix. */
export function testBlockedMessage(
  blocked: Extract<TestParamsResult, { ok: false }>,
): string {
  if (blocked.reason === "event") {
    return (
      `Can't test: "${blocked.param}" uses ${blocked.token}, which only has a ` +
      `value when the panel control fires. Enter a fixed value to test.`
    );
  }
  return (
    `Can't test: "${blocked.param}" references ${blocked.token}, ` +
    `which has no current value.`
  );
}
