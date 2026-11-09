/**
 * When a control is renamed or a page deleted, what the rest of the project
 * says about it follows.
 *
 * A control's id is held by the element itself, its children's `parent`, and
 * each arrangement's placements and hidden list; its `ui.<id>.` state keys by
 * `key`, `state_key` and `source_key` fields in bindings and a step's params,
 * by a macro step's own key and conditions (its branches included), by a
 * trigger's state key and conditions, and by a variable's source key. Each
 * holder's list is one function here (`renamedElement`, `renamedStep`,
 * `renamedTrigger`, `renamedVariable`, `renamedInLayout`), so a new place a key
 * can sit is one line in its holder's function.
 *
 * Deleting a page empties a page nav's target on it, drops the moves to it
 * from each action slot of a page's controls, takes it out of each master's
 * page list, and drops the trigger conditions that matched on it being the
 * current page (`removePageAndScrubRefs`).
 *
 * Every array and object nothing in it changed comes back as it was
 * (`mapPreserve`, `withField`): the IDE's undo history snapshots only the
 * sections a change hands back new.
 */
import type {
  Layout,
  MacroConfig,
  MacroStep,
  MasterElement,
  Placement,
  ScriptConfig,
  StepCondition,
  TriggerCondition,
  TriggerConfig,
  UIElement,
  UIPage,
  VariableConfig,
} from "../../api/types";
import { ACTION_SLOTS } from "./bindingActions";

/** The same array back when `fn` handed back every item as it was; otherwise
 *  the new one. */
export function mapPreserve<T>(arr: T[], fn: (item: T) => T): T[] {
  const next = arr.map(fn);
  return sameItems(next, arr) ? arr : next;
}

function sameItems<T>(after: T[], before: T[]): boolean {
  return after.every((item, i) => item === before[i]);
}

/** The holder with one field replaced by what `rewrite` makes of it, or the
 *  holder itself when that is the same value. A field that is not set is left
 *  alone. */
export function withField<T extends object, K extends keyof T>(
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
  return STATE_KEY_FIELDS.has(field) ? renamedStateKey(value, rename) : value;
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
  next = withField(next, "params", (p) => renamedRefsDeep(p, rename) as Record<string, unknown>);
  next = withField(next, "condition", guard);
  next = withField(next, "skip_if", guard);
  next = withField(next, "then_steps", branch);
  return withField(next, "else_steps", branch);
}

/** A trigger with its state key and its conditions' keys renamed. */
function renamedTrigger(trigger: TriggerConfig, rename: IdRename): TriggerConfig {
  const key = (k: string) => renamedStateKey(k, rename);
  const next = withField(trigger, "state_key", key);
  return withField(next, "conditions", (cs) => mapPreserve(cs, (c) => withField(c, "key", key)));
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
  if (!!layout.placements && oldId in layout.placements) {
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

/** Whether an action is a move to the page being deleted. */
function isMoveTo(action: unknown, pageId: string): boolean {
  if (!action || typeof action !== "object") return false;
  const a = action as Record<string, unknown>;
  return a.action === "ui.navigate" && a.page === pageId;
}

/** One action slot without its moves to the deleted page: the same value when
 *  it held none, undefined when it held nothing else. A slot holds a list or,
 *  in an older project, one action; a list keeps whatever else it holds. */
function slotWithoutMoves(raw: unknown, pageId: string): unknown {
  if (!Array.isArray(raw)) return isMoveTo(raw, pageId) ? undefined : raw;
  const kept = raw.filter((a) => !isMoveTo(a, pageId));
  if (kept.length === raw.length) return raw;
  return kept.length > 0 ? kept : undefined;
}

/** An element's `do` slots without their moves to the deleted page, a slot
 *  left empty removed. Only the slots' own actions: a value_map's options and
 *  an off or hold action are not looked into. */
function withoutMovesTo(el: UIElement, pageId: string): UIElement {
  const bindings = el.bindings as Record<string, unknown> | undefined;
  const doMap = bindings?.do as Record<string, unknown> | undefined;
  if (!doMap) return el;
  const nextDo: Record<string, unknown> = { ...doMap };
  let changed = false;
  for (const slot of ACTION_SLOTS) {
    const kept = slotWithoutMoves(nextDo[slot], pageId);
    if (Object.is(kept, nextDo[slot])) continue;
    changed = true;
    if (kept === undefined) delete nextDo[slot];
    else nextDo[slot] = kept;
  }
  return changed
    ? { ...el, bindings: { ...bindings, do: nextDo } as UIElement["bindings"] }
    : el;
}

/** A page's control with what pointed at the deleted page cleared. */
function controlWithoutPage(el: UIElement, pageId: string): UIElement {
  const untargeted = el.type === "page_nav" && el.target_page === pageId
    ? { ...el, target_page: "" }
    : el;
  return withoutMovesTo(untargeted, pageId);
}

/** The pages without the deleted one, each control's links to it cleared.
 *  Every page and element list comes back new. */
export function removePage(pages: UIPage[], pageId: string): UIPage[] {
  return pages
    .filter((p) => p.id !== pageId)
    .map((p) => ({
      ...p,
      elements: p.elements.map((el) => controlWithoutPage(el, pageId)),
    }));
}

/** A master without the deleted page in its page list; one left with no page
 *  shows on every page ("*"). */
function masterWithoutPage(m: MasterElement, pageId: string): MasterElement {
  if (!Array.isArray(m.pages)) return m;
  const kept = m.pages.filter((pid) => pid !== pageId);
  if (kept.length === m.pages.length) return m;
  return { ...m, pages: kept.length > 0 ? kept : "*" };
}

/** A trigger's conditions without those that matched on the deleted page
 *  being the current one, whatever their operator. */
function conditionsWithoutPage(conditions: TriggerCondition[], pageId: string): TriggerCondition[] {
  const kept = conditions.filter((c) => !(c.key === "system.current_page" && c.value === pageId));
  return kept.length === conditions.length ? conditions : kept;
}

function macroWithoutPage(macro: MacroConfig, pageId: string): MacroConfig {
  return withField(macro, "triggers", (triggers) => mapPreserve(
    triggers,
    (t) => withField(t, "conditions", (cs) => conditionsWithoutPage(cs, pageId)),
  ));
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
  return {
    pages: removePage(pages, pageId),
    masterElements: mapPreserve(masterElements, (m) => masterWithoutPage(m, pageId)),
    macros: mapPreserve(macros, (m) => macroWithoutPage(m, pageId)),
  };
}
