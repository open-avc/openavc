/**
 * What a control's binding actions name and need, as the IDE checks them.
 *
 * The slots an element's `do` map holds actions in; what a value_map runs, as
 * the binding card reads it; the device, macro or page an action names and
 * whether it is there; the fields an action cannot run without; and the device
 * commands the platform is asked about, on controls and in macro steps. The
 * binding card's badges, the Builder's Validate, page delete and the command
 * check all read these, so a new action, or a new spelling of one, is a row
 * here.
 *
 * Nothing here knows about geometry, the page review or the canvas: it reads
 * the project's types and the panel's navigation sentinels only.
 */
import type { MacroStep, ProjectConfig, UIElement } from "../../api/types";
import { NAVIGATION_SENTINELS } from "../../api/uiBindingReach.gen";

/** The `do.<interaction>` keys that hold action lists. Authored as arrays of
 *  action objects; legacy projects may carry a single object. Matrix sends one
 *  ui.route event the server demuxes into the four route slots — all four are
 *  author-time interactions. */
export const ACTION_SLOTS = [
  "press", "release", "hold", "change", "submit", "select",
  "route", "audio_route", "mute_route", "audio_mute_route",
] as const;

/**
 * Normalize one `do.<interaction>` entry to an array of action objects.
 * Interactions are authored as arrays (multiple actions per touch); legacy
 * projects may still carry a single action object — the panel runtime accepts
 * both. Pass the element's `do` map (`bindings.do`), not the whole bindings.
 */
export function slotActions(
  doMap: Record<string, unknown> | undefined,
  slot: string,
): Record<string, unknown>[] {
  const raw = doMap?.[slot];
  if (!raw || typeof raw !== "object") return [];
  if (Array.isArray(raw)) {
    return raw.filter((a) => a && typeof a === "object") as Record<string, unknown>[];
  }
  const obj = raw as Record<string, unknown>;
  return Object.keys(obj).length > 0 ? [obj] : [];
}

/**
 * The actions a value_map runs, as the binding card's checks read it: each
 * option's action, an option written as a list read item by item, anything
 * that is not an object skipped. Nothing for any other action.
 *
 * Looser than the runtime, which runs one action per option, and than Validate
 * and the page review, which read only that shape. The badges, the "does it
 * reach the device" test and the command list sent to the platform share this
 * reading, so a change to it is made here.
 */
export function valueMapActions(a: Record<string, unknown>): Record<string, unknown>[] {
  if (a.action !== "value_map" || !a.map || typeof a.map !== "object") return [];
  return Object.values(a.map as Record<string, unknown>)
    .flatMap((option) => (Array.isArray(option) ? option : [option]))
    .filter((sub): sub is Record<string, unknown> => !!sub && typeof sub === "object");
}

/** True when any action in the list issues a `device.command` — including the
 *  per-option actions inside a `value_map` (how a `select` drives a device:
 *  each chosen option maps to its own command). The engine and the AI validator
 *  treat a value_map's branches the same way, so the editor's "does this control
 *  reach the device?" check must look inside the map too, or a correct
 *  source-selector dropdown looks unwired. One map deep: a map inside an
 *  option is not looked into. */
export function actionsCommandDevice(actions: Record<string, unknown>[]): boolean {
  const isDeviceCommand = (a: Record<string, unknown>) => a.action === "device.command";
  return actions.some((a) => isDeviceCommand(a) || valueMapActions(a).some(isDeviceCommand));
}

/** Reference ids an action can dangle against. */
export interface BindingRefIds {
  deviceIds: ReadonlySet<string>;
  macroIds: ReadonlySet<string>;
  pageIds: ReadonlySet<string>;
}

/** What an action names by id, which is also the field it is named in. */
export type ReferenceKind = "device" | "macro" | "page";

/** The one id each action names: a command's device, a macro call's macro, a
 *  page move's page. A field another action carries means nothing to the
 *  runtime, so it is not read. */
const NAMED_BY_ACTION = new Map<unknown, ReferenceKind>([
  ["device.command", "device"],
  ["macro", "macro"],
  ["ui.navigate", "page"],
]);

/** How the binding card names each kind in its Broken badge. */
export const REFERENCE_NOUNS: Record<ReferenceKind, string> = {
  device: "Device",
  macro: "Macro",
  page: "Page",
};

/** Whether a page move can land: a page in the project, or a target the panel
 *  resolves itself (`$back`, `$dismiss`). A page nav's target asks the same. */
export function pageExists(page: unknown, pageIds: ReadonlySet<string>): boolean {
  return pageIds.has(page as string) || NAVIGATION_SENTINELS.has(page as string);
}

/** Which of the project's ids each kind is looked up in. */
const IDS_OF_KIND: Record<ReferenceKind, keyof BindingRefIds> = {
  device: "deviceIds",
  macro: "macroIds",
  page: "pageIds",
};

function resolves(kind: ReferenceKind, id: unknown, ids: BindingRefIds): boolean {
  if (kind === "page") return pageExists(id, ids.pageIds);
  return ids[IDS_OF_KIND[kind]].has(id as string);
}

export interface BrokenReference {
  kind: ReferenceKind;
  id: string;
}

