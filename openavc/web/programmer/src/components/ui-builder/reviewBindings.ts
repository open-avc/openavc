/**
 * What an element's type and its `show` bindings give the panel to draw.
 *
 * A type the panel has no renderer for, a page drawing its own markup (which
 * draws none of its controls), a binding slot the type's renderer never reads,
 * per-state words, icons or pictures on a type that draws none, and a read
 * binding with no key. What each type reads is generated from panel.js
 * (uiBindingReach.gen.ts).
 *
 * Mirrors "Binding findings" in openavc/ui/page_review.py, from
 * `element_type_finding` to `_keyless_slot_findings`, and `custom_page_findings`,
 * which Python keeps in its masters section; the property, content, keypad and
 * matrix findings Python also files under "Binding findings" are
 * reviewProperties.ts and reviewMatrix.ts.
 */

import type { UIElement, UIPage } from "../../api/types";
import {
  HONORED_PROPERTIES,
  HONORED_SHOW_SLOTS,
  REVIEWED_SHOW_SLOTS,
  STATE_ICON_TYPES,
  STATE_IMAGE_TYPES,
  STATE_LABEL_TYPES,
} from "../../api/uiBindingReach.gen";
import { isPlainObject, own } from "./authoredValues";
import { showOf, type ReviewFinding } from "./reviewFinding";

/** Every type the panel has a renderer for.
 *
 *  Read off the generated binding-reach table rather than listed again: a test
 *  re-derives that table's keys from the panel's renderer table (`RENDERERS` in
 *  openavc/web/panel/modules/dispatch.js), so this IS the renderer's own set. */
const RENDERED_TYPES = new Set(Object.keys(HONORED_SHOW_SLOTS));

/**
 * A type the panel has no renderer for, which draws nothing at all.
 *
 * `UIElement.type` is a free-form string and every layer downstream treats it as
 * one: the loader accepts anything, this review used to return early on a type it
 * did not know, and `renderElement`'s switch falls through to a `console.warn`
 * and returns null. So the element has an id, a placement and bindings, the write
 * comes back created, and it is simply absent from the screen.
 *
 * The message lists the whole set on purpose -- it is the only place an author
 * who guessed is told what the alternatives are.
 */
/**
 * What a page that draws its own markup is doing with the controls on it.
 *
 * Two ways this goes wrong, and neither has any geometry to look at.
 *
 * A page set to draw its own markup keeps every control that was ever placed on
 * it -- deliberately, so switching back restores the page rather than asking
 * somebody to rebuild it. But the panel draws none of them, so every other
 * finding about them would be advice about pixels nobody will see, and a clean
 * review would read as "those controls are fine".
 *
 * And a page set to draw its own markup that names no file draws its controls
 * after all, which looks from the file like the switch did nothing. That is the
 * same shape as an image with no `src`.
 */
export function customPageFindings(page: UIPage): ReviewFinding[] {
  if (page.render_mode !== "custom") return [];
  const count = page.elements?.length ?? 0;
  if (!page.custom_file) {
    return [{
      elementId: page.id,
      kind: "custom_page_without_a_file",
      message:
        `${page.id} is set to show a page you wrote but names no file, so it still ` +
        `draws its controls. Choose a file in the project's ui/ folder, or set the ` +
        `page back to controls.`,
      key: `custom_page_without_a_file|${page.id}`,
    }];
  }
  if (!count) return [];
  const controls = count === 1 ? "control" : "controls";
  const them = count === 1 ? "it" : "them";
  return [{
    elementId: page.id,
    kind: "custom_page_elements_not_drawn",
    message:
      `${page.id} shows ${page.custom_file}, so the ${count} ${controls} on it ` +
      `${count === 1 ? "is" : "are"} not drawn. Move ${them} to another page, or set ` +
      `the page back to controls to show ${them} again.`,
    key: `custom_page_elements_not_drawn|${page.id}`,
  }];
}

export function elementTypeFinding(el: UIElement): ReviewFinding | null {
  if (RENDERED_TYPES.has(el.type)) return null;
  return {
    elementId: el.id,
    kind: "unknown_element_type",
    message:
      `${el.id} has type '${el.type}', which the panel has no renderer for, so it draws ` +
      `nothing at all. The types are: ${[...RENDERED_TYPES].sort().join(", ")}.`,
    key: `unknown_element_type|${el.id}`,
  };
}

/** One element's `show` bindings, beside what its type's renderer reads. */
interface Shown {
  el: UIElement;
  honored: readonly string[];
  show: Record<string, unknown>;
}

function shownBindings(el: UIElement): Shown | null {
  const honored = own(HONORED_SHOW_SLOTS, el.type);
  if (!honored) return null; // a type this module has never heard of; say nothing
  const show = showOf(el);
  if (!show) return null;
  return { el, honored, show };
}

