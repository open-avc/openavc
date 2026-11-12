/**
 * What an element's own fields give it: something to draw, and a keypad that sends.
 *
 * A control with nothing to draw (no property, no binding standing in for it,
 * no words in its look), a property its type's renderer never reads, and a
 * keypad that never sends or sends a code before it is finished. The keypad
 * defaults are also the ones the Basic properties show.
 *
 * Mirrors `content_findings`, `property_findings` and `keypad_findings` in
 * openavc/ui/page_review.py.
 */

import type { UIElement } from "../../api/types";
import {
  HONORED_PROPERTIES,
  MATRIX_CONFIG_KEYS,
  STATE_LABEL_TYPES,
  STRUCTURAL_PROPERTIES,
} from "../../api/uiBindingReach.gen";
import { isPlainObject, own } from "./authoredValues";
import { joined, showOf, type ReviewFinding } from "./reviewFinding";

/**
 * Controls that are inert without one particular thing, and what to set.
 *
 * Not "required" -- almost nothing is, and a half-built page mid-drag should
 * not be scolded. These are the four where the element draws an empty box and
 * there is no reading under which that was the intent. The second entry is the
 * binding slot that can supply it instead, so a label bound to `show.value`
 * needs no `text`.
 *
 * Hand-mirrored from INERT_WITHOUT in page_review.py; the parity suite compares
 * the resulting sentences byte for byte, which is what keeps the two together.
 */
const INERT_WITHOUT: Record<string, [string, string | null, string]> = {
  image: ["src", null, "an asset ref ('assets://logo.png') or a URL"],
  page_nav: ["target_page", null, "the page id it should open, or '$back'"],
  label: ["text", "value", "the string to draw, or a show.value binding"],
  select: ["options", "items", "[{label, value}, ...] for the list of choices"],
  custom: ["custom_file", null, "a page in the project's ui/ folder, e.g. 'room_map/index.html'"],
};

/**
 * Whether a `show.look` puts TEXT on screen, rather than only colour.
 *
 * Two shapes do: a `states` map with a `label` on any entry, and the binary
 * form's `label_active` / `label_inactive`. A look carrying only colours
 * supplies none. Mirrors look_supplies_text in page_review.py.
 */
export function lookSuppliesText(look: unknown): boolean {
  if (!isPlainObject(look)) return false;
  return present(look.label_active) || present(look.label_inactive) || statesHaveWords(look.states);
}

/** Whether any state in a look's `states` carries words of its own. A list is
 *  read as states here, where the AI side reads none from it. */
function statesHaveWords(states: unknown): boolean {
  if (!states || typeof states !== "object") return false;
  return Object.values(states as Record<string, unknown>).some(
    (spec) => isPlainObject(spec) && present(spec.label),
  );
}

/** Neither missing nor null: Python's `is not None`, for a field read off JSON. */
function present(value: unknown): boolean {
  return value !== undefined && value !== null;
}

/**
 * A control with nothing to draw, which draws an empty box and says nothing.
 *
 * Named by a logo placeholder created as a master image and never given a src,
 * because no tool could set one. It stored, it took a placement, and it
 * rendered as nothing -- while the request that asked for it was reported done.
 */
export function contentFindings(el: UIElement): ReviewFinding[] {
  const rule = own(INERT_WITHOUT, el.type);
  if (!rule) return [];
  const [prop, slot, remedy] = rule;
  if (contentSupplied(el, prop, slot)) return [];
  const instead = slot ? `, and no show.${slot} to supply one` : "";
  return [{
    elementId: el.id,
    kind: "nothing_to_draw",
    message:
      `${el.id} (${el.type}) has no ${prop}${instead}, so it draws an empty box. ` +
      `Set ${prop} to ${remedy}.`,
    key: `nothing_to_draw|${el.id}`,
  }];
}

/**
 * Whether anything gives this element what it draws: the property itself, the
 * `show` slot that stands in for it (a label bound to `show.value` needs no
 * `text`), or, for a type that draws a look's words (STATE_LABEL_TYPES), a look
 * that has some. Warning about a state chip whose captions live in its look
 * taught authors to add a dead `text` to silence the check. Another source the
 * panel learns to draw from is one more line here, and in `_content_supplied`
 * in page_review.py.
 */
function contentSupplied(el: UIElement, prop: string, slot: string | null): boolean {
  if (isWritten((el as unknown as Record<string, unknown>)[prop])) return true;
  const show = showOf(el);
  if (!show) return false;
  if (slot && show[slot]) return true;
  return STATE_LABEL_TYPES.includes(el.type) && lookSuppliesText(show.look);
}

/** A property value an author wrote: not missing, not empty text, not an empty
 *  list. The AI side asks Python's truthiness instead, which also turns away 0,
 *  false and {}: values the project model refuses for every property in
 *  INERT_WITHOUT. */
function isWritten(value: unknown): boolean {
  if (!present(value) || value === "") return false;
  return !Array.isArray(value) || value.length > 0;
}

/** A value a full model dump carries for a field nobody set: not an authoring
 *  decision, so never worth a finding. `0` and `false` are decisions. */
function isUnset(value: unknown): boolean {
  return (
    value === null || value === undefined || value === "" ||
    (Array.isArray(value) && value.length === 0) ||
    (isPlainObject(value) && Object.keys(value).length === 0)
  );
}

/** Why a property this type does not read is there, said as the repair: the
 *  right idea one level too high on a matrix, the right idea on the wrong type,
 *  or an invented field. */
