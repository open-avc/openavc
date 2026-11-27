/**
 * The Builder's Validate: what a project's controls, pages, master elements,
 * macro steps, triggers and idle page name that is not there (a condition's
 * value naming a variable or device the project lacks included), what the
 * platform said about each command and about what the pages hold that they
 * may not (an id two controls share, a container or an inherits that names
 * nothing or loops, a binding the panel cannot read: `api/pageIssues.ts`),
 * and what the page review says about each page.
 *
 * A control's checks are `CONTROL_CHECKS`, run in order for page and master
 * elements alike; a new check is one function and one entry there. What an
 * action names, and whether it resolves, is bindingActions.ts, which the
 * binding card's badges ask too; each says it in its own words. The geometry
 * and binding reach come from the page review (pageReview.ts), so Validate
 * and the AI write path reach the same verdict about the same page.
 *
 * The AI door's counterpart is openavc/ui/page_references.py, which asks most
 * of the same questions of a page in its own sentences, and one this cannot (a
 * value_map against a select's options); the two are not pinned to each other,
 * except that both reach every place an action sits
 * (tests/fixtures/action_reference_cases.json).
 */
import type {
  Layout,
  MacroConfig,
  MacroStep,
  MasterElement,
  ProjectConfig,
  TriggerConfig,
  UIElement,
  UIPage,
} from "../../api/types";
import { readsNoValue, referencedKey } from "../../api/conditionEval";
import type { PageIssue } from "../../api/pageIssues";
import {
  ACTION_SLOTS,
  REFERENCE_NOUNS,
  actionsCommandDevice,
  brokenReference,
  nestedActions,
  pageExists,
  rowRoutes,
  slotActions,
  stepAndBranches,
  type BindingRefIds,
  type BrokenReference,
} from "./bindingActions";
import { isPlainObject } from "./authoredValues";
import { BINDING_CAPABILITIES } from "./bindingCapabilities";
import type { ElementDefaults } from "./controlMinimums";
import {
  reviewMasterElement,
  reviewPage,
  sliderThemeDefaults,
  type ReviewOptions,
} from "./pageReview";
import { showOf } from "./reviewFinding";
import { matrixEntryName } from "./reviewMatrix";

export interface ValidationIssue {
  severity: "error" | "warning";
  message: string;
  location: string;
  pageId?: string;
  elementId?: string;
}

/** Validate's words for a reference that is not there. Where the badge says
 *  `Page "x" not found`, this says the navigate goes to a deleted page. */
function validateSays({ kind, id }: BrokenReference): string {
  return kind === "page" ? `Navigate to deleted page "${id}"` : `${REFERENCE_NOUNS[kind]} "${id}" not found`;
}

/** The device a `device.<id>.<property>` state key reads, or null for a key
 *  that is not a device's. Validate and the binding card's badge both ask it
 *  before saying a key reads a device that is not in the project. */
export function stateKeyDevice(key: string | undefined): string | null {
  return key?.startsWith("device.") ? key.split(".")[1] : null;
}

/** What a project's references resolve against, and what the platform said
 *  about the commands it was asked about (`validateProject`'s second argument). */
interface ProjectRefs extends BindingRefIds {
  /** The device groups a macro step may name; no control action names one. */
  groupIds: ReadonlySet<string>;
  /** The project's variables, which a condition's value may name (`$var.<id>`). */
  variableIds: ReadonlySet<string>;
  commandIssues?: Map<Record<string, unknown>, string[]>;
}

/** One thing Validate says about a control. `where` follows the control in the
 *  location it is reported at (` > press`, ` > change > "hdmi"`). */
interface ControlIssue {
  severity: ValidationIssue["severity"];
  message: string;
  where?: string;
}

type ControlCheck = (el: UIElement, refs: ProjectRefs) => ControlIssue[];

function doMapOf(el: UIElement): Record<string, unknown> {
  return (((el.bindings || {}) as Record<string, unknown>).do || {}) as Record<string, unknown>;
}

