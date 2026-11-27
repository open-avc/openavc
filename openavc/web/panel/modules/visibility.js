/**
 * Whether a control is shown: its `visible_when`, read once when the control
 * is drawn and held against state on every pass.
 *
 * A `visible_when` is one condition, an `all:` block (every condition holds)
 * or an `any:` block (one does); a block naming both is read as `all`'s
 * conditions under `any`'s rule. A condition is `{ key, operator, value }`,
 * `eq` when it names no operator, and an operator nobody knows never holds.
 *
 * How each operator compares is the server's rule (core/condition_eval.py),
 * through the panel's copy of it, modules/compare.js, in the surface mode: a
 * reading that has not arrived is unequal to every value, so a control shown
 * while `power != on` shows before the projector reports. A value that names
 * a key (`$var.wanted`) compares with what that key holds, and a change to
 * that key draws the control again too.
 *
 * The rule worth knowing before changing anything here: the operator
 * spellings are a contract. The page review (ui/page_review.py) and the
 * Builder's (reviewStacking.ts) reason about which spellings the panel
 * evaluates and what each one means, and tests/test_ui_page_review_mirrors.py
 * runs OPERATORS here and holds the reviews' sets to them; the binding
 * check the AI's doors ask (ui/page_structure.py) and the Builder's Validate take every
 * spelling the platform's list holds, and Validate names any other. A new
 * spelling goes into those too. No DOM and no panel state.
 */

import {
    OPERATOR_SPELLINGS, SURFACE, conditionKeys, evalOperator, readValue,
} from './compare.js';

/** Each operator spelling a condition may use, and the operator it names. */
export const OPERATORS = OPERATOR_SPELLINGS;

/** Whether `actual op target` holds; an operator not in OPERATORS never does. */
export function conditionHolds(op, actual, target) {
    return evalOperator(op, actual, target, SURFACE);
}

/**
 * The visibility a control's `visible_when` asks for: its conditions, whether
 * all or any must hold, and the keys they read (`_keys`, which say which
 * state changes draw it again). Null for a control with none.
 */
export function visibilityOf(element) {
    const vw = element.bindings?.show?.visible_when;
    if (!vw) return null;
    const conditions = vw.all || vw.any || [vw];
    const mode = vw.any ? 'any' : 'all';
    const keys = [...new Set(conditions.flatMap((c) => conditionKeys(c || {})))];
    return { conditions, mode, _keys: keys };
}

/** Whether a control with this visibility is shown while state reads `state`. */
export function isShown(visibility, state) {
    const conditions = visibility.conditions || [];
    const read = (key) => state[key];
    const check = (cond) => evalOperator(cond.operator || 'eq', read(cond.key), readValue(cond.value, read), SURFACE);
    return visibility.mode === 'any'
        ? conditions.some(check)
        : conditions.every(check);
}
