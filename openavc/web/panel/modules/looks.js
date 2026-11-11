/**
 * Which appearance a look binding (`bindings.show.look`) shows for a state
 * value, and where the words it draws were authored.
 *
 * Every control with a look picks its entry here (a button's feedback, a
 * label, an image, a page nav, a container), so one state value picks the
 * same entry on all of them. What each control then draws from that entry is
 * its own business, in panel.js. No DOM and no panel state.
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
    const isActive = stateValue !== undefined &&
        String(stateValue).toLowerCase() === String(condition.equals).toLowerCase();
    const half = isActive ? 'style_active' : 'style_inactive';
    return {
        appearance: binding[half] || {},
        wordsPath: [...LOOK, half, 'label'],
        isActive,
    };
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
