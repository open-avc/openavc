/**
 * Whether a control has a reading to show: the one rule every control
 * answers it by (the dropdown, the text field, the label, the list, the
 * status LED, and the gauge, slider, fader and level meter).
 *
 * A value that is not there (none reported yet, or the key deleted) and a
 * device the panel cannot reach are the same to a control: it draws its
 * placeholder, never the last reading it showed and never a zero. A control
 * that shows a number (the gauge, slider, fader and meter) also has nothing
 * to show for a reading that does not spell one: a word, a blank, a boolean.
 * What the placeholder is stays each control's own: "--" with a unit, an
 * empty arc, no lit segment, the design canvas's sample on a slider or fader.
 */

/**
 * What reads as a number: the spelling the platform's monitors accept
 * (core/monitors.py `as_number`, monitorHelpers.ts `asNumber`), narrower
 * than Number(), which reads "0x10" as 16, a blank as 0 and true as 1.
 */
const NUMERIC = /^[+-]?(\d+(?:\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

/** No value to show: none reported, or a device that cannot be reached. */
export function nothingToShow(value, offline) {
    return offline || value === undefined || value === null;
}

/**
 * A value as a number, or null when it is not one: a finite number, or text
 * that spells one, spaces around it allowed. Never a boolean.
 */
export function asNumber(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value === 'string' && NUMERIC.test(value.trim())) return Number(value.trim());
    return null;
}

/**
 * The number a control that shows one draws for a reading, or null when it
 * has nothing to show: no reading (nothingToShow), or one that is not a
 * number.
 */
export function numberToShow(value, offline) {
    return nothingToShow(value, offline) ? null : asNumber(value);
}
