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
 * mode is here too, held as the same data, so the corpus pins both.
 *
 * A condition's value may name a key instead (`$var.wanted`): it is compared
 * with what that key holds, and a key with no value is a reading that has
 * not arrived (`holds`, run over
 * tests/fixtures/condition_reference_cases.json with the other two copies).
 * No DOM and no panel state.
 */

import { asNumber } from './readings.js';
import { FALSE_WORDS, TRUE_WORDS } from './truth_words.gen.js';

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

/** Not arrived: no reading reported yet, a key deleted, or a null. */
const notArrived = (value) => value === undefined || value === null;

/** A value as a number to compare; a boolean as 1 or 0, for ordering. */
function numberOf(value) {
    if (typeof value === 'boolean') return value ? 1 : 0;
    return asNumber(value);
}

/** A reading as true or false, or null where it is neither: the rule of
 *  openavc/drivers/truth_words.py, whose words truth_words.gen.js carries.
 *  A boolean is itself; a word trimmed and apart from case; a number by
 *  value, non-zero true. */
export function truthOf(value) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
        const word = value.trim().toLowerCase();
        if (TRUE_WORDS.has(word)) return true;
        if (FALSE_WORDS.has(word)) return false;
    }
    const number = asNumber(value);
    return number === null || !Number.isFinite(number) ? null : number !== 0;
}

/** Both sides read by `reader`, or null when either reads as nothing. */
function bothAs(reader, actual, target) {
    const pair = [reader(actual), reader(target)];
    return pair.includes(null) ? null : pair;
}

/** Whether two values, neither of them missing, are equal under the rule:
 *  as booleans when one side is one, else as numbers when the types differ,
 *  else as words apart from case. */
function same(actual, target) {
    const asBooleans = (typeof actual === 'boolean' || typeof target === 'boolean')
        && bothAs(truthOf, actual, target);
    if (asBooleans) return asBooleans[0] === asBooleans[1];
    const asNumbers = typeof actual !== typeof target && bothAs(numberOf, actual, target);
    if (asNumbers) return asNumbers[0] === asNumbers[1];
    const asWords = typeof actual === 'string' && typeof target === 'string';
    return asWords ? actual.toLowerCase() === target.toLowerCase() : actual === target;
}

/** The orderings, each as the comparison it makes. */
const ORDERINGS = new Map([
    ['gt', (a, t) => a > t], ['lt', (a, t) => a < t], ['gte', (a, t) => a >= t], ['lte', (a, t) => a <= t],
]);

/** `eq` or `ne`: never equal, and not-equal only in the surface mode, while
 *  the reading has not arrived; never either against an authored null. */
function equality(op, actual, target, mode) {
    if (notArrived(target)) return false;
    if (notArrived(actual)) return op === 'ne' && MODES.get(mode) === true;
    return same(actual, target) === (op === 'eq');
}

/** An ordering: as numbers when both read as one, else two words as text. */
function ordering(order, actual, target) {
    if (notArrived(actual) || notArrived(target)) return false;
    const numbers = bothAs(numberOf, actual, target);
    if (numbers) return order(numbers[0], numbers[1]);
    return typeof actual === 'string' && typeof target === 'string' && order(actual, target);
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
    if (canonical === 'eq' || canonical === 'ne') return equality(canonical, actual, target, mode);
    if (ORDERINGS.has(canonical)) return ordering(ORDERINGS.get(canonical), actual, target);
    if (canonical === 'truthy') return truthy(actual);
    return canonical === 'falsy' && !truthy(actual);
}

/** Whether a reading equals an authored value; never while it has not arrived. */
export function sameValue(actual, target) {
    return evalOperator('eq', actual, target);
}

/**
 * The key a condition's value names (`$var.wanted` names `var.wanted`), or
 * null for a value compared as written: anything but text that starts with
 * `$` and goes on. `referenced_key` in core/value_resolver.py.
 */
export function referencedKey(value) {
    return typeof value === 'string' && value.length > 1 && value.startsWith('$') ? value.slice(1) : null;
}

/** The keys a condition reads: its own, and the one its value names. A
 *  change to either can change its answer. */
export function conditionKeys(condition) {
    const keys = condition.key ? [condition.key] : [];
    const named = referencedKey(condition.value);
    if (named !== null && !keys.includes(named)) keys.push(named);
    return keys;
}

/**
 * Whether `actual op value` holds for a reader in `mode`, a value that names
 * a key (`referencedKey`) compared with what `read(key)` holds there. A key
 * with no value is a reading that has not arrived: never equal, and
 * not-equal only in the surface mode, as a reading that has not arrived is.
 * `holds` in core/condition_eval.py.
 */
export function holds(op, actual, value, read, mode = SURFACE) {
    const named = referencedKey(value);
    if (named === null) return evalOperator(op, actual, value, mode);
    const target = read(named);
    if (!notArrived(target)) return evalOperator(op, actual, target, mode);
    const canonical = OPERATOR_SPELLINGS.get(op);
    if (canonical === 'truthy' || canonical === 'falsy') return evalOperator(canonical, actual, null, mode);
    return canonical === 'ne' && MODES.get(mode) === true;
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
