/**
 * When a control is renamed or a page deleted, what the rest of the project
 * says about it follows.
 *
 * A control's id is held by the element itself, its children's `parent`, and
 * each arrangement's placements and hidden list; its `ui.<id>.` state keys by
 * `key`, `state_key` and `source_key` fields in bindings and a step's params,
 * by a macro step's own key and conditions (its branches included), by a
 * trigger's state key and conditions, and by a variable's source key; a
 * `$ui.<id>.` value reference by any other text field in bindings and a step's
 * params, and by a step's value; and its panel events (`ui.<event>.<id>`) by a
 * trigger's event pattern. Each holder's list is one function here
 * (`renamedElement`, `renamedStep`, `renamedTrigger`, `renamedVariable`,
 * `renamedInLayout`), so a new place a key can sit is one line in its holder's
 * function.
 *
 * Deleting a page empties a page nav's target on it, drops the moves to it
 * from every place a control or a master keeps actions (each `do` slot, the
 * off and long press actions an action carries, a value_map's options, a
 * matrix row's own route: the places the page review walks), takes it out of
 * each master's page list, drops the macro steps that move to it, and drops
 * the trigger conditions that matched on it being the current page
 * (`removePageAndScrubRefs`); `removePageFromGroups` takes it out of each page
 * group. The AI's page delete is
 * `scrub_page_references` in openavc/cloud/tools/ui_rewrites.py, and leaves
 * the same project.
 *
 * Every array and object nothing in it changed comes back as it was
 * (`mapPreserve`, `filterPreserve`, `withField`): the IDE's undo history
 * snapshots only the sections a change hands back new.
 */
import type {
  Layout,
  MacroConfig,
  MacroStep,
  MasterElement,
  PageGroup,
  Placement,
  ScriptConfig,
  StepCondition,
  TriggerCondition,
  TriggerConfig,
  UIElement,
  UIPage,
  VariableConfig,
} from "../../api/types";
import { isPlainObject, own } from "./authoredValues";
import { NESTED_ACTION_FIELDS } from "./bindingActions";

/** The same array back when `fn` handed back every item as it was; otherwise
 *  the new one. */
function mapPreserve<T>(arr: T[], fn: (item: T) => T): T[] {
  const next = arr.map(fn);
  return sameItems(next, arr) ? arr : next;
}

function sameItems<T>(after: T[], before: T[]): boolean {
  return after.every((item, i) => item === before[i]);
}

/** The same array back when `keep` kept every item; otherwise the shorter one. */
function filterPreserve<T>(arr: T[], keep: (item: T) => boolean): T[] {
  const kept = arr.filter(keep);
  return kept.length === arr.length ? arr : kept;
}

/** The holder with one field replaced by what `rewrite` makes of it, or the
 *  holder itself when that is the same value. A field that is not set is left
 *  alone. */
function withField<T extends object, K extends keyof T>(
  holder: T,
  field: K,
  rewrite: (value: NonNullable<T[K]>) => T[K],
): T {
  const before = holder[field];
  if (!before) return holder;
  const after = rewrite(before as NonNullable<T[K]>);
  return after === before ? holder : { ...holder, [field]: after };
}

/** A control's id and the one it takes. */
interface IdRename {
  oldId: string;
  newId: string;
}

/** `ui.<oldId>.<rest>` as `ui.<newId>.<rest>`; anything else as it was. */
function renamedStateKey<V>(value: V, { oldId, newId }: IdRename): V {
  if (typeof value !== "string") return value;
  const prefix = `ui.${oldId}.`;
  return (value.startsWith(prefix) ? `ui.${newId}.` + value.slice(prefix.length) : value) as V;
}

/** The fields whose text is a state key, wherever they sit in bindings or a
 *  step's params. */
const STATE_KEY_FIELDS = new Set(["key", "state_key", "source_key"]);

/** Any JSON-shaped value with its state-key fields renamed, walked to the
 *  bottom: a key field holding something other than text is walked into. */
