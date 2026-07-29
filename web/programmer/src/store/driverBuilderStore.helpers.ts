import yaml from "js-yaml";
import type { DriverDefinition } from "../api/types";
import { validateDriverSafely } from "../components/driver-builder/validateDriver";
import { validateDriverDefinition } from "../api/driverClient";

/** A driver definition is a mapping. Excludes null, arrays, and scalars —
 *  mirrors the runtime loader's isinstance(dict) gate. */
function isMapping(value: unknown): boolean {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Parse a driver definition from an imported/pasted file — accepts both JSON
 * and YAML (community drivers are YAML; our own exports are usually JSON).
 *
 * A definition is a mapping. JSON.parse and yaml.load both happily return
 * arrays and scalars for a well-formed but wrong-shaped file (`[...]`, `42`, a
 * bare string), and the old caller cast either straight to DriverDefinition —
 * so a YAML list imported here reached the API and failed with a misleading
 * "missing id" 422. Gate on a mapping up front, mirroring the runtime loader's
 * isinstance(dict) check (server/drivers/driver_loader.py), so the thrown error
 * names the real failure (not-a-mapping) instead of the cast laundering a list
 * or scalar through the type system.
 */
export function parseDriverDefinition(text: string): DriverDefinition {
  // Try JSON first (faster, more common from our own exports), then YAML.
  // Both failure modes throw a SyntaxError with a message the caller can show
  // verbatim: unparseable vs. parseable-but-wrong-shape are distinct problems.
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    try {
      parsed = yaml.load(text);
    } catch {
      throw new SyntaxError("File is not valid JSON or YAML");
    }
  }
  if (!isMapping(parsed)) {
    throw new SyntaxError(
      "Driver file must contain a set of fields (a mapping), not a list or single value",
    );
  }
  return parsed as DriverDefinition;
}

/**
 * Collections `DriverDefinition` declares as always-present and the editors
 * therefore index without a guard (`draft.default_config[key]`,
 * `Object.keys(draft.commands)`).
 *
 * The type is a promise about an editor DRAFT, not about a driver on disk:
 * only id/name/transport are required by the contract, so a hand-authored
 * .avcdriver, an imported file, or a driver created through the API can
 * legitimately omit any of these. Whatever the type says, the value can be
 * undefined at runtime, and TypeScript cannot catch the difference.
 */
const DRAFT_COLLECTIONS = [
  "default_config",
  "config_schema",
  "state_variables",
  "commands",
  "responses",
  "polling",
] as const;

/**
 * Clone a definition into an editor draft, filling in the collections the
 * editors index without guards.
 *
 * Cloning verbatim crashes a tab outright: a driver with no state_variables
 * took out State Variables / Behavior / Simulation on Object.keys(undefined),
 * and one with no default_config took out the whole Connection tab on
 * TransportPicker's `draft.default_config[key]` ("Cannot read properties of
 * undefined (reading 'port')"). The first was fixed key-by-key; this covers
 * the class, because every one of these is reachable the same way.
 *
 * Appends a key only when absent, so the YAML key order of well-formed
 * drivers is untouched on re-export.
 */
export function cloneDraft(definition: DriverDefinition): DriverDefinition {
  const draft = structuredClone(definition);
  const slots = draft as unknown as Record<string, unknown>;
  for (const key of DRAFT_COLLECTIONS) {
    // responses is the one list; the rest are keyed maps.
    if (slots[key] == null) slots[key] = key === "responses" ? [] : {};
  }
  return draft;
}

/**
 * State patch to apply after a successful driver save.
 *
 * The editor inputs (the ID field included) stay editable while the save
 * network round-trip is in flight, so the user can keep typing during the
 * await. This reconciles three outcomes without clobbering their work:
 *
 *  - draft untouched during the await   -> mark clean, select the saved id.
 *  - draft edited in place during await  -> keep it dirty (don't silently
 *    discard the edits) but still point selection at the id we actually
 *    persisted, so the next save targets the right record instead of a stale
 *    one.
 *  - user navigated to a different driver mid-save -> leave their selection
 *    alone; only clear the saving flag.
 */
export type SavePatch =
  | { saving: false }
  | { saving: false; dirty: boolean; selectedId: string };

export function reconcileAfterSave(args: {
  savedId: string;
  draftUnchanged: boolean;
  selectionUnchanged: boolean;
}): SavePatch {
  if (args.draftUnchanged) {
    return { saving: false, dirty: false, selectedId: args.savedId };
  }
  if (args.selectionUnchanged) {
    return { saving: false, dirty: true, selectedId: args.savedId };
  }
  return { saving: false };
}

/**
 * Latest-wins guard for overlapping async list refreshes. Each refresh takes a
 * token via next(); when it resolves it applies its result only if it is still
 * the latest started refresh (isCurrent). This makes the newest-started request
 * win regardless of which network GET happens to resolve last, so two
 * overlapping install/uninstall refreshes can't settle on a stale snapshot.
 */
export interface LatestWins {
  next: () => number;
  isCurrent: (token: number) => boolean;
}

export function makeLatestWins(): LatestWins {
  let latest = 0;
  return {
    next: () => {
      latest += 1;
      return latest;
    },
    isCurrent: (token: number) => token === latest,
  };
}

/**
 * Blocking problems that should stop an imported/pasted driver from being
 * created server-side. Returns clean, user-facing messages (empty array = safe
 * to create) so the import path surfaces readable issues instead of a terse
 * backend 422.
 *
 * The contract rules come from the server — the same ones the create route
 * about to run will apply — plus the editor's own duplicate-id check, which
 * the server cannot make (it validates one definition at a time and has no
 * view of the library). Transport isn't covered by either (the editor always
 * defaults one) so it's checked explicitly here.
 *
 * Async because the rules live on the platform. If the request itself fails,
 * the import is NOT blocked: a network error says nothing about the driver,
 * and the create POST that follows applies the same rules anyway.
 */
export async function importBlockers(
  definition: DriverDefinition,
  siblings: DriverDefinition[],
): Promise<string[]> {
  const messages: string[] = [];
  if (!definition.transport) {
    messages.push("Transport is required (tcp, serial, udp, http, or osc).");
  }
  for (const issue of validateDriverSafely(definition, siblings, null)) {
    if (issue.severity === "error") messages.push(issue.message);
  }
  try {
    const { issues } = await validateDriverDefinition(definition);
    for (const issue of issues) {
      if (issue.severity === "error") messages.push(issue.message);
    }
  } catch {
    // Unreachable server: let the create call be the gate.
  }
  return messages;
}
