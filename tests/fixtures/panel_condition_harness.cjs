/*
 * The panel's comparison, and every control that asks it, run over the
 * condition corpus, for tests/test_panel_condition_parity.py.
 *
 * modules/compare.js is run directly, every case in both modes. Each control
 * is then reached through what the panel itself calls (a look's pickLook, a
 * toggle's light and press, a label's words, the status LED's evaluateColor,
 * a dropdown, a list), handed the authored value as an author writes it and a
 * state holding the reading, and asked one question: does the reading match.
 * A visible_when takes an operator, so it is asked each case with the case's
 * own operator, with the reading held as null and, where it is null, with the
 * key missing too. The controls that read an authored map are also asked
 * which key a reading picks, over the corpus's `keys`.
 *
 * A condition whose value names a key ($var.wanted) is asked too, over
 * tests/fixtures/condition_reference_cases.json: the compare module's `readValue`
 * in both modes, a visible_when holding it against that state, and the keys
 * a visible_when redraws on.
 *
 * Invoked as: node panel_condition_harness.cjs <abs path to panel.js>
 * with NODE_PATH at openavc/web/programmer/node_modules so jsdom resolves and
 * { pairs: [[actual, target], ...], cases: [{ op, actual, target }, ...],
 *   keys: [{ keys, reading }, ...], references: [{ op, actual, value, state }, ...],
 *   reads: [condition, ...] } on stdin (references and reads may be left out).
 * Printed on stdout: { <comparer>: [matched, ...] } with one answer per pair,
 * `picks` with one key (or null) per keys case for each map reader, the
 * compare module's and visibleWhen's answers per case, `zeroRow`,
 * `spellings`, the operator spellings a visible_when knows, and
 * `references`: `holdsDecide`, `holdsSurface` and `visibleWhen` per reference
 * case, `keys` per reads condition, and `redrawKeys`, the keys a visible_when
 * holding each reads condition redraws on.
 */
const path = require('path');
const { pathToFileURL } = require('url');
const { readFileSync } = require('fs');
const { JSDOM } = require('jsdom');

const panelPath = process.argv[2];
const modulesDir = path.join(path.dirname(panelPath), 'modules');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { url: 'http://localhost:8080/panel', pretendToBeVisual: true });
const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
// panel.js wraps fetch as it loads (the session bridge), which jsdom lacks.
// The panel is constructed, never started: DOMContentLoaded has already fired.
window.fetch = async () => ({ ok: false, json: async () => ({}) });
// jsdom has no layout; a list scrolls its lit row into view.
window.HTMLElement.prototype.scrollIntoView = () => {};

/** One of the panel's modules, the instance panel.js linked. */
const panelModule = (name) => import(pathToFileURL(path.join(modulesDir, name)).href);

/** A colour no comparer draws unless the reading matched. */
const HIT = '#010203';
const HIT_DRAWN = 'rgb(1, 2, 3)';
const KEY = 'var.k';
/** The i-th key's own colour, and how the DOM spells it back. */
const colourOf = (i) => `#0102${(i + 4).toString(16).padStart(2, '0')}`;
const drawnColour = (i) => `rgb(1, 2, ${i + 4})`;
/** A state name no keys case uses, so "nothing picked" is told from a blank key. */
const NONE = '\u0000none';

