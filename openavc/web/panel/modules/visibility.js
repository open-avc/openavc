/**
 * Whether a control is shown: its `visible_when`, read once when the control
 * is drawn and held against state on every pass.
 *
 * A `visible_when` is one condition, an `all:` block (every condition holds)
 * or an `any:` block (one does); a block naming both is read as `all`'s
 * conditions under `any`'s rule. A condition is `{ key, operator, value }`,
 * `eq` when it names no operator, and an operator nobody knows never holds.
 *
 * The rule worth knowing before changing anything here: the operator
 * spellings are a contract. The page review (ui/page_review.py) and the
 * Builder's (reviewStacking.ts) reason about which spellings the panel
 * evaluates and what each one means, and tests/test_ui_page_review_mirrors.py
 * runs OPERATORS here and holds the reviews' sets to them; the AI's binding
 * check (cloud/tools/ui_bindings.py) takes the canonical names. A new
 * spelling goes into those too. No DOM and no panel state.
 */

/** A comparison that holds only when both sides are set (neither null nor undefined). */
const bounded = (compare) => (actual, target) => actual != null && target != null && compare(actual, target);

/**
 * Each operator spelling and whether it holds for a reading `actual` against
 * the condition's `target`. Equality is JavaScript's loose one, so a reading
 * of 5 equals a target of "5", and null equals undefined.
 */
export const OPERATORS = new Map([
    ['eq', (actual, target) => actual == target],
    ['equals', (actual, target) => actual == target],
    ['==', (actual, target) => actual == target],
    ['ne', (actual, target) => actual != target],
    ['not_equals', (actual, target) => actual != target],
    ['!=', (actual, target) => actual != target],
    ['gt', bounded((actual, target) => actual > target)],
    ['>', bounded((actual, target) => actual > target)],
    ['lt', bounded((actual, target) => actual < target)],
    ['<', bounded((actual, target) => actual < target)],
    ['gte', bounded((actual, target) => actual >= target)],
    ['>=', bounded((actual, target) => actual >= target)],
    ['lte', bounded((actual, target) => actual <= target)],
    ['<=', bounded((actual, target) => actual <= target)],
    ['truthy', (actual) => !!actual],
    ['falsy', (actual) => !actual],
]);

/** Whether `actual op target` holds; an operator not in OPERATORS never does. */
export function conditionHolds(op, actual, target) {
    const holds = OPERATORS.get(op);
    return holds ? holds(actual, target) : false;
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
    const keys = conditions.map(c => c.key).filter(Boolean);
    return { conditions, mode, _keys: keys };
}

/** Whether a control with this visibility is shown while state reads `state`. */
export function isShown(visibility, state) {
    const conditions = visibility.conditions || [];
    const check = (cond) => {
        const actual = state[cond.key];
        return conditionHolds(cond.operator || 'eq', actual, cond.value);
    };
    return visibility.mode === 'any'
        ? conditions.some(check)
        : conditions.every(check);
}