/**
 * The id an action names that is not in the project, or null when its field is
 * empty or resolves. One action only: a value_map's options are each caller's to
 * walk, because the binding card's badge and Validate read them differently
 * (`valueMapActions`). Each caller words what it finds in its own place.
 */
export function brokenReference(a: Record<string, unknown>, ids: BindingRefIds): BrokenReference | null {
  const kind = NAMED_BY_ACTION.get(a.action);
  if (!kind) return null;
  const id = a[kind];
  return id && !resolves(kind, id, ids) ? { kind, id: String(id) } : null;
}

/** First broken reference in an action — a device/macro/page id that no
 *  longer exists. A value_map runs the per-option action branches, so those
 *  are checked here too — AI tools and imports emit them, and a Broken badge
 *  that misses them lets a dead reference look fine. */
export function actionDanglingRef(
  a: Record<string, unknown>,
  ids: BindingRefIds,
): string | null {
  const broken = brokenReference(a, ids);
  if (broken) return `${REFERENCE_NOUNS[broken.kind]} "${broken.id}" not found`;
  for (const sub of valueMapActions(a)) {
    const found = actionDanglingRef(sub, ids);
    if (found) return found;
  }
  return null;
}

/** The fields each action cannot run without. */
const RUNS_ONLY_WITH = new Map<unknown, readonly string[]>([
  ["device.command", ["device", "command"]],
  ["macro", ["macro"]],
  ["state.set", ["key"]],
  ["ui.navigate", ["page"]],
  ["script.call", ["function"]],
  ["event.emit", ["event"]],
]);

/** True when an action is missing the field it can't run without — the
 *  runtime silently skips these (e.g. script.call only fires when `function`
 *  is set), so the editor badges them Incomplete instead. Descends into a
 *  value_map's per-option branches like the runtime does. An action named
 *  something else is not judged; one with no name is incomplete. */
export function actionIncompleteCheck(a: Record<string, unknown>): boolean {
  const needs = RUNS_ONLY_WITH.get(a.action);
  if (needs) return needs.some((field) => !a[field]);
  if (a.action === "value_map") {
    const map = a.map as Record<string, unknown> | undefined;
    if (!map || Object.keys(map).length === 0) return true;
    return valueMapActions(a).some(actionIncompleteCheck);
  }
  return !a.action;
}

/**
 * Every `device.command` this element sends, in the objects the project holds.
 *
 * The objects themselves rather than copies: the platform answers about them by
 * identity (`api/commandIssues.ts`), which is what keeps a walker and a
 * validator from inventing two spellings of the same path.
 *
 * An action still missing its device or its command is left out. There is
 * nothing to ask about it yet, and `actionIncompleteCheck` already says so.
 */
export function elementCommandActions(el: UIElement): Record<string, unknown>[] {
  const doMap = ((el.bindings || {}) as Record<string, unknown>).do as
    | Record<string, unknown>
    | undefined;
  const found: Record<string, unknown>[] = [];
  const collect = (a: Record<string, unknown>) => {
    if (a.action === "device.command" && a.device && a.command) found.push(a);
    // The engine runs each mapped entry as an action of its own, so a select's
    // per-option commands are as real as any other.
    valueMapActions(a).forEach(collect);
  };
  for (const slot of ACTION_SLOTS) {
    for (const a of slotActions(doMap, slot)) collect(a);
  }
  return found;
}

/**
 * Every command in the project a driver could refuse: pages, master elements
 * and macro steps.
 *
 * The macros are here because Validate reports on them already -- it is where a
 * step aimed at a deleted device turns up -- and a gate that names one kind of
 * broken step while staying quiet about another is the shape this whole check
 * exists to remove. A step's own editor marks it live; this is the sweep
 * somebody runs before handing the room over.
 */
export function projectCommandActions(project: ProjectConfig): Record<string, unknown>[] {
  const found: Record<string, unknown>[] = [];
  for (const page of project.ui?.pages ?? []) {
    for (const el of page.elements ?? []) found.push(...elementCommandActions(el));
  }
  for (const mel of project.ui?.master_elements ?? []) {
    found.push(...elementCommandActions(mel as UIElement));
  }
  for (const macro of project.macros ?? []) {
    (macro.steps ?? []).forEach((step) => {
      found.push(...(stepAndBranches(step).filter(isCommandStep) as unknown as Record<string, unknown>[]));
    });
  }
  return found;
}

/** The macro steps the platform is asked about: a device or group command
 *  with its command chosen. */
function isCommandStep(step: MacroStep): boolean {
  return (step.action === "device.command" || step.action === "group.command") && !!step.command;
}

/**
 * A macro step and every step its branches hold, each before its own branches
 * and `then_steps` before `else_steps`. Validate's step check and the command
 * list for the platform both walk this, so every answer Validate looks up by
 * step is about a step it visits.
 */
export function stepAndBranches(step: MacroStep): MacroStep[] {
  const found = [step];
  step.then_steps?.forEach((s) => found.push(...stepAndBranches(s)));
  step.else_steps?.forEach((s) => found.push(...stepAndBranches(s)));
  return found;
}