(async () => {
    await import(pathToFileURL(panelPath).href);
    const app = window.__openavcPanel;
    const compare = await panelModule('compare.js');
    const looks = await panelModule('looks.js');
    const toggle = await panelModule('toggle.js');
    const press = await panelModule('press.js');
    const label = await panelModule('label.js');
    const visibility = await panelModule('visibility.js');
    const fields = await panelModule('fields.js');
    const list = await panelModule('list.js');
    const availability = await panelModule('availability.js');

    const host = { send() {}, keepTimer() {}, remBasePx: 14, state: {} };
    const listOf = (values, state) => {
        host.state = state;
        const element = {
            id: 'l', list_style: 'selectable', items: values.map((value) => ({ label: 'row', value })),
            bindings: { show: { value: { key: KEY } } },
        };
        return new list.ListControl(host, element, document.createElement('div'), {});
    };
    const litRows = (control) => [...control.scrollArea.querySelectorAll('.list-item')]
        .map((row, i) => (row.classList.contains('active') ? i : -1)).filter((i) => i >= 0);
    const rowLit = (control) => litRows(control).length > 0;
    const selectOf = (values) => fields.drawSelect(host, 's', values.map((value) => ({ value, label: 'it' })), null);
    const keyAt = (keys, i) => (i === undefined || i < 0 ? null : keys[i]);
    const colourIndex = (drawn, keys) => keys.findIndex((_k, i) => drawn === drawnColour(i));

    // A named state, a colour map's entry and a dropdown's look are each named
    // by text (an object's key), so a target is written as that text there.
    const matchers = {
        pickState: (actual, target) =>
            looks.pickLook({ states: { [String(target)]: { label: 'hit' } } }, actual).appearance.label === 'hit',
        pickTwoState: (actual, target) =>
            looks.pickLook({ condition: { equals: target } }, actual).isActive,
        toggleLit: (actual, target) => toggle.toggleLit({ value: target }, actual, false),
        toggleIsOn: (actual, target) => {
            const action = { action: 'macro', macro: 'm', mode: 'toggle', toggle_key: KEY, toggle_value: target };
            const p = press.pressOf({ bindings: { do: { press: [action] } } });
            return press.frameAtPress(p, (key) => ({ [KEY]: actual })[key]) === 'ui.toggle_off';
        },
        conditionWords: (actual, target) =>
            label.valueWords({ condition: { equals: target }, text_true: 'T', text_false: 'F' },
                actual, false, null)[0] === 'T',
        evaluateColor: (actual, target) => {
            const element = document.createElement('div');
            app.state = { [KEY]: actual };
            app.evaluateColor({ element, binding: { key: KEY, map: { [String(target)]: HIT } } });
            return element.style.backgroundColor === HIT_DRAWN;
        },
        selectValue: (actual, target) => {
            const select = selectOf([target, '\u0000other']);
            fields.showSelectValue(select, actual, false);
            return select.selectedIndex === 0;
        },
        selectLook: (actual, target) => {
            const select = selectOf([target, '\u0000other']);
            fields.showSelectLook(select, { [String(target)]: { bg_color: HIT } }, actual, false);
            return select.style.backgroundColor === HIT_DRAWN;
        },
        listAtDraw: (actual, target) => rowLit(listOf([target], { [KEY]: actual })),
        listAfterReading: (actual, target) => {
            const control = listOf([target], {});
            list.showListSelection(control, actual, false);
            return rowLit(control);
        },
    };

    // The key each map reader picks for a reading, or null.
    const pickers = {
        pickState: (keys, reading) => {
            const states = Object.fromEntries(keys.map((k) => [k, { label: k }]));
            const look = looks.pickLook({ default_state: NONE, states }, reading);
            const picked = look.wordsPath[4];
            return picked === NONE ? null : picked;
        },
        evaluateColor: (keys, reading) => {
            const element = document.createElement('div');
            app.state = { [KEY]: reading };
            const map = Object.fromEntries(keys.map((k, i) => [k, colourOf(i)]));
            app.evaluateColor({ element, binding: { key: KEY, map } });
            return keyAt(keys, colourIndex(element.style.backgroundColor, keys));
        },
        selectValue: (keys, reading) => {
            const select = selectOf(keys);
            fields.showSelectValue(select, reading, false);
            return keyAt(keys, select.selectedIndex);
        },
        selectLook: (keys, reading) => {
            const select = selectOf(keys);
            const styleMap = Object.fromEntries(keys.map((k, i) => [k, { bg_color: colourOf(i) }]));
            fields.showSelectLook(select, styleMap, reading, false);
            return keyAt(keys, colourIndex(select.style.backgroundColor, keys));
        },
        listAtDraw: (keys, reading) => keyAt(keys, litRows(listOf(keys, { [KEY]: reading }))[0]),
        listAfterReading: (keys, reading) => {
            const control = listOf(keys, {});
            list.showListSelection(control, reading, false);
            return keyAt(keys, litRows(control)[0]);
        },
    };

    const { pairs, cases, keys, references = [], reads = [] } = JSON.parse(readFileSync(0, 'utf8'));
    const out = { spellings: [...visibility.OPERATORS.keys()], picks: {} };
    for (const [name, matches] of Object.entries(matchers)) {
        out[name] = pairs.map(([actual, target]) => matches(actual, target));
    }
    for (const [name, picks] of Object.entries(pickers)) {
        out.picks[name] = keys.map((c) => picks(c.keys, c.reading));
    }
    out.compareKeys = keys.map((c) => compare.pickKey(c.keys, c.reading));
    out.compareDecide = cases.map(({ op, actual, target }) => compare.evalOperator(op, actual, target, compare.DECIDE));
    out.compareSurface = cases.map(({ op, actual, target }) => compare.evalOperator(op, actual, target, compare.SURFACE));
    const shownWith = ({ op, target }, state) => visibility.isShown(
        visibility.visibilityOf({ bindings: { show: { visible_when: { key: KEY, operator: op, value: target } } } }),
        state,
    );
    out.visibleWhen = cases.map((c) => shownWith(c, { [KEY]: c.actual }));
    out.visibleWhenMissing = cases.map((c) => (c.actual === null ? shownWith(c, {}) : null));
    const zeroAtDraw = (reading) => litRows(listOf([0, 1], { [KEY]: reading }));
    const zeroAfterReading = (reading) => {
        const control = listOf([0, 1], {});
        list.showListSelection(control, reading, false);
        return litRows(control);
    };
    out.zeroRow = {
        atDraw: [zeroAtDraw(0), zeroAtDraw('0'), zeroAtDraw('')],
        afterReading: [zeroAfterReading(0), zeroAfterReading('0'), zeroAfterReading('')],
    };
    // A reference case's reading is the condition's own key, beside the
    // state the case holds (a key with no value is left out of it).
    const referenceState = (c) => ({ ...c.state, [KEY]: c.actual });
    const readIn = (state) => (key) => state[key];
    out.references = {
        holdsDecide: references.map((c) => compare.evalOperator(c.op, c.actual, compare.readValue(c.value, readIn(c.state)), compare.DECIDE)),
        holdsSurface: references.map((c) => compare.evalOperator(c.op, c.actual, compare.readValue(c.value, readIn(c.state)), compare.SURFACE)),
        visibleWhen: references.map((c) => visibility.isShown(
            visibility.visibilityOf({ bindings: { show: { visible_when: { key: KEY, operator: c.op, value: c.value } } } }),
            referenceState(c),
        )),
        keys: reads.map((condition) => compare.conditionKeys(condition)),
        redrawKeys: reads.map((condition) => visibility.visibilityOf(
            { bindings: { show: { visible_when: condition } } },
        )._keys),
        // Whether a change to the named key alone reaches the control, as the
        // panel's redraw filter decides it.
        namedKeyRedraws: reads.map((condition) => {
            const named = compare.referencedKey(condition.value);
            const binding = visibility.visibilityOf({ bindings: { show: { visible_when: condition } } });
            return named ? availability.changeReaches({ binding }, [named], null) : null;
        }),
    };
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => {
    process.stderr.write(String(e && e.stack || e));
    process.exit(1);
});
