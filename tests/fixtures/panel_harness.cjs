/*
 * jsdom harness for web/panel/panel.js regression tests.
 *
 * Loads the real panel.js into a jsdom window and exercises the behaviours
 * fixed in the bug-fix campaign. Each test throws on failure; results are
 * emitted as JSON on stdout for the pytest wrapper (tests/test_panel_js.py)
 * to assert on. Invoked as: node panel_harness.cjs <abs path to panel.js>
 * with cwd set to web/programmer so `require('jsdom')` resolves.
 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const panelPath = process.argv[2];
const source = fs.readFileSync(panelPath, 'utf8') + '\n;window.__PanelApp = PanelApp;';

const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
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
        app.elementMap = { a, b };
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
        app.ws = { sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
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
};

const results = {};
for (const [name, fn] of Object.entries(tests)) {
    try { fn(); results[name] = { pass: true }; }
    catch (e) { results[name] = { pass: false, error: String(e && e.message), stack: (e && e.stack || '').split('\n').slice(0, 4).join(' | ') }; }
}
// Exit explicitly once stdout is flushed so jsdom/lingering timers from the
// scenarios don't keep the process alive.
process.stdout.write(JSON.stringify(results, null, 2), () => process.exit(0));
