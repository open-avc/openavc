/**
 * The panel's one comparison of a reading with what an author wrote.
 *
 * Every panel control that asks whether a reading matches asks here: a
 * look's named states and its two-state form, a toggle's light and its
 * press, a label's text condition, `visible_when`, the status LED's colour
 * map, a dropdown and a list. The rule is the server's
 * (core/condition_eval.py, which macros and triggers ask; conditionEval.ts
 * mirrors it in the Programmer), copied here because the panel has no server
 * call to spare per reading: a boolean compares with the words and numbers
 * that mean it, values of different types compare as numbers when both read
 * as one (readings.js `asNumber`, the platform's one number reader), two
 * words compare apart from case, and a reading that has not arrived is never
 * equal. tests/test_panel_condition_parity.py runs this module, and every
 * control that asks it, over tests/fixtures/condition_parity_cases.json, the
 * corpus the other two copies run.
 *
 * The panel shows and hides, so it reads in the rule's surface mode: a
 * reading that has not arrived is unequal to every value, and a control
 * shown while `power != on` shows before the projector reports. The acting
 * mode is here too, held as the same data, so the corpus pins both. No DOM
 * and no panel state.
 */

import { asNumber } from './readings.js';

/** A reader that acts makes no decision on a reading that has not arrived. */
export const DECIDE = 'decide';
/** A reader that shows or hides reads one as unequal to every value. */
export const SURFACE = 'surface';
/** Whether a reading that has not arrived holds `ne`, by mode: `MODES` in
 *  core/condition_eval.py. */
export const MODES = new Map([[DECIDE, false], [SURFACE, true]]);

/** Each spelling the rule takes, as the operator it names. */
export const OPERATOR_SPELLINGS = new Map([
    ['eq', 'eq'], ['ne', 'ne'], ['gt', 'gt'], ['lt', 'lt'], ['gte', 'gte'], ['lte', 'lte'],
    ['truthy', 'truthy'], ['falsy', 'falsy'],
    ['equals', 'eq'], ['not_equals', 'ne'], ['==', 'eq'], ['!=', 'ne'],
    ['>', 'gt'], ['<', 'lt'], ['>=', 'gte'], ['<=', 'lte'],
    ['equal', 'eq'], ['not_equal', 'ne'],
    ['greater_than', 'gt'], ['less_than', 'lt'],
    ['greater_or_equal', 'gte'], ['less_or_equal', 'lte'],
]);

const TRUE_WORDS = new Set(['true', '1', 'yes', 'on']);
const FALSE_WORDS = new Set(['false', '0', 'no', 'off']);

/** Not arrived: no reading reported yet, a key deleted, or a null. */
const notArrived = (value) => value === undefined || value === null;

/** A value as a number to compare; a boolean as 1 or 0, for ordering. */
function numberOf(value) {
    if (typeof value === 'boolean') return value ? 1 : 0;
    return asNumber(value);
}

/** A reading as a boolean: a boolean, one of the words, or a number by
 *  value, non-zero true. Null where it is none of those. */
function booleanOf(value) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
        const low = value.toLowerCase();
        if (TRUE_WORDS.has(low)) return true;
        if (FALSE_WORDS.has(low)) return false;
    }
    const number = asNumber(value);
    return number === null ? null : number !== 0;
}

/** Whether two values, neither of them missing, are equal under the rule. */
function same(actual, target) {
    if (typeof actual === 'boolean' || typeof target === 'boolean') {
        const a = booleanOf(actual);
        const t = booleanOf(target);
        if (a !== null && t !== null) return a === t;
    }
    if (typeof actual !== typeof target) {
        const a = numberOf(actual);
        const t = numberOf(target);
        if (a !== null && t !== null) return a === t;
    }
    return typeof actual === 'string' && typeof target === 'string'
        ? actual.toLowerCase() === target.toLowerCase()
        : actual === target;
}

function ordered(op, a, t) {
    if (op === 'gt') return a > t;
    if (op === 'lt') return a < t;
    if (op === 'gte') return a >= t;
    return a <= t;
}

/** Python's bool() over a reading: only null, false, 0 and '' are false. */
function truthy(value) {
    return !(notArrived(value) || value === false || value === '' || value === 0);
}

/**
 * Whether `actual op target` holds for a reader in `mode` (the panel's is
 * SURFACE). An operator the rule does not know never holds; the server
 * refuses it instead, and a page review names it.
 */
export function evalOperator(op, actual, target, mode = SURFACE) {
    const canonical = OPERATOR_SPELLINGS.get(op);
    if (canonical === 'eq' || canonical === 'ne') {
        if (notArrived(target)) return false;
        if (notArrived(actual)) return canonical === 'ne' && MODES.get(mode) === true;
        return canonical === 'eq' ? same(actual, target) : !same(actual, target);
    }
    if (canonical === 'gt' || canonical === 'lt' || canonical === 'gte' || canonical === 'lte') {
        if (notArrived(actual) || notArrived(target)) return false;
        const a = numberOf(actual);
        const t = numberOf(target);
        if (a !== null && t !== null) return ordered(canonical, a, t);
        if (typeof actual === 'string' && typeof target === 'string') return ordered(canonical, actual, target);
        return false;
    }
    if (canonical === 'truthy') return truthy(actual);
    if (canonical === 'falsy') return !truthy(actual);
    return false;
}

/** Whether a reading equals an authored value; never while it has not arrived. */
export function sameValue(actual, target) {
    return evalOperator('eq', actual, target);
}

const byWords = (a, b) => {
    const x = String(a);
    const y = String(b);
    return x < y ? -1 : (x > y ? 1 : 0);
};

/**
 * The authored key (a look's state, an LED colour, a dropdown's option, a
 * list row's value) that `reading` picks, or null when it picks none: the
 * exact key first, then one apart from case, then one the reading equals as
 * a boolean or number. Several in one tier: the first in code point order,
 * since a map's number-like keys come out first and the author's order is
 * lost. A reading that has not arrived picks none. `match_key` in
 * core/condition_eval.py.
 */
export function pickKey(keys, reading) {
    if (notArrived(reading)) return null;
    const candidates = [...keys].filter((key) => !notArrived(key));
    const words = String(reading);
    const tiers = [
        (key) => String(key) === words,
        (key) => String(key).toLowerCase() === words.toLowerCase(),
        (key) => sameValue(reading, key),
    ];
    for (const matches of tiers) {
        const found = candidates.filter(matches).sort(byWords);
        if (found.length) return found[0];
    }
    return null;
}