/** The binding a show slot holds, when it holds one. */
function shownBinding(el: UIElement, slot: string): Record<string, unknown> | undefined {
  return showOf(el)?.[slot] as Record<string, unknown> | undefined;
}

/** A page nav aimed at a page that is not there. */
function targetPageIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const target = el.type === "page_nav" ? el.target_page : undefined;
  if (!target || pageExists(target, refs.pageIds)) return [];
  return [{ severity: "error", message: `Target page "${target}" does not exist` }];
}

/** A grant naming a device that is gone matches nothing at runtime, and a
 *  grant that matches nothing looks exactly like a grant nobody set: the
 *  control just sits there seeing no state. */
function grantIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const issues: ControlIssue[] = [];
  for (const granted of el.grant?.devices ?? []) {
    if (!refs.deviceIds.has(granted)) {
      issues.push({ severity: "error", message: `Granted device "${granted}" not found, so this control cannot read or control it` });
    }
  }
  return issues;
}

/** What Validate calls the place an action carries another one under. */
const NESTED_SAID: Record<ReturnType<typeof nestedActions>[number][0], string> = {
  off_action: "Off Action",
  hold_action: "Long Press Action",
};

/**
 * One action, the value_map options under it and the actions it carries (a
 * toggle's off action, a long press's action): what it names that is not
 * there, then what the platform said about it. Reads one action per option, as
 * the runtime runs it; the binding card's checks read a list option as well
 * (`valueMapActions` in bindingActions.ts).
 */
function actionIssuesAt(a: Record<string, unknown>, where: string, refs: ProjectRefs): ControlIssue[] {
  const broken = brokenReference(a, refs);
  const issues: ControlIssue[] = broken ? [{ severity: "error", message: validateSays(broken), where }] : [];
  // The device is here and the command is one it has, and the driver will
  // still refuse it: a parameter it declares required was never filled in.
  // The sentence is the platform's, so this warning and the refusal on the
  // wall are the same words.
  for (const message of refs.commandIssues?.get(a) ?? []) issues.push({ severity: "error", message, where });
  return [...issues, ...carriedIssues(a, where, refs)];
}

/** What the actions an action runs in its turn name: a value_map's options,
 *  then its Off Action and Long Press Action. */
function carriedIssues(a: Record<string, unknown>, where: string, refs: ProjectRefs): ControlIssue[] {
  const options = a.action === "value_map" && isPlainObject(a.map) ? Object.entries(a.map) : [];
  return [
    ...options.flatMap(([option, mapped]) => (
      isPlainObject(mapped) ? actionIssuesAt(mapped, `${where} > "${option}"`, refs) : []
    )),
    ...nestedActions(a).flatMap(([field, nested]) => actionIssuesAt(nested, `${where} > ${NESTED_SAID[field]}`, refs)),
  ];
}

/** Every action in every interaction, the slots in their own order (an
 *  interaction written as one object rather than a list is read the same),
 *  then each matrix row's own route. */
function actionIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const doMap = doMapOf(el);
  const slots = ACTION_SLOTS.flatMap((slot) =>
    slotActions(doMap, slot).flatMap((a) => actionIssuesAt(a, ` > ${slot}`, refs)),
  );
  const rows = rowRoutes(el).flatMap(([row, route]) =>
    route.flatMap((a) => actionIssuesAt(a, ` > route for "${matrixEntryName(row)}"`, refs)),
  );
  return [...slots, ...rows];
}

/** The value and the appearance read a device that is not in the project. */
function shownDeviceIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const issues: ControlIssue[] = [];
  for (const [slot, label] of [["value", "value"], ["look", "appearance"]] as const) {
    const deviceId = stateKeyDevice(shownBinding(el, slot)?.key as string | undefined);
    if (deviceId !== null && !refs.deviceIds.has(deviceId)) {
      issues.push({ severity: "warning", message: `State key references unknown device "${deviceId}"`, where: ` > ${label}` });
    }
  }
  return issues;
}

