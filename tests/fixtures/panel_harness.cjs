/*
 * jsdom harness for openavc/web/panel/panel.js regression tests.
 *
 * Loads the real panel.js into a jsdom window and exercises the behaviours
 * fixed in the bug-fix campaign. Each test throws on failure; results are
 * emitted as JSON on stdout for the pytest wrapper (tests/test_panel_js.py)
 * to assert on. Invoked as: node panel_harness.cjs <abs path to panel.js>
 * with cwd set to openavc/web/programmer so `require('jsdom')` resolves.
 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const path = require('path');

const panelPath = process.argv[2];
const source = fs.readFileSync(panelPath, 'utf8') + '\n;window.__PanelApp = PanelApp;';

// The real stylesheets, inlined. jsdom won't resolve the @import, so the two
// files are concatenated by hand. This is what lets the layout scenarios below
// assert on *computed* style — the stacking rule that keeps elements in front
// of the page background lives in CSS and nothing in JS would catch its loss.
const panelDir = path.dirname(panelPath);
const css = [
    fs.readFileSync(path.join(panelDir, 'panel-elements.css'), 'utf8'),
    fs.readFileSync(path.join(panelDir, 'panel.css'), 'utf8').replace(/@import[^;]*;/g, ''),
].join('\n');

const dom = new JSDOM(
    `<!DOCTYPE html><html><head><style>${css}</style></head><body>
        <div id="panel-root"></div>
        <div id="connection-status"></div>
        <div id="offline-overlay"></div>
        <div id="loading-state"></div>
    </body></html>`,
    { url: 'http://localhost:8080/panel', runScripts: 'outside-only', pretendToBeVisual: true },
);
const { window } = dom;
const { document } = window;

// --- Stubs the panel code touches at construction / in the paths under test ---
window.fetch = async () => ({ ok: false, json: async () => ({}) });
window.requestAnimationFrame = (cb) => { cb(0); return 0; };        // run binding batches synchronously
window.cancelAnimationFrame = () => {};
class FakeWS { constructor() { this.readyState = 1; } send() {} close() {} }
FakeWS.OPEN = 1;
window.WebSocket = FakeWS;
if (!window.Audio) window.Audio = window.HTMLAudioElement;
// jsdom doesn't implement media playback; make play() a resolved no-op.
window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
window.HTMLMediaElement.prototype.pause = function () {};

window.eval(source);
const PanelApp = window.__PanelApp;
const mkApp = () => new PanelApp();

function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }

// --- Layout-scenario helpers ------------------------------------------------

/** jsdom's window size is writable; the panel picks a layout from it. */
function setViewport(w, h) {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: h, configurable: true });
}
setViewport(1280, 800);

function el(id, type, extra) {
    return Object.assign({ id, type, label: id }, extra || {});
}

/** A minimal 0.8.0 project: one page, one primary landscape layout. */
function project({ elements, placements, background, snap, extraLayouts }) {
    const layouts = [{
        id: 'landscape', orientation: 'landscape', primary: true,
        placements: placements || {}, hidden: [],
    }].concat(extraLayouts || []);
    return {
        ui: {
            settings: {},
            master_elements: [],
            pages: [{
                id: 'main', name: 'Main', page_type: 'page',
                background: background || null,
                snap: snap || { enabled: false, x: 100 / 12, y: 100 / 8 },
                elements: elements || [],
                layouts,
            }],
        },
    };
}

function renderProject(app, proj) {
    app.uiDef = proj.ui;
    app.uiSettings = proj.ui.settings || {};
    app.currentPage = proj.ui.pages[0].id;
    app.snapshotReceived = true;
    app.renderCurrentPage();
}

