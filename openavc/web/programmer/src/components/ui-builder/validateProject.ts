/**
 * The Builder's Validate: what a project's controls, pages, master elements,
 * macro steps and idle page name that is not there, what the platform said
 * about each command, and what the page review says about each page.
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
 * value_map against a select's options); the two are not pinned to each other.
 */
import type {
  Layout,
  MacroConfig,
  MacroStep,
  MasterElement,
  ProjectConfig,
  UIElement,
  UIPage,
} from "../../api/types";
import {
  ACTION_SLOTS,
  REFERENCE_NOUNS,
  actionsCommandDevice,
  brokenReference,
  pageExists,
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

/**
 * One action and the value_map options under it: what it names that is not
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
  if (a.action === "value_map" && isPlainObject(a.map)) {
    for (const [option, mapped] of Object.entries(a.map)) {
      if (isPlainObject(mapped)) issues.push(...actionIssuesAt(mapped, `${where} > "${option}"`, refs));
    }
  }
  return issues;
}

/** Every action in every interaction, the slots in their own order (an
 *  interaction written as one object rather than a list is read the same). */
function actionIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const doMap = doMapOf(el);
  return ACTION_SLOTS.flatMap((slot) =>
    slotActions(doMap, slot).flatMap((a) => actionIssuesAt(a, ` > ${slot}`, refs)),
  );
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

/** A visibility condition reads a device that is not in the project. `all`
 *  is read before `any`, and a single condition stands on its own. */
function visibilityIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const vw = shownBinding(el, "visible_when");
  if (!vw) return [];
  const issues: ControlIssue[] = [];
  const conditions = (vw.all || vw.any || [vw]) as Array<{ key?: string }>;
  for (const c of conditions) {
    const deviceId = stateKeyDevice(c.key);
    if (deviceId !== null && !refs.deviceIds.has(deviceId)) {
      issues.push({ severity: "warning", message: `Visibility condition references unknown device "${deviceId}"`, where: " > visible_when" });
    }
  }
  return issues;
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

/** The arrangements themselves: a dangling inherits silently collapses the
 *  variant (everything without its own box falls to default placements), and
 *  orphaned ids are the residue a bad rename or hand edit leaves. */
function arrangementIssues(page: UIPage, onPage: ReadonlySet<string>): ValidationIssue[] {
  const layoutIds = new Set((page.layouts ?? []).map((l) => l.id));
  const issues: ValidationIssue[] = [];
  for (const layout of page.layouts ?? []) issues.push(...layoutIssues(layout, page, layoutIds, onPage));
  return issues;
}

function layoutIssues(
  layout: Layout,
  page: UIPage,
  layoutIds: ReadonlySet<string>,
  onPage: ReadonlySet<string>,
): ValidationIssue[] {
  const at = { location: `${page.name} > ${layout.id}`, pageId: page.id };
  const strays = (ids: Iterable<string>, says: (elId: string) => string): ValidationIssue[] =>
    [...ids].filter((elId) => !onPage.has(elId)).map((elId) => ({ severity: "warning", message: says(elId), ...at }));
  const dangling: ValidationIssue[] = layout.inherits && !layoutIds.has(layout.inherits)
    ? [{ severity: "error", message: `Arrangement inherits from "${layout.inherits}", which does not exist \u2014 controls without their own position here fall back to default boxes`, ...at }]
    : [];
  return [
    ...dangling,
    ...strays(Object.keys(layout.placements ?? {}), (elId) => `Arrangement "${layout.id}" positions "${elId}", which is not an element on this page`),
    ...strays(layout.hidden ?? [], (elId) => `Arrangement "${layout.id}" hides "${elId}", which is not an element on this page`),
  ];
}

/** A parent that doesn't exist leaves the child unrendered \u2014 the runtime
 *  draws children into their container, and there is no container. */
function containerIssues(el: UIElement, page: UIPage, onPage: ReadonlySet<string>): ValidationIssue[] {
  if (!el.parent || onPage.has(el.parent)) return [];
  return [{ severity: "error", message: `Container "${el.parent}" does not exist`, location: `${page.name} > ${el.id}`, pageId: page.id, elementId: el.id }];
}

/**
 * Everything Validate says about one page: its grant, its arrangements, each
 * control in turn, then what the page review says.
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
): ValidationIssue[] {
  const onPage = new Set(page.elements.map((e) => e.id));
  return [
    ...pageGrantIssues(page, refs),
    ...arrangementIssues(page, onPage),
    ...page.elements.flatMap((el) => [
      ...controlIssues(el, page.id, page.name, refs),
      ...containerIssues(el, page, onPage),
    ]),
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

/** The device, macro or page a step names that is not in the project: the
 *  field an action names, read as a control's action is (`brokenReference`), in
 *  the same words; and a group command's device group, which no control action
 *  names. `at` places a finding, given what to call a step with no description. */
function stepReferenceIssues(step: MacroStep, at: (unnamed: string) => string, refs: ProjectRefs): ValidationIssue[] {
  if (step.action === "group.command" && step.group && !refs.groupIds.has(step.group)) {
    return [{ severity: "error", message: `Device group "${step.group}" not found`, location: at("group.command") }];
  }
  const broken = brokenReference(step as unknown as Record<string, unknown>, refs);
  if (!broken) return [];
  return [{ severity: "error", message: validateSays(broken), location: at(UNNAMED_STEP[broken.kind]) }];
}

/** One macro step: what it names that is not there, then what the platform
 *  said about it. */
function stepIssues(step: MacroStep, prefix: string, refs: ProjectRefs): ValidationIssue[] {
  const at = (unnamed: string) => `${prefix} > ${step.description || unnamed}`;
  const said = refs.commandIssues?.get(step as unknown as Record<string, unknown>) ?? [];
  return [
    ...stepReferenceIssues(step, at, refs),
    ...said.map((message): ValidationIssue => ({ severity: "error", message, location: at(step.action) })),
  ];
}

/** Every step of every macro, nested branches included, in the order the
 *  macro holds them. */
function macroIssues(macros: MacroConfig[], refs: ProjectRefs): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  for (const macro of macros) {
    for (const step of macro.steps) {
      for (const each of stepAndBranches(step)) issues.push(...stepIssues(each, `Macro "${macro.name}"`, refs));
    }
  }
  return issues;
}