/**
 * A control whose Value reads a device key but never sends a command can't
 * actually drive that device — the drag/selection updates only the local
 * mirror, overwritten on the next poll. Two-way to a device must go through
 * a command (do.<interaction> device.command with $value), never a state
 * write. Display-only controls reading device state are fine.
 */
function deviceValueIssues(el: UIElement): ControlIssue[] {
  const cap = BINDING_CAPABILITIES[el.type];
  const valueKey = shownBinding(el, "value")?.key as string | undefined;
  if (!cap?.value?.link || !valueKey?.startsWith("device.")) return [];
  const doMap = doMapOf(el);
  if ((cap.does ?? []).some((d) => actionsCommandDevice(slotActions(doMap, d.interaction)))) return [];
  return [{
    severity: "warning",
    message: "This control shows a device value but has no command to change it. Add a command so touching it reaches the device",
    where: " > value",
  }];
}

/** One condition as Validate reads it: its key, operator and value. */
type ConditionRead = { key?: string; operator?: unknown; value?: unknown };

/**
 * What a condition's value names that is not in the project, in Validate's
 * words: a variable the project does not define or a device it does not have
 * (`$var.wanted`, `$device.proj.power`; `referencedKey`). Null for a value
 * written as itself, one the operator does not read, and a key of any other
 * kind, which Validate cannot check.
 */
function unknownNamed(condition: ConditionRead, refs: ProjectRefs, where?: "state_trigger"): string | null {
  if (readsNoValue(condition.operator ?? "eq", where)) return null;
  const named = referencedKey(condition.value);
  if (named === null) return null;
  if (named.startsWith("var.")) {
    const id = named.slice("var.".length);
    return refs.variableIds.has(id) ? null : `unknown variable "${id}"`;
  }
  const deviceId = stateKeyDevice(named);
  return deviceId !== null && !refs.deviceIds.has(deviceId) ? `unknown device "${deviceId}"` : null;
}

/** What one visibility condition reads that is not there: a device the
 *  project does not have, or a key its value names that it does not define. */
function visibilityReferenceIssues(c: ConditionRead, refs: ProjectRefs): ControlIssue[] {
  const issues: ControlIssue[] = [];
  const deviceId = stateKeyDevice(c.key);
  if (deviceId !== null && !refs.deviceIds.has(deviceId)) {
    issues.push({ severity: "warning", message: `Visibility condition references unknown device "${deviceId}"`, where: " > visible_when" });
  }
  const unknown = unknownNamed(c, refs);
  if (unknown) {
    issues.push({ severity: "warning", message: `Visibility condition compares with ${unknown}`, where: " > visible_when" });
  }
  return issues;
}

/** A visibility condition reads a device that is not in the project, or its
 *  value names a key the project does not define. `all` is read before `any`,
 *  and a single condition stands on its own. An operator nobody knows is the
 *  platform's to say (`api/pageIssues.ts`). */
function visibilityIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const vw = shownBinding(el, "visible_when");
  if (!vw) return [];
  const conditions = (vw.all || vw.any || [vw]) as ConditionRead[];
  return conditions.flatMap((c) => visibilityReferenceIssues(c, refs));
}

/** The conditions whose value names a key the project does not define, each
 *  said at `location`. */
function conditionValueIssues(
  conditions: ConditionRead[], location: string, refs: ProjectRefs, where?: "state_trigger",
): ValidationIssue[] {
  return conditions.flatMap((c): ValidationIssue[] => {
    const unknown = unknownNamed(c, refs, where);
    return unknown ? [{ severity: "warning", message: `Condition compares with ${unknown}`, location }] : [];
  });
}

/** The types a touch is meant to do something on. */
const INTERACTIVE_TYPES = ["button", "slider", "fader", "select", "text_input", "keypad", "matrix", "list"];