const tests = {
    // H-001 — matrix routes re-evaluate on incremental state.update for any of
    // their key patterns (route / audio route / labels), not just on full render.
    h001_matrix_reeval() {
        const app = mkApp();
        let ran = 0;
        app.evaluateMatrixRoutes = () => { ran++; };
        app.bindings = [{
            type: 'matrix_routes',
            element: document.createElement('div'),
            binding: { _patterns: ['device.sw.route_*', 'device.sw.audio_route_*'] },
            _matrix: {},
        }];
        app.evaluateAllBindings(['device.sw.route_1']);
        assert(ran === 1, 'matrix must re-eval when a route key changes');
        app.evaluateAllBindings(['var.unrelated']);
        assert(ran === 1, 'matrix must NOT re-eval on an unrelated key');
        app.evaluateAllBindings(['device.sw.audio_route_2']);
        assert(ran === 2, 'matrix must re-eval when an audio-route key changes');
    },

    // H-002 — value displays revert to a no-data placeholder when the bound key
    // is deleted (device removed/offline), instead of freezing on last value.
    h002_gauge_reset() {
        const app = mkApp();
        const fg = { setAttribute(k, v) { this[k] = v; } };
        const vt = {};
        const b = {
            binding: { key: 'device.g.level' },
            _svg: {
                fgPath: fg, valueText: vt, startAngle: 0, endAngle: Math.PI, radius: 50,
                min: 0, max: 100, unit: '%', gaugeColor: '#0f0', zones: null, showValue: true,
                arcPath: (a, c) => `d${a}-${c}`, polarToCart: () => ({ x: 0, y: 0 }),
            },
        };
        app.state = { 'device.g.level': 50 };
        app.evaluateGaugeValue(b);
        assert(fg.d && fg.d !== '', 'gauge arc drawn for a live value');
        assert(vt.textContent === '50%', `gauge value text, got ${vt.textContent}`);
        delete app.state['device.g.level'];
        app.evaluateGaugeValue(b);
        assert(fg.d === '', 'gauge arc cleared on key delete');
        assert(vt.textContent === '--%', `gauge placeholder on delete, got ${vt.textContent}`);
    },

    h002_meter_reset() {
        const app = mkApp();
        const bar = document.createElement('div');
        for (let i = 0; i < 5; i++) {
            const s = document.createElement('div'); s.className = 'meter-segment'; bar.appendChild(s);
        }
        const b = {
            binding: { key: 'device.m.level' },
            _meter: { segments: 5, min: -60, max: 0, bar, showPeak: true, peakValue: -Infinity, peakTime: 0, peakHoldMs: 1500 },
        };
        app.state = { 'device.m.level': 0 };
        app.evaluateLevelMeterValue(b);
        assert(bar.querySelectorAll('.meter-segment.lit').length > 0, 'meter lit at max');
        delete app.state['device.m.level'];
        app.evaluateLevelMeterValue(b);
        assert(bar.querySelectorAll('.meter-segment.lit').length === 0, 'meter cleared on key delete');
    },

    h002_m005_slider_reset_and_drag() {
        const app = mkApp();
        // The slider input runs in a normalized position domain (0..steps); the
        // binding carries the display range + the position/format closures the
        // real renderSlider builds. For a linear 0..100 step-1 slider, position
        // equals value, so the assertions below read as plain values.
        const min = 0, max = 100, step = 1, steps = 100;
        const input = document.createElement('input');
        input.type = 'range'; input.min = '0'; input.max = String(steps); input.step = '1';
        const valueToPos = (v) => Math.max(0, Math.min(steps, Math.round(((v - min) / (max - min)) * steps)));
        const fmtValue = (v) => String(v);
        const b = {
            element: input, elementDef: { min, max, step }, binding: { key: 'var.vol' },
            fill: null, valueDisplay: null, isVertical: false,
            outputMin: null, outputMax: null, scaleToFull: true,
            steps, valueToPos, fmtValue,
        };
        app.state = { 'var.vol': 75 };
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 75, `slider set to 75, got ${input.value}`);
        // M-005: inbound echo must not move the thumb while dragging.
        input._dragging = true; app.state['var.vol'] = 10;
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 75, 'slider unchanged during drag');
        input._dragging = false;
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 10, 'slider updates once drag ends');
        // H-002: reset to min on delete.
        delete app.state['var.vol'];
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 0, 'slider reset to min on delete');
    },

    h002_select_reset() {
        const app = mkApp();
        const sel = document.createElement('select');
        for (const v of ['a', 'b']) { const o = document.createElement('option'); o.value = v; o.textContent = v; sel.appendChild(o); }
        const b = { element: sel, binding: { key: 'var.sel' } };
        app.state = { 'var.sel': 'b' };
        app.evaluateSelectValue(b);
        assert(sel.value === 'b', 'select set to b');
        delete app.state['var.sel'];
        app.evaluateSelectValue(b);
        assert(sel.value === 'a', 'select falls back to first option on delete');
    },

    h002_textinput_reset() {
        const app = mkApp();
        const input = document.createElement('input'); input.type = 'text';
        const b = { element: input, binding: { key: 'var.t' } };
        app.state = { 'var.t': 'hi' };
        app.evaluateTextInputValue(b);
        assert(input.value === 'hi', 'text input set');
        delete app.state['var.t'];
        app.evaluateTextInputValue(b);
        assert(input.value === '', 'text input cleared on delete');
    },

    h002_fader_reset() {
        const app = mkApp();
        const handle = document.createElement('div');
        const b = { binding: { key: 'var.f' }, _fader: { handle, valueDisplay: null, min: 0, max: 100, unit: '%', horizontal: false, outputMin: null, outputMax: null, scaleToFull: true } };
        app.state = { 'var.f': 50 };
        app.evaluateFaderValue(b);
        assert(handle.style.bottom === '50%', `fader at 50%, got ${handle.style.bottom}`);
        delete app.state['var.f'];
        app.evaluateFaderValue(b);
        assert(handle.style.bottom === '0%', `fader reset to floor on delete, got ${handle.style.bottom}`);
    },

    // §82.4 — fractional-step sliders/faders must not leak binary float noise
    // onto the wire (Math.round(v/step)*step yields e.g. 0.30000000000000004).
    slider_fader_step_no_float_noise() {
        const app = mkApp();
        // The shared snapper both controls now route their outgoing value through.
        assert(app._snapToStep(0.3, 0.1) === 0.3, `0.1-step snap clean, got ${app._snapToStep(0.3, 0.1)}`);
        assert(app._snapToStep(0.30000000000000004, 0.1) === 0.3, 'pre-noised value cleaned');
        assert(app._snapToStep(2.5500000000001, 0.05) === 2.55, `0.05-step snap, got ${app._snapToStep(2.5500000000001, 0.05)}`);
        assert(app._snapToStep(-6.0000000001, 0.5) === -6, `negative snap, got ${app._snapToStep(-6.0000000001, 0.5)}`);
        assert(app._snapToStep(7.4, 1) === 7, 'integer step rounds to whole');
        assert(app._snapToStep(3.14159, 0) === 3.14159, 'no step returns value as-is');

        // End-to-end: a rendered fractional-step slider must send a clean value,
        // proving the snapper is wired into the real render path (not just callable).
        const sent = [];
        app.send = (m) => sent.push(m);
        const el = app.renderSlider({
            id: 's1', type: 'slider', min: 0, max: 1, step: 0.1,
            bindings: { show: { value: { key: 'var.g' } } },
        });
        const input = el.querySelector('input[type=range]');
        // STEPS = round((1-0)/0.1) = 10; position 3 maps to value 0.3.
        input.value = '3';
        input.dispatchEvent(new window.Event('change'));
        assert(sent.length === 1, `one ui.change sent, got ${sent.length}`);
        assert(sent[0].value === 0.3, `slider wire value is clean 0.3, got ${sent[0].value}`);
        assert(String(sent[0].value) === '0.3', `no float noise in wire value, got ${String(sent[0].value)}`);
    },

    // H-003 / L-007 — lock shown once per session (no re-lock on reconnect), and
    // a cleared lock_code removes a stuck overlay.
    h003_l007_lock_reconcile() {
        const app = mkApp();
        app.uiSettings = { lock_code: '1234' };
        app._reconcileLockOnDefinition();
        assert(document.getElementById('lock-overlay'), 'lock shown on first definition');
        // Operator unlocks.
        document.getElementById('lock-overlay').remove(); app.locked = false;
        // A reconnect resends ui.definition — must NOT re-lock.
        app._reconcileLockOnDefinition();
        assert(!document.getElementById('lock-overlay'), 'no re-lock on reconnect after unlock');
        // L-007: lock_code cleared while locked removes the stuck overlay.
        app._lockInitialized = false; app.uiSettings = { lock_code: '1234' };
        app._reconcileLockOnDefinition();
        assert(document.getElementById('lock-overlay'), 'lock re-armed for a fresh session');
        app.uiSettings = { lock_code: '' };
        app._reconcileLockOnDefinition();
        assert(!document.getElementById('lock-overlay'), 'cleared lock_code removes the overlay');
    },

    // H-004 — live state broadcast to a plugin iframe is scoped to its namespace.
    h004_plugin_broadcast_scope() {
        const app = mkApp();
        const mk = (pid) => {
            const el = document.createElement('div');
            el._received = [];
            el._pluginIframe = { contentWindow: { postMessage: (m) => el._received.push(m) } };
            el._pluginId = pid;
            return el;
        };
        const a = mk('a'); const b = mk('b');
        // {el, elementDef} -- the one shape every renderer uses; see
        // plugin_element_map_shape for why this scenario used to build bare nodes.
        app.elementMap = {
            a: { el: a, elementDef: { id: 'a', type: 'plugin' } },
            b: { el: b, elementDef: { id: 'b', type: 'plugin' } },
        };
        app._notifyPluginIframes('plugin.a.x', 1);
        assert(a._received.length === 1 && b._received.length === 0, 'only plugin a receives plugin.a.x');
        app._notifyPluginIframes('device.x.power', 'on');
        assert(a._received.length === 1 && b._received.length === 0, 'no plugin receives a device.* key');
        app._notifyPluginIframes('plugin.b.y', 2);
        assert(b._received.length === 1, 'plugin b receives plugin.b.y');
    },

    // H-005 — the iframe action bridge enforces the plugin's declared capabilities.
    h005_action_capability_gate() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        const element = { id: 'pe1', type: 'plugin', plugin_id: 'myplug', plugin_type: 'widget', plugin_config: {} };
        const el = app.renderPluginElement(element);
        const handler = el._pluginMessageHandler;
        const src = el._pluginIframe.contentWindow;
        const fire = (data) => handler({ source: src, data });

        // device.command without device_command capability is dropped.
        el._pluginCaps = [];
        fire({ type: 'openavc:action', action: 'device.command', device: 'd1', command: 'on', params: {} });
        assert(app.ws.sent.length === 0, 'device.command dropped without capability');
        // ...and forwarded once the capability is declared.
        el._pluginCaps = ['device_command'];
        fire({ type: 'openavc:action', action: 'device.command', device: 'd1', command: 'on', params: {} });
        assert(app.ws.sent.length === 1 && app.ws.sent[0].type === 'command', 'device.command forwarded with capability');

        // state.set is scoped: own namespace needs state_write; var.* needs variable_write; others denied.
        app.ws.sent = [];
        el._pluginCaps = ['state_write'];
        fire({ type: 'openavc:action', action: 'state.set', key: 'plugin.myplug.x', value: 1 });
        assert(app.ws.sent.length === 1, 'own-namespace state.set allowed with state_write');
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.global', value: 1 });
        assert(app.ws.sent.length === 1, 'var.* write denied without variable_write');
        fire({ type: 'openavc:action', action: 'state.set', key: 'device.d1.power', value: 1 });
        assert(app.ws.sent.length === 1, 'device.* write always denied');
        el._pluginCaps = ['variable_write'];
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.global', value: 1 });
        assert(app.ws.sent.length === 2, 'var.* write allowed with variable_write');
    },

    // A plugin element goes into elementMap the same way everything else does, so
    // the things that read an entry's elementDef reach it too. It used to store the
    // bare node, which silently excluded it from macro-busy: the entry had no
    // elementDef, the lookup came back undefined, and the element simply never lit.
    plugin_element_map_shape() {
        const app = mkApp();
        const element = {
            id: 'pe_busy', type: 'plugin', plugin_id: 'myplug', plugin_type: 'widget',
            bindings: { do: { press: [{ action: 'macro', macro: 'lights_up' }] } },
        };
        const el = app.renderPluginElement(element);
        const entry = app.elementMap['pe_busy'];
        assert(entry && entry.el === el, 'entry carries the rendered node as .el');
        assert(entry.elementDef === element, 'entry carries the element definition');

        // The payoff: macro-busy now reaches a plugin element.
        app._runningMacros = { lights_up: true };
        app._updateMacroBusyState('lights_up');
        assert(el.classList.contains('macro-busy'), 'plugin element picks up macro-busy');
        assert(el.getAttribute('data-macro-busy') === 'lights_up', 'busy attribute names the macro');
        app._runningMacros = {};
        app._updateMacroBusyState('lights_up');
        assert(!el.classList.contains('macro-busy'), 'busy state clears when the macro stops');
    },

    // The iframe bridge cannot reach the room while the designer is authoring.
    // It used to write straight to the socket, skipping send()'s edit-mode guard;
    // that only looked safe because edit mode has no socket to write to.
    plugin_bridge_respects_edit_mode() {
        const app = mkApp();
        app.editMode = true;
        // A live socket in edit mode is the situation the design canvas creates
        // once it renders custom controls for real. Nothing may reach it.
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        const element = { id: 'pe2', type: 'plugin', plugin_id: 'myplug', plugin_type: 'widget', plugin_config: {} };
        const el = app.renderPluginElement(element);
        // Edit mode renders a placeholder, so the bridge is not wired at all --
        // that is the first line of defence and worth pinning.
        assert(!el._pluginMessageHandler, 'no bridge handler on the edit-mode placeholder');

        // Second line: even with a fully wired bridge (what 4.5 is about to build),
        // every action it forwards dies at send().
        app.editMode = false;
        const live = app.renderPluginElement({ ...element, id: 'pe3' });
        app.editMode = true;
        const handler = live._pluginMessageHandler;
        const src = live._pluginIframe.contentWindow;
        const fire = (data) => handler({ source: src, data });
        live._pluginCaps = ['device_command', 'state_write', 'variable_write'];
        fire({ type: 'openavc:action', action: 'device.command', device: 'd1', command: 'on', params: {} });
        fire({ type: 'openavc:action', action: 'state.set', key: 'plugin.myplug.x', value: 1 });
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.global', value: 1 });
        assert(app.ws.sent.length === 0, `nothing reaches the room in edit mode, got ${app.ws.sent.length}`);

        // Same handler, out of edit mode, does send -- so the assertion above is
        // about the guard and not about a bridge that never worked.
        app.editMode = false;
        fire({ type: 'openavc:action', action: 'device.command', device: 'd1', command: 'on', params: {} });
        assert(app.ws.sent.length === 1, 'the same action lands once authoring is over');
    },

    // M-001 / L-003 — countdown prefers a live state key over target_time and
    // ignores unparseable dates.
    m001_l003_countdown() {
        const app = mkApp();
        const near = new Date(Date.now() + 5000).toISOString();
        const el = app.renderClock({ id: 'cd', type: 'clock', clock_mode: 'countdown', target_time: '2099-01-01T00:00:00Z', bindings: { show: { value: { key: 'var.cd' } } } });
        if (app._clockInterval) { window.clearInterval(app._clockInterval); app._clockInterval = null; }
        app.state = { 'var.cd': near };
        el._clockUpdate();
        const txt = el.querySelector('.clock-display').textContent;
        assert(txt.length <= 5, `state key wins over target_time (short countdown), got "${txt}"`);
        // L-003: an unparseable value renders the placeholder, not NaN.
        app.state = { 'var.cd': 'not-a-date' };
        const el2 = app.renderClock({ id: 'cd2', type: 'clock', clock_mode: 'countdown', bindings: { show: { value: { key: 'var.cd' } } } });
        if (app._clockInterval) { window.clearInterval(app._clockInterval); app._clockInterval = null; }
        const txt2 = el2.querySelector('.clock-display').textContent;
        assert(txt2 === '--:--:--', `invalid date -> placeholder, got "${txt2}"`);
    },

    // M-004 — conditional text uses a normalized compare (numeric 1 matches '1').
    m004_text_loose_compare() {
        const app = mkApp();
        const el = document.createElement('div');
        const b = { element: el, elementDef: {}, binding: { key: 'var.x', condition: { equals: '1' }, text_true: 'ON', text_false: 'OFF' } };
        app.state = { 'var.x': 1 };
        app.evaluateText(b);
        assert(el.textContent === 'ON', `numeric 1 matches '1', got ${el.textContent}`);
    },

    // L-002 — format replaces every {value} and treats the value literally.
    l002_format_replace_all() {
        const app = mkApp();
        const el = document.createElement('div');
        const b = { element: el, elementDef: {}, binding: { key: 'var.s', format: '[{value}] [{value}]' } };
        app.state = { 'var.s': '$&' };
        app.evaluateText(b);
        assert(el.textContent === '[$&] [$&]', `all placeholders replaced literally, got ${el.textContent}`);
    },

    // A device float crosses the wire at float64 width, so a float32 reading of
    // 0.06 arrives as 0.06000000238418579 and a label printed it whole. The
    // element's display_decimals rounds it, through the format placeholder and
    // through the bare no-format path alike.
    label_display_decimals_rounds_a_numeric_value() {
        const app = mkApp();
        const raw = 0.06000000238418579;

        const withFormat = document.createElement('div');
        const bf = {
            element: withFormat,
            elementDef: { id: 'amps', type: 'label', display_decimals: 2 },
            binding: { key: 'device.amp.ac_line_current', format: '{value} A' },
        };
        app.state = { 'device.amp.ac_line_current': raw };
        app.evaluateText(bf);
        assert(withFormat.textContent === '0.06 A', `rounded inside the format, got ${withFormat.textContent}`);

        const bare = document.createElement('div');
        const bb = {
            element: bare,
            elementDef: { id: 'amps2', type: 'label', display_decimals: 2 },
            binding: { key: 'device.amp.ac_line_current' },
        };
        app.evaluateText(bb);
        assert(bare.textContent === '0.06', `rounded with no format string, got ${bare.textContent}`);

        // Unset is unchanged: a label that never asked still prints the value raw.
        const untouched = document.createElement('div');
        const bu = { element: untouched, elementDef: { id: 'amps3', type: 'label' }, binding: { key: 'device.amp.ac_line_current' } };
        app.evaluateText(bu);
        assert(untouched.textContent === String(raw), `unset label unchanged, got ${untouched.textContent}`);
    },

    // Most labels are bound to text — names, input modes, firmware versions —
    // and a version of "2.10" must not be reformatted into "2.1".
    label_display_decimals_leaves_text_alone() {
        const app = mkApp();
        for (const [value, expected] of [['2.10', '2.10'], ['Blu-ray', 'Blu-ray'], [true, 'true']]) {
            const el = document.createElement('div');
            const b = { element: el, elementDef: { id: 'v', type: 'label', display_decimals: 1 }, binding: { key: 'var.v' } };
            app.state = { 'var.v': value };
            app.evaluateText(b);
            assert(el.textContent === expected, `${JSON.stringify(value)} left alone, got ${el.textContent}`);
        }
    },

    // The gauge readout honours display_decimals, and an unset one draws exactly
    // what it always drew (one decimal, trailing zeros dropped) so no panel
    // built before this moves.
    gauge_display_decimals() {
        const app = mkApp();
        const mkGauge = (extra) => {
            const el = app.renderGauge(Object.assign(
                { id: 'g', type: 'gauge', min: 0, max: 1, unit: ' A', bindings: { show: { value: { key: 'var.a' } } } },
                extra,
            ));
            return { el, b: app.bindings[app.bindings.length - 1] };
        };
        const readout = (el) => el.querySelector('[data-role=gauge-value]').textContent;

        app.state = { 'var.a': 0.06000000238418579 };
        const two = mkGauge({ display_decimals: 2 });
        app.evaluateGaugeValue(two.b);
        assert(readout(two.el) === '0.06 A', `two decimals, got ${readout(two.el)}`);

        const zero = mkGauge({ display_decimals: 0, min: 0, max: 100 });
        app.state = { 'var.a': 72.6 };
        app.evaluateGaugeValue(zero.b);
        assert(readout(zero.el) === '73 A', `zero decimals rounds, got ${readout(zero.el)}`);

        // Unset: a whole number stays whole (not "50.0"), a long float still
        // collapses to one decimal. Both are today's output, unchanged.
        const auto = mkGauge({ min: 0, max: 100 });
        app.state = { 'var.a': 50 };
        app.evaluateGaugeValue(auto.b);
        assert(readout(auto.el) === '50 A', `unset drops trailing zeros, got ${readout(auto.el)}`);
        app.state = { 'var.a': 0.06000000238418579 };
        app.evaluateGaugeValue(auto.b);
        assert(readout(auto.el) === '0.1 A', `unset rounds to one decimal, got ${readout(auto.el)}`);
    },

    // A project value toFixed would throw on must not take the render pass down.
    display_decimals_out_of_range_cannot_throw() {
        const app = mkApp();
        for (const bad of [-3, 999, 'lots', NaN]) {
            const el = document.createElement('div');
            const b = { element: el, elementDef: { id: 'x', type: 'label', display_decimals: bad }, binding: { key: 'var.n' } };
            app.state = { 'var.n': 1.23456 };
            app.evaluateText(b);   // throwing here fails the scenario
            assert(el.textContent.length > 0, `${JSON.stringify(bad)} still renders something`);
        }
        assert(app._displayDecimals({ display_decimals: -3 }) === 0, 'negative clamps to 0');
        assert(app._displayDecimals({ display_decimals: 999 }) === 20, 'huge clamps to 20');
        assert(app._displayDecimals({ display_decimals: 'lots' }) === null, 'non-numeric reads as unset');
        assert(app._displayDecimals({}) === null, 'absent reads as unset');
    },

    // L-004 — the reconnect backoff cap field is wired up.
    l004_max_reconnect_delay() {
        assert(mkApp().maxReconnectDelay === 30000, 'maxReconnectDelay is 30000');
    },

    // L-005 — status LED is inactive for off-like values, not just literal 'off'.
    l005_status_led_active() {
        const app = mkApp();
        const cases = [[0, false], ['off', false], [false, false], ['', false], ['on', true], [1, true]];
        for (const [val, expectActive] of cases) {
            const el = document.createElement('div');
            const b = { element: el, binding: { key: 'var.l', map: {}, default: '#999' } };
            app.state = { 'var.l': val };
            app.evaluateColor(b);
            assert(el.classList.contains('active') === expectActive, `value ${JSON.stringify(val)} active=${expectActive}`);
        }
    },

    // L-009 — _activeAudio is capped so it can't grow unbounded.
    l009_audio_cap() {
        const app = mkApp();
        for (let i = 0; i < 12; i++) app._playSound(`http://x/${i}.mp3`, 1);
        assert(app._activeAudio.size <= 8, `audio set capped, size=${app._activeAudio.size}`);
    },

    // M-006 — meeting timer baseline survives a re-render (doesn't restart).
    m006_meeting_baseline_persists() {
        const app = mkApp();
        app.renderClock({ id: 'mt', type: 'clock', clock_mode: 'meeting', duration_minutes: 60 });
        if (app._clockInterval) { window.clearInterval(app._clockInterval); app._clockInterval = null; }
        const first = app._meetingStartTimes.mt;
        assert(first, 'meeting start anchored on first render');
        app.renderClock({ id: 'mt', type: 'clock', clock_mode: 'meeting', duration_minutes: 60 });
        if (app._clockInterval) { window.clearInterval(app._clockInterval); app._clockInterval = null; }
        assert(app._meetingStartTimes.mt === first, 'meeting start unchanged across re-render');
    },

    // M-007 — a ui.* override reverts to the rendered base when its key is deleted.
    m007_ui_override_revert() {
        const app = mkApp();
        const el = document.createElement('div');
        el.style.backgroundColor = 'red';
        app.elementMap = { b1: { el, elementDef: { label: 'Base' } } };
        app.state = { 'ui.b1.bg_color': 'blue' };
        app.evaluateUiOverrides();
        assert(el.style.backgroundColor === 'blue', 'override applied');
        delete app.state['ui.b1.bg_color'];
        app.evaluateUiOverrides();
        assert(el.style.backgroundColor === 'red', `override reverted to base, got ${el.style.backgroundColor}`);
    },

    // M-010 / M-011 — CSS sanitizers neutralize breakout while keeping valid input.
    m010_m011_css_sanitizers() {
        const app = mkApp();
        const v = app._sanitizeCssValue('red); background-image: url(http://evil)');
        assert(!/url\s*\(/i.test(v) && !v.includes(';'), `value breakout neutralized, got "${v}"`);
        const rgb = app._sanitizeCssValue('rgb(10, 20, 30)');
        assert(rgb.includes('rgb(') && rgb.includes(','), `rgb() preserved, got "${rgb}"`);
        const u1 = app._sanitizeCssUrl('http://x/y z").evil');
        assert(!u1.includes('"') && !u1.includes(')'), `url breakout neutralized, got "${u1}"`);
        assert(app._sanitizeCssUrl('javascript:alert(1)') === '', 'javascript: url rejected');
        assert(app._sanitizeCssUrl('data:text/html,x') === '', 'data:text url rejected');
        assert(app._sanitizeCssUrl('/api/projects/default/assets/a.png') === '/api/projects/default/assets/a.png', 'relative asset url preserved');
    },

    // M-002 / M-003 — dismissing an overlay unregisters its clock update
    // closures and removes its plugin iframe message listeners.
    m002_m003_overlay_cleanup() {
        const app = mkApp();
        const overlay = document.createElement('div');
        overlay.className = 'panel-overlay';
        overlay.dataset.pageId = 'ov1';
        const clock = document.createElement('div');
        clock.className = 'panel-clock';
        const clockFn = () => {};
        clock._clockUpdate = clockFn;
        app._clockElements.push(clockFn);
        overlay.appendChild(clock);
        const plug = document.createElement('div');
        plug.className = 'panel-plugin';
        const handler = () => {};
        plug._pluginMessageHandler = handler;
        app._pluginMessageHandlers.add(handler);
        overlay.appendChild(plug);
        document.body.appendChild(overlay);
        app.overlayStack = ['ov1'];

        app.dismissOverlay();
        assert(!app._clockElements.includes(clockFn), 'overlay clock closure unregistered on dismiss');
        assert(!app._pluginMessageHandlers.has(handler), 'overlay plugin listener removed on dismiss');
    },

    // M-008 / L-006 — going offline clears the idle timer and disables open
    // overlays; reconnecting re-enables them.
    m008_l006_offline_handling() {
        const app = mkApp();
        const overlay = document.createElement('div');
        overlay.className = 'panel-overlay';
        document.body.appendChild(overlay);
        app.idleTimer = window.setTimeout(() => {}, 100000);
        app.setConnectionStatus(false);
        assert(app._offline === true, 'offline flag set');
        assert(app.idleTimer === null, 'idle timer cleared while offline');
        assert(overlay.style.pointerEvents === 'none', 'open overlay disabled offline');
        app.setConnectionStatus(true);
        if (app._statusHideTimer) window.clearTimeout(app._statusHideTimer);
        assert(app._offline === false, 'online flag cleared on reconnect');
        assert(overlay.style.pointerEvents === '', 'overlay re-enabled on reconnect');
    },

    // Select per-option styling (show.look.style_map) — the Appearance card
    // authors it and the docs promise it; the control must take the matched
    // option's colors and drop them when nothing matches.
    select_look_applies_matching_option_style() {
        const app = mkApp();
        const sel = document.createElement('select');
        const b = {
            element: document.createElement('div'),
            select: sel,
            elementDef: { style: {} },
            binding: {
                source: 'state',
                key: 'var.scene',
                style_map: { movie: { bg_color: '#ff0000', text_color: '#ffffff' } },
            },
        };
        app.state = { 'var.scene': 'movie' };
        app.evaluateSelectLook(b);
        assert(sel.style.backgroundColor !== '', 'matched option bg applied');
        assert(sel.style.color !== '', 'matched option text color applied');
        // A value with no configured style returns the control to the theme.
        app.state['var.scene'] = 'tv';
        app.evaluateSelectLook(b);
        assert(sel.style.backgroundColor === '', 'bg cleared on unmapped value');
        assert(sel.style.color === '', 'text color cleared on unmapped value');
        // Key deleted (device offline / var removed) — same fallback.
        app.state['var.scene'] = 'movie';
        app.evaluateSelectLook(b);
        delete app.state['var.scene'];
        app.evaluateSelectLook(b);
        assert(sel.style.backgroundColor === '', 'bg cleared on key delete');
    },

    select_look_registered_and_dispatched() {
        const app = mkApp();
        const element = {
            id: 's1', type: 'select',
            options: [{ value: 'movie', label: 'Movie' }, { value: 'tv', label: 'TV' }],
            bindings: {
                show: {
                    look: {
                        source: 'state', key: 'var.scene',
                        style_map: { movie: { bg_color: '#123456', text_color: '#ffffff' } },
                    },
                },
            },
        };
        const el = app.renderSelect(element);
        const sel = el.querySelector('select');
        assert(app.bindings.some((x) => x.type === 'select_look'), 'select_look binding registered');
        // Option rows carry their configured colors for browsers that
        // support styling native options.
        const movieOpt = sel.querySelector('option[value="movie"]');
        assert(movieOpt.style.backgroundColor !== '', 'option row carries its configured bg');
        // A state change for the bound key flows through the dispatch loop.
        app.state = { 'var.scene': 'movie' };
        app.evaluateAllBindings(['var.scene']);
        assert(sel.style.backgroundColor !== '', 'dispatch applies the matched style to the control');
    },

    // L-001 — degenerate ranges don't produce NaN.
    l001_divide_by_zero_guards() {
        const app = mkApp();
        const fg = { setAttribute(k, v) { this[k] = v; } };
        const vt = {};
        const b = {
            binding: { key: 'var.g' },
            _svg: { fgPath: fg, valueText: vt, startAngle: 0, endAngle: 1, radius: 10, min: 50, max: 50, unit: '%', gaugeColor: '#0f0', zones: null, showValue: true, arcPath: () => 'd', polarToCart: () => ({ x: 0, y: 0 }) },
        };
        app.state = { 'var.g': 50 };
        app.evaluateGaugeValue(b);
        assert(!String(vt.textContent).includes('NaN'), `gauge min==max no NaN, got ${vt.textContent}`);
        // level meter span 0
        const bar = document.createElement('div');
        const seg = document.createElement('div'); seg.className = 'meter-segment'; bar.appendChild(seg);
        const mb = { binding: { key: 'var.m' }, _meter: { segments: 1, min: 0, max: 0, bar, showPeak: false, peakValue: -Infinity, peakTime: 0, peakHoldMs: 1500 } };
        app.state['var.m'] = 0;
        app.evaluateLevelMeterValue(mb); // must not throw
    },

    // --- Layout engine (percentage geometry, 0.8.0) ---------------------

    // The one deletion in the layout sweep that would break rendering
    // silently: without a stacking rule, page elements paint *behind* the
    // page's background layers and nothing says so. Asserted on computed
    // style so it survives the selector being rewritten.
    layout_elements_paint_above_page_background() {
        const app = mkApp();
        renderProject(app, project({
            background: { color: '#101010', gradient: { from: '#000', to: '#fff', angle: 90 } },
            elements: [el('b1', 'button')],
            placements: { b1: { x: 10, y: 10, w: 20, h: 20 } },
        }));
        const surface = document.querySelector('#panel-root .panel-page');
        const node = surface.querySelector('[data-element-id="b1"]');
        const image = surface.querySelector('.panel-page-bg-gradient');
        assert(image, 'the page painted a background layer to sit in front of');
        const elZ = Number(window.getComputedStyle(node).zIndex);
        const bgZ = Number(image.style.zIndex);
        assert(Number.isFinite(elZ), `element must carry a numeric z-index, got ${elZ}`);
        assert(elZ > bgZ, `element z-index ${elZ} must beat background layer ${bgZ}`);
    },

    // Geometry is four percentages of the parent box, on every path.
    layout_placement_is_percentages() {
        const app = mkApp();
        renderProject(app, project({
            elements: [el('b1', 'button')],
            placements: { b1: { x: 12.5, y: 20, w: 30, h: 15 } },
        }));
        const node = document.querySelector('[data-element-id="b1"]');
        assert(node.style.position === 'absolute', `absolute, got ${node.style.position}`);
        assert(node.style.left === '12.5%', `left, got ${node.style.left}`);
        assert(node.style.top === '20%', `top, got ${node.style.top}`);
        assert(node.style.width === '30%', `width, got ${node.style.width}`);
        assert(node.style.height === '15%', `height, got ${node.style.height}`);
        assert(!node.style.gridColumn, 'no grid placement survives');
        const surface = document.querySelector('#panel-root .panel-page');
        assert(!surface.style.gridTemplateColumns, 'the page is not a grid');
        assert(!surface.style.gap, 'the page has no gap');
    },

    // Portrait glass picks the portrait layout; landscape picks the primary.
    layout_selected_by_orientation() {
        const app = mkApp();
        const proj = project({
            elements: [el('b1', 'button')],
            placements: { b1: { x: 0, y: 0, w: 50, h: 50 } },
            extraLayouts: [{
                id: 'portrait', orientation: 'portrait', inherits: 'landscape',
                placements: { b1: { x: 5, y: 60, w: 90, h: 10 } }, hidden: [],
            }],
        });
        setViewport(1280, 800);
        renderProject(app, proj);
        assert(document.querySelector('[data-element-id="b1"]').style.top === '0%',
            'landscape viewport uses the primary layout');
        setViewport(800, 1280);
        app.renderCurrentPage();
        assert(document.querySelector('[data-element-id="b1"]').style.top === '60%',
            'portrait viewport uses the portrait layout');
        setViewport(1280, 800);
    },

    // No layout matches the viewport -> the primary is the answer, always.
    layout_falls_back_to_primary() {
        const app = mkApp();
        setViewport(800, 1280);
        renderProject(app, project({
            elements: [el('b1', 'button')],
            placements: { b1: { x: 7, y: 8, w: 9, h: 10 } },
        }));
        assert(document.querySelector('[data-element-id="b1"]').style.left === '7%',
            'portrait viewport with only a landscape layout still renders it');
        setViewport(1280, 800);
    },

    // A secondary layout stores deltas: what it doesn't mention follows the
    // layout it inherits from, and its hidden list applies.
    layout_inherits_merges_deltas() {
        const app = mkApp();
        setViewport(800, 1280);
        renderProject(app, project({
            elements: [el('moved', 'button'), el('stayed', 'button'), el('gone', 'label')],
            placements: {
                moved: { x: 1, y: 1, w: 10, h: 10 },
                stayed: { x: 40, y: 41, w: 12, h: 13 },
                gone: { x: 80, y: 80, w: 10, h: 10 },
            },
            extraLayouts: [{
                id: 'portrait', orientation: 'portrait', inherits: 'landscape',
                placements: { moved: { x: 90, y: 91, w: 5, h: 6 } },
                hidden: ['gone'],
            }],
        }));
        const moved = document.querySelector('[data-element-id="moved"]');
        const stayed = document.querySelector('[data-element-id="stayed"]');
        assert(moved.style.left === '90%', `delta wins, got ${moved.style.left}`);
        assert(stayed.style.left === '40%', `untouched element inherits, got ${stayed.style.left}`);
        assert(!document.querySelector('[data-element-id="gone"]'), 'hidden element is not rendered');
        setViewport(1280, 800);
    },

    // Containers are real: children render inside the container's node, so
    // their percentages are percentages of it and the group moves as one.
    layout_container_children_render_inside_parent() {
        const app = mkApp();
        renderProject(app, project({
            elements: [
                el('box', 'group'),
                Object.assign(el('inner', 'button'), { parent: 'box' }),
                el('outer', 'button'),
            ],
            placements: {
                box: { x: 10, y: 10, w: 50, h: 50 },
                inner: { x: 25, y: 50, w: 50, h: 25 },
                outer: { x: 70, y: 70, w: 10, h: 10 },
            },
        }));
        const surface = document.querySelector('#panel-root .panel-page');
        const box = surface.querySelector('[data-element-id="box"]');
        const inner = box.querySelector('[data-element-id="inner"]');
        assert(inner, 'the child renders into its container, not the page');
        assert(inner.style.left === '25%', 'the child is a percentage of the container');
        assert(box.style.overflow === 'visible', 'a container does not clip its children');
        assert(surface.querySelector(':scope > [data-element-id="outer"]'),
            'a page-level element still renders on the page');
        assert(!surface.querySelector(':scope > [data-element-id="inner"]'),
            'the child is not also a peer on the page');
    },

    // A container's box and its contents' coordinate space have to be the SAME
    // box. CSS resolves an absolutely positioned child against its ancestor's
    // padding box -- border box minus the border -- so the theme's 1px frame
    // would quietly move everything inside in by a pixel and shrink it by two,
    // while the builder measures against the rectangle it drew. An outline
    // draws the same frame and takes no space.
    layout_container_border_does_not_shift_its_contents() {
        const app = mkApp();
        renderProject(app, project({
            elements: [
                Object.assign(el('box', 'group'), { style: { border_width: 1, border_color: '#123456' } }),
                Object.assign(el('inner', 'button'), { parent: 'box' }),
                Object.assign(el('leaf', 'button'), { style: { border_width: 1, border_color: '#123456' } }),
            ],
            placements: {
                box: { x: 10, y: 10, w: 50, h: 50 },
                inner: { x: 0, y: 0, w: 100, h: 100 },
                leaf: { x: 70, y: 70, w: 10, h: 10 },
            },
        }));
        const box = document.querySelector('[data-element-id="box"]');
        const leaf = document.querySelector('[data-element-id="leaf"]');
        assert(parseFloat(box.style.borderWidth) === 0, `a container carries no border width, got ${box.style.borderWidth}`);
        assert(box.style.outline.includes('rgb(18, 52, 86)') && box.style.outline.includes('solid'),
            `the frame survives as an outline, got ${box.style.outline}`);
        assert(/^calc\(0px - /.test(box.style.outlineOffset),
            `the outline draws inside the box, got ${box.style.outlineOffset}`);
        // A leaf element's border is nobody's frame of reference, so it keeps it.
        assert(leaf.style.borderWidth && leaf.style.borderWidth !== '0',
            `a leaf keeps its border, got ${leaf.style.borderWidth}`);
        assert(!leaf.style.outline, `a leaf gets no outline, got ${leaf.style.outline}`);
    },

    // The same rule, for a border that never went through applyStyle. A theme
    // writes borders inline, but a stylesheet can put one on a container just
    // as easily -- panel-elements.css already does, and css_class plus a
    // project stylesheet is a reserved hook -- and one that isn't converted
    // shifts every child inside it by its width. This is why the conversion
    // reads computed style and runs after the page is in the document.
    layout_container_border_from_a_stylesheet_is_converted_too() {
        const sheet = document.createElement('style');
        sheet.textContent = '[data-element-id="ssbox"] { border: 3px solid rgb(1, 2, 3); }';
        document.head.appendChild(sheet);
        try {
            const app = mkApp();
            renderProject(app, project({
                elements: [
                    el('ssbox', 'group'),
                    Object.assign(el('inner', 'button'), { parent: 'ssbox' }),
                ],
                placements: {
                    ssbox: { x: 10, y: 10, w: 50, h: 50 },
                    inner: { x: 0, y: 0, w: 100, h: 100 },
                },
            }));
            const box = document.querySelector('[data-element-id="ssbox"]');
            assert(box, 'the container rendered');
            assert(window.getComputedStyle(box).borderTopWidth === '0px',
                `a stylesheet border is taken off the container too, got ${window.getComputedStyle(box).borderTopWidth}`);
            assert(box.style.outline.includes('3px') && box.style.outline.includes('rgb(1, 2, 3)'),
                `the stylesheet frame survives as an outline, got ${box.style.outline}`);
            assert(/^calc\(0px - /.test(box.style.outlineOffset),
                `the outline draws inside the box, got ${box.style.outlineOffset}`);
        } finally {
            sheet.remove();
        }
    },

    layout_containers_nest() {
        const app = mkApp();
        renderProject(app, project({
            elements: [
                el('outer', 'group'),
                Object.assign(el('middle', 'group'), { parent: 'outer' }),
                Object.assign(el('leaf', 'button'), { parent: 'middle' }),
            ],
            placements: {
                outer: { x: 0, y: 0, w: 80, h: 80 },
                middle: { x: 10, y: 10, w: 50, h: 50 },
                leaf: { x: 20, y: 20, w: 40, h: 40 },
            },
        }));
        const leaf = document.querySelector(
            '[data-element-id="outer"] [data-element-id="middle"] [data-element-id="leaf"]');
        assert(leaf, 'containers nest');
    },

    // A parent cycle is representable in a hand-edited file. The panel still
    // has to draw something rather than blow the stack.
    layout_parent_cycle_does_not_hang() {
        const app = mkApp();
        renderProject(app, project({
            elements: [
                Object.assign(el('a', 'group'), { parent: 'b' }),
                Object.assign(el('b', 'group'), { parent: 'a' }),
                el('free', 'button'),
            ],
            placements: {
                a: { x: 0, y: 0, w: 10, h: 10 },
                b: { x: 0, y: 0, w: 10, h: 10 },
                free: { x: 50, y: 50, w: 10, h: 10 },
            },
        }));
        assert(document.querySelector('[data-element-id="free"]'),
            'an unrelated element still renders when two containers name each other');
    },

    // An aspect-locked element holds its ratio when its box is stretched.
    layout_aspect_lock_centres_within_its_box() {
        const app = mkApp();
        renderProject(app, project({
            elements: [
                Object.assign(el('led', 'status_led'), { aspect_lock: 1 }),
                el('btn', 'button'),
            ],
            placements: {
                led: { x: 10, y: 10, w: 20, h: 8 },
                btn: { x: 50, y: 50, w: 20, h: 8 },
            },
        }));
        const surface = document.querySelector('#panel-root .panel-page');
        const box = surface.querySelector('.panel-placement[data-placement-for="led"]');
        assert(box, 'an aspect-locked element gets a placement box to shrink inside');
        assert(box.style.width === '20%' && box.style.height === '8%',
            'the placement box takes the layout rect');
        const led = box.querySelector('[data-element-id="led"]');
        assert(led, 'the element sits inside its placement box');
        assert(String(led.style.aspectRatio) === '1', `led holds 1:1, got ${led.style.aspectRatio}`);
        const btn = surface.querySelector('[data-element-id="btn"]');
        assert(!btn.style.aspectRatio, 'a button stretches freely');
        assert(!surface.querySelector('.panel-placement[data-placement-for="btn"]'),
            'an unlocked element needs no placement box');
    },

    // No per-type default in the renderer: a migrated project's elements keep
    // the rect they were authored with. The default belongs to the palette.
    layout_unlocked_elements_take_their_box() {
        const app = mkApp();
        renderProject(app, project({
            elements: [
                el('led', 'status_led'),
                Object.assign(el('zero', 'status_led'), { aspect_lock: 0 }),
            ],
            placements: {
                led: { x: 0, y: 0, w: 20, h: 8 },
                zero: { x: 0, y: 40, w: 20, h: 8 },
            },
        }));
        const led = document.querySelector('[data-element-id="led"]');
        assert(!led.style.aspectRatio, 'no ratio is invented for an unlocked element');
        assert(led.style.width === '20%', 'it takes its authored box directly');
        const zero = document.querySelector('[data-element-id="zero"]');
        assert(!zero.style.aspectRatio, 'a stored 0 never becomes aspect-ratio: 0');
        assert(zero.style.width === '20%', 'and stretches like any unlocked element');
    },

    // Overlays run the same renderer. They used to carry a second copy of the
    // placement math and were the last part of a panel measured in hard px.
    layout_overlay_uses_percentages() {
        const app = mkApp();
        const proj = project({ elements: [], placements: {} });
        proj.ui.pages.push({
            id: 'pop', name: 'Pop', page_type: 'overlay',
            overlay: { width: 40, height: 50, position: 'center' },
            snap: { enabled: false },
            elements: [el('ok', 'button')],
            layouts: [{
                id: 'landscape', orientation: 'landscape', primary: true,
                placements: { ok: { x: 10, y: 60, w: 80, h: 30 } }, hidden: [],
            }],
        });
        renderProject(app, proj);
        app.renderOverlay(proj.ui.pages[1]);
        const content = document.querySelector('.panel-overlay .overlay-content');
        assert(content.style.width === '40%', `overlay box is a viewport %, got ${content.style.width}`);
        assert(content.style.height === '50%', `overlay box height, got ${content.style.height}`);
        const surface = content.querySelector('.panel-page');
        assert(!surface.style.gridTemplateColumns, 'the overlay surface is not a grid');
        const ok = surface.querySelector('[data-element-id="ok"]');
        assert(ok.style.left === '10%' && ok.style.height === '30%',
            'overlay contents are percentages of the overlay box');
        app.dismissAllOverlays();
    },

    // Masters carry their own orientation-keyed placements, valid on every
    // page, and sit behind the page's own elements by DOM order alone.
    layout_master_elements_place_by_orientation() {
        const app = mkApp();
        const proj = project({
            elements: [el('b1', 'button')],
            placements: { b1: { x: 50, y: 50, w: 10, h: 10 } },
        });
        proj.ui.master_elements = [{
            id: 'topbar', type: 'label', text: 'Bar', pages: '*',
            placements: {
                landscape: { x: 0, y: 0, w: 100, h: 8 },
                portrait: { x: 0, y: 0, w: 100, h: 5 },
            },
        }];
        setViewport(1280, 800);
        renderProject(app, proj);
        const surface = document.querySelector('#panel-root .panel-page');
        const master = surface.querySelector('[data-element-id="topbar"]');
        assert(master.style.height === '8%', `landscape master, got ${master.style.height}`);
        assert(!master.style.zIndex,
            'a master no longer carries an inline z-index that drops it behind the page gradient');
        const kids = Array.from(surface.children);
        assert(kids.indexOf(master) < kids.indexOf(surface.querySelector('[data-element-id="b1"]')),
            'masters come first so DOM order keeps them behind page elements');
        setViewport(800, 1280);
        app.renderCurrentPage();
        assert(document.querySelector('[data-element-id="topbar"]').style.height === '5%',
            'portrait master placement');
        setViewport(1280, 800);
    },

    // The snap overlay is a ruler drawn from page.snap. Showing it, hiding it
    // or changing the increment moves nothing.
    layout_snap_overlay_follows_page_snap() {
        const app = mkApp();
        app.editMode = true;
        const proj = project({
            elements: [el('b1', 'button')],
            placements: { b1: { x: 10, y: 10, w: 10, h: 10 } },
            snap: { enabled: true, x: 8.3333, y: 12.5 },
        });
        renderProject(app, proj);
        const overlay = document.querySelector('.panel-page-snap-overlay');
        assert(overlay, 'edit mode draws the snap overlay');
        assert(overlay.style.backgroundSize === '8.3333% 12.5%',
            `overlay steps match page.snap, got ${overlay.style.backgroundSize}`);
        const before = document.querySelector('[data-element-id="b1"]').style.left;

        // Snapping off does NOT hide the ruler: the grid toggle controls what
        // you see, the snap toggle what pulls. Keying the overlay off
        // snap.enabled made the builder's grid button do nothing on a page
        // with snapping switched off.
        proj.ui.pages[0].snap = { enabled: false, x: 8.3333, y: 12.5 };
        app.renderCurrentPage();
        assert(document.querySelector('.panel-page-snap-overlay'),
            'the ruler still draws with snapping off');
        assert(document.querySelector('[data-element-id="b1"]').style.left === before,
            'turning snap off moves no element');

        // The builder's grid button is what hides it.
        app._editShowGrid = false;
        app.renderCurrentPage();
        assert(!document.querySelector('.panel-page-snap-overlay'),
            'grid toggled off draws nothing');
        app._editShowGrid = true;
        app.editMode = false;
    },

    // The migration writes rem, so the renderer has to read rem. Left as px
    // this renders a migrated project at a fourteenth of its size.
    layout_style_units_are_rem() {
        const app = mkApp();
        const node = document.createElement('div');
        app.applyStyle(node, {
            font_size: 24 / 14, padding: 8 / 14, border_radius: 4 / 14,
            border_width: 1 / 14, letter_spacing: 2 / 14,
        });
        assert(node.style.fontSize.endsWith('rem'), `font-size in rem, got ${node.style.fontSize}`);
        assert(node.style.padding.includes('rem'), `padding in rem, got ${node.style.padding}`);
        assert(node.style.borderRadius.endsWith('rem'), `radius in rem, got ${node.style.borderRadius}`);
        // Borders scale but never vanish: a 1px hairline would be 0.0714rem,
        // which on small glass rounds away to nothing.
        assert(node.style.borderWidth.includes('rem') && node.style.borderWidth.includes('1px'),
            `border scales with a 1px floor, got ${node.style.borderWidth}`);
        assert(node.style.letterSpacing.endsWith('rem'), `tracking in rem, got ${node.style.letterSpacing}`);
    },

    // The panel stylesheets are rem so a design scales with the glass. The
    // exceptions are deliberate and narrow -- border/outline widths and
    // anything <= 2px, which exist to be thin lines and would round away on a
    // phone. Anything else left in px is a value that silently stops scaling.
    layout_stylesheets_are_rem_except_hairlines() {
        const offenders = [];
        // Strip comments first. Prose mentions pixel sizes ("14px at the
        // reference"), and a line-by-line skip misses the middle of a
        // multi-line comment -- which is exactly how the sweep that wrote
        // this rule managed to rewrite its own documentation.
        for (const line of css.replace(/\/\*[\s\S]*?\*\//g, '').split('\n')) {
            const decl = line.trim();
            if (!decl.includes(':')) continue;
            const prop = decl.split(':')[0].trim();
            if (/^(border|outline)(-(top|right|bottom|left))?(-width)?$/.test(prop)) continue;
            for (const m of decl.matchAll(/(?<![\w.-])(\d+(?:\.\d+)?)px/g)) {
                if (parseFloat(m[1]) > 2) offenders.push(`${prop}: ${m[0]}`);
            }
        }
        assert(offenders.length === 0,
            `panel stylesheets must be rem; found ${offenders.length}: ${offenders.slice(0, 6).join(', ')}`);
    },

    // The type scale has to land on the old base size at the reference, or
    // every rem below it is off by a constant and the migration's divide-by-14
    // stops being exact.
    layout_type_scale_calibration() {
        const root = /:root\s*\{[^}]*font-size:\s*calc\(var\(--panel-type-scale,\s*([\d.]+)\)/.exec(css);
        assert(root, 'the :root type scale rule is present');
        const scale = parseFloat(root[1]);
        // vmin at the 1280x800 reference is 8px.
        assert(Math.abs(scale * 8 - 14) < 0.001,
            `${scale}vmin must be 14px at the reference, got ${scale * 8}`);
    },

    // The builder previews an overlay page in a box the size of the overlay,
    // not the screen, so it pins the type scale to the preset's vmin.
    layout_vmin_override_hook() {
        const app = mkApp();
        const root = document.documentElement;
        app._applyVminOverride('8');
        assert(root.style.getPropertyValue('--panel-vmin') === '8px',
            `override sets a concrete px vmin, got ${root.style.getPropertyValue('--panel-vmin')}`);
        app._applyVminOverride(null);
        assert(root.style.getPropertyValue('--panel-vmin') === '',
            'clearing hands the scale back to the viewport');
    },

    // --- Power-user hooks: element.css_class + ui.custom_css ----------------

    // The author's classes reach the rendered node, alongside the ones the
    // renderer puts there itself.
    power_css_class_on_element() {
        const app = mkApp();
        renderProject(app, project({
            elements: [el('tile', 'button', { css_class: 'brand-tile accent' })],
            placements: { tile: { x: 0, y: 0, w: 50, h: 50 } },
        }));
        const node = app.root.querySelector('[data-element-id="tile"]');
        assert(node, 'the element rendered');
        assert(node.classList.contains('brand-tile'), 'first authored class applied');
        assert(node.classList.contains('accent'), 'second authored class applied');
        assert(node.classList.contains('panel-element'),
            'the renderer\'s own classes survive');
    },

    // Master elements go through the same placement chokepoint, so they get the
    // hook too -- they are the page furniture most likely to want restyling.
    power_css_class_on_master() {
        const app = mkApp();
        const proj = project({ elements: [], placements: {} });
        proj.ui.master_elements = [Object.assign(
            el('logo', 'label', { css_class: 'brand-logo' }),
            { pages: '*', placements: { landscape: { x: 0, y: 0, w: 20, h: 10 } } },
        )];
        renderProject(app, proj);
        const node = app.root.querySelector('[data-element-id="logo"]');
        assert(node, 'the master element rendered');
        assert(node.classList.contains('brand-logo'), 'authored class applied to a master');
    },

    // An aspect-locked element is wrapped in a placement box. The class belongs
    // on the element that was styled, not on the box that positions it.
    power_css_class_under_aspect_lock() {
        const app = mkApp();
        renderProject(app, project({
            elements: [el('led', 'status_led', { css_class: 'brand-led', aspect_lock: 1.0 })],
            placements: { led: { x: 0, y: 0, w: 40, h: 20 } },
        }));
        const box = app.root.querySelector('.panel-placement');
        assert(box, 'the aspect-lock wrapper exists');
        assert(!box.classList.contains('brand-led'),
            'the wrapper is not what the author styled');
        const node = app.root.querySelector('[data-element-id="led"]');
        assert(node && node.classList.contains('brand-led'),
            'the element inside carries the class');
    },

    // The field is hand-written today, so it will arrive ragged. Stray
    // whitespace must not add empty classes, and an invalid name must not take
    // the whole page down.
    power_css_class_tolerates_ragged_input() {
        const app = mkApp();
        renderProject(app, project({
            elements: [el('tile', 'button', { css_class: '  spaced   out  ' })],
            placements: { tile: { x: 0, y: 0, w: 50, h: 50 } },
        }));
        const node = app.root.querySelector('[data-element-id="tile"]');
        assert(node.classList.contains('spaced') && node.classList.contains('out'),
            'both real tokens applied');
        for (const name of node.classList) {
            assert(name.trim() !== '', 'no empty class name was added');
        }
    },

    // The project stylesheet lands in the head, after the panel's own sheets so
    // it wins ties, and carries the authored text verbatim.
    power_custom_css_injected() {
        const app = mkApp();
        const proj = project({
            elements: [el('tile', 'button', { css_class: 'brand-tile' })],
            placements: { tile: { x: 0, y: 0, w: 50, h: 50 } },
        });
        proj.ui.custom_css = '.brand-tile { border-radius: 0; }';
        renderProject(app, proj);
        const style = document.getElementById('panel-custom-css');
        assert(style, 'the stylesheet node exists');
        assert(style.parentNode === document.head, 'it lives in the head');
        assert(style.textContent === '.brand-tile { border-radius: 0; }',
            `text is verbatim, got ${style.textContent}`);
        assert(document.head.lastElementChild === style,
            'it comes last so it wins ties against panel-elements.css');
    },

    // Re-rendering must not stack up style nodes, and clearing the field must
    // actually take the rules away rather than leave the last ones applied.
    power_custom_css_replaced_and_cleared() {
        const app = mkApp();
        const proj = project({ elements: [], placements: {} });
        proj.ui.custom_css = '.a { color: red; }';
        renderProject(app, proj);
        proj.ui.custom_css = '.b { color: blue; }';
        renderProject(app, proj);
        assert(document.querySelectorAll('#panel-custom-css').length === 1,
            'exactly one stylesheet node, not one per render');
        assert(document.getElementById('panel-custom-css').textContent === '.b { color: blue; }',
            'the node was updated in place');
        proj.ui.custom_css = '';
        renderProject(app, proj);
        assert(!document.getElementById('panel-custom-css'),
            'clearing the field removes the sheet');
    },
};

const results = {};
for (const [name, fn] of Object.entries(tests)) {
    try { fn(); results[name] = { pass: true }; }
    catch (e) { results[name] = { pass: false, error: String(e && e.message), stack: (e && e.stack || '').split('\n').slice(0, 4).join(' | ') }; }
}
// Exit explicitly once stdout is flushed so jsdom/lingering timers from the
// scenarios don't keep the process alive.
process.stdout.write(JSON.stringify(results, null, 2), () => process.exit(0));