function idlePageIssues(project: ProjectConfig, pageIds: ReadonlySet<string>): ValidationIssue[] {
  const idlePage = project.ui.settings.idle_page;
  if (!idlePage || pageIds.has(idlePage)) return [];
  return [{ severity: "error", message: `Idle page "${idlePage}" does not exist`, location: "Settings > Idle Page" }];
}

/**
 * @param commandIssues What the platform says about each `device.command` in
 *   the project, keyed by the action object (see `api/commandIssues.ts`).
 *   Omitted means it was not asked or could not be reached, and nothing is
 *   claimed either way -- this file cannot see the drivers, and guessing is
 *   how Validate came to answer "No Issues" for a control the device refuses.
 */
export function validateProject(
  project: ProjectConfig,
  commandIssues?: Map<Record<string, unknown>, string[]>,
): ValidationIssue[] {
  const refs: ProjectRefs = {
    pageIds: new Set(project.ui.pages.map((p) => p.id)),
    deviceIds: new Set(project.devices.map((d) => d.id)),
    macroIds: new Set(project.macros.map((m) => m.id)),
    groupIds: new Set((project.device_groups ?? []).map((g) => g.id)),
    commandIssues,
  };
  const theme = sliderThemeDefaults(project);
  const review = { theme, masters: project.ui.master_elements ?? [] };
  return [
    ...project.ui.pages.flatMap((page) => pageIssues(page, refs, review)),
    ...(project.ui.master_elements || []).flatMap((mel) => masterIssues(mel, refs, theme)),
    ...macroIssues(project.macros, refs),
    ...idlePageIssues(project, refs.pageIds),
  ];
}
