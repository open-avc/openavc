/**
 * Which appearance a look binding (`bindings.show.look`) shows for a state
 * value, where the words it draws were authored, and every picture its
 * entries name.
 *
 * Every control with a look picks its entry here (a button's feedback, a
 * label, an image, a page nav, a container), so one state value picks the
 * same entry on all of them. What each control then draws from that entry is
 * its own business, in panel.js. No DOM and no panel state. A look's two
 * forms, `states` and the two halves, are read here (`pickLook`,
 * `lookPictures`, `withTwoStateWords`) and by a button's words (`lookWords`
 * in modules/button.js, which reads a two-state look's own word fields): a
 * new form is taught to both modules.
 *
 * Every caller hands this module a look binding, never a control's
 * definition, and that has to stay so: the page review's renderer scan
 * (tests/test_ui_page_review_mirrors.py) credits a module's whole text to
 * any control that hands it the element, and the picture `lookPictures`
 * reads would then count that control as one that draws a state's picture.
 */

const LOOK = ['bindings', 'show', 'look'];

/**
 * The entry a look shows for `stateValue`: `{ appearance, wordsPath }`, plus
 * `isActive` on the two-state form. `appearance` is `{}` where the look has
 * nothing for the value, and the control keeps its own look. `wordsPath` is
 * where an edit of the words the entry draws belongs.
 */
export function pickLook(binding, stateValue) {
    return binding.states ? pickState(binding, stateValue) : pickTwoState(binding, stateValue);
}

/**
 * A look with `states`: the value's words are matched exactly, case and all.
 * No value, or one no state names, takes `default_state`; a look without one
 * takes its blank state `''`. The words belong to the state the value
 * matched, even when that state's entry is empty and the default was drawn.
 */
function pickState(binding, stateValue) {
    const { states } = binding;
    const fallback = binding.default_state || '';
    const stateKey = stateValue != null ? String(stateValue) : fallback;
    const matched = states[stateKey] !== undefined ? stateKey : fallback;
    return {
        appearance: states[stateKey] || states[fallback] || {},
        wordsPath: [...LOOK, 'states', matched, 'label'],
    };
}

/**
 * The two-state form (`condition.equals`, `style_active`, `style_inactive`):
 * active when the value's words equal `equals` apart from case. A missing
 * value is inactive; null is compared as the word "null".
 */
function pickTwoState(binding, stateValue) {
    const condition = binding.condition || {};
    const isActive = stateValue !== undefined && sameWords(stateValue, condition.equals);
    const half = isActive ? 'style_active' : 'style_inactive';
    return {
        appearance: binding[half] || {},
        wordsPath: [...LOOK, half, 'label'],
        isActive,
    };
}

/** An entry a look can draw from: an object, never null, a word or a number. */
const isEntry = (value) => typeof value === 'object' && value !== null;

/**
 * Every picture a look's entries name (an entry's `button_image`), in the
 * order they are written, each once and as a word: the states', then the
 * active half's and the inactive half's. A control fetches them all when it
 * is drawn, so a state change swaps its picture at once.
 */
export function lookPictures(look) {
    const entries = [
        ...(isEntry(look.states) ? Object.values(look.states) : []),
        look.style_active,
        look.style_inactive,
    ];
    const named = entries.filter(isEntry).map((entry) => entry.button_image).filter(Boolean);
    return [...new Set(named.map(String))];
}

/**
 * Two readings compared as words apart from case, so a driver's boolean
 * `true`, the word "True" and a value written as "true" are the same. Every
 * rule that asks whether a reading is "on" compares this way: a look's
 * two-state form, a toggle's press (modules/press.js) and its indication, a
 * label's text condition. What a missing reading is stays each rule's own.
 */
export function sameWords(a, b) {
    return String(a).toLowerCase() === String(b).toLowerCase();
}

/**
 * A label's or page nav's two-state words: `label_active` or
 * `label_inactive`, whenever it is set (an empty one included), over the
 * entry's own label. A button takes them only when they have words, and
 * decides that in its own evaluator.
 */
export function withTwoStateWords(binding, look) {
    if (binding.states) return look;
    const words = look.isActive ? binding.label_active : binding.label_inactive;
    if (words === undefined || words === null) return look;
    return {
        ...look,
        appearance: { ...look.appearance, label: words },
        wordsPath: [...LOOK, look.isActive ? 'label_active' : 'label_inactive'],
    };
}