function renamedRefsDeep(value: unknown, rename: IdRename): unknown {
  if (Array.isArray(value)) return mapPreserve(value, (v) => renamedRefsDeep(v, rename));
  if (!value || typeof value !== "object") return value;
  const fields = value as Record<string, unknown>;
  let changed = false;
  const next: Record<string, unknown> = {};
  for (const [field, before] of Object.entries(fields)) {
    const after = renamedField(field, before, rename);
    if (after !== before) changed = true;
    next[field] = after;
  }
  return changed ? next : value;
}

function renamedField(field: string, value: unknown, rename: IdRename): unknown {
  if (typeof value !== "string") return renamedRefsDeep(value, rename);
  return STATE_KEY_FIELDS.has(field) ? renamedStateKey(value, rename) : renamedValueRef(value, rename);
}

/** `$ui.<oldId>.<rest>`, a value read from that state key when the action or
 *  step runs, as `$ui.<newId>.<rest>`; anything else as it was. */
function renamedValueRef<V>(value: V, rename: IdRename): V {
  if (typeof value !== "string" || !value.startsWith("$")) return value;
  const renamed = renamedStateKey(value.slice(1), rename);
  return (renamed === value.slice(1) ? value : `$${renamed}`) as V;
}

/** An element under a new id, as a rename reads it: how a copied or
 *  re-mastered element takes its new id. */
export function rewriteElement(el: UIElement, oldId: string, newId: string): UIElement {
  return renamedElement(el, { oldId, newId });
}

/** The element under its new id, wherever it holds the old one: its own id,
 *  its `parent` (a child of a renamed container), its bindings' keys and the
 *  untyped `visibility` field. */
function renamedElement(el: UIElement, rename: IdRename): UIElement {
  const { oldId, newId } = rename;
  let next = el.id === oldId ? { ...el, id: newId } : el;
  if (next.parent === oldId) next = { ...next, parent: newId };
  next = withField(next, "bindings", (b) => renamedRefsDeep(b, rename) as UIElement["bindings"]);
  const record = next as unknown as Record<string, unknown>;
  return withField(record, "visibility", (v) => renamedRefsDeep(v, rename)) as unknown as UIElement;
}

/** A macro step with its keys renamed: its own key, its params, its condition
 *  and skip guard, and every step in its branches. */
function renamedStep(step: MacroStep, rename: IdRename): MacroStep {
  const key = (k: string) => renamedStateKey(k, rename);
  const guard = (c: StepCondition) => withField(c, "key", key);
  const branch = (steps: MacroStep[]) => mapPreserve(steps, (s) => renamedStep(s, rename));
  let next = withField(step, "key", key);
  next = withField(next, "value", (v) => renamedValueRef(v, rename));
  next = withField(next, "params", (p) => renamedRefsDeep(p, rename) as Record<string, unknown>);
  next = withField(next, "condition", guard);
  next = withField(next, "skip_if", guard);
  next = withField(next, "then_steps", branch);
  return withField(next, "else_steps", branch);
}

/** A trigger with its state key, its conditions' keys and its event pattern
 *  renamed. */
function renamedTrigger(trigger: TriggerConfig, rename: IdRename): TriggerConfig {
  const key = (k: string) => renamedStateKey(k, rename);
  let next = withField(trigger, "state_key", key);
  next = withField(next, "event_pattern", (p) => renamedEventPattern(p, rename));
  return withField(next, "conditions", (cs) => mapPreserve(cs, (c) => withField(c, "key", key)));
}

/** `ui.<event>.<oldId>`, the event the panel sends when that control is
 *  touched, as `ui.<event>.<newId>`; any other pattern as it was. */
function renamedEventPattern(pattern: string, { oldId, newId }: IdRename): string {
  const event = /^ui\.([^.]+)\.([^.]+)$/.exec(pattern);
  return event?.[2] === oldId ? `ui.${event[1]}.${newId}` : pattern;
}

/** A macro with its steps and triggers renamed. A macro that changes carries
 *  a new steps array, even when only a trigger moved. */
