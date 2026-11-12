/**
 * The dropdown and the text field: a value a person picks or types, sent as
 * `ui.change` once they stop (`quietSender`: the dropdown after 100 ms, the
 * text field after 300), and the value the device reports shown in it.
 *
 * Shown or not, the rule for a reading is the same in both: a value that is
 * not there (no reading yet, the key deleted) or a device that cannot be
 * reached shows nothing at all, never the last value or the first option.
 *
 * The panel draws each control's element and caption and registers its
 * bindings; what is drawn here takes plain values, never a definition, so
 * nothing here is credited to either control by the page review's renderer
 * scan (tests/test_ui_page_review_mirrors.py).
 */

const SELECT_QUIET_MS = 100;
const TEXT_QUIET_MS = 300;

/** Send `ui.change` with what `read()` gives, once `ms` pass with no new call. */
function quietSender(host, elementId, ms, read) {
    let pending = null;
    return () => {
        if (pending) clearTimeout(pending);
        pending = setTimeout(() => {
            host.send({ type: 'ui.change', element_id: elementId, value: read() });
        }, ms);
        host.keepTimer(pending);
    };
}

/** No value to show: none reported, or a device that cannot be reached. */
function nothingToShow(value, offline) {
    return offline || value === undefined || value === null;
}

/**
 * The dropdown's <select>: an option per entry, each in the colours its
 * value has in `styleMap` (show.look's, from the Builder's Appearance card;
 * an open list shows them where the browser styles native options).
 */
export function drawSelect(host, elementId, options, styleMap) {
    const select = document.createElement('select');
    for (const opt of options) {
        const option = document.createElement('option');
        option.value = opt.value;
        option.textContent = opt.label;
        const optStyle = styleMap && styleMap[opt.value];
        if (optStyle) {
            if (optStyle.bg_color) option.style.backgroundColor = optStyle.bg_color;
            if (optStyle.text_color) option.style.color = optStyle.text_color;
        }
        select.appendChild(option);
    }
    select.addEventListener('change', quietSender(host, elementId, SELECT_QUIET_MS, () => select.value));
    return select;
}

/** No selection at all rather than a stale one or the first option: a
 *  dropdown reading "HDMI 2" for a switcher nobody can reach is a confident
 *  wrong answer, and so is "HDMI 1" for one that has not said yet. */
export function showSelectValue(select, value, offline) {
    if (nothingToShow(value, offline)) {
        select.selectedIndex = -1;
        return;
    }
    select.value = String(value);
}

/** The dropdown in the colours of the option its key's value matches, and in
 *  the theme's when nothing matches. Both colours are written every time, so
 *  a previous match never lingers. */
export function showSelectLook(select, styleMap, value, offline) {
    const matched = nothingToShow(value, offline) ? undefined : styleMap[String(value)];
    select.style.backgroundColor = (matched && matched.bg_color) || '';
    select.style.color = (matched && matched.text_color) || '';
}

/** The text field's <input>. */
export function drawTextInput(host, elementId, placeholder) {
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = placeholder;
    input.addEventListener('input', quietSender(host, elementId, TEXT_QUIET_MS, () => input.value));
    return input;
}

/** Empty rather than a value that is no longer coming from anywhere. (While
 *  the person is typing in it the panel shows nothing new at all.) */
export function showTextValue(input, value, offline) {
    if (nothingToShow(value, offline)) {
        input.value = '';
        return;
    }
    input.value = String(value);
}