/** An interactive control with no action and no two-way value does nothing
 *  when touched. */
function noActionIssues(el: UIElement): ControlIssue[] {
  if (!INTERACTIVE_TYPES.includes(el.type)) return [];
  const doMap = doMapOf(el);
  const acts = ACTION_SLOTS.some((slot) => slotActions(doMap, slot).length > 0);
  const writesBack = !!shownBinding(el, "value")?.write_back;
  return acts || writesBack ? [] : [{ severity: "warning", message: "Interactive element has no action" }];
}

/** Validate's checks of one control, reported in this order. A new check is one
 *  function and one entry here; pages and master elements both run the list. */
const CONTROL_CHECKS: readonly ControlCheck[] = [
  targetPageIssues,
  grantIssues,
  actionIssues,
  shownDeviceIssues,
  deviceValueIssues,
  visibilityIssues,
  noActionIssues,
];

/** What Validate says about one control, placed under its page (a master's
 *  page is the empty string). */
function controlIssues(el: UIElement, pageId: string, pageName: string, refs: ProjectRefs): ValidationIssue[] {
  const location = `${pageName} > ${el.id}`;
  return CONTROL_CHECKS.flatMap((check) => check(el, refs)).map((issue) => ({
    severity: issue.severity,
    message: issue.message,
    location: location + (issue.where ?? ""),
    pageId,
    elementId: el.id,
  }));
}

/** A custom page carries the same grant an iframe element does, and can name a
 *  device that has since left the project in exactly the same invisible way: it
 *  matches nothing, which looks like a grant nobody set. */
function pageGrantIssues(page: UIPage, refs: ProjectRefs): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  for (const granted of page.grant?.devices ?? []) {
    if (!refs.deviceIds.has(granted)) {
      issues.push({ severity: "error", message: `Granted device "${granted}" not found, so the page shown here cannot read or control it`, location: `${page.name} > page`, pageId: page.id });
    }
  }
  return issues;
}

/** The ids an arrangement positions or hides that are not on the page: the
 *  residue a bad rename or hand edit leaves. What an arrangement inherits is
 *  the platform's to judge (`api/pageIssues.ts`). */
function arrangementIssues(page: UIPage, onPage: ReadonlySet<string>): ValidationIssue[] {
  return (page.layouts ?? []).flatMap((layout) => layoutIssues(layout, page, onPage));
}

function layoutIssues(layout: Layout, page: UIPage, onPage: ReadonlySet<string>): ValidationIssue[] {
  const at = { location: `${page.name} > ${layout.id}`, pageId: page.id };
  const strays = (ids: Iterable<string>, says: (elId: string) => string): ValidationIssue[] =>
    [...ids].filter((elId) => !onPage.has(elId)).map((elId) => ({ severity: "warning", message: says(elId), ...at }));
  return [
    ...strays(Object.keys(layout.placements ?? {}), (elId) => `Arrangement "${layout.id}" positions "${elId}", which is not an element on this page`),
    ...strays(layout.hidden ?? [], (elId) => `Arrangement "${layout.id}" hides "${elId}", which is not an element on this page`),
  ];
}

/** Where Validate places one issue the platform found in the pages: under its
 *  page and control (or arrangement), or among the master elements. */
function heldIssue(issue: PageIssue, page?: UIPage): ValidationIssue {
  const said: ValidationIssue = { severity: "error", message: issue.message, location: "" };
  if (!page) {
    return { ...said, location: `Master Elements > ${issue.element ?? ""}`, ...(issue.element ? { elementId: issue.element } : {}) };
  }
  const where = issue.element ?? issue.layout ?? "page";
  return { ...said, location: `${page.name} > ${where}`, pageId: page.id, ...(issue.element ? { elementId: issue.element } : {}) };
}