function renamedMacro(macro: MacroConfig, rename: IdRename): MacroConfig {
  const steps = macro.steps.map((s) => renamedStep(s, rename));
  const triggers = macro.triggers && mapPreserve(macro.triggers, (t) => renamedTrigger(t, rename));
  if (sameItems(steps, macro.steps) && triggers === macro.triggers) return macro;
  return { ...macro, steps, triggers };
}

function renamedVariable(v: VariableConfig, rename: IdRename): VariableConfig {
  return withField(v, "source_key", (k) => renamedStateKey(k, rename));
}

/** Geometry does not live on the element: every arrangement keys its
 *  placements and hidden list by element id, so a rename has to reach them
 *  or the old id stays behind as an orphan that Validate then reports, once
 *  per renamed control. Same layout back when it never held the id. */
function renamedInLayout(layout: Layout, rename: IdRename): Layout {
  const { oldId, newId } = rename;
  let next = layout;
  if (!!layout.placements && own(layout.placements, oldId) !== undefined) {
    next = { ...next, placements: placementsRenamed(layout.placements, rename) };
  }
  if (layout.hidden?.includes(oldId)) {
    next = { ...next, hidden: layout.hidden.map((id) => (id === oldId ? newId : id)) };
  }
  return next;
}

/** The placements in their order, the renamed one under its new id. */
function placementsRenamed(
  placements: Record<string, Placement>,
  { oldId, newId }: IdRename,
): Record<string, Placement> {
  const next: Record<string, Placement> = {};
  for (const [id, box] of Object.entries(placements)) next[id === oldId ? newId : id] = box;
  return next;
}

function renamedPage(page: UIPage, rename: IdRename): UIPage {
  const elements = mapPreserve(page.elements, (el) => renamedElement(el, rename));
  const layouts = page.layouts && mapPreserve(page.layouts, (l) => renamedInLayout(l, rename));
  if (elements === page.elements && layouts === page.layouts) return page;
  return { ...page, elements, layouts };
}

export interface RenameResult {
  pages: UIPage[];
  master_elements: MasterElement[];
  macros: MacroConfig[];
  variables: VariableConfig[];
  scriptsToReview: string[];  // script file names that mention `ui.<oldId>.`
}

/**
 * Validate a proposed element ID. Returns null if valid, else an error
 * message. Allowed chars: lowercase letters, digits, underscores. Must
 * start with a letter. Must not collide with any existing element ID
 * across all pages or master_elements (excluding the element being
 * renamed itself).
 */
export function validateElementId(
  newId: string,
  currentId: string,
  pages: UIPage[],
  masterElements: MasterElement[],
): string | null {
  if (!newId) return "ID cannot be empty.";
  if (newId === currentId) return null;
  if (!/^[a-z][a-z0-9_]*$/.test(newId)) {
    return "ID must start with a lowercase letter and contain only lowercase letters, digits, and underscores.";
  }
  const existing = new Set<string>();
  for (const p of pages) for (const el of p.elements) existing.add(el.id);
  for (const m of masterElements) existing.add(m.id);
  existing.delete(currentId);
  if (existing.has(newId)) return `An element with ID "${newId}" already exists.`;
  return null;
}

/**
 * Rename an element across the entire project, rewriting every reference
 * in element bindings, visibility conditions, master elements, macro steps,
 * trigger conditions, and variable source_keys. Scripts are NOT
 * auto-rewritten: their source is not read here, so every script with a file
 * is returned in `scriptsToReview` for the caller to warn about.
 */
export function renameElement(
  pages: UIPage[],
  masterElements: MasterElement[],
  macros: MacroConfig[],
  variables: VariableConfig[],
  scripts: ScriptConfig[],
  oldId: string,
  newId: string,
): RenameResult {
  const rename = { oldId, newId };
  return {
    pages: mapPreserve(pages, (p) => renamedPage(p, rename)),
    master_elements: mapPreserve(masterElements, (m) => renamedElement(m, rename) as MasterElement),
    macros: mapPreserve(macros, (m) => renamedMacro(m, rename)),
    variables: mapPreserve(variables, (v) => renamedVariable(v, rename)),
    scriptsToReview: scripts.filter((s) => s.file && s.id).map((s) => s.file),
  };
}

