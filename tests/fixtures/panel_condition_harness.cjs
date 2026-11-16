/*
 * The panel's comparers run over the condition corpus, for
 * tests/test_panel_condition_parity.py.
 *
 * Each comparer is reached through what the panel itself calls (a look's
 * pickLook, a toggle's light and press, a label's words, the status LED's
 * evaluateColor, a dropdown, a list), handed the authored value as an author
 * writes it and a state holding the reading, and asked one question: does
 * the reading match. A visible_when takes an operator, so it is asked each
 * case with the case's own operator.
 *
 * Invoked as: node panel_condition_harness.cjs <abs path to panel.js>
 * with NODE_PATH at openavc/web/programmer/node_modules so jsdom resolves and
 * { pairs: [[actual, target], ...], cases: [{ op, actual, target }, ...] } on
 * stdin. Printed on stdout: { <comparer>: [matched, ...] } with one answer per
 * pair, visibleWhen's one per case, and `spellings`, the operator spellings a
 * visible_when knows.
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

(async () => {
    await import(pathToFileURL(panelPath).href);
    const app = window.__openavcPanel;
    const looks = await panelModule('looks.js');
    const toggle = await panelModule('toggle.js');
    const press = await panelModule('press.js');
    const label = await panelModule('label.js');
    const visibility = await panelModule('visibility.js');
    const fields = await panelModule('fields.js');
    const list = await panelModule('list.js');

    const host = { send() {}, keepTimer() {}, remBasePx: 14, state: {} };
    const listOf = (target, state) => {
        host.state = state;
        const element = {
            id: 'l', list_style: 'selectable', items: [{ label: 'row', value: target }],
            bindings: { show: { value: { key: KEY } } },
        };
        return new list.ListControl(host, element, document.createElement('div'), {});
    };
    const rowLit = (control) => control.scrollArea.querySelector('.list-item').classList.contains('active');
    const selectOf = (target) => fields.drawSelect(host, 's',
        [{ value: target, label: 'it' }, { value: '\u0000other', label: 'other' }], null);

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
            const select = selectOf(target);
            fields.showSelectValue(select, actual, false);
            return select.selectedIndex === 0;
        },
        selectLook: (actual, target) => {
            const select = selectOf(target);
            fields.showSelectLook(select, { [String(target)]: { bg_color: HIT } }, actual, false);
            return select.style.backgroundColor === HIT_DRAWN;
        },
        listAtDraw: (actual, target) => rowLit(listOf(target, { [KEY]: actual })),
        listAfterReading: (actual, target) => {
            const control = listOf(target, {});
            list.showListSelection(control, actual, false);
            return rowLit(control);
        },
    };

    const { pairs, cases } = JSON.parse(readFileSync(0, 'utf8'));
    const out = { spellings: [...visibility.OPERATORS.keys()] };
    for (const [name, matches] of Object.entries(matchers)) {
        out[name] = pairs.map(([actual, target]) => matches(actual, target));
    }
    out.visibleWhen = cases.map(({ op, actual, target }) => visibility.isShown(
        visibility.visibilityOf({ bindings: { show: { visible_when: { key: KEY, operator: op, value: target } } } }),
        { [KEY]: actual },
    ));
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => {
    process.stderr.write(String(e && e.stack || e));
    process.exit(1);
});
