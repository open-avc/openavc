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

    // H-004 — live state broadcast to an iframe element is scoped to what that
    // element may see. Rendered through the real renderers rather than
    // hand-built nodes, so the scoping under test is the shipped rule and not
    // one the scenario set up for itself.
    h004_plugin_broadcast_scope() {
        const app = mkApp();
        const record = (el) => {
            el._received = [];
            el._pluginIframe = { contentWindow: { postMessage: (m) => el._received.push(m) } };
            return el;
        };
        const a = record(app.renderPluginElement({ id: 'a', type: 'plugin', plugin_id: 'a', plugin_type: 'widget' }));
        const b = record(app.renderPluginElement({ id: 'b', type: 'plugin', plugin_id: 'b', plugin_type: 'widget' }));
        // A custom control has no namespace of its own, so with no grant on it
        // it sees nothing at all -- and it must never be handed some other
        // element's state.
        const c = record(app.renderCustomElement({ id: 'c', type: 'custom', custom_file: 'room_map/index.html' }));

        app._notifyPluginIframes('plugin.a.x', 1);
        assert(a._received.length === 1 && b._received.length === 0, 'only plugin a receives plugin.a.x');
        assert(c._received.length === 0, 'a custom control does not receive another element\'s state');
        app._notifyPluginIframes('device.x.power', 'on');
        assert(a._received.length === 1 && b._received.length === 0, 'an ungranted plugin receives no device.* key');
        assert(c._received.length === 0, 'an ungranted custom control receives no device state');
        app._notifyPluginIframes('plugin.b.y', 2);
        assert(b._received.length === 1, 'plugin b receives plugin.b.y');
    },

    // The grant decides what an iframe element may SEE, and the opening
    // snapshot and the live pushes have to answer identically -- a frame told
    // at startup it cannot see a key, then pushed that key, is the failure this
    // one rule in one place exists to prevent.
    grant_scopes_what_an_element_sees() {
        const app = mkApp();
        app.state = {
            'device.dsp1.mute': false,
            'device.dsp1.input.01.gain': -6,   // a child entity, never listed by hand
            'device.dsp10.mute': true,         // the prefix trap: dsp1 is not dsp10
            'device.proj1.power': 'on',
            'var.room_volume': 30,
            'var.room_volume_max': 60,         // exact match, not a prefix
            'system.version': '9.9.9',
            'plugin.other.secret': 'nope',
        };
        const element = {
            id: 'map', type: 'custom', custom_file: 'map/index.html',
            grant: { devices: ['dsp1'], variables: ['room_volume'] },
        };
        const el = app.renderCustomElement(element);
        const seen = (key) => el._stateFilter(key);
        assert(seen('device.dsp1.mute'), 'a granted device is visible');
        assert(seen('device.dsp1.input.01.gain'), 'a granted device covers its child entities');
        assert(!seen('device.dsp10.mute'), 'a grant on dsp1 does not reach dsp10');
        assert(!seen('device.proj1.power'), 'an ungranted device stays hidden');
        assert(seen('var.room_volume'), 'a granted variable is visible');
        assert(!seen('var.room_volume_max'), 'a variable is matched exactly, not by prefix');
        assert(!seen('system.version'), 'system.* is not grantable');
        assert(!seen('plugin.other.secret'), 'another element\'s plugin namespace stays hidden');

        // And the live broadcast asks that same filter, so what arrives after
        // startup matches what the opening snapshot said was visible.
        const received = [];
        Object.defineProperty(el._pluginIframe, 'contentWindow', {
            value: { postMessage: (m) => received.push(m) }, configurable: true,
        });
        app.elementMap['map'] = { el, elementDef: element };
        app._notifyPluginIframes('device.dsp1.mute', true);
        app._notifyPluginIframes('device.dsp10.mute', false);
        app._notifyPluginIframes('var.room_volume_max', 60);
        app._notifyPluginIframes('var.room_volume', 12);
        assert(received.length === 2, `only granted keys are pushed, got ${received.length}`);
        assert(received.map(m => m.key).join(',') === 'device.dsp1.mute,var.room_volume',
            `pushed keys match the filter, got ${received.map(m => m.key).join(',')}`);
    },

    // The init message carries the same scoped snapshot the live pushes honour,
    // plus the grant itself so a control can adapt to what it was given.
    async grant_scopes_the_opening_snapshot() {
        const app = mkApp();
        app.state = { 'device.dsp1.mute': false, 'device.proj1.power': 'on', 'var.room_volume': 30 };
        const el = app.renderCustomElement({
            id: 'map', type: 'custom', custom_file: 'map/index.html',
            grant: { devices: ['dsp1'], variables: ['room_volume'], macros: true },
        });
        const posted = [];
        Object.defineProperty(el._pluginIframe, 'contentWindow', {
            value: { postMessage: (m) => posted.push(m) }, configurable: true,
        });
        el._pluginIframe.dispatchEvent(new window.Event('load'));
        await Promise.resolve();
        assert(posted.length === 1, `one init message, got ${posted.length}`);
        const init = posted[0];
        assert(init.type === 'openavc:init', 'it is the init message');
        assert(Object.keys(init.state).sort().join(',') === 'device.dsp1.mute,var.room_volume',
            `snapshot holds only granted keys, got ${Object.keys(init.state).sort().join(',')}`);
        assert(init.grant.devices.join(',') === 'dsp1', 'the control is told which devices it has');
        assert(init.grant.macros === true && init.grant.navigate === false,
            'the control is told which switches it has');
    },

    // A custom control is the plugin iframe's machinery pointed at a file in
    // the project instead of a plugin's panel/ directory: same sandbox, same
    // elementMap shape, same bridge -- and a relative URL, which is what makes
    // it work through the cloud tunnel.
    custom_element_render() {
        const app = mkApp();
        const el = app.renderCustomElement({
            id: 'map', type: 'custom', custom_file: 'room map/index.html',
            custom_config: { room: '204' },
        });
        const iframe = el._pluginIframe;
        assert(iframe, 'a custom control renders an iframe');
        // The attribute, not the property: the property is resolved against the
        // document base, and it is the attribute that has to stay relative for
        // the cloud tunnel to rewrite nothing.
        const src = iframe.getAttribute('src');
        assert(src === '/api/projects/default/ui/room%20map/index.html',
            `relative ui/ URL with each segment encoded, got ${src}`);
        assert(!/^[a-z]+:/i.test(src), 'never an absolute URL');
        assert(iframe.getAttribute('sandbox') === 'allow-scripts',
            `sandboxed with allow-scripts only, got "${iframe.getAttribute('sandbox')}"`);
        assert(!iframe.getAttribute('allow'), 'no extra allow features');
        assert(el._grant.devices.length === 0 && el._grant.variables.length === 0 &&
            !el._grant.macros && !el._grant.navigate,
            'a control placed without a grant reaches nothing');
        assert(el._ownNamespace === null, 'and owns no state namespace of its own');
        const entry = app.elementMap['map'];
        assert(entry && entry.el === el && entry.elementDef.id === 'map',
            'filed in elementMap the same way every other element is');
        assert(el._pluginMessageHandler, 'the bridge is wired');
    },

    // Nothing a custom control sends reaches the room until it has been granted
    // something. It holds no capabilities, so every branch of the bridge drops.
    custom_element_sends_nothing_without_a_grant() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        const el = app.renderCustomElement({ id: 'map', type: 'custom', custom_file: 'map/index.html' });
        const fire = (data) => el._pluginMessageHandler({ source: el._pluginIframe.contentWindow, data });
        fire({ type: 'openavc:action', action: 'device.command', device: 'd1', command: 'on', params: {} });
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.global', value: 1 });
        fire({ type: 'openavc:action', action: 'state.set', key: 'plugin.anything.x', value: 1 });
        assert(app.ws.sent.length === 0, `nothing reaches the room, got ${app.ws.sent.length}`);
    },

    // A control with no file chosen draws a box that says so, and boots nothing.
    custom_element_without_a_file() {
        const app = mkApp();
        for (const bad of [undefined, '', '/etc/passwd', '../../secret.html']) {
            const el = app.renderCustomElement({ id: 'x', type: 'custom', custom_file: bad });
            assert(!el._pluginIframe, `no iframe for custom_file ${JSON.stringify(bad)}`);
            assert(el.textContent.includes('Custom control'), 'the box says what is missing');
        }
    },

    // The designer draws the control for real, because it is the thing being
    // written. A plugin element is somebody else's shipped code and stays a
    // placeholder: nothing to see, and no reason to run it while dragging.
    custom_control_draws_in_the_designer() {
        const app = mkApp();
        app.editMode = true;
        const custom = app.renderCustomElement({
            id: 'map', type: 'custom', custom_file: 'map/index.html',
        });
        assert(custom._pluginIframe, 'a custom control renders its page in the designer');
        const plugin = app.renderPluginElement({
            id: 'pe', type: 'plugin', plugin_id: 'p', plugin_type: 'widget',
        });
        assert(!plugin._pluginIframe, 'a plugin element is still a placeholder there');
        assert(plugin.textContent.includes('Plugin'), 'and says what it stands for');
    },

    // Drawing for real in the designer must not mean acting for real. The
    // canvas has no socket, and send() refuses anyway -- both, because either
    // one alone is a promise resting on the other.
    custom_control_in_the_designer_reaches_nothing() {
        const app = mkApp();
        app.editMode = true;
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        let navigatedTo = null;
        app.navigateToPage = (p) => { navigatedTo = p; };
        const el = app.renderCustomElement({
            id: 'map', type: 'custom', custom_file: 'map/index.html',
            grant: { devices: ['dsp1'], variables: ['vol'], macros: true, navigate: true },
        });
        const fire = (d) => el._pluginMessageHandler({ source: el._pluginIframe.contentWindow, data: d });
        fire({ type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'on', params: {} });
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.vol', value: 1 });
        fire({ type: 'openavc:action', action: 'macro.run', macro: 'system_on' });
        assert(app.ws.sent.length === 0, `nothing reaches the room while authoring, got ${app.ws.sent.length}`);
        // Navigation is the one that does not go through send(), so it is
        // checked separately rather than assumed to ride along.
        fire({ type: 'openavc:navigate', page: 'admin' });
        assert(navigatedTo === null, 'and the canvas does not walk to another page');
    },

    // The opening message tells a control it is drawing for its author, and
    // hands it the sample state the Builder supplied -- scoped by the same
    // grant as on glass, so the designer cannot show what the panel would not.
    async custom_control_init_says_it_is_the_designer() {
        const app = mkApp();
        app.editMode = true;
        app.state = { 'device.dsp1.mute': false, 'device.other.x': 1 };
        const el = app.renderCustomElement({
            id: 'map', type: 'custom', custom_file: 'map/index.html',
            grant: { devices: ['dsp1'] },
        });
        const posted = [];
        Object.defineProperty(el._pluginIframe, 'contentWindow', {
            value: { postMessage: (m) => posted.push(m) }, configurable: true,
        });
        el._pluginIframe.dispatchEvent(new window.Event('load'));
        await Promise.resolve();
        assert(posted.length === 1, `one init message, got ${posted.length}`);
        assert(posted[0].edit === true, 'the control is told it is in the designer');
        assert(Object.keys(posted[0].state).join(',') === 'device.dsp1.mute',
            `sample state is still scoped by the grant, got ${Object.keys(posted[0].state).join(',')}`);
    },

    // A file that is not there renders the server's JSON error as text, which
    // reads as an unknowable breakage. The box says which file instead, and
    // the designer is told so it can put it in front of the author.
    async custom_control_says_when_its_file_is_missing() {
        const app = mkApp();
        const toParent = [];
        app._postToParent = (m) => toParent.push(m);
        window.fetch = async () => ({ ok: false, status: 404 });
        const el = app.renderCustomElement({ id: 'map', type: 'custom', custom_file: 'map/index.html' });
        await Promise.resolve(); await Promise.resolve();
        const strip = el.querySelector('.panel-iframe-fault');
        assert(strip, 'the box carries a failure strip');
        assert(strip.textContent === 'map/index.html could not be loaded (404)',
            `it names the file and the status, got "${strip.textContent}"`);
        assert(toParent.some(m => m.type === 'openavc:element-error' && m.elementId === 'map'),
            'and the designer is told which element failed');
        window.fetch = async () => ({ ok: false, json: async () => ({}) });
    },

    // Nothing outside a sandboxed frame can see a script error inside it, so
    // the control reports its own and the panel shows it where the control is.
    custom_control_reports_its_own_error() {
        const app = mkApp();
        const toParent = [];
        app._postToParent = (m) => toParent.push(m);
        const el = app.renderCustomElement({ id: 'map', type: 'custom', custom_file: 'map/index.html' });
        el._pluginMessageHandler({
            source: el._pluginIframe.contentWindow,
            data: { type: 'openavc:error', message: 'ReferenceError: lights is not defined' },
        });
        const strip = el.querySelector('.panel-iframe-fault');
        assert(strip && strip.textContent.includes('ReferenceError'),
            `the message shows in the element's box, got "${strip && strip.textContent}"`);
        assert(toParent.some(m => m.type === 'openavc:element-error'), 'the designer hears about it too');
    },

    // Saving a file changes no project data, so the version the Builder bumps
    // is the only thing that stops the browser drawing the copy it already has.
    custom_control_reloads_when_a_file_is_saved() {
        const app = mkApp();
        const plain = app.renderCustomElement({ id: 'a', type: 'custom', custom_file: 'map/index.html' });
        assert(plain._pluginIframe.getAttribute('src') === '/api/projects/default/ui/map/index.html',
            `no version at runtime, got ${plain._pluginIframe.getAttribute('src')}`);
        app._uiFilesVersion = 7;
        const busted = app.renderCustomElement({ id: 'b', type: 'custom', custom_file: 'map/index.html' });
        assert(busted._pluginIframe.getAttribute('src') === '/api/projects/default/ui/map/index.html?v=7',
            `the saved version rides on the URL, got ${busted._pluginIframe.getAttribute('src')}`);
    },

    // H-005 — the iframe action bridge enforces the grant the element was
    // placed with. Same scenario for a plugin panel element and a custom
    // control, because the two share one bridge: the only difference is that a
    // plugin also owns its own plugin.<id>.* namespace.
    h005_action_grant_gate() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        const el = app.renderPluginElement({
            id: 'pe1', type: 'plugin', plugin_id: 'myplug', plugin_type: 'widget', plugin_config: {},
            grant: { devices: ['dsp1'], variables: ['room_volume'] },
        });
        const src = el._pluginIframe.contentWindow;
        const fire = (data) => el._pluginMessageHandler({ source: src, data });

        // A command reaches a granted device and nothing else.
        fire({ type: 'openavc:action', action: 'device.command', device: 'proj1', command: 'on', params: {} });
        assert(app.ws.sent.length === 0, 'a command on an ungranted device is dropped');
        fire({ type: 'openavc:action', action: 'device.command', device: 'dsp10', command: 'on', params: {} });
        assert(app.ws.sent.length === 0, 'a grant on dsp1 does not let dsp10 be commanded');
        fire({ type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'on', params: {} });
        assert(app.ws.sent.length === 1 && app.ws.sent[0].type === 'command', 'a granted device can be commanded');

        // Writes ride the same list: a granted variable, exactly; a plugin's
        // own namespace, always; anything else, never.
        app.ws.sent = [];
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.room_volume', value: 1 });
        assert(app.ws.sent.length === 1, 'a granted variable can be written');
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.room_volume_max', value: 1 });
        assert(app.ws.sent.length === 1, 'a variable grant is exact, not a prefix');
        fire({ type: 'openavc:action', action: 'state.set', key: 'var.other', value: 1 });
        assert(app.ws.sent.length === 1, 'an ungranted variable is refused');
        fire({ type: 'openavc:action', action: 'state.set', key: 'device.dsp1.mute', value: 1 });
        assert(app.ws.sent.length === 1, 'a device state write is refused even for a granted device');
        fire({ type: 'openavc:action', action: 'state.set', key: 'plugin.myplug.x', value: 1 });
        assert(app.ws.sent.length === 2, 'a plugin writes its own namespace with no grant');
        fire({ type: 'openavc:action', action: 'state.set', key: 'plugin.other.x', value: 1 });
        assert(app.ws.sent.length === 2, 'and not another plugin\'s');
    },

    // Running a macro and moving the panel are separate switches, off unless
    // the integrator turned them on. Navigation was ungated before grants
    // existed: any plugin iframe could move the panel out from under whoever
    // was standing at it.
    grant_switches_gate_macros_and_navigation() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        let navigatedTo = null;
        app.navigateToPage = (p) => { navigatedTo = p; };

        const off = app.renderCustomElement({ id: 'off', type: 'custom', custom_file: 'a/index.html' });
        const fireOff = (d) => off._pluginMessageHandler({ source: off._pluginIframe.contentWindow, data: d });
        fireOff({ type: 'openavc:action', action: 'macro.run', macro: 'lights_up' });
        assert(app.ws.sent.length === 0, 'no macro without the switch');
        fireOff({ type: 'openavc:navigate', page: 'admin' });
        assert(navigatedTo === null, 'no page change without the switch');

        const on = app.renderCustomElement({
            id: 'on', type: 'custom', custom_file: 'b/index.html',
            grant: { macros: true, navigate: true },
        });
        const fireOn = (d) => on._pluginMessageHandler({ source: on._pluginIframe.contentWindow, data: d });
        fireOn({ type: 'openavc:action', action: 'macro.run', macro: 'lights_up' });
        assert(app.ws.sent.length === 1 && app.ws.sent[0].type === 'macro.execute' &&
            app.ws.sent[0].macro_id === 'lights_up', 'the macro runs with the switch on');
        fireOn({ type: 'openavc:navigate', page: 'admin' });
        assert(navigatedTo === 'admin', 'the page changes with the switch on');
    },

    // ---- Custom pages: the whole screen, not one box ------------------------

    // A custom page draws one frame filling the screen. Its own elements are
    // kept in the project and are not drawn, so switching back restores the
    // page exactly rather than asking the author to rebuild it.
    custom_page_renders_one_full_box_frame() {
        const app = mkApp();
        const proj = project({
            elements: [el('b1', 'button')],
            placements: { b1: { x: 10, y: 10, w: 20, h: 10 } },
        });
        Object.assign(proj.ui.pages[0], {
            render_mode: 'custom',
            custom_file: 'room map/index.html',
            custom_config: { room: '204' },
        });
        renderProject(app, proj);
        const frames = app.root.querySelectorAll('.panel-custom');
        assert(frames.length === 1, `one frame for the page, got ${frames.length}`);
        const frame = frames[0];
        assert(frame.style.left === '0%' && frame.style.top === '0%'
            && frame.style.width === '100%' && frame.style.height === '100%',
            `the frame is the whole page, got ${frame.style.left}/${frame.style.top} `
            + `${frame.style.width}x${frame.style.height}`);
        assert(frame.classList.contains('panel-custom-page'),
            'and is marked as a page so it keeps square corners');
        const src = frame._pluginIframe.getAttribute('src');
        assert(src === '/api/projects/default/ui/room%20map/index.html',
            `the same relative ui/ URL a control gets, got ${src}`);
        assert(frame._pluginIframe.getAttribute('sandbox') === 'allow-scripts',
            'and the same sandbox -- a whole page is not a reason to widen it');
        assert(frame._pluginConfig.room === '204', 'the page config is handed over');
        assert(!app.root.querySelector('[data-element-id="b1"]'),
            "the page's own elements are not drawn");
        assert(proj.ui.pages[0].elements.length === 1, 'and are not deleted either');
        assert(!app.root.querySelector('.panel-page-snap-overlay'),
            'and there is no ruler to snap to on a page nothing is placed on');
    },

    // Master elements draw OVER a custom page. Every child of .panel-page gets
    // the same z-index, so DOM order decides, and the frame is appended first
    // for exactly that reason: a master nav bar is how somebody gets off a
    // custom page, and burying it strands them there.
    custom_page_draws_master_elements_over_it() {
        const app = mkApp();
        const proj = project({});
        Object.assign(proj.ui.pages[0], {
            render_mode: 'custom', custom_file: 'room/index.html',
        });
        proj.ui.master_elements = [{
            id: 'home', type: 'button', label: 'Home', pages: '*',
            placements: { landscape: { x: 0, y: 0, w: 12, h: 8 } },
        }];
        setViewport(1280, 800);
        renderProject(app, proj);
        const surface = document.querySelector('#panel-root .panel-page');
        const kids = Array.from(surface.children).filter(n => n.dataset && n.dataset.elementId);
        assert(kids.length === 2, `the frame and the master element, got ${kids.length}`);
        assert(kids[0].dataset.elementId === 'main', 'the frame is drawn first');
        assert(kids[1].dataset.elementId === 'home',
            'so the master element paints over it rather than under it');
    },

    // A page that says custom but names no file draws its elements. A blank
    // screen would be indistinguishable from a page with nothing on it, and
    // the elements are the better thing to show while somebody is half way
    // through setting it up.
    custom_page_without_a_file_falls_back_to_its_elements() {
        const app = mkApp();
        for (const bad of [undefined, '', null]) {
            const proj = project({
                elements: [el('b1', 'button')],
                placements: { b1: { x: 10, y: 10, w: 20, h: 10 } },
            });
            Object.assign(proj.ui.pages[0], { render_mode: 'custom', custom_file: bad });
            renderProject(app, proj);
            assert(!app.root.querySelector('.panel-custom'),
                `no frame for custom_file ${JSON.stringify(bad)}`);
            assert(app.root.querySelector('[data-element-id="b1"]'),
                'the page draws what it has instead');
        }
    },

    // The grant is the element grant, on the page. It scopes the opening
    // snapshot and the live pushes through the one filter both already ask.
    async custom_page_grant_scopes_what_it_sees() {
        const app = mkApp();
        app.state = { 'device.dsp1.mute': false, 'device.other.x': 1, 'var.vol': 3 };
        const proj = project({});
        Object.assign(proj.ui.pages[0], {
            render_mode: 'custom',
            custom_file: 'room/index.html',
            grant: { devices: ['dsp1'], variables: ['vol'], macros: false, navigate: false },
        });
        renderProject(app, proj);
        const frame = app.root.querySelector('.panel-custom');
        const posted = [];
        Object.defineProperty(frame._pluginIframe, 'contentWindow', {
            value: { postMessage: (m) => posted.push(m) }, configurable: true,
        });
        frame._pluginIframe.dispatchEvent(new window.Event('load'));
        await Promise.resolve();
        assert(posted.length === 1, `one init message, got ${posted.length}`);
        assert(Object.keys(posted[0].state).sort().join(',') === 'device.dsp1.mute,var.vol',
            `the snapshot is scoped by the page grant, got ${Object.keys(posted[0].state).join(',')}`);
        assert(posted[0].elementId === 'main', 'and the frame is named for the page it fills');
        // Live pushes ride the same filter, reached through elementMap.
        app._notifyPluginIframes('device.dsp1.mute', true);
        app._notifyPluginIframes('device.other.x', 9);
        const states = posted.filter(m => m.type === 'openavc:state').map(m => m.key);
        assert(states.join(',') === 'device.dsp1.mute',
            `only granted keys are pushed, got ${states.join(',')}`);
    },

    // A dialog can be hand-written too. It is the same frame in the overlay
    // box, so refusing it would be a special case to explain rather than one
    // to write.
    custom_overlay_page_renders_a_frame() {
        const app = mkApp();
        const proj = project({});
        proj.ui.pages.push({
            id: 'dlg', name: 'Dialog', page_type: 'overlay',
            render_mode: 'custom', custom_file: 'dialog/index.html',
            elements: [], layouts: [{ id: 'landscape', orientation: 'landscape', primary: true, placements: {}, hidden: [] }],
        });
        renderProject(app, proj);
        app.navigateToPage('dlg');
        const overlay = document.querySelector('.panel-overlay[data-page-id="dlg"]');
        assert(overlay, 'the overlay opens');
        const frame = overlay.querySelector('.panel-custom');
        assert(frame && frame._pluginIframe, 'and runs the author page inside it');
        assert(frame._pluginIframe.getAttribute('src') === '/api/projects/default/ui/dialog/index.html',
            'from the same ui/ tree');
        // The overlay cleanup already matches .panel-custom, so dismissing must
        // take the bridge listener with it rather than leaking one per open.
        const before = app._pluginMessageHandlers.size;
        app.dismissOverlay();
        assert(app._pluginMessageHandlers.size === before - 1,
            'dismissing takes the frame bridge listener with it');
    },

    // Navigating from inside a frame tells the server, exactly as the page-nav
    // button does. It used to move the panel silently, so a trigger bound to
    // `ui.page.<id>` never fired for anyone who got there from a control.
    frame_navigation_tells_the_server() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        app.navigateToPage = () => {};
        const el = app.renderCustomElement({
            id: 'nav', type: 'custom', custom_file: 'a/index.html',
            grant: { navigate: true },
        });
        el._pluginMessageHandler({
            source: el._pluginIframe.contentWindow,
            data: { type: 'openavc:navigate', page: 'presentation' },
        });
        const pageMsgs = app.ws.sent.filter(m => m.type === 'ui.page');
        assert(pageMsgs.length === 1 && pageMsgs[0].page_id === 'presentation',
            `the server hears which page, got ${JSON.stringify(app.ws.sent)}`);
    },

    // Nothing inside a cross-origin sandboxed frame reaches the document
    // listeners that reset the idle timer, so a person working a custom page
    // is invisible to it -- the panel navigates away and locks under their
    // hands. A message from the frame counts as activity, but only while the
    // frame HAS FOCUS, or a control nobody is touching could hold a wall panel
    // unlocked all night by posting in a loop.
    frame_activity_resets_the_idle_timer_only_when_focused() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        let resets = 0;
        app.resetIdleTimer = () => { resets++; };
        const el = app.renderCustomElement({
            id: 'map', type: 'custom', custom_file: 'a/index.html',
            grant: { devices: ['dsp1'] },
        });
        const fire = (d) => el._pluginMessageHandler({ source: el._pluginIframe.contentWindow, data: d });

        app._frameHasFocus = () => false;
        fire({ type: 'openavc:activity' });
        fire({ type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'on', params: {} });
        assert(resets === 0, `an unfocused frame cannot keep the panel awake, got ${resets}`);
        assert(app.ws.sent.length === 1, 'though its granted action still lands');

        app._frameHasFocus = () => true;
        fire({ type: 'openavc:activity' });
        assert(resets === 1, 'a focused frame saying so resets the timer');
        fire({ type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'on', params: {} });
        assert(resets === 2, 'and so does anything else it does, with no extra line to write');
    },

    // The offline notice outranks an open dialog. It sat below the overlay
    // stack, so a dialog left open when the room dropped drew straight over
    // "System Offline": the panel was dead and the screen said nothing.
    offline_overlay_outranks_an_open_dialog() {
        const zOf = (sel) => {
            const node = document.querySelector(sel) || document.createElement('div');
            if (!node.isConnected) document.body.appendChild(node);
            return parseInt(window.getComputedStyle(node).zIndex, 10);
        };
        const probe = (cls) => {
            const n = document.createElement('div');
            n.className = cls;
            document.body.appendChild(n);
            const z = parseInt(window.getComputedStyle(n).zIndex, 10);
            n.remove();
            return z;
        };
        const offline = zOf('#offline-overlay');
        const dialog = probe('panel-overlay');
        const lock = probe('lock-overlay');
        assert(offline > dialog,
            `the offline notice draws over an open dialog (${offline} vs ${dialog})`);
        assert(lock > offline,
            `and the lock screen still draws over both (${lock} vs ${offline})`);
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
        live._grant = { devices: ['d1'], variables: ['global'], macros: true, navigate: true };
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

    // --- Q-213: a control whose device is unreachable draws no value --------
    //
    // Measured on real hardware: an amplifier genuinely at -6.0 dB and muted,
    // with its port broken. The fader drew "0.0 dB" with the thumb at the top
    // of its travel, the mute button drew its not-muted look, and a label drew
    // "Amp draw: 0.00 A". None of those is a neutral wrong answer.

    // The photographed fader, both halves: the value it was drawing before,
    // and what it must draw once the device is known to be unreachable.
    q213_fader_draws_no_value_for_an_unreachable_device() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'gain', type: 'fader', label: 'Gain',
                min: -80, max: 0, unit: 'dB',
                bindings: { show: { value: { key: 'device.amp.gain' } } },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        // 0.0 is the value the platform invented on disconnect. It is a real
        // number in state, so nothing about a null branch reaches this.
        app.state = { 'device.amp.connected': true, 'device.amp.gain': 0.0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="gain"]');
        const handle = el.querySelector('.fader-handle');
        const readout = el.querySelector('.fader-value');
        assert(handle.style.bottom === '100%',
            `connected: the handle draws the value it was given, got ${handle.style.bottom}`);
        assert(readout.textContent === '0.0 dB',
            `connected: and prints it, got ${readout.textContent}`);

        app.state['device.amp.connected'] = false;
        app.evaluateAllBindings(['device.amp.connected']);
        assert(readout.textContent === '-- dB',
            `offline: no number, got ${readout.textContent}`);
        assert(el.classList.contains('device-offline'),
            'offline: and the control reads as unavailable');
        assert(window.getComputedStyle(handle).visibility === 'hidden',
            'offline: the handle is hidden -- a handle at the floor claims minimum');
        assert(handle.getAttribute('aria-valuenow') === null,
            'offline: and nothing is announced to a screen reader either');

        app.state['device.amp.connected'] = true;
        app.evaluateAllBindings(['device.amp.connected']);
        assert(readout.textContent === '0.0 dB' && handle.style.bottom === '100%',
            'reconnect: the reading comes straight back');
        assert(!el.classList.contains('device-offline'),
            'reconnect: and the mark goes');
    },

    // The flip itself has to reach the binding. Its own key does not move when
    // a device goes away, so the incremental filter would skip it entirely --
    // the value is unchanged and what it is worth is not.
    q213_a_connectivity_flip_reaches_bindings_on_that_device() {
        const app = mkApp();
        let ran = 0;
        app.evaluateFaderValue = () => { ran++; };
        app.bindings = [{
            type: 'fader_value', element: document.createElement('div'),
            binding: { key: 'device.amp.gain' }, _fader: {},
        }];
        app.evaluateAllBindings(['device.amp.connected']);
        assert(ran === 1, 'the fader re-evaluates when its device changes reachability');
        app.evaluateAllBindings(['device.other.connected']);
        assert(ran === 1, "but not for a device it does not read");
        app.evaluateAllBindings(['var.unrelated']);
        assert(ran === 1, 'and not for an unrelated key');
    },

    // A child key carries the device in the same second segment, so one rule
    // covers `device.<id>.<prop>` and `device.<id>.<type>.<local>.<prop>`.
    q213_child_entity_keys_belong_to_their_parent_device() {
        const app = mkApp();
        assert(app._deviceIdForKey('device.amp.gain') === 'amp', 'device property');
        assert(app._deviceIdForKey('device.amp.input.1.mute') === 'amp', 'child property');
        assert(app._deviceIdForKey('var.volume') === null, 'a variable names no device');
        assert(app._deviceIdForKey('plugin.x.y') === null, 'nor does a plugin key');
        // The platform-maintained ones describe the device rather than report
        // from it, so a panel bound to them is reporting the fault.
        for (const prop of ['connected', 'name', 'offline_reason', 'offline_detail',
            'enabled', 'paused', 'orphaned', 'orphan_reason',
            'reconnect_attempt', 'reconnect_failed']) {
            assert(app._deviceIdForKey(`device.amp.${prop}`) === null,
                `device.<id>.${prop} keeps telling the truth when the device is gone`);
        }
    },

    // The one thing that must NOT go quiet: the controls that exist to report
    // the fault. A label bound to offline_detail and an LED bound to connected
    // are the panel's only honest voice while everything else is unknown.
    q213_the_controls_that_report_the_fault_keep_working() {
        const app = mkApp();
        const proj = project({
            elements: [
                { id: 'why', type: 'label', text: '',
                  bindings: { show: { value: { key: 'device.amp.offline_detail' } } } },
                { id: 'dot', type: 'status_led',
                  bindings: { show: { look: { key: 'device.amp.connected',
                      map: { true: '#0f0', false: '#f00' }, default: '#9E9E9E' } } } },
            ],
            placements: { why: { x: 5, y: 5, w: 30, h: 8 }, dot: { x: 40, y: 5, w: 6, h: 6 } },
        });
        app.state = {
            'device.amp.connected': false,
            'device.amp.offline_detail': 'Connection refused by 192.168.4.75:4321',
        };
        renderProject(app, proj);
        const why = app.root.querySelector('[data-element-id="why"]');
        assert(why.textContent === 'Connection refused by 192.168.4.75:4321',
            `the reason still prints, got "${why.textContent}"`);
        assert(!why.classList.contains('device-offline'),
            'and is not marked unavailable -- it is the thing that works');
        const dot = app.root.querySelector('[data-element-id="dot"]');
        assert(!dot.classList.contains('device-offline'),
            'the connected LED is not blanked by the state it exists to show');
    },

    // The mute button. `evaluateFeedback` resolves a null value to
    // `default_state`, so "unknown" was rendering as whatever state was
    // nominated as the default -- the not-muted face, over a muted amplifier.
    q213_a_state_look_asserts_nothing_while_the_device_is_gone() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'mute', type: 'button', label: 'Amp mute',
                style: { bg_color: '#222222' },
                bindings: { show: { look: {
                    key: 'device.amp.input.1.mute', default_state: 'false',
                    states: {
                        true: { label: 'MUTED', bg_color: '#cc0000' },
                        false: { label: 'MUTE', bg_color: '#111111' },
                    },
                } } },
            }],
            placements: { mute: { x: 5, y: 5, w: 20, h: 10 } },
        });
        // Connected, with the child key cleared: the default state is drawn,
        // which is the documented behaviour and stays.
        app.state = { 'device.amp.connected': true };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        assert(el.textContent.includes('MUTE') && !el.textContent.includes('MUTED'),
            `connected: the default state is drawn, got "${el.textContent}"`);
        assert(el.style.backgroundColor === 'rgb(17, 17, 17)',
            `connected: with its colour, got ${el.style.backgroundColor}`);

        app.state['device.amp.connected'] = false;
        app.evaluateAllBindings(['device.amp.connected']);
        assert(el.textContent.includes('Amp mute'),
            `offline: the button falls back to its own label, got "${el.textContent}"`);
        assert(el.style.backgroundColor === 'rgb(34, 34, 34)',
            `offline: and its own colour, asserting no state, got ${el.style.backgroundColor}`);
        assert(el.classList.contains('device-offline'), 'offline: and reads as unavailable');

        // A state's WORD is a claim too, and a control with no name of its own
        // has nothing honest to fall back to -- so it says nothing.
        const app2 = mkApp();
        const proj2 = project({
            elements: [
                { id: 'nameless', type: 'button',
                  bindings: { show: { look: { key: 'device.amp.mute_state', default_state: 'off',
                      states: { on: { label: 'MUTED' }, off: { label: 'LIVE' } } } } } },
                { id: 'lbl', type: 'label', text: 'Amplifier',
                  bindings: { show: { look: { key: 'device.amp.mute_state', default_state: 'off',
                      states: { on: { label: 'MUTED', text_color: '#f00' }, off: { label: 'LIVE' } } } } } },
            ],
            placements: { nameless: { x: 5, y: 20, w: 20, h: 10 }, lbl: { x: 30, y: 20, w: 20, h: 10 } },
        });
        app2.state = { 'device.amp.connected': true, 'device.amp.mute_state': 'on' };
        renderProject(app2, proj2);
        const nameless = app2.root.querySelector('[data-element-id="nameless"]');
        const lbl = app2.root.querySelector('[data-element-id="lbl"]');
        assert(nameless.textContent === 'MUTED', `connected, got "${nameless.textContent}"`);
        assert(lbl.textContent === 'MUTED', `connected label, got "${lbl.textContent}"`);
        app2.state['device.amp.connected'] = false;
        app2.evaluateAllBindings(['device.amp.connected']);
        assert(nameless.textContent === '',
            `offline: a nameless button says nothing, got "${nameless.textContent}"`);
        assert(lbl.textContent === 'Amplifier',
            `offline: a label goes back to its own words, got "${lbl.textContent}"`);
        assert(lbl.style.color === '', `offline: and its own colour, got ${lbl.style.color}`);
    },

    // The photographed label: "Amp draw: 0.00 A" for an amplifier drawing
    // 0.076 A through a broken port. The sentence stays, the number goes.
    q213_a_bound_label_keeps_its_sentence_and_loses_its_number() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'draw', type: 'label', text: '',
                bindings: { show: { value: {
                    key: 'device.amp.ac_line_current', format: 'Amp draw: {value} A',
                } } },
            }],
            placements: { draw: { x: 5, y: 5, w: 30, h: 8 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.ac_line_current': 0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="draw"]');
        assert(el.textContent === 'Amp draw: 0 A', `connected, got "${el.textContent}"`);
        app.state['device.amp.connected'] = false;
        app.evaluateAllBindings(['device.amp.connected']);
        assert(el.textContent === 'Amp draw: -- A', `offline, got "${el.textContent}"`);
    },

    // Every other value renderer. Each of these printed something before: the
    // slider its range floor, the select its stale option, the list its last
    // lit row, the meter and gauge their frozen readings.
    q213_every_value_renderer_has_an_unknown_form() {
        const app = mkApp();
        app.state = { 'device.d.v': 42, 'device.d.connected': true };
        const offline = () => { app.state['device.d.connected'] = false; };

        // Slider
        const input = document.createElement('input'); input.type = 'range';
        const fill = document.createElement('div');
        const sliderOut = document.createElement('div');
        const sb = {
            type: 'slider_value', element: input, elementDef: { id: 's', min: 0, max: 100 },
            binding: { key: 'device.d.v' }, fill, valueDisplay: sliderOut,
            isVertical: false, outputMin: null, outputMax: null, scaleToFull: true,
            steps: 100, unit: '%', valueToPos: (v) => v, fmtValue: (v) => `${v} %`,
        };
        app.evaluateSliderValue(sb);
        assert(sliderOut.textContent === '42 %', `slider connected, got ${sliderOut.textContent}`);
        offline();
        app.evaluateSliderValue(sb);
        assert(sliderOut.textContent === '-- %', `slider offline, got ${sliderOut.textContent}`);
        assert(fill.style.width === '0%', `slider fill emptied, got ${fill.style.width}`);

        // Select — no selection at all, not the first option
        app.state['device.d.connected'] = true;
        const sel = document.createElement('select');
        for (const v of ['a', 'b']) { const o = document.createElement('option'); o.value = v; sel.appendChild(o); }
        app.state['device.d.input'] = 'b';
        const selB = { type: 'select_value', element: sel, elementDef: { id: 'sel' }, binding: { key: 'device.d.input' } };
        app.evaluateSelectValue(selB);
        assert(sel.value === 'b', 'select connected');
        offline();
        app.evaluateSelectValue(selB);
        assert(sel.selectedIndex === -1, `select offline shows nothing, got index ${sel.selectedIndex}`);

        // Gauge
        app.state['device.d.connected'] = true;
        const fgPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        const valueText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        const gb = {
            type: 'gauge_value', element: document.createElement('div'), elementDef: { id: 'g' },
            binding: { key: 'device.d.v' },
            _svg: { fgPath, valueText, startAngle: 0, endAngle: 180, radius: 10,
                min: 0, max: 100, unit: '%', gaugeColor: '#0f0', zones: [], showValue: true,
                displayDecimals: null, arcPath: () => 'M0 0' },
        };
        app.evaluateGaugeValue(gb);
        assert(valueText.textContent === '42%', `gauge connected, got ${valueText.textContent}`);
        offline();
        app.evaluateGaugeValue(gb);
        assert(valueText.textContent === '--%', `gauge offline, got ${valueText.textContent}`);
        assert(fgPath.getAttribute('d') === '', 'gauge arc cleared');

        // Level meter
        app.state['device.d.connected'] = true;
        const bar = document.createElement('div');
        for (let i = 0; i < 4; i++) { const s = document.createElement('div'); s.className = 'meter-segment'; bar.appendChild(s); }
        const mb = {
            type: 'level_meter_value', element: document.createElement('div'), elementDef: { id: 'm' },
            binding: { key: 'device.d.v' },
            _meter: { segments: 4, min: 0, max: 100, bar, showPeak: false, peakValue: -Infinity, peakTime: 0, peakHoldMs: 1500 },
        };
        app.evaluateLevelMeterValue(mb);
        assert(bar.querySelectorAll('.lit').length > 0, 'meter connected lights segments');
        offline();
        app.evaluateLevelMeterValue(mb);
        assert(bar.querySelectorAll('.lit').length === 0, 'meter offline lights nothing');

        // Text input
        app.state['device.d.connected'] = true;
        const ti = document.createElement('input'); ti.type = 'text';
        const tb = { type: 'text_input_value', element: ti, elementDef: { id: 't' }, binding: { key: 'device.d.name_field' } };
        app.state['device.d.name_field'] = 'Podium';
        app.evaluateTextInputValue(tb);
        assert(ti.value === 'Podium', 'text input connected');
        offline();
        app.evaluateTextInputValue(tb);
        assert(ti.value === '', 'text input offline is empty');

        // List selection
        app.state['device.d.connected'] = true;
        const scrollArea = document.createElement('div');
        const row = document.createElement('div');
        row.className = 'list-item'; row.dataset.value = 'x';
        scrollArea.appendChild(row);
        const lb = {
            type: 'list_selected', element: document.createElement('div'), elementDef: { id: 'l' },
            binding: { key: 'device.d.sel' },
            _list: { scrollArea, itemBg: '#111', itemActiveBg: '#0af', selectedValues: new Set() },
        };
        app.state['device.d.sel'] = 'x';
        app.evaluateListSelected(lb);
        assert(row.classList.contains('active'), 'list connected lights the routed row');
        offline();
        app.evaluateListSelected(lb);
        assert(!row.classList.contains('active'), 'list offline lights nothing');
    },

    // A matrix can span several switchers, so going quiet about the live ones
    // because a third is down would be its own lie. It is marked per row.
    //
    // ALL THREE STYLES, deliberately: they are three different renderers and
    // only the tile wall has a row element that looks like a row. Covering the
    // tile wall alone is what let the crosspoint ship marking nothing at all --
    // it is a flat CSS grid, so the `closest('tr')` that found a row in a table
    // found null in a browser, silently, with this scenario green.
    q213_a_matrix_is_marked_one_destination_at_a_time() {
        const proj = (style) => project({
            elements: [{
                id: 'mx', type: 'matrix', matrix_style: style,
                matrix_config: {
                    sources: [{ value: 1, label: 'Laptop' }, { value: 2, label: 'Cam' }],
                    destinations: [
                        { value: 1, label: 'Left', route_key: 'device.sw_a.route_1' },
                        { value: 2, label: 'Right', route_key: 'device.sw_b.route_1' },
                    ],
                },
            }],
            placements: { mx: { x: 2, y: 2, w: 90, h: 60 } },
        });
        const state = () => ({
            'device.sw_a.connected': true, 'device.sw_a.route_1': 1,
            'device.sw_b.connected': false, 'device.sw_b.route_1': 2,
        });

        // Tiles — one card per destination.
        const tilesApp = mkApp();
        tilesApp.state = state();
        renderProject(tilesApp, proj('tiles'));
        const tiles = tilesApp.root.querySelectorAll('.matrix-tile');
        assert(tiles.length === 2, `two destination tiles, got ${tiles.length}`);
        assert(!tiles[0].classList.contains('device-offline'),
            'tiles: the live switcher keeps drawing its route');
        assert(tiles[1].classList.contains('device-offline'),
            'tiles: the unreachable one is marked, and only it');
        const routed = tiles[1].querySelector('.matrix-tile-source');
        assert(!/Cam/.test(routed ? routed.textContent : ''),
            `tiles: and it does not name the source it last reported, got "${routed && routed.textContent}"`);

        // List — one row per destination.
        const listApp = mkApp();
        listApp.state = state();
        renderProject(listApp, proj('list'));
        const rows = listApp.root.querySelectorAll('.matrix-list-row');
        assert(rows.length === 2, `two list rows, got ${rows.length}`);
        assert(!rows[0].classList.contains('device-offline'), 'list: the live row is untouched');
        assert(rows[1].classList.contains('device-offline'), 'list: the dead row is marked');

        // Crosspoint — a FLAT grid with no row element at all: a destination
        // name followed by one cell per source. The whole row has to be marked
        // node by node, or a row of live-looking crosspoints sits beside a
        // name that says the device is gone.
        const crossApp = mkApp();
        crossApp.state = state();
        renderProject(crossApp, proj('crosspoint'));
        const headers = crossApp.root.querySelectorAll('.matrix-output-header[data-output-idx]');
        assert(headers.length === 2, `two destination headers, got ${headers.length}`);
        assert(!headers[0].closest('tr'),
            'the crosspoint is a CSS grid, not a table -- if this ever fails, revisit the walk');
        assert(!headers[0].classList.contains('device-offline'), 'crosspoint: live name untouched');
        assert(headers[1].classList.contains('device-offline'), 'crosspoint: dead name marked');
        const rowCells = (header) => {
            const out = [];
            let n = header.nextElementSibling;
            while (n && !n.classList.contains('matrix-output-header')) { out.push(n); n = n.nextElementSibling; }
            return out;
        };
        const live = rowCells(headers[0]);
        const dead = rowCells(headers[1]);
        assert(live.length === 2 && dead.length === 2,
            `one cell per source in each row, got ${live.length} and ${dead.length}`);
        assert(live.every(c => !c.classList.contains('device-offline')),
            'crosspoint: not one cell of the live row is marked');
        assert(dead.every(c => c.classList.contains('device-offline')),
            'crosspoint: every cell of the dead row is marked, not just its name');
    },

    // The absent case is NOT the offline case. A panel drawn before its first
    // snapshot, and the Builder canvas previewing a page with no device state
    // at all, must render normally rather than as a wall of dead controls.
    q213_no_connected_key_is_not_a_claim_that_the_device_is_down() {
        const app = mkApp();
        const b = {
            type: 'text', element: document.createElement('div'),
            elementDef: { id: 'x' }, binding: { key: 'device.amp.gain' },
        };
        app.state = {};
        assert(app._bindingOffline(b) === false, 'no key at all');
        app.state = { 'device.amp.connected': true };
        assert(app._bindingOffline(b) === false, 'connected');
        app.state = { 'device.amp.connected': false };
        assert(app._bindingOffline(b) === true, 'and only an explicit false counts');
    },

    // The design canvas never draws it. Being unreachable is a runtime
    // condition, and the treatment REPLACES the design rather than filling it
    // in -- so on a bench where the gear is not plugged in yet, an author
    // would be laying out colours and artwork they cannot see. Preview runs
    // the same renderer over a real socket and is where that question is
    // answered.
    q213_the_design_canvas_draws_the_live_look() {
        const proj = () => project({
            elements: [{
                id: 'gain', type: 'fader', label: 'Gain',
                min: -80, max: 0, unit: 'dB',
                bindings: { show: { value: { key: 'device.amp.gain' } } },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        const state = { 'device.amp.connected': false, 'device.amp.gain': -6.0 };

        const designer = mkApp();
        designer.editMode = true;
        designer.state = { ...state };
        renderProject(designer, proj());
        const dEl = designer.root.querySelector('[data-element-id="gain"]');
        assert(!dEl.classList.contains('device-offline'),
            'the canvas draws the control as designed, not as the room finds it');
        assert(dEl.querySelector('.fader-value').textContent === '-6.0 dB',
            `and shows the live value, got ${dEl.querySelector('.fader-value').textContent}`);
        assert(window.getComputedStyle(dEl.querySelector('.fader-handle')).visibility === 'visible',
            'with its handle, which is half of what a fader looks like');

        // Preview and the real panel are the same renderer with editMode off.
        const preview = mkApp();
        preview.state = { ...state };
        renderProject(preview, proj());
        const pEl = preview.root.querySelector('[data-element-id="gain"]');
        assert(pEl.classList.contains('device-offline'),
            'preview shows what the room sees');
        assert(pEl.querySelector('.fader-value').textContent === '-- dB',
            `including no value, got ${pEl.querySelector('.fader-value').textContent}`);
    },

    // One element can carry several bindings naming different devices. It is
    // unavailable while ANY of them is gone, and only becomes available again
    // when the last one comes back.
    q213_an_element_is_unavailable_while_any_of_its_devices_is() {
        const app = mkApp();
        const host = document.createElement('div');
        app.elementMap = { e: { el: host, elementDef: { id: 'e' } } };
        const a = { elementDef: { id: 'e' }, element: host, binding: { key: 'device.a.v' } };
        const b = { elementDef: { id: 'e' }, element: host, binding: { key: 'device.b.v' } };
        app._markBindingAvailability(a, true);
        app._markBindingAvailability(b, false);
        assert(host.classList.contains('device-offline'), 'one gone is enough');
        app._markBindingAvailability(a, false);
        assert(!host.classList.contains('device-offline'), 'and both back clears it');
    },

    // --- Q-206: a refused command leaves the operator's value standing ------
    //
    // Measured on real hardware: the fader was dragged to -40.5 dB, the command
    // was refused, the band said so and went away, and the wall kept reading
    // -40.5 dB over an amplifier at 0.0. A refusal changes no state, so there
    // is no push coming to overwrite the move -- only a reload heals it, and a
    // wall tablet is the one browser that is never reloaded.

    // Found while writing the scenarios above, and fixed with them: the first
    // draw of a fader used an inline copy of the drag's update, missing its one
    // aria line. Nothing announced a reading until somebody dragged the handle,
    // and the arrow keys read their starting point out of that attribute -- so
    // the first press of Down on a freshly-drawn fader went to the floor.
    q206_a_fader_announces_the_value_it_is_drawing() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'gain', type: 'fader', min: -80, max: 0, unit: 'dB',
                bindings: { show: { value: { key: 'device.amp.gain' } } },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.gain': -6.0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="gain"]');
        const handle = el.querySelector('.fader-handle');
        assert(handle.getAttribute('aria-valuenow') === '-6',
            `drawn without being touched, got ${handle.getAttribute('aria-valuenow')}`);

        // And a state update keeps it in step, or the next keystroke would step
        // off the value before last.
        app.state['device.amp.gain'] = -12.0;
        app.evaluateAllBindings(['device.amp.gain']);
        assert(handle.getAttribute('aria-valuenow') === '-12',
            `after a state update, got ${handle.getAttribute('aria-valuenow')}`);
        handle.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown' }));
        assert(el.querySelector('.fader-value').textContent === '-13.0 dB',
            `so an arrow key steps from where the handle is, got ${el.querySelector('.fader-value').textContent}`);
    },

    // The photographed fader. The optimistic move is the panel working; what
    // has to change is what is left on screen once the answer is "no".
    q206_a_refused_change_puts_the_fader_back() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'gain', type: 'fader', label: 'Gain',
                min: -80, max: 0, unit: 'dB',
                bindings: { show: { value: { key: 'device.amp.gain' } } },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.gain': -6.0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="gain"]');
        const handle = el.querySelector('.fader-handle');
        const readout = el.querySelector('.fader-value');
        assert(readout.textContent === '-6.0 dB', `starts at the device value, got ${readout.textContent}`);

        // The press, through the real path: the keyboard arrow moves the handle
        // and sends, exactly as a drag does, without needing a layout engine.
        handle.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown' }));
        assert(readout.textContent === '-7.0 dB',
            `the control moves the moment it is touched, got ${readout.textContent}`);

        app.handleMessage({
            type: 'error', source_type: 'ui.change', element_id: 'gain',
            message: "'set_fader': 'channel' is required",
        });
        assert(readout.textContent === '-6.0 dB',
            `refused: back to the value the panel knows, got ${readout.textContent}`);
        assert(handle.style.bottom === '92.5%',
            `refused: and the handle with it, got ${handle.style.bottom}`);
        // The band is the other half and must survive the revert.
        const band = document.getElementById('panel-failure-message');
        assert(band && band.classList.contains('visible')
            && band.textContent === "'set_fader': 'channel' is required",
            'the reason is still on screen');
    },

    // Why a plain re-evaluation is not the fix, and must not be substituted for
    // one later: the state never moved, so every memoised renderer decides it
    // has nothing to do and leaves the rejected value exactly where it is.
    q206_the_memo_is_what_kept_the_wrong_value_standing() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'gain', type: 'fader', min: -80, max: 0, unit: 'dB',
                bindings: { show: { value: { key: 'device.amp.gain' } } },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.gain': -6.0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="gain"]');
        const readout = el.querySelector('.fader-value');
        el.querySelector('.fader-handle')
            .dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown' }));
        assert(readout.textContent === '-7.0 dB', 'moved');

        app.evaluateAllBindings();
        assert(readout.textContent === '-7.0 dB',
            'a plain re-evaluation is a no-op here -- the memo short-circuits it');
        app._revertRefusedInteraction('gain');
        assert(readout.textContent === '-6.0 dB',
            `the revert forgets the memo first, got ${readout.textContent}`);
    },

    // The frame names the control it is about, so a slow failure from one press
    // cannot reach in and rewrite whatever was touched after it. With no name
    // on the frame -- a rate limit, an ack, a macro step -- the last thing
    // touched is the answer, which is what the failure band already assumes.
    q206_the_revert_lands_on_the_control_the_refusal_is_about() {
        const app = mkApp();
        const fader = (id, key) => ({
            id, type: 'fader', min: -80, max: 0, unit: 'dB',
            bindings: { show: { value: { key } } },
        });
        const proj = project({
            elements: [fader('a', 'device.amp.gain'), fader('b', 'device.amp.aux')],
            placements: { a: { x: 5, y: 5, w: 20, h: 60 }, b: { x: 30, y: 5, w: 20, h: 60 } },
        });
        app.state = {
            'device.amp.connected': true, 'device.amp.gain': -6.0, 'device.amp.aux': -20.0,
        };
        renderProject(app, proj);
        const readout = (id) => app.root
            .querySelector(`[data-element-id="${id}"] .fader-value`);
        const nudge = (id) => app.root.querySelector(`[data-element-id="${id}"] .fader-handle`)
            .dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown' }));
        nudge('a');
        nudge('b');
        assert(readout('a').textContent === '-7.0 dB' && readout('b').textContent === '-21.0 dB',
            'both moved');

        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'a', message: 'no' });
        assert(readout('a').textContent === '-6.0 dB', 'the named control goes back');
        assert(readout('b').textContent === '-21.0 dB',
            `and nothing else does, got ${readout('b').textContent}`);

        // Unnamed: the panel last sent for 'b' (send() records it), so that is
        // the one a connection-level refusal is about.
        app.send({ type: 'ui.change', element_id: 'b', value: -21.0 });
        app.handleMessage({ type: 'error', message: 'Rate limit exceeded' });
        assert(readout('b').textContent === '-20.0 dB',
            `an unnamed refusal falls back to the last control touched, got ${readout('b').textContent}`);
    },

    // A range input keeps focus after the drag that set it, and the renderer
    // refuses to touch a focused input -- so honouring that guard here would
    // mean a slider is the one control that never goes back.
    q206_a_refused_slider_goes_back_even_though_it_still_has_focus() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'vol', type: 'slider', min: 0, max: 100, unit: '%',
                style: { show_value: true },
                bindings: { show: { value: { key: 'device.amp.vol' } } },
            }],
            placements: { vol: { x: 5, y: 5, w: 40, h: 10 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.vol': 30 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="vol"]');
        const input = el.querySelector('input[type=range]');
        const readout = el.querySelector('.slider-value');
        input.focus();
        input.value = '80';
        input.dispatchEvent(new window.Event('input'));
        assert(readout.textContent === '80 %', `moved, got ${readout.textContent}`);
        assert(document.activeElement === input, 'and still has focus, as a real drag leaves it');

        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'vol', message: 'no' });
        assert(readout.textContent === '30 %', `refused: back to 30, got ${readout.textContent}`);
        assert(input.value === '30', `and the thumb with it, got ${input.value}`);

        // The drag half of that guard still holds: a finger on the control wins.
        input.value = '80';
        input.dispatchEvent(new window.Event('input'));
        input._dragging = true;
        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'vol', message: 'no' });
        assert(readout.textContent === '80 %',
            `a control being dragged is left alone, got ${readout.textContent}`);
    },

    // Same rule on the fader: the release sends again, so a refusal of THAT
    // send is what puts it back. Yanking the handle away from the finger
    // holding it would be worse than the stale number.
    q206_a_control_the_operator_is_still_holding_is_left_alone() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'gain', type: 'fader', min: -80, max: 0, unit: 'dB',
                bindings: { show: { value: { key: 'device.amp.gain' } } },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.gain': -6.0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="gain"]');
        const handle = el.querySelector('.fader-handle');
        const readout = el.querySelector('.fader-value');
        handle.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown' }));
        handle._dragging = true;
        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'gain', message: 'no' });
        assert(readout.textContent === '-7.0 dB',
            `mid-drag the value stands, got ${readout.textContent}`);
        handle._dragging = false;
        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'gain', message: 'no' });
        assert(readout.textContent === '-6.0 dB',
            `and the refusal of the released value puts it back, got ${readout.textContent}`);
    },

    // A dropdown left showing an input the switcher was never told to take is
    // the same lie with a name instead of a number on it.
    q206_a_refused_selection_goes_back() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'src', type: 'select',
                options: [{ value: '1', label: 'Laptop' }, { value: '2', label: 'Room PC' }],
                bindings: { show: { value: { key: 'device.sw.route_1' } } },
            }],
            placements: { src: { x: 5, y: 5, w: 30, h: 10 } },
        });
        app.state = { 'device.sw.connected': true, 'device.sw.route_1': '1' };
        renderProject(app, proj);
        const select = app.root.querySelector('[data-element-id="src"] select');
        assert(select.value === '1', `starts on the routed source, got ${select.value}`);
        select.value = '2';
        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'src', message: 'no' });
        assert(select.value === '1', `refused: back to what is actually routed, got ${select.value}`);
    },

    // The one control where focus IS the gesture. Pulling characters out from
    // under a cursor mid-sentence is its own fault, so a text box being typed
    // into is left alone until the cursor leaves it.
    q206_a_text_box_being_typed_into_is_not_yanked() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'name', type: 'text_input',
                bindings: { show: { value: { key: 'device.dsp.preset_name' } } },
            }],
            placements: { name: { x: 5, y: 5, w: 30, h: 8 } },
        });
        app.state = { 'device.dsp.connected': true, 'device.dsp.preset_name': 'Meeting' };
        renderProject(app, proj);
        const input = app.root.querySelector('[data-element-id="name"] input');
        input.focus();
        input.value = 'Lecture';
        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'name', message: 'no' });
        assert(input.value === 'Lecture', `typing is not interrupted, got ${input.value}`);
        input.blur();
        app.handleMessage({ type: 'error', source_type: 'ui.change', element_id: 'name', message: 'no' });
        assert(input.value === 'Meeting', `and once the cursor leaves, back it goes, got ${input.value}`);
    },

    // The must-not-regress half: a command that WORKED leaves the control
    // where the operator put it. Most devices echo their new value, but a
    // polled one can take seconds, and snapping back in the meantime would
    // undo the thing that makes the panel feel like hardware.
    q206_an_accepted_command_leaves_the_control_alone() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'gain', type: 'fader', min: -80, max: 0, unit: 'dB',
                bindings: { show: { value: { key: 'device.amp.gain' } } },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.gain': -6.0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="gain"]');
        const readout = el.querySelector('.fader-value');
        el.querySelector('.fader-handle')
            .dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown' }));
        // Everything a working press produces: an ack that succeeded, and
        // unrelated state moving underneath.
        app.handleMessage({ type: 'command.ack', success: true });
        app.handleMessage({ type: 'state.update', changes: { 'var.other': 1 } });
        assert(readout.textContent === '-7.0 dB',
            `nothing reverts without a refusal, got ${readout.textContent}`);
    },

    // A step that never reached its device arrives on macro.step_error rather
    // than an error frame, and nothing on it names a control -- so the revert
    // has to ride the same claim that decides whether to draw the band at all.
    // A macro this panel did not start reaches neither.
    q206_a_failed_macro_step_puts_its_control_back() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'gain', type: 'fader', min: -80, max: 0, unit: 'dB',
                bindings: {
                    show: { value: { key: 'device.amp.gain' } },
                    do: { change: [{ action: 'macro', macro: 'set_level' }] },
                },
            }],
            placements: { gain: { x: 5, y: 5, w: 20, h: 60 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.gain': -6.0 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="gain"]');
        const readout = el.querySelector('.fader-value');
        el.querySelector('.fader-handle')
            .dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown' }));
        assert(readout.textContent === '-7.0 dB', 'moved');

        // Somebody else's macro: no claim, no band, and nothing moves here.
        app.handleMessage({
            type: 'macro.step_error', macro_id: 'someone_elses', message: 'nope',
        });
        assert(readout.textContent === '-7.0 dB',
            `another panel's macro failing is not this control's business, got ${readout.textContent}`);

        app.send({ type: 'ui.change', element_id: 'gain', value: -7.0 });
        app.handleMessage({
            type: 'macro.step_error', macro_id: 'set_level',
            message: 'amp: connection refused',
        });
        assert(readout.textContent === '-6.0 dB',
            `the failed step puts the control back, got ${readout.textContent}`);
    },
};

const results = {};
// Awaited, so a scenario that returns a promise reports its failure instead of
// passing vacuously the moment it is called.
(async () => {
    for (const [name, fn] of Object.entries(tests)) {
        try { await fn(); results[name] = { pass: true }; }
        catch (e) { results[name] = { pass: false, error: String(e && e.message), stack: (e && e.stack || '').split('\n').slice(0, 4).join(' | ') }; }
    }
    // Exit explicitly once stdout is flushed so jsdom/lingering timers from the
    // scenarios don't keep the process alive.
    process.stdout.write(JSON.stringify(results, null, 2), () => process.exit(0));
})();