// --- Deleting a page ---

/** Whether an action is a move to the page being deleted: a `ui.navigate` to
 *  it, or a `ui.subpage` that would show it (a sub-page deleted with its
 *  page), or a `ui.subpage` whose container lived on it -- the switch's
 *  target is gone either way, and what would be left names nothing the panel
 *  draws.
 *  动作是否指向被删除的页面：导航到它、会显示它的 ui.subpage（子页面随页
 *  面一起删除），或其容器原在这页上的 ui.subpage——无论哪种，切换的目标都
 *  没了，留下的部分指向面板不会绘制的东西。 */
function isMoveTo(action: unknown, pageId: string, survivingContainers?: ReadonlySet<string> | undefined): boolean {
  if (!action || typeof action !== "object") return false;
  const a = action as Record<string, unknown>;
  if (a.action === "ui.subpage") {
    if (a.page === pageId) return true;
    // A container that did not survive the deletion: the switch is dead
    // weight either way, dropped whole -- the same rule the AI's page delete
    // keeps, so the two scrubbers leave the same project.
    // 删除后已不存在的容器：切换无论如何都是死重，整个删除——与 AI 删页
    // 保持同一规则，两个清理器产出相同的项目。
    if (survivingContainers && typeof a.container === "string" && a.container && !survivingContainers.has(a.container)) return true;
  }
  return a.action === "ui.navigate" && a.page === pageId;
}

/** A list of actions without its moves to the deleted page, and what each one
 *  left carries cleaned the same way: the same list when nothing changed.
 *  Whatever is not an action is kept. */
function actionsWithoutMoves(actions: unknown[], pageId: string, survivingContainers?: ReadonlySet<string>): unknown[] {
  const kept = filterPreserve(actions, (a) => !isMoveTo(a, pageId, survivingContainers));
  return mapPreserve(kept, (a) => actionWithoutMoves(a, pageId, survivingContainers));
}

/** An action that is not itself a move to the deleted page, with the moves
 *  dropped from what it carries: its off and long press actions (each removed
 *  when it was one) and a value_map's options (an option that was one is
 *  removed). The same action when it carried none. */
function actionWithoutMoves(action: unknown, pageId: string, survivingContainers?: ReadonlySet<string>): unknown {
  if (!isPlainObject(action)) return action;
  let next = action;
  for (const field of NESTED_ACTION_FIELDS) {
    const carried = own(next, field);
    if (carried === undefined) continue;
    if (isMoveTo(carried, pageId, survivingContainers)) {
      next = { ...next };
      delete next[field];
    } else {
      next = withField(next, field, (c) => actionWithoutMoves(c, pageId, survivingContainers));
    }
  }
  if (next.action === "value_map" && isPlainObject(next.map)) {
    next = withField(next, "map", (m) => mapWithoutMoves(m as Record<string, unknown>, pageId, survivingContainers));
  }
  return next;
}

/** A value_map's options without those that move to the deleted page. */
function mapWithoutMoves(map: Record<string, unknown>, pageId: string, survivingContainers?: ReadonlySet<string>): Record<string, unknown> {
  let changed = false;
  const next: Record<string, unknown> = {};
  for (const [option, action] of Object.entries(map)) {
    if (isMoveTo(action, pageId, survivingContainers)) { changed = true; continue; }
    const kept = actionWithoutMoves(action, pageId, survivingContainers);
    if (kept !== action) changed = true;
    next[option] = kept;
  }
  return changed ? next : map;
}

/** One action slot without its moves to the deleted page: the same value when
 *  it held none, undefined when it held nothing else. A slot holds a list or,
 *  in an older project, one action; a list keeps whatever else it holds. */