/** `show.look` when this type reads one at all, else null. */
function shownLook(shown: Shown): Record<string, unknown> | null {
  const look = shown.show.look;
  return shown.honored.includes("look") && isPlainObject(look) ? look : null;
}

/** The sentence for a per-state setting this type takes nothing of from show.look.
 *
 *  `setting` continues `show.look`: ` sets an icon for on, off`. */
function lookDropped(shown: Shown, setting: string, noun: string, remedy: string): string {
  const { el } = shown;
  return (
    `${el.id} (${el.type}): show.look${setting}, but ${withArticle(el.type)} takes only ` +
    `${lookTakes(el.type)} from show.look, so that ${noun} never appears. ${remedy}`
  );
}

/**
 * Bindings this element type's renderer never reads.
 *
 * Nothing rejects these and nothing logs them at runtime: the element draws, the
 * state key resolves, and the binding simply has no code path behind it for this
 * type. From the outside it is indistinguishable from a value that never
 * changes -- and the Builder does not even offer an editor for the slot, so a
 * binding written by anything else is invisible until it is badged.
 */
export function bindingFindings(el: UIElement): ReviewFinding[] {
  const shown = shownBindings(el);
  if (!shown) return [];
  return [
    ...unreadSlotFindings(shown),
    ...lookLabelFindings(shown),
    ...lookFieldFindings(shown),
    ...keylessSlotFindings(shown),
  ];
}

/** A whole show slot this type has no code path for. */
function unreadSlotFindings({ el, honored, show }: Shown): ReviewFinding[] {
  const reads =
    REVIEWED_SHOW_SLOTS.filter((slot) => honored.includes(slot))
      .map((slot) => `show.${slot}`)
      .join(", ") || "nothing from show but show.visible_when";
  const findings: ReviewFinding[] = [];
  for (const slot of REVIEWED_SHOW_SLOTS) {
    if (!show[slot] || honored.includes(slot)) continue;
    const extra =
      slot === "look" && honored.includes("value")
        ? " Put whatever depended on it into show.value (a condition with " +
          "text_true / text_false), or use a button."
        : "";
    findings.push({
      elementId: el.id,
      kind: "binding_not_rendered",
      message:
        `${el.id} (${el.type}) declares show.${slot}, which ${withArticle(el.type)} does not render ` +
        `-- it reads ${reads}. That binding has no effect.${extra}`,
      key: `binding_not_rendered|${el.id}|${slot}`,
    });
  }
  return findings;
}

/**
 * Per-state words on a type that takes only colour from a look.
 *
 * A look binding can be honored for its colour while its state labels are not:
 * a status LED tints its dot, a select styles its options, an image swaps its
 * picture, and none of them has anywhere to put text. A label that is present
 * counts even when it is empty: the renderer acts on the field.
 */
function lookLabelFindings(shown: Shown): ReviewFinding[] {
  const look = shownLook(shown);
  const { el } = shown;
  if (!look || STATE_LABEL_TYPES.includes(el.type)) return [];
  const states = look.states;
  if (!isPlainObject(states)) return [];
  const labelled = Object.entries(states)
    .filter(([, spec]) => isPlainObject(spec) && (spec.label ?? null) !== null)
    .map(([name]) => name)
    .sort();
  if (!labelled.length) return [];
  const remedy =
    own(STATE_TEXT_REMEDY, el.type) ??
    "Use the element's own label, or a label element bound to the same key.";
  return [{
    elementId: el.id,
    kind: "binding_not_rendered",
    message: lookDropped(shown, `.states sets a label for ${labelled.join(", ")}`, "text", remedy),
    key: `binding_not_rendered|${el.id}|look.states.label`,
  }];
}

/** A per-state look field only some types draw, and what to say when one is dropped. */
interface LookField {
  field: string;
  drawnBy: readonly string[];
  /** How the sentence introduces one: `an icon`. */
  named: string;
  /** How it refers back to it: `icon`. */
  noun: string;
  /** What to do instead, from the element's type. */
  remedy: (type: string) => string;
}

function iconRemedy(type: string): string {
  return (own(HONORED_PROPERTIES, type) ?? []).includes("icon")
    ? "Set the element's own icon -- it draws that one, it just cannot change it per state."
    : "Use a button, which does draw one.";
}

/**
 * Content a look can set per state, and the only types that draw it. A look can
 * be honored for its colour AND its text while a per-state icon is still
 * dropped, because an icon is content rather than style and only the button's
 * evaluator rebuilds it; a per-state picture is drawn only by the types in
 * STATE_IMAGE_TYPES, and a label, a status LED or a select stores one and never
 * shows it. A new per-state field the panel draws for some types is a row here,
 * beside its own STATE_* type set (generated from page_review.py) and its word
 * in lookTakes.
 */
