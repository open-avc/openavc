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
 * Whether a reading matches a state, or a two-state look's value, is
 * modules/compare.js's to say, as it is for every control.
 */

import { pickKey, sameValue } from './compare.js';

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
 * A look with `states`: the state the value picks (compare.js `pickKey`: the
 * exact name, then one apart from case, then one the value equals as a
 * boolean or number, so a state `on` takes a reading of `ON` or `true`). No
 * value, or one no state names, takes `default_state`; a look without one
 * takes its blank state `''`. The words belong to the state the value
 * matched, even when that state's entry is empty and the default was drawn.
 */
function pickState(binding, stateValue) {
    const { states } = binding;
    const fallback = binding.default_state || '';
    const picked = pickKey(Object.keys(states), stateValue);
    const matched = picked === null ? fallback : picked;
    return {
        appearance: (picked !== null && states[picked]) || states[fallback] || {},
        wordsPath: [...LOOK, 'states', matched, 'label'],
    };
}

/**
 * The two-state form (`condition.equals`, `style_active`, `style_inactive`):
 * active when the value equals `equals` (compare.js `sameValue`). A value
 * that has not arrived, null included, is inactive, and so is a look with no
 * `equals`.
 */
function pickTwoState(binding, stateValue) {
    const condition = binding.condition || {};
    const isActive = sameValue(stateValue, condition.equals);
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
 * order they are written, each once: the states', then the active half's and
 * the inactive half's. Only text names a picture (assetUrl, modules/base_path.js),
 * so a number is none and is never fetched. A control fetches them all when
 * it is drawn, so a state change swaps its picture at once.
 */
export function lookPictures(look) {
    const entries = [
        ...(isEntry(look.states) ? Object.values(look.states) : []),
        look.style_active,
        look.style_inactive,
    ];
    const named = entries.filter(isEntry).map((entry) => entry.button_image)
        .filter((image) => typeof image === 'string' && image !== '');
    return [...new Set(named)];
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