function slotWithoutMoves(raw: unknown, pageId: string, survivingContainers?: ReadonlySet<string>): unknown {
  if (!Array.isArray(raw)) return isMoveTo(raw, pageId, survivingContainers) ? undefined : actionWithoutMoves(raw, pageId, survivingContainers);
  const kept = actionsWithoutMoves(raw, pageId, survivingContainers);
  if (kept === raw) return raw;
  // A removed FIRST press action carried the slot's press mode (pressOf
  // reads it off the first entry): hand mode and repeat interval to the next
  // survivor, or a hold button quietly becomes a tap.
  // 被移除的首位按压动作携带该槽位的按压模式（pressOf 从首条读取）：把模式
  // 与重复间隔交给下一个幸存者，否则 hold 按钮悄然变成 tap。
  if (
    kept.length > 0 &&
    Array.isArray(raw) &&
    isPlainObject(raw[0]) &&
    kept[0] !== raw[0] &&
    typeof (raw[0] as Record<string, unknown>).mode === "string" &&
    isPlainObject(kept[0])
  ) {
    const removed = raw[0] as Record<string, unknown>;
    const survivor = kept[0] as Record<string, unknown>;
    if (!survivor.mode) survivor.mode = removed.mode;
    if (removed.hold_repeat_ms !== undefined && survivor.hold_repeat_ms === undefined) {
      survivor.hold_repeat_ms = removed.hold_repeat_ms;
    }
  }
  return kept.length > 0 ? kept : undefined;
}

/** An element's `do` slots without their moves to the deleted page, every
 *  slot the map holds (the panel runs whichever interaction it is sent), a
 *  slot left empty removed. */
function withoutMovesTo<T extends UIElement | MasterElement>(el: T, pageId: string, survivingContainers?: ReadonlySet<string>): T {
  const bindings = el.bindings as Record<string, unknown> | undefined;
  const doMap = bindings?.do as Record<string, unknown> | undefined;
  if (!doMap || typeof doMap !== "object") return el;
  const nextDo: Record<string, unknown> = { ...doMap };
  let changed = false;
  for (const slot of Object.keys(doMap)) {
    const kept = slotWithoutMoves(nextDo[slot], pageId, survivingContainers);
    if (Object.is(kept, nextDo[slot])) continue;
    changed = true;
    if (kept === undefined) delete nextDo[slot];
    else nextDo[slot] = kept;
  }
  return changed
    ? { ...el, bindings: { ...bindings, do: nextDo } as UIElement["bindings"] }
    : el;
}

/** A matrix's rows without their own route's moves to the deleted page. A row
 *  whose route held nothing else keeps an empty one, which the panel reads as
 *  a row made to do nothing; taking the route away would hand the row to the
 *  matrix's own route instead. Only a list of rows is written; a generated
 *  axis has no route of its own. */
function rowsWithoutMoves<T extends UIElement | MasterElement>(el: T, pageId: string, survivingContainers?: ReadonlySet<string>): T {
  if (el.type !== "matrix") return el;
  const record = el as unknown as Record<string, unknown>;
  return withField(record, "matrix_config", (config) => {
    if (!isPlainObject(config) || !Array.isArray(config.destinations)) return config;
    return withField(config, "destinations", (rows) => mapPreserve(rows as unknown[], (row) => (
      isPlainObject(row) && Array.isArray(row.route)
        ? withField(row, "route", (route) => actionsWithoutMoves(route as unknown[], pageId, survivingContainers))
        : row
    )));
  }) as unknown as T;
}

/** A control with what pointed at the deleted page cleared: a nav's target,
 *  and a container's default when the page deleted was the sub-page it opened
 *  with (the container then starts empty, like one never given a default).
 *  清除控件上指向被删除页面的东西：导航的目标页，以及容器在删除页恰是其
 *  初始子页面时的 default_page（容器随后从空白开始，如同从未设置过默认页）。 */
function controlWithoutPage<T extends UIElement | MasterElement>(el: T, pageId: string, survivingContainers?: ReadonlySet<string>): T {
  let untargeted = el;
  if (el.type === "page_nav" && el.target_page === pageId) {
    untargeted = { ...el, target_page: "" };
  }
  if (el.type === "subpage_container" && (el as UIElement).default_page === pageId) {
    untargeted = { ...untargeted, default_page: "" };
  }
  return rowsWithoutMoves(withoutMovesTo(untargeted, pageId, survivingContainers), pageId, survivingContainers);
}