/**
 * Everything Validate says about one page: what the platform found it holds
 * that it may not, its grant, its arrangements, each control in turn, then
 * what the page review says.
 *
 * Geometry and binding reach come back from the shared review -- the same code,
 * the same numbers and the same sentences the AI write path answers with, so
 * the two surfaces cannot reach different verdicts about the same file.
 * Geometry is per-arrangement: a portrait variant can push a control off-screen
 * or starve it while the primary is fine, so every layout is checked, and an
 * issue is reported once per element naming the arrangement only when it isn't
 * the primary's fault.
 */
function pageIssues(
  page: UIPage,
  refs: ProjectRefs,
  review: Pick<ReviewOptions, "theme" | "masters">,
  held: readonly PageIssue[],
): ValidationIssue[] {
  const onPage = new Set(page.elements.map((e) => e.id));
  return [
    ...held.map((issue) => heldIssue(issue, page)),
    ...pageGrantIssues(page, refs),
    ...arrangementIssues(page, onPage),
    ...page.elements.flatMap((el) => controlIssues(el, page.id, page.name, refs)),
    ...reviewPage(page, review).map((finding): ValidationIssue => ({
      severity: "warning",
      message: finding.message,
      location: `${page.name} > ${finding.elementId}`,
      pageId: page.id,
      elementId: finding.elementId,
    })),
  ];
}

/** A master element: the control checks with no page, its own review, then
 *  each page it names that is not in the project. */
function masterIssues(mel: MasterElement, refs: ProjectRefs, theme: ElementDefaults): ValidationIssue[] {
  const issues: ValidationIssue[] = [
    ...controlIssues(mel, "", "Master Elements", refs),
    ...reviewMasterElement(mel, theme).map((finding): ValidationIssue => ({
      severity: "warning",
      message: finding.message,
      location: `Master Elements > ${finding.elementId}`,
      elementId: finding.elementId,
    })),
  ];
  if (Array.isArray(mel.pages)) {
    for (const pid of mel.pages) {
      if (!refs.pageIds.has(pid)) {
        issues.push({ severity: "error", message: `References deleted page "${pid}"`, location: `Master Elements > ${mel.id}` });
      }
    }
  }
  return issues;
}

/** What a finding calls a step with no description, by the kind of thing the
 *  step names. A step's page is not checked, so a page has no entry. */
const UNNAMED_STEP: Record<BrokenReference["kind"], string> = {
  device: "device.command",
  macro: "call macro",
  page: "ui.navigate",
};

/** The device group a group command names that is not in the project. */
function missingGroup(step: MacroStep, refs: ProjectRefs): string | null {
  if (step.action !== "group.command" || !step.group) return null;
  return refs.groupIds.has(step.group) ? null : step.group;
}

/** The device, macro or page a step names that is not in the project: the
 *  field an action names, read as a control's action is (`brokenReference`), in
 *  the same words; and a group command's device group, which no control action
 *  names. `at` places a finding, given what to call a step with no description. */
function stepReferenceIssues(step: MacroStep, at: (unnamed: string) => string, refs: ProjectRefs): ValidationIssue[] {
  const group = missingGroup(step, refs);
  if (group) return [{ severity: "error", message: `Device group "${group}" not found`, location: at("group.command") }];
  const broken = brokenReference(step as unknown as Record<string, unknown>, refs);
  if (!broken) return [];
  return [{ severity: "error", message: validateSays(broken), location: at(UNNAMED_STEP[broken.kind]) }];
}

/** One macro step: what it names that is not there, then what the platform
 *  said about it, then what its Skip If, or a Conditional's or Wait Until's
 *  condition, compares with that the project does not define. */
function stepIssues(step: MacroStep, prefix: string, refs: ProjectRefs): ValidationIssue[] {
  const at = (unnamed: string) => `${prefix} > ${step.description || unnamed}`;
  const said = refs.commandIssues?.get(step as unknown as Record<string, unknown>) ?? [];
  const conditions = [step.skip_if, step.condition].filter(isPlainObject) as ConditionRead[];
  return [
    ...stepReferenceIssues(step, at, refs),
    ...said.map((message): ValidationIssue => ({ severity: "error", message, location: at(step.action) })),
    ...conditionValueIssues(conditions, at(step.action), refs),
  ];
}

