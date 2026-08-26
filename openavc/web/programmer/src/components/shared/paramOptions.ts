import type { ChildEntityEntry, ChildEntityStateVarDef } from "../../api/types";
import { CHILD_RESERVED_PROPS } from "../../api/types";

/** One dropdown option: the value sent to the runtime + a human label. */
export interface ParamOption {
  value: string;
  label: string;
}

/**
 * A row in a state-published option list that says more than "here is a
 * choice". Every field past `label` is optional, and every consumer written
 * before them ignores them, so a publisher can add them freely.
 *
 * The important one is `value` being OPTIONAL. A row with no value cannot be
 * chosen: it is on screen to say why something that ought to be here is not.
 * `normalizeOptionList` drops those rows entirely, which is what makes the
 * whole convention safe to publish at a picker that predates it.
 */
export interface OptionRow {
  /** Absent = this row cannot be picked. */
  value?: string;
  /** Stable identity for a row with no value (a value-bearing row has one). */
  id?: string;
  label: string;
  /** Heading to list this row under, e.g. the device it belongs to. */
  group?: string;
  /** "" or absent = nothing to say. Anything else is marked against the row. */
  status?: string;
  /** One sentence about that status, written by whoever published the row. */
  detail?: string;
  /** A device config field that would make this row usable. */
  setup?: { device: string; field: string };
}

/** Find a child by a sibling param's chosen value — matches the numeric/string
 *  local_id or its zero-padded form (a `child_id` param stores `String(local_id)`).
 *  Used by both the option cascade and the value-type cascade. */
export function findChildByValue(
  children: ChildEntityEntry[] | undefined,
  value: unknown,
): ChildEntityEntry | undefined {
  const v = value == null ? "" : String(value);
  if (!v || !children) return undefined;
  return children.find(
    (c) => String(c.local_id) === v || c.local_id_padded === v,
  );
}

/**
 * Parse a state-published option list into `{value, label}` rows.
 *
 * A driver (or plugin) opts a param into a dropdown by publishing the
 * enumerable set as a single state value. Since state values are flat
 * primitives (no arrays), the list is a JSON-encoded string. Two shapes are
 * accepted so the simplest driver case stays trivial:
 *   - `["Scene A", "Scene B"]`            -> value === label
 *   - `[{"value": "a", "label": "Bank A"}]` -> explicit label (plugin style)
 *   - `[{"value": "a"}]`                   -> label falls back to the value
 * Anything else (missing key, malformed JSON, not a string) yields `[]` so a
 * not-yet-published source renders an empty list rather than throwing.
 *
 * This is the one shared contract behind every state-sourced param dropdown
 * (`options_state` on device command/action params, and the plugin-side
 * `options_source` on plugin macro-action and panel-element config selects).
 */
/**
 * Normalize an already-parsed option list into `{value, label}` rows. Each
 * entry may be a plain scalar (value === label) or a `{value, label}` object
 * (label falls back to value). Shared by the static enum `values` path (a
 * driver's declared option list) and the state-published-list path.
 */
export function normalizeOptionList(parsed: unknown[]): ParamOption[] {
  const out: ParamOption[] = [];
  for (const item of parsed) {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
      out.push({ value: String(item), label: String(item) });
    } else if (item && typeof item === "object" && "value" in item) {
      const v = (item as { value: unknown }).value;
      const l = (item as { label?: unknown }).label;
      if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
        out.push({ value: String(v), label: typeof l === "string" ? l : String(v) });
      }
    }
  }
  return out;
}

/**
 * The display label for a wire value against an enum option list (entries may
 * be plain strings or `{value, label}`). Returns the value itself when the list
 * is absent or has no matching labeled entry — so a stored code still renders
 * legibly even if the option set changed. Shared by every read-side surface
 * that shows a persisted enum value (device settings, current-value chips).
 */
export function optionLabel(
  values: readonly unknown[] | undefined,
  wireValue: string,
): string {
  if (!values) return wireValue;
  const match = normalizeOptionList(values as unknown[]).find(
    (o) => o.value === wireValue,
  );
  return match ? match.label : wireValue;
}

export function parseStateOptionList(raw: unknown): ParamOption[] {
  if (typeof raw !== "string" || !raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return normalizeOptionList(parsed);
}

/**
 * The same list, read whole: rows that cannot be picked included.
 *
 * `parseStateOptionList` answers "what can I choose", which is all a plain
 * dropdown needs. This answers "what is there", which is what a picker needs
 * to say that a source exists and is one setting away from working instead of
 * showing an empty list. A row with neither a `value` nor an `id` is skipped —
 * there would be nothing to key it by.
 */
export function parseStateOptionRows(raw: unknown): OptionRow[] {
  if (typeof raw !== "string" || !raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const out: OptionRow[] = [];
  for (const item of parsed) {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
      out.push({ value: String(item), label: String(item) });
      continue;
    }
    if (!item || typeof item !== "object") continue;
    const raw = item as Record<string, unknown>;
    const value = raw.value;
    const hasValue =
      typeof value === "string" || typeof value === "number" || typeof value === "boolean";
    const id = typeof raw.id === "string" ? raw.id : undefined;
    if (!hasValue && !id) continue;
    const setup = raw.setup as { device?: unknown; field?: unknown } | undefined;
    out.push({
      ...(hasValue ? { value: String(value) } : {}),
      ...(id ? { id } : {}),
      label:
        typeof raw.label === "string" && raw.label
          ? raw.label
          : hasValue
            ? String(value)
            : (id as string),
      ...(typeof raw.group === "string" && raw.group ? { group: raw.group } : {}),
      ...(typeof raw.status === "string" && raw.status ? { status: raw.status } : {}),
      ...(typeof raw.detail === "string" && raw.detail ? { detail: raw.detail } : {}),
      ...(setup && typeof setup.device === "string" && typeof setup.field === "string"
        ? { setup: { device: setup.device, field: setup.field } }
        : {}),
    });
  }
  return out;
}

// Platform-managed child state vars — never offered as selectable controls in
// a `child_schema` cascade (they're injected into every dynamic child).
// Generated from the driver contract rather than listed here: a new reserved
// key added to the platform would otherwise start offering itself as a
// control the moment it shipped.
const PLATFORM_CHILD_KEYS = CHILD_RESERVED_PROPS;

/**
 * Build the option list for a param that cascades off a sibling child's
 * schema (`options_from: { param, source: "child_schema" }`).
 *
 * Given the chosen child's per-instance schema (`ChildEntityEntry.schema`,
 * already on the client from `GET /api/devices/{id}/children`), offer its
 * controls as options. A driver can mark which state vars are settable
 * controls with `control: true`; when any entry does, only those are offered
 * (keeps a Q-SYS component's real controls separate from its metadata /
 * display-mirror vars). When nothing is flagged, every key except the
 * platform-managed `online` / `label` is offered, so a driver that hasn't
 * opted in still gets a usable list.
 *
 * The option `value` is the schema key (the control name the driver's command
 * expects); the `label` is the var-def's `label` when present.
 */
export function childSchemaOptions(
  schema: Record<string, ChildEntityStateVarDef> | undefined,
): ParamOption[] {
  if (!schema) return [];
  const entries = Object.entries(schema);
  const flagged = entries.filter(([, def]) => def && def.control === true);
  const chosen = flagged.length > 0
    ? flagged
    : entries.filter(([key]) => !PLATFORM_CHILD_KEYS.has(key));
  return chosen.map(([key, def]) => ({
    value: key,
    label: def?.label || key,
  }));
}
