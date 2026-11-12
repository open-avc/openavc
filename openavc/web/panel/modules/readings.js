/**
 * Whether a control has a reading to show: the one rule the dropdown, the
 * text field, the gauge and the level meter answer it by.
 *
 * A value that is not there (none reported yet, or the key deleted) and a
 * device the panel cannot reach are the same to a control: it draws its
 * placeholder, never the last reading it showed and never a zero. The slider
 * and the fader keep their own form in level.js, where a value that is not a
 * number is no reading either.
 */

/** No value to show: none reported, or a device that cannot be reached. */
export function nothingToShow(value, offline) {
    return offline || value === undefined || value === null;
}
