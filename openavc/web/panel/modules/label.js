/**
 * What a label's words say: a bound value's sentence with the field an
 * in-place edit of it writes, a number as a label prints it, and rich text.
 *
 * A value is printed rounded to the label's decimals when it is a real
 * number, and set into its format at every `{value}`. A value nobody
 * reported, or one from a device that is away, keeps the sentence and loses
 * the number: "Amp draw: -- A" rather than a blank, or a stale 0.00 for an
 * amplifier drawing 0.076 A through a broken port. A text condition picks one
 * of two words instead. The panel keeps the node, the edit in flight and the
 * memo of what it last wrote (`evaluateText`, modules/text_edit.js).
 *
 * Plain functions over the binding and the reading: no DOM, no panel state,
 * and no control's definition (the caller hands over the decimals).
 */

import { sameWords } from './looks.js';

/** Where a bound value's words are authored. */
const VALUE = ['bindings', 'show', 'value'];
const FORMAT = [...VALUE, 'format'];
/** What a label shows in place of a number it does not have. */
const NO_READING = '--';

/**
 * The words a label's value binding draws for `value`, as [text, the field
 * an edit of them writes]. The field is null where what is drawn is not
 * authored anywhere: a bare value, or the "--" an unreachable device earns.
 * `offline` is whether the device behind the key is away; `decimals` the
 * label's rounding, or null.
 */
export function valueWords(binding, value, offline, decimals) {
    if (offline) return withoutReading(binding);
    if (binding.condition) return conditionWords(binding, value);
    if (value === undefined || value === null) return withoutReading(binding);
    const shown = printedValue(value, decimals);
    // A bare value with no format around it is not authored text. The editor
    // opens it seeded with the literal `{value}` instead, so typing a
    // sentence around it CREATES the format.
    return binding.format ? [filled(binding.format, shown), FORMAT] : [shown, null];
}

/** The sentence without its number, or "--" where there is no sentence. */
function withoutReading(binding) {
    return binding.format ? [filled(binding.format, NO_READING), FORMAT] : [NO_READING, null];
}

/**
 * A text condition's word: `text_true` when the reading's words equal
 * `equals` apart from case, so a numeric 1 or a boolean true matches an
 * equals of '1' or 'true'; `text_false` otherwise, a missing or null reading
 * included.
 */
function conditionWords(binding, value) {
    const isMatch = value !== undefined && value !== null &&
        sameWords(value, binding.condition.equals);
    return [
        isMatch ? (binding.text_true || '') : (binding.text_false || ''),
        [...VALUE, isMatch ? 'text_true' : 'text_false'],
    ];
}

/**
 * `format` with every `{value}` replaced by `text`. Split and joined rather
 * than String.replace, so a device value holding a $-sequence (a track
 * title, a path) is set in literally.
 */
function filled(format, text) {
    return String(format).split('{value}').join(text);
}

/**
 * A bound value as a label prints it. A device float arrives at float64
 * width, so a float32 reading of 0.06 comes across as 0.06000000238418579 and
 * a raw print is unreadable on a panel: `decimals` rounds it. Only real
 * numbers are touched: most labels are bound to text (device names, input
 * modes, firmware versions), and reformatting a version string of "2.10"
 * would be wrong.
 */
export function printedValue(value, decimals) {
    return decimals != null && isRealNumber(value) ? value.toFixed(decimals) : String(value);
}

/** A number that is neither NaN nor infinite. */
function isRealNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Rich text as markup: **bold** and *italic*. Everything in the text is
 * escaped before the two tags go in, and any other tag is taken out again,
 * so the only elements it can make are those two.
 */
export function richText(text) {
    let html = String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    return html.replace(/<(?!\/?(?:strong|em)>)[^>]*>/gi, '');
}