/** A trigger's guards, then a State Change trigger's own value, that compare
 *  with a key the project does not define. */
function triggerIssues(trigger: TriggerConfig, prefix: string, refs: ProjectRefs): ValidationIssue[] {
  const location = `${prefix} > Trigger "${trigger.id}"`;
  const own = trigger.type === "state_change"
    ? conditionValueIssues([{ operator: trigger.state_operator, value: trigger.state_value }], location, refs, "state_trigger")
    : [];
  return [...conditionValueIssues((trigger.conditions ?? []) as ConditionRead[], location, refs), ...own];
}

/** Every step of every macro, nested branches included, in the order the
 *  macro holds them, then its triggers. */
function macroIssues(macros: MacroConfig[], refs: ProjectRefs): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  for (const macro of macros) {
    const prefix = `Macro "${macro.name}"`;
    for (const step of macro.steps) {
      for (const each of stepAndBranches(step)) issues.push(...stepIssues(each, prefix, refs));
    }
    for (const trigger of macro.triggers ?? []) issues.push(...triggerIssues(trigger, prefix, refs));
  }
  return issues;
}

function idlePageIssues(project: ProjectConfig, pageIds: ReadonlySet<string>): ValidationIssue[] {
  const idlePage = project.ui.settings.idle_page;
  if (!idlePage || pageIds.has(idlePage)) return [];
  return [{ severity: "error", message: `Idle page "${idlePage}" does not exist`, location: "Settings > Idle Page" }];
}

/** The platform's page issues by the page they are on, in its order; a
 *  master's under null. Two pages with one id share one list, said once,
 *  under the first. */
function heldByPage(held: readonly PageIssue[]): Map<string | null, PageIssue[]> {
  const byPage = new Map<string | null, PageIssue[]>();
  for (const issue of held) byPage.set(issue.page, [...(byPage.get(issue.page) ?? []), issue]);
  return byPage;
}

/**
 * @param commandIssues What the platform says about each `device.command` in
 *   the project, keyed by the action object (see `api/commandIssues.ts`).
 *   Omitted means it was not asked or could not be reached, and nothing is
 *   claimed either way -- this file cannot see the drivers, and guessing is
 *   how Validate came to answer "No Issues" for a control the device refuses.
 * @param held What the platform says the pages and master elements hold that
 *   they may not (`api/pageIssues.ts`). Omitted, likewise, claims nothing.
 */
export function validateProject(
  project: ProjectConfig,
  commandIssues?: Map<Record<string, unknown>, string[]>,
  held: readonly PageIssue[] = [],
): ValidationIssue[] {
  const refs: ProjectRefs = {
    pageIds: new Set(project.ui.pages.map((p) => p.id)),
    deviceIds: new Set(project.devices.map((d) => d.id)),
    macroIds: new Set(project.macros.map((m) => m.id)),
    groupIds: new Set((project.device_groups ?? []).map((g) => g.id)),
    variableIds: new Set((project.variables ?? []).map((v) => v.id)),
    commandIssues,
  };
  const theme = sliderThemeDefaults(project);
  const review = { theme, masters: project.ui.master_elements ?? [] };
  const byPage = heldByPage(held);
  const onPages = project.ui.pages.flatMap((page) => {
    const here = byPage.get(page.id) ?? [];
    byPage.delete(page.id);
    return pageIssues(page, refs, review, here);
  });
  return [
    ...onPages,
    ...(byPage.get(null) ?? []).map((issue) => heldIssue(issue)),
    ...(project.ui.master_elements || []).flatMap((mel) => masterIssues(mel, refs, theme)),
    ...macroIssues(project.macros, refs),
    ...idlePageIssues(project, refs.pageIds),
  ];
}