function unreadReason(type: string, name: string, honored: readonly string[]): string {
  if (type === "matrix" && MATRIX_CONFIG_KEYS.includes(name)) {
    return `'${name}' belongs inside matrix_config, not on the element.`;
  }
  const elsewhere = Object.entries(HONORED_PROPERTIES)
    .filter(([, props]) => props.includes(name))
    .map(([other]) => other)
    .sort();
  if (!elsewhere.length) return `No element type reads '${name}'.`;
  const why = `'${name}' is read by ${joined(elsewhere.map((t) => `\`${t}\``))}, not by a ${type}.`;
  return name === "label" && honored.includes("text") ? `${why} A label element draws \`text\`.` : why;
}

/**
 * Properties this element type's renderer never reads.
 *
 * The same silence as an unread binding: the field is declared on UIElement for
 * some other type (or none), the loader stores it, and no renderer has a line
 * that looks at it. `unreadReason` says which of those it is, because the repair
 * is different.
 */
export function propertyFindings(el: UIElement): ReviewFinding[] {
  const honored = own(HONORED_PROPERTIES, el.type);
  if (!honored) return []; // a type this module has never heard of; say nothing
  const reads = [...honored].sort().join(", ");
  return Object.entries(el as unknown as Record<string, unknown>)
    .filter(([name, value]) =>
      !honored.includes(name) && !STRUCTURAL_PROPERTIES.includes(name) && !isUnset(value))
    .map(([name]) => ({
      elementId: el.id,
      kind: "property_not_rendered",
      message:
        `${el.id} (${el.type}) sets '${name}', which a ${el.type} does not render. ` +
        `${unreadReason(el.type, name, honored)} A ${el.type} reads: ${reads}.`,
      key: `property_not_rendered|${el.id}|${name}`,
    }));
}

/**
 * The keypad's defaults, as the panel's keypad (modules/keypad.js) reads them. The delay is also
 * the review's threshold: nobody has measured how long a person takes between
 * two keys on a panel, so the one number with a claim to it is the default.
 * Hand-mirrored from page_review.py; the keypad's own properties show the same
 * two numbers as its defaults.
 */
export const KEYPAD_DIGITS_DEFAULT = 4;
export const KEYPAD_SEND_DELAY_DEFAULT_MS = 1500;

/** How a keypad sends, with the panel's defaults filled in the way the panel's keypad fills them. */
interface Keypad {
  id: string;
  /** The Phone style, which has no Enter key. */
  phone: boolean;
  /** Send without Enter. */
  autoSend: boolean;
  digits: unknown;
  /** Send Delay, in ms. */
  delay: unknown;
}

function keypadOf(el: UIElement): Keypad {
  return {
    id: el.id,
    phone: el.keypad_style === "phone",
    autoSend: Boolean(el.auto_send),
    digits: el.digits ?? KEYPAD_DIGITS_DEFAULT,
    delay: el.auto_send_delay_ms ?? KEYPAD_SEND_DELAY_DEFAULT_MS,
  };
}

/** Whether a pause after a key press sends what was typed: only with Send
 *  without Enter on, and only with more than one digit to wait for. */
function pauseSends(pad: Keypad): boolean {
  if (!pad.autoSend || !Number.isInteger(pad.digits)) return false;
  return (pad.digits as number) >= 2;
}

/** The Phone style has no Enter key, so with Send without Enter off nothing typed is sent. */
function neverSends(pad: Keypad): ReviewFinding | null {
  if (!pad.phone || pad.autoSend) return null;
  return {
    elementId: pad.id,
    kind: "keypad_never_sends",
    message:
      `${pad.id} (keypad) uses the Phone style, which has no Enter key, and ` +
      `Send without Enter is off, so nothing typed on it is ever sent. Turn on ` +
      `Send without Enter, or choose the Numeric style.`,
    key: `keypad_never_sends|${pad.id}`,
  };
}

/** A Send Delay under the default can send a code before the last digit is typed. */
function sendsEarly(pad: Keypad): ReviewFinding | null {
  if (!pauseSends(pad) || typeof pad.delay !== "number") return null;
  if (pad.delay >= KEYPAD_SEND_DELAY_DEFAULT_MS) return null;
  return {
    elementId: pad.id,
    kind: "keypad_sends_early",
    message:
      `${pad.id} (keypad) sends what has been typed ${pad.delay} ms after the last ` +
      `key press, shorter than the ${KEYPAD_SEND_DELAY_DEFAULT_MS} ms default, so a ` +
      `code can be sent before it is finished. Set Send Delay to ` +
      `${KEYPAD_SEND_DELAY_DEFAULT_MS} ms or more; an entry of ${pad.digits} digits is sent ` +
      `as soon as the last one is pressed.`,
    key: `keypad_sends_early|${pad.id}`,
  };
}

/** A keypad's warnings, in the order they are said. The two cannot both fire:
 *  one needs Send without Enter off and the other needs it on. */
const KEYPAD_CHECKS: ((pad: Keypad) => ReviewFinding | null)[] = [neverSends, sendsEarly];

/**
 * A keypad that never sends, or sends a code before it is finished.
 *
 * With `auto_send` the panel sends at `digits` keys AND after
 * `auto_send_delay_ms` with no key press. The phone layout has no Enter key, so
 * with `auto_send` off it cannot send at all; a delay under the default can
 * send after a PIN's first digit. With one digit the delay never runs. Mirrors
 * keypad_findings in page_review.py, pinned sentence for sentence.
 */
export function keypadFindings(el: UIElement): ReviewFinding[] {
  if (el.type !== "keypad") return [];
  const pad = keypadOf(el);
  return KEYPAD_CHECKS.map((check) => check(pad)).filter((f): f is ReviewFinding => f !== null);
}