const LOOK_FIELDS: readonly LookField[] = [
  { field: "icon", drawnBy: STATE_ICON_TYPES, named: "an icon", noun: "icon", remedy: iconRemedy },
  {
    field: "button_image",
    drawnBy: STATE_IMAGE_TYPES,
    named: "a picture (button_image)",
    noun: "picture",
    remedy: () => "Use an image element bound to the same key.",
  },
];

/** Each per-state field in LOOK_FIELDS set on a type that does not draw it. */
function lookFieldFindings(shown: Shown): ReviewFinding[] {
  const look = shownLook(shown);
  if (!look) return [];
  const { el } = shown;
  const findings: ReviewFinding[] = [];
  for (const spec of LOOK_FIELDS) {
    if (spec.drawnBy.includes(el.type)) continue;
    const names = lookStatesNaming(look, spec.field);
    if (!names.length) continue;
    findings.push({
      elementId: el.id,
      kind: "binding_not_rendered",
      message: lookDropped(
        shown, ` sets ${spec.named} for ${names.join(", ")}`, spec.noun, spec.remedy(el.type),
      ),
      key: `binding_not_rendered|${el.id}|look.${spec.field}`,
    });
  }
  return findings;
}

/** Whether a show binding names what it reads: its state key, or its key pattern
 *  for `items`. A value shown from a macro's progress reads neither. */
function namesItsSource(slot: string, binding: Record<string, unknown>): boolean {
  if (slot === "value" && binding.source === "macro_progress") return true;
  const field = slot === "items" ? "key_pattern" : "key";
  return Boolean(String(binding[field] ?? "").trim());
}

/**
 * A binding the renderer DOES read, that names nothing to read.
 *
 * It is the half-finished state every one of these passes through -- the editor
 * writes the binding when the source is chosen and the key a moment later -- and
 * left that way it is worse than no binding at all, because every other check
 * here treats a present binding as a supplied one and goes quiet.
 */
function keylessSlotFindings({ el, honored, show }: Shown): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const slot of REVIEWED_SHOW_SLOTS) {
    const binding = show[slot];
    if (!honored.includes(slot) || !isPlainObject(binding)) continue;
    if (namesItsSource(slot, binding)) continue;
    const named = slot === "items" ? "key pattern" : "state key";
    findings.push({
      elementId: el.id,
      kind: "binding_without_key",
      message:
        `${el.id} (${el.type}): show.${slot} has no ${named}, so it reads nothing and ` +
        `never updates. ${UNBOUND_CONSEQUENCE[slot]} Pick the ${named} it should ` +
        `read, or remove the binding.`,
      key: `binding_without_key|${el.id}|${slot}`,
    });
  }
  return findings;
}

/**
 * Which states of a `show.look` name `field` (an icon, a picture), in both
 * spellings.
 *
 * The multi-state form carries one appearance per state; the binary form
 * carries two, `style_active` and `style_inactive`. Both are offered by the
 * same editor, so both reach the file. Mirrors _look_states_naming in
 * page_review.py.
 */
function lookStatesNaming(look: Record<string, unknown>, field: string): string[] {
  const names = (spec: unknown) => isPlainObject(spec) && !!spec[field];
  const states = look.states;
  if (isPlainObject(states)) {
    return Object.entries(states)
      .filter(([, spec]) => names(spec))
      .map(([name]) => name)
      .sort();
  }
  return ([["active", "style_active"], ["inactive", "style_inactive"]] as const)
    .filter(([, side]) => names(look[side]))
    .map(([half]) => half);
}

/** What a type draws from `show.look`, in words. Mirrors _look_takes in page_review.py. */
function lookTakes(type: string): string {
  const parts = ["color"];
  if (STATE_LABEL_TYPES.includes(type)) parts.push("text");
  if (STATE_ICON_TYPES.includes(type)) parts.push("an icon");
  if (STATE_IMAGE_TYPES.includes(type)) parts.push("a picture");
  return parts.length === 1
    ? parts[0]
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** A type name with its article: "a button", "an image". Mirrors _a in page_review.py. */
function withArticle(type: string): string {
  return ["a", "e", "i", "o", "u"].includes(type.slice(0, 1)) ? `an ${type}` : `a ${type}`;
}

/**
 * Where to put per-state words instead, for a type with no words of its own to
 * change. Mirrors _STATE_TEXT_REMEDY in page_review.py.
 */
const STATE_TEXT_REMEDY: Record<string, string> = {
  image: "Place a label element bound to the same key over it.",
};

/**
 * What an author sees when a slot is bound to nothing, per slot. Mirrors
 * _UNBOUND_CONSEQUENCE in page_review.py.
 */
const UNBOUND_CONSEQUENCE: Record<string, string> = {
  value: "The control shows what an unset value looks like: blank text, or the low end of a range.",
  look: "Its appearance stays on whatever an unmatched state gives it.",
  items: "The list comes up empty.",
};
