/**
 * What a control's binding actions name and need, as the IDE checks them.
 *
 * The slots an element's `do` map holds actions in; what a value_map runs, as
 * the binding card reads it; the device, macro or page an action names and
 * whether it is there; the fields an action cannot run without; and the device
 * commands the platform is asked about, on controls and in macro steps. The
 * binding card's badges, the Builder's Validate, page delete and the command
 * check all read these, so a new action, or a new spelling of one, is a row
 * here. So is a new place an action can sit (`NESTED_ACTION_FIELDS`, and a
 * matrix row's own route, `rowRoutes`), which the page review walks too.
 *
 * Nothing here knows about geometry, the page review or the canvas: it reads
 * the project's types, the matrix model and the panel's navigation sentinels
 * only.
 */
import type { MacroStep, ProjectConfig, UIElement } from "../../api/types";
import { NAVIGATION_SENTINELS } from "../../api/uiBindingReach.gen";
import { resolveMatrixAxis, type MatrixEntry } from "./matrixModel";

/** The `do.<interaction>` keys that hold action lists. Authored as arrays of
 *  action objects; legacy projects may carry a single object. Matrix sends one
 *  ui.route event the server demuxes into the four route slots — all four are
 *  author-time interactions. */
export const ACTION_SLOTS = [
  "press", "release", "hold", "change", "submit", "select",
  "route", "audio_route", "mute_route", "audio_mute_route",
] as const;

/** The fields inside an action that hold another action: a toggle's off
 *  action and a long press's action, both carried by the first press action.
 *  The panel runs each in its turn, so every reader of a control's actions
 *  looks inside them. Mirrors `_NESTED_ACTION_FIELDS` in page_review.py. */
export const NESTED_ACTION_FIELDS = ["off_action", "hold_action"] as const;

/** The actions this action carries under `NESTED_ACTION_FIELDS`, in that order. */
export function nestedActions(a: Record<string, unknown>): [(typeof NESTED_ACTION_FIELDS)[number], Record<string, unknown>][] {
  return NESTED_ACTION_FIELDS.flatMap((field) => {
    const nested = a[field];
    return nested && typeof nested === "object" && !Array.isArray(nested)
      ? [[field, nested as Record<string, unknown>] as [(typeof NESTED_ACTION_FIELDS)[number], Record<string, unknown>]]
      : [];
  });
}

/** Each matrix row that carries its own route, with that route's actions. The
 *  panel runs a row's route instead of the element's `do.route` for that row. */
export function rowRoutes(el: UIElement): [MatrixEntry, Record<string, unknown>[]][] {
  if (el.type !== "matrix") return [];
  const config = (el as unknown as Record<string, unknown>).matrix_config;
  return resolveMatrixAxis(config, "destinations").flatMap((row) => {
    const route = row.route;
    if (!Array.isArray(route)) return [];
    const actions = route.filter((a) => a && typeof a === "object" && !Array.isArray(a)) as Record<string, unknown>[];
    return [[row, actions] as [MatrixEntry, Record<string, unknown>[]]];
  });
}

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
function valueMapActions(a: Record<string, unknown>): Record<string, unknown>[] {
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
  // The sub-pages and the sub-page containers, for the ui.subpage action:
  // its page must be a sub-page specifically, its container a
  // subpage_container element specifically -- a regular page or an ordinary
  // element named there is broken in a way "page exists" cannot see.
  // 子页面与子页面容器集合，供 ui.subpage 动作使用：其 page 必须是子页面，
  // 其 container 必须是 subpage_container 元素——在这里填普通页面或普通元素
  // 是"页面存在"检查发现不了的坏法。
  subpageIds: ReadonlySet<string>;
  containerIds: ReadonlySet<string>;
}

/** What an action names by id, which is also the field it is named in. */
type ReferenceKind = "device" | "macro" | "page" | "subpage" | "container";

/** What each action names, as (kind, field) pairs: a command's device, a macro
 *  call's macro, a page move's page, and a sub-page switch's container AND
 *  sub-page -- two ids of two kinds. A field another action carries means
 *  nothing to the runtime, so it is not read. */
const NAMED_BY_ACTION = new Map<unknown, readonly (readonly [ReferenceKind, string])[]>([
  ["device.command", [["device", "device"]]],
  ["macro", [["macro", "macro"]]],
  ["ui.navigate", [["page", "page"]]],
  ["ui.subpage", [["container", "container"], ["subpage", "page"]]],
]);

/** How the binding card names each kind in its Broken badge. */
export const REFERENCE_NOUNS: Record<ReferenceKind, string> = {
  device: "Device",
  macro: "Macro",
  page: "Page",
  subpage: "Sub-page",
  container: "Container",
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
  subpage: "subpageIds",
  container: "containerIds",
};

function resolves(kind: ReferenceKind, id: unknown, ids: BindingRefIds): boolean {
  // A page move must land on a page that is not a sub-page: the panel refuses
  // navigation to one, so "exists" alone would leave the badge quiet on a
  // target that can never work.
  // 页面切换必须落在非子页面上：面板拒绝导航到子页面，只查"存在"会让徽章
  // 对永远无法生效的目标保持沉默。
  if (kind === "page") return pageExists(id, ids.pageIds) && !ids.subpageIds.has(id as string);
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
 * Validate reads a macro step through this too, since a step names its device
 * or macro in the same field an action does.
 */
export function brokenReference(a: Record<string, unknown>, ids: BindingRefIds): BrokenReference | null {
  const named = NAMED_BY_ACTION.get(a.action);
  if (!named) return null;
  for (const [kind, field] of named) {
    const id = a[field];
    if (id && !resolves(kind, id, ids)) return { kind, id: String(id) };
  }
  return null;
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
  ["ui.subpage", ["container", "page"]],
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
    // per-option commands are as real as any other; so are a toggle's off
    // action and a long press's action.
    valueMapActions(a).forEach(collect);
    for (const [, nested] of nestedActions(a)) collect(nested);
  };
  for (const slot of ACTION_SLOTS) {
    for (const a of slotActions(doMap, slot)) collect(a);
  }
  for (const [, route] of rowRoutes(el)) route.forEach(collect);
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