/** The pages without the deleted one, each control's links to it cleared.
 *  Every page and element list comes back new. */
export function removePage(pages: UIPage[], pageId: string, survivingContainers?: ReadonlySet<string>): UIPage[] {
  return pages
    .filter((p) => p.id !== pageId)
    .map((p) => ({
      ...p,
      elements: p.elements.map((el) => controlWithoutPage(el, pageId, survivingContainers)),
    }));
}

/** A master without the deleted page in its page list (one left with no page
 *  shows on every page, "*"), and its links to the page cleared as a
 *  control's are. */
function masterWithoutPage(m: MasterElement, pageId: string, survivingContainers?: ReadonlySet<string>): MasterElement {
  let next = m;
  if (Array.isArray(m.pages)) {
    const kept = filterPreserve(m.pages, (pid) => pid !== pageId);
    if (kept !== m.pages) next = { ...m, pages: kept.length > 0 ? kept : "*" };
  }
  return controlWithoutPage(next, pageId, survivingContainers);
}

/** A trigger's conditions without those that matched on the deleted page
 *  being the current one, whatever their operator. */
function conditionsWithoutPage(conditions: TriggerCondition[], pageId: string): TriggerCondition[] {
  return filterPreserve(conditions, (c) => !(c.key === "system.current_page" && c.value === pageId));
}

/** Macro steps without those that move to the deleted page, their branches
 *  included. */
function stepsWithoutPage(steps: MacroStep[], pageId: string, survivingContainers?: ReadonlySet<string>): MacroStep[] {
  const kept = filterPreserve(steps, (step) => !isMoveTo(step as unknown as Record<string, unknown>, pageId, survivingContainers));
  const branch = (s: MacroStep[]) => stepsWithoutPage(s, pageId, survivingContainers);
  return mapPreserve(kept, (step) => withField(withField(step, "then_steps", branch), "else_steps", branch));
}

function macroWithoutPage(macro: MacroConfig, pageId: string, survivingContainers?: ReadonlySet<string>): MacroConfig {
  const next = withField(macro, "steps", (steps) => stepsWithoutPage(steps, pageId, survivingContainers));
  return withField(next, "triggers", (triggers) => mapPreserve(
    triggers,
    (t) => withField(t, "conditions", (cs) => conditionsWithoutPage(cs, pageId)),
  ));
}

/** The page groups without the deleted page: the same array back when no
 *  group held it. */
export function removePageFromGroups(groups: PageGroup[], pageId: string): PageGroup[] {
  return mapPreserve(groups, (group) => withField(group, "pages", (pages) => filterPreserve(pages, (pid) => pid !== pageId)));
}

export function removePageAndScrubRefs(
  pages: UIPage[],
  pageId: string,
  masterElements: MasterElement[],
  macros: MacroConfig[],
): {
  pages: UIPage[];
  masterElements: MasterElement[];
  macros: MacroConfig[];
} {
  // The containers that SURVIVE the deletion (the other pages' and the
  // masters'): a switch naming a container outside this set is dropped whole,
  // even when its sub-page survives -- the same post-state rule the AI's page
  // delete keeps, so the two scrubbers leave the same project.
  // 删除后仍然存活的容器（其余页面与 master 的）：指名集合之外容器的切换
  // 动作整个删除——即使其子页面还在。与 AI 删页同一"删除后状态"规则，两个
  // 清理器产出相同的项目。
  const survivingContainers = new Set(
    [
      ...pages.filter((p) => p.id !== pageId).flatMap((p) => p.elements ?? []),
      ...masterElements,
    ]
      .filter((el) => el.type === "subpage_container")
      .map((el) => el.id),
  );
  return {
    pages: removePage(pages, pageId, survivingContainers),
    masterElements: mapPreserve(masterElements, (m) => masterWithoutPage(m, pageId, survivingContainers)),
    macros: mapPreserve(macros, (m) => macroWithoutPage(m, pageId, survivingContainers)),
  };
}
