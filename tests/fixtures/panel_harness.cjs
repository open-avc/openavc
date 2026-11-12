/*
 * jsdom harness for openavc/web/panel/panel.js regression tests.
 *
 * Loads the real panel.js into a jsdom window and exercises the behaviours
 * fixed in the bug-fix campaign. Each test throws on failure; results are
 * emitted as JSON on stdout for the pytest wrapper (tests/test_panel_js.py)
 * to assert on. Invoked as:
 *   node --experimental-vm-modules panel_harness.cjs <abs path to panel.js>
 * with NODE_PATH at openavc/web/programmer/node_modules so `require('jsdom')`
 * resolves.
 */
const fs = require('fs');
const vm = require('vm');
const { pathToFileURL, fileURLToPath } = require('url');
const { JSDOM } = require('jsdom');

const path = require('path');

const panelPath = process.argv[2];

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
// Records what it was handed, so a scenario can assert on the headers the
// panel's own fetch patch attaches. panel.js binds THIS as its originalFetch
// at eval time, which is the only way to see them from outside.
window.__fetchCalls = [];
// jsdom ships Headers but neither Request nor fetch. The panel's fetch patch
// asks `input instanceof Request` to find headers on a Request argument, and
// without the global that line is a ReferenceError rather than a false.
if (!window.Request) window.Request = class Request {};
window.fetch = async (input, init) => {
    window.__fetchCalls.push({ input, init });
    return { ok: false, json: async () => ({}) };
};
window.requestAnimationFrame = (cb) => { cb(0); return 0; };        // run binding batches synchronously
window.cancelAnimationFrame = () => {};
class FakeWS { constructor() { this.readyState = 1; } send() {} close() {} }
FakeWS.OPEN = 1;
window.WebSocket = FakeWS;
if (!window.Audio) window.Audio = window.HTMLAudioElement;
// jsdom doesn't implement media playback; make play() a resolved no-op.
window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
window.HTMLMediaElement.prototype.pause = function () {};

/**
 * Run panel.js and the modules it imports inside the jsdom window's own
 * context, so the panel runs as a browser runs it: strict, with jsdom's window
 * as its global, its imports resolved relative to each file. Linking is
 * asynchronous, and jsdom has already fired DOMContentLoaded by the time it
 * finishes, so it is fired again here: a browser runs a module script before
 * that event, which is when the panel's start-up listener expects it.
 */
let panelModules = new Map();
/** The exports of one of the panel's modules (`modules/<name>`), as the panel linked it. */
function panelModule(name) {
    const file = [...panelModules.keys()].find((f) => f.endsWith(`${path.sep}modules${path.sep}${name}`));
    return panelModules.get(file).namespace;
}

async function loadPanel(entry) {
    const context = dom.getInternalVMContext();
    const modules = new Map();
    const load = (file) => {
        if (!modules.has(file)) {
            modules.set(file, new vm.SourceTextModule(fs.readFileSync(file, 'utf8'),
                { context, identifier: pathToFileURL(file).href }));
        }
        return modules.get(file);
    };
    panelModules = modules;
    const root = load(entry);
    await root.link((specifier, referrer) =>
        load(path.resolve(path.dirname(fileURLToPath(referrer.identifier)), specifier)));
    await root.evaluate();
    document.dispatchEvent(new window.Event('DOMContentLoaded'));
}

// Set once the panel has loaded, before any scenario runs.
// panel.js replaced window.fetch with its credential-attaching wrapper. Held
// here because a later scenario reassigns window.fetch for its own purposes,
// and the auth scenarios must exercise the real wrapper whatever ran first.
let patchedFetch;
let PanelApp;
const mkApp = () => new PanelApp();

function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }

// --- What a style writes onto a node, called the way the panel calls it ----
//
// The paint scenarios reach the panel's style writers through these, so the
// pins below say what is drawn, not where the code that draws it lives.
const paint = () => panelModule('paint.js');
const paintStyle = (node, style) => paint().applyStyle(node, style);
const paintPicture = (node, ref, opts) => paint().applyImageEffect(node, ref, opts);
const paintTint = (node, colour) => paint().updateImageTint(node, colour);
const paintFrameless = (node) => paint().applyFrameless(node);
const clearColours = (node, rest) => paint().clearStateColours(node, rest);
const sanitizeValue = (value) => paint().sanitizeCssValue(value);
const sanitizeUrl = (url) => paint().sanitizeCssUrl(url);
const assetUrl = (ref) => panelModule('base_path.js').assetUrl(ref);

// --- The level controls (slider and fader), as a page draws them -----------
//
// The scenarios below drive the nodes a slider or fader drew and the value
// binding it registered, so they say what the control does, not how its code
// is laid out. The step snap is reached through `snapToStep`.
const snapToStep = (value, step) => panelModule('level.js').snapToStep(value, step);

/** A slider or fader drawn by the panel, and the value binding it registered. */
function drawnLevel(app, def) {
    const node = app.renderElement(def);
    return { node, b: app.bindings.find((x) => x.elementDef === def) };
}

/**
 * The level pins: what each slider and fader drew and sent, step by step, in
 * panel_level_pins.json, recorded the way the paint pins are.
 */
const LEVEL_PINS = JSON.parse(fs.readFileSync(path.join(__dirname, 'panel_level_pins.json'), 'utf8'));

/** The live sender's clock: performance.now and the timers, moved by hand. */
function withFakeClock(run) {
    const perf = window.performance;
    const real = { set: window.setTimeout, clear: window.clearTimeout };
    let now = 1000;
    let seq = 0;
    const timers = new Map();
    perf.now = () => now;
    window.setTimeout = (fn, ms) => {
        seq += 1;
        timers.set(seq, { at: now + (Number(ms) || 0), fn });
        return seq;
    };
    window.clearTimeout = (id) => { timers.delete(id); };
    const advance = (ms) => {
        const until = now + ms;
        for (;;) {
            const due = [...timers].filter(([, t]) => t.at <= until)
                .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
            if (!due) break;
            timers.delete(due[0]);
            now = due[1].at;
            due[1].fn();
        }
        now = until;
    };
    try {
        return run(advance);
    } finally {
        delete perf.now;
        window.setTimeout = real.set;
        window.clearTimeout = real.clear;
    }
}

/**
 * Every node under `node`, by position (`r`, then child indexes): its tag and
 * attribute names in order (`@`), each attribute (`@name`) and its own text (`#`).
 */
function nodeSnapshot(node) {
    const out = {};
    const walk = (n, at) => {
        out[`${at}@`] = [n.tagName.toLowerCase(), ...[...n.attributes].map((a) => a.name)].join(' ');
        for (const a of n.attributes) out[`${at}@${a.name}`] = a.value;
        const text = [...n.childNodes].filter((c) => c.nodeType === 3).map((c) => c.data).join('');
        if (text) out[`${at}#`] = text;
        [...n.children].forEach((c, i) => walk(c, `${at}.${i}`));
    };
    walk(node, 'r');
    return out;
}

/** A frame the panel sent, as one line. */
const frameLine = ({ type, element_id: id, value, ...rest }) =>
    `${type} ${id} ${JSON.stringify(value)}${Object.keys(rest).length ? ` ${JSON.stringify(rest)}` : ''}`;

/**
 * A level control on a page of its own, live unless `edit`, with a recorder:
 * `look()` notes what changed on its nodes since the last look (`c`:
 * everything, the first time), the range input's position (`p`), and every
 * frame sent since (`s`). `before(app)` runs before the page is drawn.
 */
function levelMount(def, { state, edit, before } = {}) {
    const app = mkApp();
    app.editMode = !!edit;
    const sent = [];
    app.send = (frame) => sent.push(frame);
    if (before) before(app);
    renderProject(app, project({
        elements: [def], placements: { [def.id]: { x: 5, y: 5, w: 30, h: 30 } },
    }));
    app.state = { ...(state || {}) };
    app.evaluateAllBindings(Object.keys(app.state));
    const m = { app, sent, seen: {}, steps: [] };
    m.node = () => app.root.querySelector(`[data-element-id="${def.id}"]`);
    m.set = (key, value) => {
        if (value === undefined) delete app.state[key];
        else app.state[key] = value;
        app.evaluateAllBindings([key]);
    };
    m.look = () => {
        const now = nodeSnapshot(m.node());
        const changed = {};
        for (const k of new Set([...Object.keys(m.seen), ...Object.keys(now)])) {
            if (m.seen[k] !== now[k]) changed[k] = now[k] ?? null;
        }
        m.seen = now;
        const step = {};
        if (Object.keys(changed).length) step.c = changed;
        const input = m.node().querySelector('input');
        if (input) step.p = input.value;
        if (sent.length) step.s = sent.splice(0).map(frameLine);
        m.steps.push(step);
    };
    return m;
}

/** The readings every level pin case is taken through, the device going away and back. */
function levelReadings(m, key) {
    m.look();
    for (const value of [60, 250, -5, null]) { m.set(key, value); m.look(); }
    m.set('device.d.connected', false); m.look();
    m.set(key, 30); m.look();
    m.set('device.d.connected', true); m.look();
    m.set(key, undefined); m.look();
    m.set(key, '55'); m.look();
    for (const value of [true, false, '', 'abc', 42]) { m.set(key, value); m.look(); }
}

/** One pin case: mounted, taken through the readings, then `gestures`; a throw is pinned as its last step. */
function levelCase(got, def, opts, gestures) {
    let m;
    try {
        m = levelMount(def, opts);
        levelReadings(m, LEVEL_KEY);
        gestures(m);
    } catch (e) {
        (m ? m.steps : []).push({ threw: String(e && e.message) });
    }
    got[def.id] = m ? m.steps : [{ threw: 'mount' }];
}

const LEVEL_KEY = 'device.d.v';
const levelDef = (type) => (id, extra) => ({ id, type, bindings: { show: { value: { key: LEVEL_KEY } } }, ...extra });
const LIVE_WITH_READING = { state: { 'device.d.connected': true, [LEVEL_KEY]: 40 } };
const LIVE_NO_READING = { state: { 'device.d.connected': true } };

/**
 * The paint pins: what each case drew when it was pinned, in
 * panel_paint_pins.json. Compared whole, so a value, a missing write or a
 * change of order fails. PAINT_PINS_RECORD=<file> writes what this run drew
 * instead, for reading by eye before it replaces the pins.
 */
const PAINT_PINS = JSON.parse(fs.readFileSync(path.join(__dirname, 'panel_paint_pins.json'), 'utf8'));
const recordedPins = {};
function comparePins(group, got, expected) {
    if (process.env.PAINT_PINS_RECORD) { recordedPins[group] = got; return; }
    const wrong = Object.keys({ ...expected, ...got })
        .filter((k) => JSON.stringify(got[k]) !== JSON.stringify(expected?.[k]))
        .map((k) => `${k}: got ${JSON.stringify(got[k])}, pinned ${JSON.stringify(expected?.[k])}`);
    assert(wrong.length === 0, `${group}: ${wrong.join(' ;; ')}`);
}

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

/** A button in Toggle mode: the "Mute Ch 1" an integrator builds. */
function toggleButton(id, key, onValue, extra) {
    return Object.assign({
        id, type: 'button', label: id,
        style: { bg_color: '#424242' },
        bindings: { do: { press: [{
            action: 'device.command', device: 'amp', command: 'mute_on',
            mode: 'toggle', toggle_key: key, toggle_value: onValue,
            off_action: { action: 'device.command', device: 'amp', command: 'mute_off' },
        }] } },
    }, extra || {});
}

/** The failure band is appended to the shared document and outlives a
 *  scenario, so anything asserting on it starts from no band at all. */
function clearFailureBand() {
    const band = document.getElementById('panel-failure-message');
    if (band) band.remove();
}

function renderProject(app, proj) {
    app.uiDef = proj.ui;
    app.uiSettings = proj.ui.settings || {};
    app.currentPage = proj.ui.pages[0].id;
    app.snapshotReceived = true;
    app.renderCurrentPage();
}

/**
 * One look evaluation on a bare element, in edit mode unless `live` is set:
 * edit mode is where the panel records the authored slot its words came from,
 * and where no device is ever unreachable. The element starts out reading
 * "Own" in its own grey.
 */
function runLook(method, type, binding, value, { ownStyle, state, live } = {}) {
    const app = mkApp();
    app.editMode = !live;
    app.currentPage = 'main';
    app.state = value === undefined ? {} : { [binding.key]: value };
    Object.assign(app.state, state || {});
    const element = document.createElement('div');
    element.setAttribute('data-element-id', 'probe');
    element.textContent = 'Own';
    document.body.appendChild(element);
    const elementDef = {
        id: 'probe', type, label: 'Own', text: 'Own', target_page: 'elsewhere',
        style: { bg_color: '#555555', ...(ownStyle || {}) },
    };
    try {
        app[method]({ type: 'look', element, elementDef, binding: JSON.parse(JSON.stringify(binding)) });
        return {
            bg: element.style.backgroundColor,
            text: element.textContent,
            path: JSON.parse(element.getAttribute('data-avc-text-src') || 'null'),
        };
    } finally {
        element.remove();
    }
}

const LOOK_OWN_BG = 'rgb(85, 85, 85)';
const LOOK_ENTRIES = {
    A: { label: 'A', bg_color: '#110000' },
    B: { label: 'B', bg_color: '#002200' },
    C: { label: 'C', bg_color: '#000033' },
    E: { label: 'E', bg_color: '#440044' },
};
const LOOK_BG = { A: 'rgb(17, 0, 0)', B: 'rgb(0, 34, 0)', C: 'rgb(0, 0, 51)', E: 'rgb(68, 0, 68)' };
const LOOK_STATES = { key: 'var.v', default_state: 'off', states: { on: LOOK_ENTRIES.A, off: LOOK_ENTRIES.B, 1: LOOK_ENTRIES.C } };
const LOOK_BLANK = { key: 'var.v', states: { on: LOOK_ENTRIES.A, '': LOOK_ENTRIES.E } };
const LOOK_BARE = { key: 'var.v', states: { on: LOOK_ENTRIES.A } };
const LOOK_DEFAULT_AND_BLANK = {
    key: 'var.v', default_state: 'off', states: { on: LOOK_ENTRIES.A, off: LOOK_ENTRIES.B, '': LOOK_ENTRIES.E },
};
const LOOK_NULL_ENTRY = { key: 'var.v', default_state: 'off', states: { on: null, off: LOOK_ENTRIES.B } };
const twoState = (equals) => ({
    key: 'var.v', condition: { equals }, style_active: LOOK_ENTRIES.A, style_inactive: LOOK_ENTRIES.B,
});
// [what, binding, state value, entry it draws (null: the control's own look),
//  the authored slot its words come from]
const LOOK_CASES = [
    ['a matching state', LOOK_STATES, 'on', 'A', ['states', 'on']],
    ['a state is matched exactly, case and all', LOOK_STATES, 'ON', 'B', ['states', 'off']],
    ['an unknown value takes the default', LOOK_STATES, 'zzz', 'B', ['states', 'off']],
    ['null takes the default', LOOK_STATES, null, 'B', ['states', 'off']],
    ['no value takes the default', LOOK_STATES, undefined, 'B', ['states', 'off']],
    ['a number matches its words', LOOK_STATES, 1, 'C', ['states', '1']],
    ['no default: null is the blank state', LOOK_BLANK, null, 'E', ['states', '']],
    ['no default: an unknown value is the blank state', LOOK_BLANK, 'zzz', 'E', ['states', '']],
    ['no default and no blank state: its own look', LOOK_BARE, 'zzz', null, null],
    ['null takes the default over a blank state', LOOK_DEFAULT_AND_BLANK, null, 'B', ['states', 'off']],
    ['an empty value takes the blank state', LOOK_DEFAULT_AND_BLANK, '', 'E', ['states', '']],
    ['a blank entry draws the default but names its own state', LOOK_NULL_ENTRY, 'on', 'B', ['states', 'on']],
    ['two-state: equal apart from case is active', twoState('On'), 'on', 'A', ['style_active']],
    ['two-state: anything else is inactive', twoState('On'), 'off', 'B', ['style_inactive']],
    ['two-state: no value is inactive', twoState('On'), undefined, 'B', ['style_inactive']],
    ['two-state: a number matches its words', twoState('1'), 1, 'A', ['style_active']],
    ['two-state: true matches TRUE', twoState('TRUE'), true, 'A', ['style_active']],
    ['two-state: null is compared as the word null', twoState('null'), null, 'A', ['style_active']],
    ['two-state with no condition: the word undefined is active',
        { key: 'var.v', style_active: LOOK_ENTRIES.A, style_inactive: LOOK_ENTRIES.B }, 'undefined', 'A', ['style_active']],
    ['two-state with no inactive look: its own look',
        { key: 'var.v', condition: { equals: 'x' }, style_active: LOOK_ENTRIES.A }, 'y', null, null],
    ['two-state with no active look: its own look',
        { key: 'var.v', condition: { equals: 'x' }, style_inactive: LOOK_ENTRIES.B }, 'x', null, null],
];

/**
 * Every LOOK_CASES row through one look evaluator. Image and Container have no
 * words, so only their colour is read. Where nothing is picked, a label keeps
 * the words it had and records no source, while a page nav and a button write
 * their own label back.
 */
function checkLookTable(method, type) {
    const words = type !== 'image' && type !== 'group';
    for (const [what, binding, value, pick, slot] of LOOK_CASES) {
        const r = runLook(method, type, binding, value);
        const bg = pick ? LOOK_BG[pick] : LOOK_OWN_BG;
        assert(r.bg === bg, `${what}: colour ${bg}, got ${r.bg}`);
        if (!words) continue;
        const text = pick || 'Own';
        const path = pick
            ? ['bindings', 'show', 'look', ...slot, 'label']
            : (type === 'label' ? null : ['label']);
        assert(r.text === text, `${what}: words "${text}", got "${r.text}"`);
        assert(JSON.stringify(r.path) === JSON.stringify(path),
            `${what}: words from ${JSON.stringify(path)}, got ${JSON.stringify(r.path)}`);
    }
}

function startupPage(id) {
    const page = project({ elements: [el(`${id}_heading`, 'label', { text: `${id} heading` })] }).ui.pages[0];
    return { ...page, id, name: id };
}

function openingPanel(pages, query = '') {
    const previousUrl = window.location.href;
    window.history.replaceState({}, '', '/panel/' + query);
    const app = mkApp();
    window.history.replaceState({}, '', previousUrl);
    // Use the opening wire messages, without renderProject's explicit page
    // selection. The theme is already settled so this render is synchronous.
    app._firstThemeSettled = true;
    app.handleMessage({ type: 'state.snapshot', state: {} });
    app.handleMessage({ type: 'ui.definition', ui: { ...project({}).ui, pages } });
    return app;
}

// Match the server's ui.page reply. A no-op send stub hides double navigation.
function navigationPanel() {
    const pages = ['home', 'details', 'help'].map(startupPage);
    pages[2].page_type = 'overlay';
    const app = openingPanel(pages);
    app.ws = { readyState: 1, send(raw) {
        const msg = JSON.parse(raw);
        if (msg.type === 'ui.page') {
            app.handleMessage({ type: 'ui.navigate', page_id: msg.page_id });
        }
    } };
    return app;
}

/** A theme shaped like the eight built-ins: the page background is a `var(name)`
 *  reference into the theme's own variables, which is what a project override
 *  has to reach. `page_defaults` is overridable so a scenario can swap in a
 *  gradient. */
function themeWithPageDefaults(pageDefaults) {
    return {
        id: 'slate',
        variables: { panel_bg: '#1e2a3a', accent: '#1976D2' },
        element_defaults: {},
        page_defaults: pageDefaults || { background_color: 'var(panel_bg)' },
    };
}

/** The project the page-background scenarios draw: one button, and settings naming the
 *  inline theme so applyTheme takes its synchronous path. */
function themedProject(settings) {
    const proj = project({
        elements: [el('b1', 'button')],
        placements: { b1: { x: 0, y: 0, w: 10, h: 10 } },
    });
    proj.ui.settings = Object.assign({ theme_id: 'slate' }, settings || {});
    return proj;
}

// --- Panel-access helpers ---------------------------------------------------

/** The waiting screen is appended to the shared document and outlives a
 *  scenario, so anything asserting on it starts from none at all. */
function clearAccessOverlay() {
    const overlay = document.getElementById('panel-access-overlay');
    if (overlay) overlay.remove();
}

/**
 * A fake server for start(): `answers` are the check-in bodies handed out in
 * order (the last one repeats; `null` is a 503), `claim` is what the
 * admin-password route answers, and `calls` records every request and every
 * socket in the order the page made them. `restore()` puts the real stubs
 * back; `settle()` lets the page's awaits run.
 */
function accessRig(answers) {
    const rig = { answers: answers.slice(), calls: [], claim: null };
    const savedFetch = window.fetch;
    const savedWS = window.WebSocket;
    window.fetch = async (url, init) => {
        const u = String(url);
        rig.calls.push({ url: u, init: init || {} });
        if (u.includes('/api/panel/access/claim')) {
            const answer = rig.claim
                || { status: 401, body: { detail: 'That password is not correct.' } };
            return { ok: answer.status < 300, status: answer.status, json: async () => answer.body };
        }
        if (u.includes('/api/panel/access')) {
            const body = rig.answers.length > 1 ? rig.answers.shift() : rig.answers[0];
            if (body === null) return { ok: false, status: 503, json: async () => ({}) };
            return { ok: true, status: 200, json: async () => body };
        }
        return { ok: false, status: 404, json: async () => ({}) };
    };
    class RecordingWS extends FakeWS {
        constructor(url, protocols) {
            super();
            rig.calls.push({ ws: url, protocols: protocols || null });
        }
    }
    RecordingWS.OPEN = 1;
    window.WebSocket = RecordingWS;
    rig.restore = () => {
        window.fetch = savedFetch;
        window.WebSocket = savedWS;
    };
    rig.settle = async (rounds = 8) => {
        for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 0));
    };
    return rig;
}

const PENDING_ANSWER = {
    access: 'approved', status: 'pending', code: '482-915', space: 'Executive Boardroom',
};

// --- Remote Access helpers --------------------------------------------------

// What the cloud answers, as bare text, for a session that is gone.
const REMOTE_ACCESS_ENDED = 'Remote access ended. Open a new session from the portal.';

/** An answer shaped like fetch's. `json` marks it as the system's own: its
 *  API answers every error as JSON, and the cloud's refusals are bare text. */
function reply(status, body, json) {
    return {
        ok: status < 300,
        status,
        headers: new window.Headers(json ? { 'content-type': 'application/json' } : {}),
        text: async () => body,
        json: async () => JSON.parse(body),
    };
}

/** Run `fn` on a page at `path`, then put the address back. The panel reads
 *  its path when it asks, not when it starts, so it stays set throughout. */
async function atPath(path, fn) {
    const previous = window.location.href;
    window.history.replaceState({}, '', path);
    try {
        return await fn();
    } finally {
        window.history.replaceState({}, '', previous);
    }
}

/**
 * A server that gives every request `answer` (an Error is a network failure)
 * and counts the sockets opened. `fetched` is every URL asked, in order.
 */
function remoteRig(answer) {
    const rig = { answer, fetched: [], sockets: 0 };
    const savedFetch = window.fetch;
    const savedWS = window.WebSocket;
    window.fetch = async (url) => {
        rig.fetched.push(String(url));
        if (rig.answer instanceof Error) throw rig.answer;
        return rig.answer;
    };
    class CountingWS extends FakeWS {
        constructor() {
            super();
            rig.sockets++;
        }
    }
    CountingWS.OPEN = 1;
    window.WebSocket = CountingWS;
    rig.restore = () => {
        window.fetch = savedFetch;
        window.WebSocket = savedWS;
    };
    rig.settle = async (rounds = 8) => {
        for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 0));
    };
    return rig;
}

/** Drop `app`'s socket with `code`, and return the reconnect delays the close
 *  scheduled (none run). */
function dropSocket(app, code) {
    const scheduled = [];
    const realSetTimeout = window.setTimeout;
    window.setTimeout = (fn, ms) => { scheduled.push(ms); return 0; };
    try {
        app.ws.onclose({ code });
    } finally {
        window.setTimeout = realSetTimeout;
    }
    return scheduled;
}

/** A dropped socket leaves the shared document offline; the scenarios after
 *  this one expect it as the document started. */
function resetAfterDrop(app) {
    app.access.hide();
    clearAccessOverlay();
    document.getElementById('offline-overlay').classList.remove('visible');
    if (app.root) app.root.style.pointerEvents = '';
}
const APPROVED_ANSWER = { access: 'approved', status: 'approved', name: 'Room 101 wall' };

const tests = {
    page_nav_opens_one_overlay_per_press() {
        const app = navigationPanel();
        app.renderPageNav(el('help_nav', 'page_nav', { target_page: 'help' })).click();
        assert(app.overlayStack.length === 1, 'one tap must open one overlay');
        assert(document.querySelectorAll('.panel-overlay').length === 1,
            'the reply must not draw a second copy');
        app.dismissAllOverlays();
    },

    page_nav_back_dismisses_without_leaving_the_page_behind() {
        const app = navigationPanel();
        app.navigateToPage('details');
        app.navigateToPage('help');
        app.renderPageNav(el('back_nav', 'page_nav', { target_page: '$back' })).click();
        assert(app.overlayStack.length === 0, 'Back dismisses the overlay');
        assert(app.currentPage === 'details', 'Back must not also pop page history');
        assert(app.pageHistory.join() === 'home', 'the previous page remains in history');
        app.dismissAllOverlays();
    },

    custom_navigation_opens_one_overlay_per_request() {
        const app = navigationPanel();
        const control = app.renderCustomElement({
            id: 'help_control', type: 'custom', custom_file: 'help/index.html',
            grant: { navigate: true },
        });
        control._pluginMessageHandler({ source: control._pluginIframe.contentWindow,
            data: { type: 'openavc:navigate', page: 'help' } });
        assert(app.overlayStack.length === 1, 'one frame request must open one overlay');
        app.dismissAllOverlays();
    },

    startup_uses_the_home_page_even_when_main_exists() {
        const app = openingPanel([startupPage('camera'), startupPage('main')]);
        assert(app.currentPage === 'camera', 'the first regular page is home');
        assert(app.root.textContent.includes('camera heading'), 'the home page is drawn');
        assert(!app.root.textContent.includes('main heading'), 'main does not override home');
    },

    startup_keeps_main_when_it_is_home() {
        const app = openingPanel([startupPage('main'), startupPage('camera')]);
        assert(app.currentPage === 'main', 'the usual Main-first project is unchanged');
        assert(app.root.textContent.includes('main heading'), 'Main is drawn');
    },

    startup_honors_an_explicit_page_link() {
        const app = openingPanel([startupPage('camera'), startupPage('main')], '?page=main');
        assert(app.currentPage === 'main', 'the URL may request another valid page');
        assert(app.root.textContent.includes('main heading'), 'the requested page is drawn');
    },

    startup_skips_overlays_when_choosing_home() {
        const overlay = { ...startupPage('help'), page_type: 'overlay' };
        const app = openingPanel([overlay, startupPage('camera'), startupPage('main')]);
        assert(app.currentPage === 'camera', 'an overlay is not the home page');
        assert(app.root.textContent.includes('camera heading'), 'the regular home page is drawn');
    },

    startup_does_not_reset_navigation_on_a_new_definition() {
        const app = openingPanel([startupPage('camera'), startupPage('main')]);
        app.navigateToPage('main');
        app.handleMessage({ type: 'ui.definition', ui: app.uiDef });
        assert(app.currentPage === 'main', 'a live definition keeps the page the operator chose');
        assert(app.root.textContent.includes('main heading'), 'the selected page stays visible');
    },

    startup_recovers_an_invalid_page_link_to_home() {
        const app = openingPanel([startupPage('camera'), startupPage('main')], '?page=removed');
        assert(app.currentPage === 'camera', 'a missing requested page falls back to home');
        assert(app.root.textContent.includes('camera heading'), 'the fallback is drawn');
    },

    // matrix routes re-evaluate on incremental state.update for any of
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

    // value displays revert to a no-data placeholder when the bound key
    // is deleted (device removed/offline), instead of freezing on last value.
    h002_gauge_reset() {
        const app = mkApp();
        const { node, b } = drawnLevel(app, {
            id: 'g', type: 'gauge', min: 0, max: 100, unit: '%',
            bindings: { show: { value: { key: 'device.g.level' } } },
        });
        const fg = { get d() { return node.querySelector('[data-role=gauge-fg]').getAttribute('d'); } };
        const vt = node.querySelector('[data-role=gauge-value]');
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
        const { node, b } = drawnLevel(app, {
            id: 'm', type: 'level_meter', min: -60, max: 0, style: { meter_segments: 5, show_peak: true },
            bindings: { show: { value: { key: 'device.m.level' } } },
        });
        const bar = node.querySelector('.meter-bar');
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
        const { node, b } = drawnLevel(app, {
            id: 's', type: 'slider', min: 0, max: 100, step: 1,
            bindings: { show: { value: { key: 'var.vol' } } },
        });
        const input = node.querySelector('input[type=range]');
        app.state = { 'var.vol': 75 };
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 75, `slider set to 75, got ${input.value}`);
        // inbound echo must not move the thumb while dragging.
        input._dragging = true; app.state['var.vol'] = 10;
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 75, 'slider unchanged during drag');
        input._dragging = false;
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 10, 'slider updates once drag ends');
        // no reading is not the floor. The travel empties and
        // the thumb goes away rather than parking at min, which would be a
        // claim of minimum -- the same one an unreachable device gets.
        delete app.state['var.vol'];
        app.evaluateSliderValue(b);
        assert(Number(input.value) === 0, 'slider travel emptied on delete');
        assert(input.classList.contains('no-reading'), 'slider thumb hidden with no reading');
        assert(!input.hasAttribute('aria-valuetext'), 'slider asserts no value to a screen reader');
    },

    h002_select_reset() {
        const app = mkApp();
        const sel = document.createElement('select');
        for (const v of ['a', 'b']) { const o = document.createElement('option'); o.value = v; o.textContent = v; sel.appendChild(o); }
        const b = { element: sel, binding: { key: 'var.sel' } };
        app.state = { 'var.sel': 'b' };
        app.evaluateSelectValue(b);
        assert(sel.value === 'b', 'select set to b');
        // no selection at all. Falling back to the first option was a
        // confident wrong answer -- "HDMI 1" for a switcher that never said.
        delete app.state['var.sel'];
        app.evaluateSelectValue(b);
        assert(sel.selectedIndex === -1, `select shows no selection with no reading, got ${sel.selectedIndex}`);
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
        const { node, b } = drawnLevel(app, {
            id: 'f', type: 'fader', min: 0, max: 100, unit: '%',
            bindings: { show: { value: { key: 'var.f' } } },
        });
        const handle = node.querySelector('.fader-handle');
        app.state = { 'var.f': 50 };
        app.evaluateFaderValue(b);
        assert(handle.style.bottom === '50%', `fader at 50%, got ${handle.style.bottom}`);
        // the handle goes away rather than parking at the floor, which
        // on a -80..0 fader reads as fully attenuated.
        delete app.state['var.f'];
        app.evaluateFaderValue(b);
        assert(handle.classList.contains('no-reading'), 'fader handle hidden with no reading');
        assert(!handle.hasAttribute('aria-valuenow'), 'fader asserts no value to a screen reader');
    },

    // fractional-step sliders/faders must not leak binary float noise
    // onto the wire (Math.round(v/step)*step yields e.g. 0.30000000000000004).
    slider_fader_step_no_float_noise() {
        // The shared snapper both controls now route their outgoing value through.
        assert(snapToStep(0.3, 0.1) === 0.3, `0.1-step snap clean, got ${snapToStep(0.3, 0.1)}`);
        assert(snapToStep(0.30000000000000004, 0.1) === 0.3, 'pre-noised value cleaned');
        assert(snapToStep(2.5500000000001, 0.05) === 2.55, `0.05-step snap, got ${snapToStep(2.5500000000001, 0.05)}`);
        assert(snapToStep(-6.0000000001, 0.5) === -6, `negative snap, got ${snapToStep(-6.0000000001, 0.5)}`);
        assert(snapToStep(7.4, 1) === 7, 'integer step rounds to whole');
        assert(snapToStep(3.14159, 0) === 3.14159, 'no step returns value as-is');

        // End-to-end: a rendered fractional-step slider must send a clean value,
        // proving the snapper is wired into the real render path (not just callable).
        const app = mkApp();
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

    // lock shown once per session (no re-lock on reconnect), and
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
        // lock_code cleared while locked removes the stuck overlay.
        app._lockInitialized = false; app.uiSettings = { lock_code: '1234' };
        app._reconcileLockOnDefinition();
        assert(document.getElementById('lock-overlay'), 'lock re-armed for a fresh session');
        app.uiSettings = { lock_code: '' };
        app._reconcileLockOnDefinition();
        assert(!document.getElementById('lock-overlay'), 'cleared lock_code removes the overlay');
    },

    // live state broadcast to an iframe element is scoped to what that
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

    // the iframe action bridge enforces the grant the element was
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
        assert(app.ws.sent[1].type === 'ui.page' && app.ws.sent[1].page_id === 'admin',
            'the permitted navigation reaches the server');
        assert(navigatedTo === null, 'navigation waits for the server reply');
        app.handleMessage({ type: 'ui.navigate', page_id: 'admin' });
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

    // ---- a page nobody has built yet ---------------------------------

    // The seed project ships one empty page, so opening /panel right after
    // claiming an instance drew the connection badge on black and nothing
    // else, which reads as a broken system rather than an unbuilt one.
    q160_an_empty_page_says_so() {
        const app = mkApp();
        renderProject(app, project({}));
        const notice = app.root.querySelector('.panel-page-empty');
        assert(notice, 'an empty page draws the notice');
        assert(notice.textContent === 'This page has no controls yet.',
            `and says only that, got ${JSON.stringify(notice.textContent)}`);
        // A wall panel shows this same document, and the reader there is an end
        // user. Nothing may send them to the IDE.
        assert(!/programmer|programme|sign in|admin/i.test(notice.textContent),
            'and points nobody at the Programmer');
    },

    // Guards the guard: a page with a control on it must stay silent, or the
    // notice would print over every panel in the field.
    q160_a_page_with_a_control_says_nothing() {
        const app = mkApp();
        renderProject(app, project({
            elements: [el('b1', 'button')],
            placements: { b1: { x: 10, y: 10, w: 20, h: 10 } },
        }));
        assert(!app.root.querySelector('.panel-page-empty'),
            'a page with an element draws no notice');
    },

    // A page whose only content is a master element is a real design -- a
    // landing page whose one control is the nav bar -- and it is what the
    // server already counts as panel content, so an appliance display IS on
    // this page. Counting `page.elements` alone would print the notice over a
    // working nav bar.
    q160_a_master_element_on_the_page_is_content() {
        const app = mkApp();
        const proj = project({});
        proj.ui.master_elements = [{
            id: 'home', type: 'button', label: 'Home', pages: '*',
            placements: { landscape: { x: 0, y: 0, w: 12, h: 8 } },
        }];
        setViewport(1280, 800);
        renderProject(app, proj);
        assert(app.root.querySelector('[data-element-id="home"]'), 'the master element draws');
        assert(!app.root.querySelector('.panel-page-empty'),
            'so the page is not empty and says nothing');
    },

    // ...but a master element that does not name this page draws nowhere on
    // it, so the page really is blank and the notice belongs. The render loop
    // reads `pages` exactly this way; the check must not be looser.
    q160_a_master_element_on_another_page_is_not_content() {
        const app = mkApp();
        for (const pages of [['other'], undefined]) {
            const proj = project({});
            proj.ui.master_elements = [{
                id: 'home', type: 'button', label: 'Home', pages,
                placements: { landscape: { x: 0, y: 0, w: 12, h: 8 } },
            }];
            renderProject(app, proj);
            assert(app.root.querySelector('.panel-page-empty'),
                `a master element scoped to ${JSON.stringify(pages)} leaves this page blank`);
        }
    },

    // A hidden master element draws nothing, same as one scoped elsewhere.
    q160_a_hidden_master_element_is_not_content() {
        const app = mkApp();
        const proj = project({});
        proj.ui.master_elements = [{
            id: 'home', type: 'button', label: 'Home', pages: '*', hidden: true,
            placements: { landscape: { x: 0, y: 0, w: 12, h: 8 } },
        }];
        renderProject(app, proj);
        assert(app.root.querySelector('.panel-page-empty'), 'a hidden master leaves the page blank');
    },

    // A custom page carries no elements by design -- the author's file fills
    // the screen. Printing "no controls yet" over a finished custom room would
    // be the notice calling a working panel broken.
    q160_a_custom_page_says_nothing() {
        const app = mkApp();
        const proj = project({});
        Object.assign(proj.ui.pages[0], {
            render_mode: 'custom', custom_file: 'room/index.html',
        });
        renderProject(app, proj);
        assert(app.root.querySelector('.panel-custom'), 'the author page draws');
        assert(!app.root.querySelector('.panel-page-empty'), 'and the notice does not');
    },

    // The Builder canvas embeds this document with `edit=1`. An author who has
    // just made a page is one drop away from their first control and does not
    // need to be told the page is empty.
    q160_the_designer_shows_no_notice() {
        const app = mkApp();
        app.editMode = true;
        renderProject(app, project({}));
        assert(!app.root.querySelector('.panel-page-empty'), 'no notice on the design canvas');
        app.editMode = false;
    },

    // The notice must never be mistaken for a control or eat a press meant for
    // something under it.
    q160_the_notice_takes_no_touches() {
        const app = mkApp();
        renderProject(app, project({}));
        const notice = app.root.querySelector('.panel-page-empty');
        assert(!notice.dataset.elementId, 'it is not an element');
        assert(!notice.onclick && !notice.getAttribute('onclick'), 'and handles no press');
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

    // countdown prefers a live state key over target_time and
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
        // an unparseable value renders the placeholder, not NaN.
        app.state = { 'var.cd': 'not-a-date' };
        const el2 = app.renderClock({ id: 'cd2', type: 'clock', clock_mode: 'countdown', bindings: { show: { value: { key: 'var.cd' } } } });
        if (app._clockInterval) { window.clearInterval(app._clockInterval); app._clockInterval = null; }
        const txt2 = el2.querySelector('.clock-display').textContent;
        assert(txt2 === '--:--:--', `invalid date -> placeholder, got "${txt2}"`);
    },

    // conditional text uses a normalized compare (numeric 1 matches '1').
    m004_text_loose_compare() {
        const app = mkApp();
        const el = document.createElement('div');
        const b = { element: el, elementDef: {}, binding: { key: 'var.x', condition: { equals: '1' }, text_true: 'ON', text_false: 'OFF' } };
        app.state = { 'var.x': 1 };
        app.evaluateText(b);
        assert(el.textContent === 'ON', `numeric 1 matches '1', got ${el.textContent}`);
    },

    // format replaces every {value} and treats the value literally.
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

    // the reconnect backoff cap field is wired up.
    l004_max_reconnect_delay() {
        assert(mkApp().maxReconnectDelay === 30000, 'maxReconnectDelay is 30000');
    },

    // status LED is inactive for off-like values, not just literal 'off'.
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

    // _activeAudio is capped so it can't grow unbounded.
    l009_audio_cap() {
        const app = mkApp();
        for (let i = 0; i < 12; i++) app._playSound(`http://x/${i}.mp3`, 1);
        assert(app._activeAudio.size <= 8, `audio set capped, size=${app._activeAudio.size}`);
    },

    // meeting timer baseline survives a re-render (doesn't restart).
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

    // a ui.* override reverts to the rendered base when its key is deleted.
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

    // CSS sanitizers neutralize breakout while keeping valid input.
    m010_m011_css_sanitizers() {
        const app = mkApp();
        const v = sanitizeValue('red); background-image: url(http://evil)');
        assert(!/url\s*\(/i.test(v) && !v.includes(';'), `value breakout neutralized, got "${v}"`);
        const rgb = sanitizeValue('rgb(10, 20, 30)');
        assert(rgb.includes('rgb(') && rgb.includes(','), `rgb() preserved, got "${rgb}"`);
        const u1 = sanitizeUrl('http://x/y z").evil');
        assert(!u1.includes('"') && !u1.includes(')'), `url breakout neutralized, got "${u1}"`);
        assert(sanitizeUrl('javascript:alert(1)') === '', 'javascript: url rejected');
        assert(sanitizeUrl('data:text/html,x') === '', 'data:text url rejected');
        assert(sanitizeUrl('/api/projects/default/assets/a.png') === '/api/projects/default/assets/a.png', 'relative asset url preserved');
    },

    // dismissing an overlay unregisters its clock update
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

    // going offline clears the idle timer and disables open
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

    // The "Connected" pill shows for 3 s on a real panel when the link comes
    // back. In the Builder's Preview, which reconnects every time it opens, it
    // sat over whatever control was in the top-right corner, so there it is
    // hidden at once.
    connection_status_hides_at_once_in_preview() {
        const app = mkApp();
        app.embedded = true;
        app.setConnectionStatus(true);
        assert(app.statusEl.classList.contains('hidden'), 'hidden at once in Preview');
        assert(!app._statusHideTimer, 'no hide timer in Preview');
        const panel = mkApp();
        panel.embedded = false;
        panel.setConnectionStatus(true);
        assert(!panel.statusEl.classList.contains('hidden'), 'shown on a real panel');
        assert(!!panel._statusHideTimer, 'hidden after a delay on a real panel');
        window.clearTimeout(panel._statusHideTimer);
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

    // degenerate ranges don't produce NaN.
    l001_divide_by_zero_guards() {
        const app = mkApp();
        const { node, b } = drawnLevel(app, {
            id: 'g', type: 'gauge', min: 50, max: 50, unit: '%', bindings: { show: { value: { key: 'var.g' } } },
        });
        const vt = node.querySelector('[data-role=gauge-value]');
        app.state = { 'var.g': 50 };
        app.evaluateGaugeValue(b);
        assert(!String(vt.textContent).includes('NaN'), `gauge min==max no NaN, got ${vt.textContent}`);
        // level meter span 0
        const { b: mb } = drawnLevel(app, {
            id: 'm', type: 'level_meter', min: 0, max: 0, style: { meter_segments: 1, show_peak: false },
            bindings: { show: { value: { key: 'var.m' } } },
        });
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
        paintStyle(node, {
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
    // phone; and a pixel floor beside a rem inside max(), which still scales
    // above the floor and exists because the rem alone is about eight pixels
    // on a phone held upright (the waiting screen). Anything else left in px
    // is a value that silently stops scaling.
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
            // A max() that names a rem is a floor, not a fixed size; a max()
            // without one is scanned like anything else.
            const scanned = decl.replace(/max\([^()]*rem[^()]*\)/g, '');
            for (const m of scanned.matchAll(/(?<![\w.-])(\d+(?:\.\d+)?)px/g)) {
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

    // --- a control whose device is unreachable draws no value --------
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

    // A device put to sleep on purpose keeps its wake button live: the press
    // goes out with the device off the network (`wake_devices`, resolved on
    // the server) and its feedback is the true "off". Every other control
    // bound to the device is marked, and a device that is merely unreachable
    // marks both.
    asleep_device_keeps_its_wake_control_live() {
        const app = mkApp();
        const proj = project({
            elements: [
                {
                    id: 'on', type: 'button', label: 'Power On',
                    bindings: {
                        show: { look: { key: 'device.tv.power', default_state: 'off',
                            states: { on: { label: 'ON' }, off: { label: 'OFF' } } } },
                        do: { press: [{ action: 'device.command', device: 'tv', command: 'power_on' }] },
                    },
                    wake_devices: ['tv'],
                },
                {
                    id: 'vol', type: 'fader', label: 'Volume', min: 0, max: 100,
                    bindings: { show: { value: { key: 'device.tv.volume' } } },
                },
            ],
            placements: { on: { x: 5, y: 5, w: 20, h: 10 }, vol: { x: 30, y: 5, w: 20, h: 60 } },
        });
        app.state = { 'device.tv.connected': true, 'device.tv.power': 'off', 'device.tv.volume': 20 };
        renderProject(app, proj);
        const on = app.root.querySelector('[data-element-id="on"]');
        const vol = app.root.querySelector('[data-element-id="vol"]');
        assert(app.bindings.some(b => b.elementDef?.id === 'on'),
            'the wake button reads a device key, so it has a binding to mark');

        app.state['device.tv.connected'] = false;
        app.state['device.tv.asleep'] = true;
        app.evaluateAllBindings(['device.tv.connected', 'device.tv.asleep']);
        assert(!on.classList.contains('device-offline'), 'asleep: the wake button stays live');
        assert(vol.classList.contains('device-offline'), 'asleep: the volume control is marked');

        app.state['device.tv.asleep'] = null;
        app.evaluateAllBindings(['device.tv.asleep']);
        assert(on.classList.contains('device-offline'),
            'unreachable, not asleep: the wake button is marked like the rest');
        assert(vol.classList.contains('device-offline'), 'unreachable: the volume control too');
    },

    // A fader that names no unit prints none. It used to print "%", so a dB
    // gain whose driver declared no unit read "-41.0 %"; the slider and the
    // gauge never did. A fader that names one still prints it.
    fader_with_no_unit_prints_no_unit() {
        const app = mkApp();
        const proj = project({
            elements: [
                { id: 'absent', type: 'fader', min: -100, max: 20,
                  bindings: { show: { value: { key: 'device.dsp.gain' } } } },
                { id: 'cleared', type: 'fader', min: -100, max: 20, unit: '',
                  bindings: { show: { value: { key: 'device.dsp.gain' } } } },
                { id: 'percent', type: 'fader', min: 0, max: 100, unit: '%',
                  bindings: { show: { value: { key: 'var.volume' } } } },
            ],
            placements: {
                absent: { x: 5, y: 5, w: 20, h: 60 },
                cleared: { x: 30, y: 5, w: 20, h: 60 },
                percent: { x: 55, y: 5, w: 20, h: 60 },
            },
        });
        app.state = { 'device.dsp.connected': true, 'device.dsp.gain': -41, 'var.volume': 50 };
        renderProject(app, proj);
        const readout = (id) => app.root.querySelector(`[data-element-id="${id}"] .fader-value`).textContent;
        assert(readout('absent') === '-41.0', `no unit field: no unit, got ${readout('absent')}`);
        assert(readout('cleared') === '-41.0', `cleared unit: no unit, got ${readout('cleared')}`);
        assert(readout('percent') === '50.0 %', `a named unit still prints, got ${readout('percent')}`);

        app.state['device.dsp.connected'] = false;
        app.evaluateAllBindings(['device.dsp.connected']);
        assert(readout('absent') === '--', `offline with no unit: just the dashes, got ${readout('absent')}`);
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
        const { node: sliderNode, b: sb } = drawnLevel(app, {
            id: 's', type: 'slider', min: 0, max: 100, unit: '%', style: { show_value: true },
            bindings: { show: { value: { key: 'device.d.v' } } },
        });
        const input = sliderNode.querySelector('input[type=range]');
        const fill = sliderNode.querySelector('.slider-fill');
        const sliderOut = sliderNode.querySelector('.slider-value');
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
        const { node: gaugeNode, b: gb } = drawnLevel(app, {
            id: 'g', type: 'gauge', min: 0, max: 100, unit: '%', zones: [],
            bindings: { show: { value: { key: 'device.d.v' } } },
        });
        const fgPath = gaugeNode.querySelector('[data-role=gauge-fg]');
        const valueText = gaugeNode.querySelector('[data-role=gauge-value]');
        app.evaluateGaugeValue(gb);
        assert(valueText.textContent === '42%', `gauge connected, got ${valueText.textContent}`);
        offline();
        app.evaluateGaugeValue(gb);
        assert(valueText.textContent === '--%', `gauge offline, got ${valueText.textContent}`);
        assert(fgPath.getAttribute('d') === '', 'gauge arc cleared');

        // Level meter
        app.state['device.d.connected'] = true;
        const { node: meterNode, b: mb } = drawnLevel(app, {
            id: 'm', type: 'level_meter', min: 0, max: 100, style: { meter_segments: 4, show_peak: false },
            bindings: { show: { value: { key: 'device.d.v' } } },
        });
        const bar = meterNode.querySelector('.meter-bar');
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

    // --- a refused command leaves the operator's value standing ------
    //
    // Measured on real hardware: the fader was dragged to -40.5 dB, the command
    // was refused, the band said so and went away, and the wall kept reading
    // -40.5 dB over an amplifier at 0.0. A refusal changes no state, so there
    // is no push coming to overwrite the move -- only a reload heals it, and a
    // wall tablet is the one browser that is never reloaded.

    // the device is RIGHT THERE and has not reported this reading yet.
    //
    // A different sentence from the offline mark's "the device is unreachable", and it now
    // happens on every fresh connection: a declared state variable holds no
    // value until the device sends one, where it used to hold a number the
    // platform invented. Three renderers' null branch invented one of their own
    // on top of that -- the fader and the slider parked at their floor, the
    // select showed its first option -- which on a live device is a confident
    // wrong answer with nothing to mark it.
    q172_a_live_device_that_has_not_reported_draws_no_value() {
        const app = mkApp();
        app.state = { 'device.d.connected': true, 'device.d.v': null };

        // Fader: no handle, no aria value, "-- dB".
        const { node: faderNode, b: fb } = drawnLevel(app, {
            id: 'f', type: 'fader', min: -80, max: 0, unit: 'dB',
            bindings: { show: { value: { key: 'device.d.v' } } },
        });
        const handle = faderNode.querySelector('.fader-handle');
        const faderOut = faderNode.querySelector('.fader-value');
        app.evaluateFaderValue(fb);
        assert(faderOut.textContent === '-- dB', `fader unreported, got ${faderOut.textContent}`);
        assert(handle.classList.contains('no-reading'), 'fader handle hidden while unreported');
        assert(!handle.hasAttribute('aria-valuenow'), 'fader announces no value while unreported');

        // Slider: empty travel, hidden thumb, "-- %".
        const { node: sliderNode, b: sb } = drawnLevel(app, {
            id: 's', type: 'slider', min: 0, max: 100, unit: '%', style: { show_value: true },
            bindings: { show: { value: { key: 'device.d.v' } } },
        });
        const input = sliderNode.querySelector('input[type=range]');
        const fill = sliderNode.querySelector('.slider-fill');
        const sliderOut = sliderNode.querySelector('.slider-value');
        app.evaluateSliderValue(sb);
        assert(sliderOut.textContent === '-- %', `slider unreported, got ${sliderOut.textContent}`);
        assert(input.classList.contains('no-reading'), 'slider thumb hidden while unreported');
        assert(fill.style.width === '0%', `slider fill emptied, got ${fill.style.width}`);

        // Select: no selection, not the first option.
        const sel = document.createElement('select');
        for (const v of ['hdmi1', 'hdmi2']) {
            const o = document.createElement('option'); o.value = v; sel.appendChild(o);
        }
        app.state['device.d.input'] = null;
        const selB = {
            type: 'select_value', element: sel, elementDef: { id: 'sel' },
            binding: { key: 'device.d.input' },
        };
        app.evaluateSelectValue(selB);
        assert(sel.selectedIndex === -1, `select unreported shows nothing, got ${sel.selectedIndex}`);

        // Gauge: no arc, "--%".
        const { node: gaugeNode, b: gb } = drawnLevel(app, {
            id: 'g', type: 'gauge', min: 0, max: 100, unit: '%', zones: [],
            bindings: { show: { value: { key: 'device.d.v' } } },
        });
        const fgPath = gaugeNode.querySelector('[data-role=gauge-fg]');
        const valueText = gaugeNode.querySelector('[data-role=gauge-value]');
        app.evaluateGaugeValue(gb);
        assert(valueText.textContent === '--%', `gauge unreported, got ${valueText.textContent}`);
        assert(fgPath.getAttribute('d') === '', 'gauge arc cleared while unreported');

        // Formatted label: the sentence keeps its shape, "Amp draw: -- A".
        // Rendered through the real page, because the label's text is written
        // on the way through renderProject rather than by a named evaluator.
        const app2 = mkApp();
        const proj = project({
            elements: [{
                id: 'draw', type: 'label', text: '',
                bindings: { show: { value: {
                    key: 'device.amp.ac_line_current', format: 'Amp draw: {value} A',
                } } },
            }],
            placements: { draw: { x: 5, y: 5, w: 30, h: 8 } },
        });
        app2.state = { 'device.amp.connected': true, 'device.amp.ac_line_current': null };
        renderProject(app2, proj);
        const labelEl = app2.root.querySelector('[data-element-id="draw"]');
        assert(labelEl.textContent === 'Amp draw: -- A',
            `label unreported, got "${labelEl.textContent}"`);
    },

    // The two states must not look the same. The offline dimmed-and-dashed mark
    // means "this control cannot be trusted"; a device that is answering and
    // has simply not sent this reading yet has not earned it, and its own CSS
    // comment says "--" alone cannot be told from "nothing reported yet" --
    // which is exactly the distinction being drawn here.
    q172_an_unreported_reading_is_not_the_unavailable_mark() {
        const app = mkApp();
        const { node: el, b } = drawnLevel(app, {
            id: 'f', type: 'fader', min: -80, max: 0, unit: 'dB',
            bindings: { show: { value: { key: 'device.d.v' } } },
        });
        const handle = el.querySelector('.fader-handle');
        const out = el.querySelector('.fader-value');

        // Live device, nothing reported: no value, no mark.
        app.state = { 'device.d.connected': true, 'device.d.v': null };
        app.evaluateFaderValue(b);
        assert(out.textContent === '-- dB', 'unreported reads --');
        assert(!el.classList.contains('device-offline'),
            'a live device is not marked unavailable');

        // Same absent value, device now gone: the mark appears.
        app.state['device.d.connected'] = false;
        app.evaluateFaderValue(b);
        assert(el.classList.contains('device-offline'),
            'an unreachable device is marked unavailable');

        // And a real reading clears both.
        app.state['device.d.connected'] = true;
        app.state['device.d.v'] = -6;
        app.evaluateFaderValue(b);
        assert(out.textContent === '-6.0 dB', `reported reads the value, got ${out.textContent}`);
        assert(!handle.classList.contains('no-reading'), 'handle returns with a reading');
        assert(!el.classList.contains('device-offline'), 'mark cleared with a reading');
    },

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

    slider_keeps_receiving_feedback_after_pointer_release() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'level', type: 'slider', min: 0, max: 100, unit: '%',
                send_on_release: true, style: { show_value: true },
                bindings: { show: { value: { key: 'device.amp.level' } } },
            }],
            placements: { level: { x: 5, y: 5, w: 40, h: 15 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.level': 35 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="level"]');
        const input = el.querySelector('input[type=range]');
        const readout = el.querySelector('.slider-value');
        input.focus();
        input.dispatchEvent(new window.Event('pointerdown'));
        input.value = '43';
        input.dispatchEvent(new window.Event('input'));
        app.handleMessage({ type: 'state.update', changes: { 'device.amp.level': 37 } });
        assert(readout.textContent === '43 %', 'an active drag keeps its selected value');
        input.dispatchEvent(new window.Event('change'));
        input.dispatchEvent(new window.Event('pointerup'));
        app.handleMessage({ type: 'state.update', changes: { 'device.amp.level': 27 } });
        assert(document.activeElement === input, 'release retains keyboard focus');
        assert(readout.textContent === '27 %', `released control follows the device, got ${readout.textContent}`);
        assert(input.value === '27', 'the thumb follows the external change too');
        app.handleMessage({ type: 'state.update', changes: { 'device.amp.connected': false } });
        assert(readout.textContent === '-- %', 'focus cannot preserve a reading after disconnection');
        assert(input.classList.contains('no-reading'), 'the unavailable thumb is hidden');
    },

    slider_keeps_receiving_feedback_after_keyboard_change() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'level', type: 'slider', min: 0, max: 100, unit: '%',
                send_on_release: true, style: { show_value: true },
                bindings: { show: { value: { key: 'device.amp.level' } } },
            }],
            placements: { level: { x: 5, y: 5, w: 40, h: 15 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.level': 35 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="level"]');
        const input = el.querySelector('input[type=range]');
        const readout = el.querySelector('.slider-value');
        input.focus();
        input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight' }));
        input.value = '36'; // jsdom does not implement the native range default action.
        input.dispatchEvent(new window.Event('input'));
        input.dispatchEvent(new window.Event('change'));
        input.dispatchEvent(new window.KeyboardEvent('keyup', { key: 'ArrowRight' }));
        app.handleMessage({ type: 'state.update', changes: { 'device.amp.level': 36 } });
        app.handleMessage({ type: 'state.update', changes: { 'device.amp.level': 22 } });
        assert(document.activeElement === input, 'the control stays available to the keyboard');
        assert(readout.textContent === '22 %', `keyboard focus must not suppress later feedback, got ${readout.textContent}`);
        assert(input.value === '22', 'the next key press starts from the actual level');
    },

    // A range input retains focus after a drag. A refusal must restore the
    // reported value without requiring the operator to move focus elsewhere.
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

    // --- a toggle button says whether it is on -------------
    //
    // Toggle mode used to be a dispatch rule and never a display rule: the
    // photographed mute button was pixel-identical muted and unmuted while
    // muting the amplifier every time.

    q209_a_toggle_button_shows_whether_it_is_on() {
        const app = mkApp();
        const proj = project({
            elements: [toggleButton('mute', 'device.amp.channel.01.mute', true)],
            placements: { mute: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.channel.01.mute': false };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        const off = el.style.backgroundColor;
        assert(!el.classList.contains('toggle-on'), 'off: not lit');

        app.state['device.amp.channel.01.mute'] = true;
        app.evaluateAllBindings(['device.amp.channel.01.mute']);
        assert(el.classList.contains('toggle-on'), 'on: the button reads as on');
        assert(el.style.backgroundColor === 'rgb(33, 150, 243)',
            `on: lit in the accent, got ${el.style.backgroundColor}`);
        assert(el.style.backgroundColor !== off,
            'on and off must not draw the same pixels — that is the whole finding');

        app.state['device.amp.channel.01.mute'] = false;
        app.evaluateAllBindings(['device.amp.channel.01.mute']);
        assert(!el.classList.contains('toggle-on'), 'off again: not lit');
        assert(el.style.backgroundColor === off,
            `off again: back to its own look, got ${el.style.backgroundColor}`);

        // A button that names no colours of its own takes them from the
        // stylesheet, and only an empty inline value lets that through -- the
        // lit colours have to be dropped rather than written over.
        const app2 = mkApp();
        const bare = toggleButton('bare', 'var.on', true);
        delete bare.style;
        const proj2 = project({ elements: [bare], placements: { bare: { x: 5, y: 5, w: 20, h: 10 } } });
        app2.state = { 'var.on': true };
        renderProject(app2, proj2);
        const el2 = app2.root.querySelector('[data-element-id="bare"]');
        assert(el2.style.backgroundColor === 'rgb(33, 150, 243)',
            `on: lit, got ${el2.style.backgroundColor}`);
        app2.state['var.on'] = false;
        app2.evaluateAllBindings(['var.on']);
        assert(el2.style.backgroundColor === '',
            `off: the lit colour is dropped, not painted over, got "${el2.style.backgroundColor}"`);
        assert(el2.style.color === '',
            `off: and so is the text colour, got "${el2.style.color}"`);
    },

    // The press half is what already worked. It has never had a test, and it
    // shares a function with the new display half.
    q209_the_press_still_picks_its_half_from_the_same_state() {
        const app = mkApp();
        const sent = [];
        app.send = (m) => sent.push(m);
        const proj = project({
            elements: [toggleButton('mute', 'device.amp.mute', true)],
            placements: { mute: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'device.amp.connected': true, 'device.amp.mute': false };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        el.dispatchEvent(new window.MouseEvent('mousedown', { cancelable: true }));
        assert(sent[0] && sent[0].type === 'ui.press',
            `off -> the on action, got ${JSON.stringify(sent[0])}`);
        app.state['device.amp.mute'] = true;
        app.evaluateAllBindings(['device.amp.mute']);
        sent.length = 0;
        el.dispatchEvent(new window.MouseEvent('mousedown', { cancelable: true }));
        assert(sent[0] && sent[0].type === 'ui.toggle_off',
            `on -> the off action, got ${JSON.stringify(sent[0])}`);
    },

    // The built-in accents run from #1976D2 to #00E676, so one text colour
    // cannot serve them: white on the bright green is unreadable.
    q209_the_lit_colour_follows_the_accent_and_stays_readable() {
        const cases = [
            ['#1976D2', 'rgb(255, 255, 255)', 'rgb(25, 118, 210)'],
            ['#00E676', 'rgb(17, 17, 17)', 'rgb(0, 230, 118)'],
            ['#FFA726', 'rgb(17, 17, 17)', 'rgb(255, 167, 38)'],
        ];
        for (const [accent, text, bg] of cases) {
            document.documentElement.style.setProperty('--panel-accent', accent);
            const app = mkApp();
            const proj = project({
                elements: [toggleButton('t', 'var.on', true)],
                placements: { t: { x: 5, y: 5, w: 20, h: 10 } },
            });
            app.state = { 'var.on': true };
            renderProject(app, proj);
            const el = app.root.querySelector('[data-element-id="t"]');
            assert(el.style.backgroundColor === bg,
                `${accent}: lit in the theme accent, got ${el.style.backgroundColor}`);
            assert(el.style.color === text,
                `${accent}: text that reads on it, got ${el.style.color}`);
        }
        document.documentElement.style.removeProperty('--panel-accent');

        // An element that names its own accent lights in that one.
        const app = mkApp();
        const own = toggleButton('t', 'var.on', true);
        own.style = { accent_color: '#D4AF37' };
        const proj = project({ elements: [own], placements: { t: { x: 5, y: 5, w: 20, h: 10 } } });
        app.state = { 'var.on': true };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="t"]');
        assert(el.style.backgroundColor === 'rgb(212, 175, 55)',
            `an element's own accent wins, got ${el.style.backgroundColor}`);
        assert(el.style.color === 'rgb(17, 17, 17)',
            `and is read for contrast too, got ${el.style.color}`);
    },

    // on_label / off_label are documented in the project format and in the UI
    // Builder guide as changing the button text per state. Nothing read them.
    q209_a_toggle_says_the_words_it_was_given() {
        const app = mkApp();
        const btn = toggleButton('mute', 'device.amp.mute', true);
        btn.label = 'Mute Ch 1';
        btn.bindings.do.press[0].on_label = 'MUTED';
        btn.bindings.do.press[0].off_label = 'MUTE';
        const proj = project({ elements: [btn], placements: { mute: { x: 5, y: 5, w: 20, h: 10 } } });
        app.state = { 'device.amp.connected': true, 'device.amp.mute': false };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        assert(el.textContent === 'MUTE', `off word, got "${el.textContent}"`);
        assert(el.getAttribute('aria-label') === 'MUTE', 'accessible name follows the off label');
        app.state['device.amp.mute'] = true;
        app.evaluateAllBindings(['device.amp.mute']);
        assert(el.textContent === 'MUTED', `on word, got "${el.textContent}"`);
        assert(el.getAttribute('aria-label') === 'MUTED', 'accessible name follows the on label');

        // Only one of the two named: the other state keeps the button's name.
        const app2 = mkApp();
        const half = toggleButton('p', 'var.power', true);
        half.label = 'Power';
        half.bindings.do.press[0].on_label = 'ON';
        const proj2 = project({ elements: [half], placements: { p: { x: 5, y: 5, w: 20, h: 10 } } });
        app2.state = { 'var.power': false };
        renderProject(app2, proj2);
        const el2 = app2.root.querySelector('[data-element-id="p"]');
        assert(el2.textContent === 'Power', `no off word, so its own, got "${el2.textContent}"`);
        app2.state['var.power'] = true;
        app2.evaluateAllBindings(['var.power']);
        assert(el2.textContent === 'ON', `on word, got "${el2.textContent}"`);
    },

    // A toggle naming no words is left holding whatever it is already showing,
    // rather than having its label rewritten on every state batch -- which
    // would fight a ui.<id>.label override and rebuild an icon layout for
    // nothing.
    q209_a_toggle_naming_no_words_is_left_alone() {
        const app = mkApp();
        const proj = project({
            elements: [toggleButton('mute', 'var.on', true)],
            placements: { mute: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'var.on': false };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        app.state['ui.mute.label'] = 'From a macro';
        app.evaluateAllBindings(['ui.mute.label']);
        assert(el.textContent === 'From a macro',
            `an override still owns the words, got "${el.textContent}"`);
        app.state['var.on'] = true;
        app.evaluateAllBindings(['var.on']);
        assert(el.textContent === 'From a macro',
            `and the toggle lighting up does not take them, got "${el.textContent}"`);
        assert(el.classList.contains('toggle-on'), 'while the look still tracks the state');
        assert(el.getAttribute('aria-label') === 'From a macro', 'accessible name follows the override');
        app.state['ui.mute.label'] = '';
        app.evaluateAllBindings(['ui.mute.label']);
        assert(el.textContent === '', 'an empty override clears the visible label');
        assert(el.getAttribute('aria-label') === 'mute', 'a blank button retains its configured name');
        delete app.state['ui.mute.label'];
        app.evaluateAllBindings(['ui.mute.label']);
        assert(el.getAttribute('aria-label') === el.textContent, 'clearing the override restores both names');
    },

    // The author's own appearance binding takes the whole job. Two bindings
    // writing one element's colour and label is how the two drift apart.
    q209_an_appearance_binding_takes_the_whole_job() {
        const app = mkApp();
        const btn = toggleButton('mute', 'device.amp.mute', true);
        btn.label = 'Mute';
        btn.bindings.show = { look: {
            key: 'device.amp.mute', default_state: 'false',
            states: { true: { label: 'MUTED', bg_color: '#2e7d32' },
                      false: { label: 'MUTE', bg_color: '#c62828' } },
        } };
        const proj = project({ elements: [btn], placements: { mute: { x: 5, y: 5, w: 20, h: 10 } } });
        app.state = { 'device.amp.connected': true, 'device.amp.mute': true };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        assert(app.bindings.every(b => b.type !== 'toggle_look'),
            'a button with a look binding registers no toggle look of its own');
        assert(el.style.backgroundColor === 'rgb(46, 125, 50)',
            `the author's colour, not the accent, got ${el.style.backgroundColor}`);
        assert(el.textContent === 'MUTED', `and the author's word, got "${el.textContent}"`);
        assert(el.getAttribute('aria-label') === 'MUTED', 'appearance labels are accessible too');
        assert(!el.classList.contains('toggle-on'),
            'and no lit ring underneath it');
    },

    // The offline rule, one control further on: an unreachable device's last known
    // state is not a state to draw, and "off" is a claim.
    q209_an_unreachable_device_is_not_a_toggle_that_is_off() {
        const app = mkApp();
        const btn = toggleButton('mute', 'device.amp.mute', true);
        btn.label = 'Mute Ch 1';
        btn.bindings.do.press[0].on_label = 'MUTED';
        btn.bindings.do.press[0].off_label = 'MUTE';
        const proj = project({ elements: [btn], placements: { mute: { x: 5, y: 5, w: 20, h: 10 } } });
        app.state = { 'device.amp.connected': true, 'device.amp.mute': true };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        assert(el.classList.contains('toggle-on') && el.textContent === 'MUTED', 'lit while connected');

        app.state['device.amp.connected'] = false;
        app.evaluateAllBindings(['device.amp.connected']);
        assert(!el.classList.contains('toggle-on'), 'offline: not lit');
        assert(el.textContent === 'Mute Ch 1',
            `offline: its own name, not "MUTE" -- which would claim it is unmuted, got "${el.textContent}"`);
        assert(el.classList.contains('device-offline'), 'offline: and reads as unavailable');
        assert(el.getAttribute('aria-label') === 'Mute Ch 1', 'offline restores the configured accessible name');

        app.state['device.amp.connected'] = true;
        app.evaluateAllBindings(['device.amp.connected']);
        assert(el.classList.contains('toggle-on') && el.textContent === 'MUTED',
            'back when the device is back');
    },

    // A frameless or image button takes its fill from artwork, so the outline
    // in panel-elements.css is the whole indication there.
    q209_a_frameless_toggle_is_ringed_rather_than_filled() {
        const app = mkApp();
        const btn = toggleButton('mute', 'var.on', true);
        btn.frameless = true;
        const proj = project({ elements: [btn], placements: { mute: { x: 5, y: 5, w: 20, h: 10 } } });
        app.state = { 'var.on': true };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="mute"]');
        assert(el.classList.contains('toggle-on'), 'a frameless toggle still reads as on');
        assert(el.style.backgroundColor === 'transparent',
            `and keeps its frameless background, got ${el.style.backgroundColor}`);
        // jsdom does not expand the shorthand, so read it as written.
        const outline = window.getComputedStyle(el).getPropertyValue('outline');
        assert(/solid/.test(outline) && /accent/.test(outline),
            `the ring in the accent is what shows it, got outline "${outline}"`);
    },

    // Without a state key there is nothing to indicate, and the press falls
    // back to tap -- so no dead ring on a button that is not really a toggle.
    q209_a_toggle_with_no_state_key_indicates_nothing() {
        const app = mkApp();
        const btn = { id: 'b', type: 'button', label: 'Go', bindings: { do: { press: [
            { action: 'macro', macro: 'go', mode: 'toggle' },
        ] } } };
        const proj = project({ elements: [btn], placements: { b: { x: 5, y: 5, w: 20, h: 10 } } });
        app.state = {};
        renderProject(app, proj);
        assert(app.bindings.every(x => x.type !== 'toggle_look'),
            'no state key, nothing to indicate');
        assert(!app.root.querySelector('[data-element-id="b"]').classList.contains('toggle-on'),
            'and no ring');
    },

    // a macro is a fact about the room, so every panel sees every
    // run of it, and no frame says which run it belongs to. Clearing the claim
    // on the first ending run threw away the press somebody is standing there
    // waiting for.
    q139_a_run_that_ends_does_not_take_another_runs_claim() {
        const app = mkApp();
        app.ws = { readyState: 1, send() {} };
        clearFailureBand();

        // A schedule is already running "System On" when somebody walks up.
        app.handleMessage({ type: 'macro.started', macro_id: 'system_on', total_steps: 2 });
        // They press the button that runs it. Through send(), which is what
        // makes the macro this panel's to report on.
        app.send({ type: 'macro.execute', macro_id: 'system_on' });
        app.handleMessage({ type: 'macro.started', macro_id: 'system_on', total_steps: 2 });
        // The schedule's run finishes first -- it started earlier.
        app.handleMessage({ type: 'macro.completed', macro_id: 'system_on' });
        // And then theirs cannot reach the projector.
        app.handleMessage({
            type: 'macro.step_error', macro_id: 'system_on',
            message: 'Ceiling Projector is not connected.',
        });

        const band = document.getElementById('panel-failure-message');
        assert(band && band.classList.contains('visible')
            && band.textContent === 'Ceiling Projector is not connected.',
            `the press still reports its own failure, got "${band && band.textContent}"`);
    },

    // The other direction, and the reason the count is not simply "never
    // forget": once the run somebody started has ended, a later failure of the
    // same macro belongs to whoever started THAT one.
    q139_a_claim_is_given_back_when_the_run_it_started_ends() {
        const app = mkApp();
        app.ws = { readyState: 1, send() {} };
        clearFailureBand();

        app.send({ type: 'macro.execute', macro_id: 'system_on' });
        app.handleMessage({ type: 'macro.started', macro_id: 'system_on', total_steps: 2 });
        app.handleMessage({ type: 'macro.completed', macro_id: 'system_on' });

        // Somebody else's run, minutes later, fails.
        app.handleMessage({ type: 'macro.started', macro_id: 'system_on', total_steps: 2 });
        app.handleMessage({
            type: 'macro.step_error', macro_id: 'system_on',
            message: 'Ceiling Projector is not connected.',
        });

        assert(!document.getElementById('panel-failure-message'),
            'a room where nobody touched anything stays quiet');
    },

    // A label bound to a macro's progress reads the running step. A Wait Until
    // sends more progress while it waits (waiting, then satisfied or timeout)
    // with no description and no step numbers, and so do a group command's
    // result and a condition's answer: the label read "Step 1 of 0" for as
    // long as the wait lasted, in the middle of starting the room.
    macro_progress_keeps_its_step_through_a_wait() {
        const app = mkApp();
        const label = {
            id: 'status', type: 'label', text: '',
            bindings: { show: { value: { source: 'macro_progress', macro: 'system_on', idle_text: 'Ready' } } },
        };
        renderProject(app, project({ elements: [label], placements: { status: { x: 5, y: 5, w: 40, h: 10 } } }));
        const el = app.bindings.find(b => b.type === 'macro_progress').element;
        assert(el.textContent === 'Ready', `idle, got "${el.textContent}"`);

        const waiting = 'Waiting for cameras to finish waking up...';
        app.handleMessage({ type: 'macro.started', macro_id: 'system_on', total_steps: 3 });
        app.handleMessage({
            type: 'macro.progress', macro_id: 'system_on', step_index: 1, total_steps: 3,
            step_path: [1], action: 'wait_until', description: waiting, status: 'running',
        });
        assert(el.textContent === waiting, `the step, got "${el.textContent}"`);
        for (const extra of [
            { action: 'wait_until', condition_key: 'device.cam1.power', status: 'waiting', timeout: 30 },
            { action: 'wait_until', condition_key: 'device.cam1.power', status: 'satisfied', waited_seconds: 9.8 },
            { action: 'wait_until', condition_key: 'device.cam1.power', status: 'timeout', waited_seconds: 30, on_timeout: 'continue' },
            { action: 'group.command', group: 'cameras', command: 'power_on', device_results: [], status: 'group_complete' },
            { action: 'conditional', condition_result: true, branch: 'then', status: 'evaluated' },
        ]) {
            app.handleMessage({ type: 'macro.progress', macro_id: 'system_on', ...extra });
            assert(el.textContent === waiting,
                `still the step after a ${extra.status} message, got "${el.textContent}"`);
        }

        // A step with no description of its own shows its number, and keeps
        // it through a wait the same way.
        app.handleMessage({
            type: 'macro.progress', macro_id: 'system_on', step_index: 2, total_steps: 3,
            step_path: [2], action: 'wait_until', description: '', status: 'running',
        });
        assert(el.textContent === 'Step 3 of 3', `the number, got "${el.textContent}"`);
        app.handleMessage({ type: 'macro.progress', macro_id: 'system_on', action: 'wait_until', status: 'waiting' });
        assert(el.textContent === 'Step 3 of 3', `still the number, got "${el.textContent}"`);

        app.handleMessage({ type: 'macro.completed', macro_id: 'system_on' });
        assert(el.textContent === 'Ready', `idle again, got "${el.textContent}"`);
    },

    // A run whose steps are not known (a started frame without a count, a
    // progress frame before any step) never reads "of 0": it keeps the idle text.
    macro_progress_never_counts_to_zero() {
        const app = mkApp();
        const label = {
            id: 'status', type: 'label', text: '',
            bindings: { show: { value: { source: 'macro_progress', macro: 'system_on', idle_text: 'Ready' } } },
        };
        renderProject(app, project({ elements: [label], placements: { status: { x: 5, y: 5, w: 40, h: 10 } } }));
        const el = app.bindings.find(b => b.type === 'macro_progress').element;
        app.handleMessage({ type: 'macro.started', macro_id: 'system_on' });
        app.handleMessage({ type: 'macro.progress', macro_id: 'system_on', action: 'wait_until', status: 'waiting' });
        assert(el.textContent === 'Ready', `no count to show, got "${el.textContent}"`);
        app.handleMessage({ type: 'macro.completed', macro_id: 'system_on' });
    },

    // A macro's id is whatever its author called it. Kept in plain objects, a
    // macro named `__proto__` wrote its progress onto every object in the
    // panel, and one named `constructor` read as running before it ever ran.
    a_macro_named_like_an_object_built_in_is_an_ordinary_macro() {
        const proto = window.Object.prototype;
        const touched = ['running', 'description', 'step_index', 'total_steps'];
        try {
            const app = mkApp();
            app.ws = { readyState: 1, send() {} };
            // A panel that connects mid-run hears progress before any start.
            app.handleMessage({
                type: 'macro.progress', macro_id: '__proto__', step_index: 0, total_steps: 2,
                description: 'Warming up',
            });
            app.handleMessage({ type: 'macro.started', macro_id: '__proto__', total_steps: 2 });
            app.handleMessage({
                type: 'macro.progress', macro_id: '__proto__', step_index: 1, total_steps: 2,
                description: 'Lowering the screen',
            });
            for (const name of touched) {
                assert(!Object.prototype.hasOwnProperty.call(proto, name),
                    `a macro called __proto__ left "${name}" on every object`);
            }
            app.handleMessage({ type: 'macro.completed', macro_id: '__proto__' });

            const proj = project({
                elements: [{
                    id: 'btn_ctor', type: 'button', label: 'Start',
                    bindings: { do: { press: [{ action: 'macro', macro: 'constructor' }] } },
                }],
                placements: { btn_ctor: { x: 5, y: 5, w: 20, h: 10 } },
            });
            renderProject(app, proj);
            const btn = app.elementMap['btn_ctor'].el;
            app._updateMacroBusyState('constructor');
            assert(!btn.classList.contains('macro-busy'), 'a macro called constructor is idle until it runs');
            app.handleMessage({ type: 'macro.started', macro_id: 'constructor', total_steps: 1 });
            assert(btn.classList.contains('macro-busy'), 'and busy while it runs');
            app.handleMessage({ type: 'macro.completed', macro_id: 'constructor' });
            assert(!btn.classList.contains('macro-busy'), 'and idle again once it ends');
        } finally {
            for (const name of touched) delete proto[name];
        }
    },

    // The panel only ever talks to a Programmer on its own origin. Any page can
    // frame /panel with ?preview=1; a '*' target handed that page the editor
    // messages too.
    embedded_panel_posts_only_to_its_own_origin() {
        const previousUrl = window.location.href;
        const ownParent = Object.getOwnPropertyDescriptor(window, 'parent');
        const posted = [];
        Object.defineProperty(window, 'parent', {
            value: { postMessage: (msg, target) => posted.push({ msg, target }) },
            configurable: true,
        });
        window.history.replaceState({}, '', '/panel/?preview=1');
        try {
            const app = mkApp();
            assert(app.embedded, 'a Programmer preview is embedded');
            app._postToParent({ type: 'openavc:element-error', elementId: 'x', message: 'Broken' });
            assert(posted.length === 1, `one message to the parent, got ${posted.length}`);
            assert(posted[0].target === window.location.origin,
                `addressed to "${window.location.origin}", got "${posted[0].target}"`);
        } finally {
            window.history.replaceState({}, '', previousUrl);
            if (ownParent) Object.defineProperty(window, 'parent', ownParent);
            else delete window.parent;
        }
    },

    // Rich text is written into innerHTML, from a label's text or a state
    // value. Markup in it is escaped before the bold and italic tags go in,
    // so the only elements it can make are those two.
    rich_text_escapes_markup_before_adding_its_own() {
        const app = mkApp();
        const host = document.createElement('div');
        host.innerHTML = app._formatRichText(
            '<img src=x onerror="alert(1)"> <script>bad()</script> **loud** and *soft* &amp;');
        assert(!host.querySelector('img, script'), `no element from the text, got ${host.innerHTML}`);
        const tags = [...host.querySelectorAll('*')].map(n => n.tagName).join(',');
        assert(tags === 'STRONG,EM', `only the formatting tags, got ${tags}`);
        assert(host.textContent.startsWith('<img src=x onerror="alert(1)"> <script>bad()</script> '),
            `the markup shows as text, got ${host.textContent}`);
        assert(host.textContent.endsWith('loud and soft &amp;'),
            `an entity in the text is shown as typed, got ${host.textContent}`);
    },

    // The other direction. Any page can frame /panel with ?preview=1 and is
    // then the panel's parent, so the source check alone let it hand the
    // panel a definition to draw.
    editor_messages_from_another_origin_are_ignored() {
        const app = mkApp();
        app._setupEditModeListener();
        const ui = project({ elements: [] }).ui;
        const post = (origin) => window.dispatchEvent(new window.MessageEvent('message', {
            data: { type: 'openavc:editor-project', project: { ui }, pageId: 'main' },
            origin,
            source: window.parent,
        }));
        post('https://somewhere.example');
        assert(!app.uiDef, 'a page on another origin cannot hand the panel a definition');
        post(window.location.origin);
        assert(app.uiDef === ui, "the Programmer on the panel's own origin still can");
    },

    // A list's key pattern is a glob: `*` is the only wildcard. Anything else
    // in it was handed to the regex engine as-is, so a bracket threw and broke
    // the list, and parentheses became a capture that took the item's value.
    list_key_pattern_is_literal_apart_from_the_star() {
        const app = mkApp();
        const rendered = [];
        const list = (pattern) => ({
            binding: { key_pattern: pattern },
            _list: { renderItems: (items) => rendered.push(items), staticItems: [{ label: 'None', value: '' }] },
        });
        app.state = {
            'var.zone(a).one': 'First',
            'var.zonea.two': 'Second',
            'var.room[1].three': 'Third',
        };

        app.evaluateListItems(list('var.zone(a).*'));
        let items = rendered.pop();
        assert(items.length === 1 && items[0].label === 'First' && items[0].value === 'one',
            `parentheses match themselves, got ${JSON.stringify(items)}`);

        app.evaluateListItems(list('var.room[1].*'));
        items = rendered.pop();
        assert(items.length === 1 && items[0].label === 'Third' && items[0].value === 'three',
            `a bracket matches itself, got ${JSON.stringify(items)}`);

        app.evaluateListItems(list('var.hall[*'));
        items = rendered.pop();
        assert(items.length === 1 && items[0].label === 'None',
            `an unmatched bracket falls back to the list's own items, got ${JSON.stringify(items)}`);
    },

    // The same count, one surface over: a button lit while its macro runs went
    // dark on the first ending run even though another was still going.
    q139_a_button_stays_busy_while_a_second_run_is_still_going() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'btn_on', type: 'button', label: 'System On',
                bindings: { do: { press: [{ action: 'macro', macro: 'system_on' }] } },
            }],
            placements: { btn_on: { x: 5, y: 5, w: 20, h: 10 } },
        });
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="btn_on"]');

        app.handleMessage({ type: 'macro.started', macro_id: 'system_on', total_steps: 2 });
        app.handleMessage({ type: 'macro.started', macro_id: 'system_on', total_steps: 2 });
        assert(el.classList.contains('macro-busy'), 'two runs, the button is busy');

        app.handleMessage({ type: 'macro.completed', macro_id: 'system_on' });
        assert(el.classList.contains('macro-busy'),
            'one has ended and the other has not, so it is still busy');

        app.handleMessage({ type: 'macro.completed', macro_id: 'system_on' });
        assert(!el.classList.contains('macro-busy'), 'both ended, and it goes dark');
    },

    // neither of a matrix's two odd frames names an element: a
    // preset sends macro.execute and a lock sends state.set. The failure band
    // would then place itself against whatever was touched before, and cover
    // the control somebody still has a finger on.
    q139_a_matrix_preset_and_lock_name_the_control_they_came_from() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        const proj = project({
            elements: [{
                id: 'mx', type: 'matrix', matrix_style: 'list',
                matrix_config: {
                    show_lock: true,
                    sources: [{ value: 1, label: 'Laptop' }],
                    destinations: [{
                        value: 1, label: 'Left', route_key: 'device.sw.route_1',
                        lock_key: 'var.left_lock',
                    }],
                    presets: [{ name: 'Presentation', macro: 'presentation' }],
                },
            }],
            placements: { mx: { x: 2, y: 2, w: 90, h: 60 } },
        });
        app.state = { 'device.sw.connected': true, 'device.sw.route_1': 1 };
        renderProject(app, proj);

        app._lastTouchedElementId = 'btn_somewhere_else';
        app.root.querySelector('.matrix-preset-btn')
            .dispatchEvent(new window.MouseEvent('click', { cancelable: true }));
        assert(app.ws.sent.some(m => m.type === 'macro.execute' && m.macro_id === 'presentation'),
            'the preset ran its macro');
        assert(app._lastTouchedElementId === 'mx',
            `the preset names the matrix, got "${app._lastTouchedElementId}"`);

        app._lastTouchedElementId = 'btn_somewhere_else';
        app.root.querySelector('.matrix-lock-btn')
            .dispatchEvent(new window.MouseEvent('click', { cancelable: true }));
        assert(app.ws.sent.some(m => m.type === 'state.set' && m.key === 'var.left_lock'),
            'the lock wrote its variable');
        assert(app._lastTouchedElementId === 'mx',
            `the lock names the matrix, got "${app._lastTouchedElementId}"`);
    },

    // A crosspoint gesture listens on the document, outside the element, so
    // a page redraw has to end it: a finger lifted after the redraw must not
    // route a cell that is no longer on the page.
    matrix_a_redraw_ends_a_crosspoint_gesture_without_routing() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        renderProject(app, project({
            elements: [{
                id: 'mx', type: 'matrix',
                matrix_config: {
                    sources: [{ value: 1, label: 'Laptop' }],
                    destinations: [{ value: 1, label: 'Left', route_key: 'device.sw.route_1' }],
                },
            }],
            placements: { mx: { x: 2, y: 2, w: 90, h: 60 } },
        }));
        const press = (type) => new window.MouseEvent(type, { bubbles: true, clientX: 5, clientY: 5 });
        const routes = () => app.ws.sent.filter(m => m.type === 'ui.route').length;

        app.root.querySelector('.matrix-cell').dispatchEvent(press('pointerdown'));
        document.dispatchEvent(press('pointerup'));
        assert(routes() === 1, `a tap routes once, got ${routes()}`);

        app.root.querySelector('.matrix-cell').dispatchEvent(press('pointerdown'));
        app.renderCurrentPage();
        document.dispatchEvent(press('pointerup'));
        assert(routes() === 1, `a gesture the redraw ended still routed (${routes()} routes)`);
    },

    // Two fingers down on the crosspoints and one lifted: one route goes out,
    // the second finger's source to the first finger's destination. Pinned as
    // it behaves; the gesture keeps one starting column per matrix.
    matrix_two_fingers_on_the_crosspoints_send_one_route() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        renderProject(app, project({
            elements: [{
                id: 'mx', type: 'matrix',
                matrix_config: {
                    sources: [{ value: 1, label: 'A' }, { value: 2, label: 'B' }],
                    destinations: [
                        { value: 11, label: 'L', route_key: 'device.sw.route_1' },
                        { value: 12, label: 'R', route_key: 'device.sw.route_2' },
                    ],
                },
            }],
            placements: { mx: { x: 2, y: 2, w: 90, h: 60 } },
        }));
        const press = (type) => new window.MouseEvent(type, { bubbles: true, clientX: 5, clientY: 5 });
        const cell = (src, dst) => app.root.querySelector(
            `.matrix-crosspoint[data-input="${src}"][data-output="${dst}"]`).parentElement;
        cell(1, 11).dispatchEvent(press('pointerdown'));
        cell(2, 12).dispatchEvent(press('pointerdown'));
        document.dispatchEvent(press('pointerup'));
        const routes = app.ws.sent.filter(m => m.type === 'ui.route');
        assert(JSON.stringify(routes) === JSON.stringify(
            [{ type: 'ui.route', element_id: 'mx', input: 2, output: 11 }]),
            `two fingers sent ${JSON.stringify(routes)}`);
    },

    // A tile wall follows the arrangement being drawn: on a portrait page its
    // eight tiles are two across and four down, on a landscape one four across.
    matrix_a_tile_wall_takes_the_shape_of_the_page_it_is_drawn_on() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'mx', type: 'matrix', matrix_style: 'tiles',
                matrix_config: {
                    sources: [{ value: 1, label: 'Laptop' }],
                    destinations: Array.from({ length: 8 }, (_, i) => ({ value: i + 1, label: `D${i + 1}` })),
                },
            }],
            placements: { mx: { x: 0, y: 0, w: 100, h: 100 } },
            extraLayouts: [{
                id: 'portrait', orientation: 'portrait', inherits: 'landscape',
                placements: { mx: { x: 0, y: 0, w: 100, h: 100 } }, hidden: [],
            }],
        });
        const shape = () => {
            const wall = app.root.querySelector('.matrix-tiles');
            return [wall.style.gridTemplateColumns.split(',')[0], wall.style.gridTemplateRows.split(',')[0]];
        };
        try {
            setViewport(1280, 800);
            renderProject(app, proj);
            assert(JSON.stringify(shape()) === '["repeat(4","repeat(2"]', `landscape wall drew ${shape()}`);
            setViewport(800, 1280);
            app.renderCurrentPage();
            assert(JSON.stringify(shape()) === '["repeat(2","repeat(4"]', `portrait wall drew ${shape()}`);
        } finally {
            setViewport(1280, 800);
        }
    },

    // A tile's source chooser lives on document.body, outside the element, so
    // a page redraw has to take it down: it would otherwise route a tile that
    // is no longer on the page.
    matrix_a_redraw_takes_down_an_open_chooser() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        renderProject(app, project({
            elements: [{
                id: 'mx', type: 'matrix', matrix_style: 'tiles',
                matrix_config: {
                    sources: [{ value: 1, label: 'Laptop' }],
                    destinations: [{ value: 1, label: 'Left', route_key: 'device.sw.route_1' }],
                },
            }],
            placements: { mx: { x: 2, y: 2, w: 90, h: 60 } },
        }));
        app.root.querySelector('.matrix-tile')
            .dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
        assert(document.querySelectorAll('.matrix-chooser').length === 1, 'the tile opened its chooser');
        app.renderCurrentPage();
        assert(document.querySelectorAll('.matrix-chooser').length === 0, 'a redraw left the chooser open');
    },

    // A lock is watched by its own key, so a lock variable that changes on
    // its own -- another panel pressed it -- redraws the lock here.
    matrix_a_lock_variable_alone_redraws_the_lock() {
        const app = mkApp();
        app.ws = { readyState: 1, sent: [], send(m) { this.sent.push(JSON.parse(m)); } };
        app.state = { 'device.sw.route_1': 1 };
        renderProject(app, project({
            elements: [{
                id: 'mx', type: 'matrix', matrix_style: 'list',
                matrix_config: {
                    show_lock: true,
                    sources: [{ value: 1, label: 'Laptop' }],
                    destinations: [{
                        value: 1, label: 'Left', route_key: 'device.sw.route_1',
                        lock_key: 'var.left_lock',
                    }],
                },
            }],
            placements: { mx: { x: 2, y: 2, w: 90, h: 60 } },
        }));
        const lock = app.root.querySelector('.matrix-lock-btn');
        assert(!lock.classList.contains('locked'), 'the lock starts open');
        app.state['var.left_lock'] = true;
        app.evaluateAllBindings(['var.left_lock']);
        assert(lock.classList.contains('locked'), 'the lock did not follow its variable');
        assert(app.root.querySelector('.matrix-list-select').disabled, 'a locked row keeps its dropdown');
    },

    // The Programmer session bridge: the token the IDE keeps in sessionStorage
    // rides on every /api call as a Bearer header and on the socket as the
    // auth.bearer subprotocol, the two channels the IDE itself uses. This is
    // what keeps the Builder's Preview working on an instance with a password,
    // now that the panel gate refuses a socket with no credential and no cookie.
    async bridge_sends_the_programmer_session_as_a_bearer_token() {
        window.sessionStorage.setItem('openavc.programmer.session', 'tok-abc_123');
        window.__fetchCalls.length = 0;
        try {
            await patchedFetch('/api/status');
            const call = window.__fetchCalls[0];
            assert(call, 'the patched fetch reached the underlying one');
            const header = call.init && call.init.headers
                && call.init.headers.get('Authorization');
            assert(header === 'Bearer tok-abc_123',
                `expected the token as a Bearer header, got "${header}"`);
            assert(window.__openavcGetAuthSubprotocol() === 'auth.bearer.tok-abc_123',
                'the socket offers the same token as its subprotocol');
        } finally {
            window.sessionStorage.removeItem('openavc.programmer.session');
        }
    },

    // Guards the guard: no session must still mean no header, or the one above
    // would pass against a build that always attached one. The key the IDE
    // stopped writing, a JSON blob holding the password, is not read either.
    async bridge_attaches_nothing_without_a_session() {
        window.sessionStorage.removeItem('openavc.programmer.session');
        window.sessionStorage.setItem(
            'openavc.programmer.auth', JSON.stringify({ user: 'admin', pass: 'secret' }),
        );
        window.__fetchCalls.length = 0;
        try {
            await patchedFetch('/api/status');
            const call = window.__fetchCalls[0];
            assert(call, 'the request still went through');
            assert(!(call.init && call.init.headers && call.init.headers.get('Authorization')),
                'no session, no Authorization header');
            assert(window.__openavcGetAuthSubprotocol() === null, 'no session, no subprotocol');
        } finally {
            window.sessionStorage.removeItem('openavc.programmer.auth');
        }
    },

    // --- Panel access: the check-in comes first, and a refusal waits --------

    // Even on an instance that admits anyone, the check-in is the first
    // request the page makes: then the plugin list, then the socket.
    // The check-in names the zone this screen's browser runs in: from the
    // system's own screen, the server sets its clock by it.
    async access_the_check_in_names_the_screens_time_zone() {
        clearAccessOverlay();
        const rig = accessRig([{ access: 'open' }]);
        try {
            const app = mkApp();
            app.start();
            await rig.settle();
            const first = rig.calls.find((c) => c.url && c.url.includes('/api/panel/access'));
            const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
            assert(first, 'the check-in was made');
            assert(first.url.endsWith(`/api/panel/access?time_zone=${encodeURIComponent(zone)}`),
                `the check-in names ${zone}, got ${first.url}`);
        } finally {
            rig.restore();
            clearAccessOverlay();
        }
    },

    async access_the_check_in_comes_before_everything_else() {
        clearAccessOverlay();
        const rig = accessRig([{ access: 'open' }]);
        try {
            const app = mkApp();
            app.start();
            await rig.settle();
            const order = rig.calls.map((c) => (c.ws ? 'ws' : c.url));
            assert(order[0] && order[0].split('?')[0].endsWith('/api/panel/access'),
                `the check-in is first, got ${JSON.stringify(order)}`);
            assert(order[1] && order[1].endsWith('/api/plugins/extensions'),
                `the plugin list is second, got ${JSON.stringify(order)}`);
            assert(order[2] === 'ws', `the socket opens last, got ${JSON.stringify(order)}`);
            assert(!document.getElementById('panel-access-overlay'),
                'an instance open to anyone shows no waiting screen');
        } finally {
            rig.restore();
        }
    },

    // A device that is not approved sees the sentence, the space and the code,
    // and nothing else happens: no plugin list, no socket. The poll that
    // answers "approved" lets it in without a reload.
    async access_a_waiting_device_sees_its_code_and_is_let_in_on_approval() {
        clearAccessOverlay();
        const rig = accessRig([PENDING_ANSWER]);
        try {
            const app = mkApp();
            app.start();
            await rig.settle();
            const overlay = document.getElementById('panel-access-overlay');
            assert(overlay, 'the waiting screen is up');
            assert(overlay.querySelector('.panel-access-title').textContent === 'Waiting for approval',
                'the title');
            const text = overlay.querySelector('.panel-access-message').textContent;
            assert(text === 'This panel needs approval before it can control Executive Boardroom. '
                + 'Ask your AV administrator to approve code 482-915.',
            `the sentence, got "${text}"`);
            assert(overlay.querySelector('.panel-access-code').textContent === '482-915',
                'the code stands on its own');
            assert(!rig.calls.some((c) => c.ws), 'no socket is opened while waiting');
            assert(!rig.calls.some((c) => c.url && c.url.includes('/api/plugins/extensions')),
                'nothing else is fetched while waiting');
            assert(document.getElementById('loading-state').style.display === 'none',
                'the loading text is hidden');
            assert(document.getElementById('connection-status').classList.contains('hidden'),
                'the connection pill is hidden');
            assert(!document.getElementById('offline-overlay').classList.contains('visible'),
                'the offline overlay is not shown');
            const link = overlay.querySelector('.panel-access-claim-link');
            assert(link && !link.hidden && link.textContent === 'Approve with the admin password',
                'the password route is offered');

            // The programmer approves it. The next poll says so and the page
            // goes on exactly as an open one does.
            rig.answers = [APPROVED_ANSWER];
            app.access.wake();
            await rig.settle();
            assert(!document.getElementById('panel-access-overlay'), 'the waiting screen is gone');
            assert(!document.getElementById('connection-status').classList.contains('hidden'),
                'the connection pill is back');
            assert(rig.calls.some((c) => c.url && c.url.includes('/api/plugins/extensions')),
                'the plugin list is fetched once admitted');
            assert(rig.calls.some((c) => c.ws), 'the socket opens once admitted');
        } finally {
            clearAccessOverlay();
            rig.restore();
        }
    },

    // A denied device and one the system had no room for are told what to do.
    // The password route stays open to a denied device (the admin can still
    // approve it); when the system is full there is nothing to approve.
    access_denied_and_unavailable_say_what_to_do() {
        clearAccessOverlay();
        const app = mkApp();
        try {
            app.access.showWaiting({ access: 'approved', status: 'denied' });
            let overlay = document.getElementById('panel-access-overlay');
            assert(overlay.querySelector('.panel-access-title').textContent === 'Not approved',
                'the denied title');
            assert(overlay.querySelector('.panel-access-message').textContent
                === 'This panel was not approved. Ask your AV administrator if you need to use it.',
            'the denied sentence');
            assert(!overlay.querySelector('.panel-access-claim-link').hidden,
                'a denied device may still be approved with the password');

            app.access.showWaiting({ access: 'approved', status: 'unavailable' });
            overlay = document.getElementById('panel-access-overlay');
            assert(document.querySelectorAll('#panel-access-overlay').length === 1,
                'one screen, refilled, never stacked');
            assert(overlay.querySelector('.panel-access-title').textContent === 'Not accepting new panels',
                'the unavailable title');
            assert(overlay.querySelector('.panel-access-message').textContent
                === 'This system is not accepting new panels right now. Ask your AV administrator.',
            'the unavailable sentence');
            assert(overlay.querySelector('.panel-access-claim-link').hidden
                && overlay.querySelector('.panel-access-claim').hidden,
            'nothing to approve when the system is full');

            app.access.hide();
            assert(!document.getElementById('panel-access-overlay'), 'gone once the page may connect');
        } finally {
            clearAccessOverlay();
        }
    },

    // The server accepts the socket and closes it with 4010 when the device is
    // not approved, which is also what a revoke does to a connected panel. The
    // page stops reconnecting and asks again, and the waiting screen carries
    // whatever the check-in answers (a new code after a revoke). Every other
    // close code reconnects as it always did.
    async access_a_4010_close_asks_again_instead_of_reconnecting() {
        clearAccessOverlay();
        const rig = accessRig([{ ...PENDING_ANSWER, code: '111-222' }]);
        const realSetTimeout = window.setTimeout;
        try {
            const app = mkApp();
            app.connect();
            const scheduled = [];
            window.setTimeout = (fn, ms) => { scheduled.push(ms); return 0; };
            try {
                app.ws.onclose({ code: 4010 });
            } finally {
                window.setTimeout = realSetTimeout;
            }
            assert(scheduled.length === 0,
                `a refusal schedules no reconnect, got ${JSON.stringify(scheduled)}`);
            assert(app.reconnectAttempts === 0, 'a refusal is not a reconnect attempt');
            await rig.settle();
            const overlay = document.getElementById('panel-access-overlay');
            assert(overlay, 'the waiting screen is up after 4010');
            assert(overlay.querySelector('.panel-access-code').textContent === '111-222',
                'with the code the check-in answered');
            assert(!document.getElementById('offline-overlay').classList.contains('visible'),
                'the offline overlay is not shown over it');

            // A plain drop still reconnects.
            const dropped = mkApp();
            dropped.connect();
            const scheduledAfterDrop = [];
            window.setTimeout = (fn, ms) => { scheduledAfterDrop.push(ms); return 0; };
            try {
                dropped.ws.onclose({ code: 1006 });
            } finally {
                window.setTimeout = realSetTimeout;
            }
            assert(scheduledAfterDrop.includes(1000),
                `a dropped socket schedules the reconnect, got ${JSON.stringify(scheduledAfterDrop)}`);
            assert(dropped.reconnectAttempts === 1, 'a drop counts as an attempt');

            // Approval ends the wait and the refused page connects again.
            rig.answers = [APPROVED_ANSWER];
            app.access.wake();
            await rig.settle();
            assert(!document.getElementById('panel-access-overlay'), 'the waiting screen is gone');
            assert(rig.calls.filter((c) => c.ws).length === 3,
                `the refused page opened a new socket, got ${rig.calls.filter((c) => c.ws).length}`);
        } finally {
            window.setTimeout = realSetTimeout;
            clearAccessOverlay();
            rig.restore();
        }
    },

    // "Approve with the admin password": the link reveals the form, the
    // password goes to the claim route once as HTTP Basic, a wrong one is
    // said on the screen (never by a browser dialog), and a right one wakes
    // the poll so the page connects at once.
    async access_the_admin_password_approves_from_the_panel() {
        clearAccessOverlay();
        const rig = accessRig([PENDING_ANSWER]);
        try {
            const app = mkApp();
            app.access.showWaiting(PENDING_ANSWER);
            const overlay = document.getElementById('panel-access-overlay');
            const link = overlay.querySelector('.panel-access-claim-link');
            const form = overlay.querySelector('.panel-access-claim');
            assert(form.hidden, 'the form waits behind the link');
            link.click();
            assert(link.hidden && !form.hidden, 'the link reveals the form');
            const user = form.querySelector('input[name="username"]');
            const pass = form.querySelector('input[name="password"]');
            assert(user.value === 'admin', 'the username is prefilled as first-run setup offers it');
            assert(pass.value === '' && pass.getAttribute('autocomplete') === 'new-password',
                'the password field starts empty and is not autofilled');

            let woke = 0;
            app.access.wake = () => { woke++; };
            pass.value = 'wr\u00f6ng';
            form.dispatchEvent(new window.Event('submit', { cancelable: true }));
            await rig.settle();
            const claim = rig.calls.find((c) => c.url && c.url.endsWith('/api/panel/access/claim'));
            assert(claim && claim.init.method === 'POST', 'the password goes to the claim route');
            const header = claim.init.headers && claim.init.headers.Authorization;
            assert(header === 'Basic ' + Buffer.from('admin:wr\u00f6ng', 'utf8').toString('base64'),
                `as HTTP Basic, UTF-8 encoded, got "${header}"`);
            assert(form.querySelector('.panel-access-error').textContent === 'That password is not correct.',
                'a wrong password says so on the screen');
            assert(pass.value === '', 'and clears the field');
            assert(woke === 0, 'nothing wakes the poll on a refusal');

            rig.claim = { status: 200, body: APPROVED_ANSWER };
            pass.value = 'right';
            form.dispatchEvent(new window.Event('submit', { cancelable: true }));
            await rig.settle();
            assert(woke === 1, 'a successful claim wakes the poll so the page connects at once');
            assert(form.querySelector('.panel-access-error').textContent === '',
                'no error left standing');
        } finally {
            clearAccessOverlay();
            rig.restore();
        }
    },

    // The design canvas has no socket and the Preview is parent-driven with
    // the Programmer's session on its socket, so neither checks in: a
    // programmer never sees the waiting screen inside the IDE.
    async access_the_designer_and_the_preview_skip_the_check_in() {
        clearAccessOverlay();
        const rig = accessRig([PENDING_ANSWER]);
        const statusEl = document.getElementById('connection-status');
        const offline = document.getElementById('offline-overlay');
        // Edit mode announces itself with console.log, which jsdom forwards to
        // stdout, where the results JSON goes. Keep it off the wire.
        const realLog = window.console.log;
        try {
            const designer = mkApp();
            designer.editMode = true;
            designer.embedded = true;
            window.console.log = () => {};
            try {
                designer.start();
                await rig.settle();
            } finally {
                window.console.log = realLog;
            }
            assert(!rig.calls.some((c) => c.url && c.url.includes('/api/panel/access')),
                'the designer never checks in');
            assert(!rig.calls.some((c) => c.ws), 'and opens no socket');

            rig.calls.length = 0;
            window.sessionStorage.setItem('openavc.programmer.session', 'tok-preview');
            const preview = mkApp();
            preview.embedded = true;
            preview.start();
            await rig.settle();
            assert(!rig.calls.some((c) => c.url && c.url.includes('/api/panel/access')),
                'the preview never checks in');
            const ws = rig.calls.find((c) => c.ws);
            assert(ws && ws.protocols && ws.protocols[0] === 'auth.bearer.tok-preview',
                `the preview socket carries the session, got ${JSON.stringify(ws)}`);
            assert(!document.getElementById('panel-access-overlay'), 'no waiting screen in the IDE');
        } finally {
            window.console.log = realLog;
            window.sessionStorage.removeItem('openavc.programmer.session');
            // Edit mode removes the pill and hides the offline overlay; the
            // scenarios after this one expect both where the document put them.
            if (!document.getElementById('connection-status')) document.body.appendChild(statusEl);
            offline.style.display = '';
            clearAccessOverlay();
            rig.restore();
        }
    },

    // --- Remote Access: a session that ended says so ------------------------

    // The cloud's answers for a session that is gone: a 404 once it was closed
    // or timed out, a 401 for an access token that expired, and a 403 saying
    // remote access ended. The Programmer reads the same three the same way.
    async remote_session_the_clouds_refusals_end_it() {
        const app = mkApp();
        await atPath('/tunnel/t1/panel/', async () => {
            for (const status of [404, 401, 403]) {
                assert(await app.access.isEndedRemoteSession(reply(status, REMOTE_ACCESS_ENDED)),
                    `the cloud's bare-text ${status} ends the session`);
            }
        });
    },

    // Everything else leaves the session standing: the system's own errors,
    // the cloud's 403 for a request outside the session's scope, its 502
    // while the system is not connected, and any answer at all on a panel
    // opened on the system's own address.
    async remote_session_other_answers_leave_it_standing() {
        const app = mkApp();
        await atPath('/tunnel/t1/panel/', async () => {
            for (const [status, body, json] of [
                [401, '{"detail":"Authentication required"}', true],
                [404, '{"detail":"Not Found"}', true],
                [403, 'This remote session is for the touch panel and cannot change '
                    + "the system's configuration.", false],
                [502, 'Agent not connected to tunnel', false],
                [200, '{"access":"open"}', true],
            ]) {
                assert(!(await app.access.isEndedRemoteSession(reply(status, body, json))),
                    `${status} "${body}" leaves the session standing`);
            }
        });
        await atPath('/panel/', async () => {
            assert(!(await app.access.isEndedRemoteSession(reply(404, REMOTE_ACCESS_ENDED))),
                "a panel on the system's own address has no remote session to end");
        });
    },

    // A dropped socket under Remote Access asks the panel's check-in once.
    // The cloud's refusal puts the sentence in place of the panel and stops
    // reconnecting: the reconnect already scheduled opens no socket, and a
    // late close schedules nothing and asks nothing.
    async remote_session_a_drop_asks_once_and_stops_reconnecting() {
        clearAccessOverlay();
        const rig = remoteRig(reply(404, REMOTE_ACCESS_ENDED));
        const app = mkApp();
        try {
            await atPath('/tunnel/t1/panel/', async () => {
                app.connect();
                assert(rig.sockets === 1, 'the panel opened its socket');
                dropSocket(app, 1006);
                assert(JSON.stringify(rig.fetched) === '["/tunnel/t1/api/panel/access"]',
                    `one question, to the panel's check-in, got ${JSON.stringify(rig.fetched)}`);
                await rig.settle();

                const overlay = document.getElementById('panel-access-overlay');
                assert(overlay && overlay.dataset.status === 'ended', 'the ended screen is up');
                assert(overlay.querySelector('.panel-access-title').textContent === 'Remote session ended',
                    'the title');
                const text = overlay.querySelector('.panel-access-message').textContent;
                assert(text === 'This remote session has ended. Open it again from the portal.',
                    `the Programmer's sentence, got "${text}"`);
                assert(overlay.querySelector('.panel-access-claim-link').hidden
                    && overlay.querySelector('.panel-access-claim').hidden,
                'no admin password: it cannot bring the session back');
                assert(!document.getElementById('offline-overlay').classList.contains('visible'),
                    'the offline overlay is not shown over it');

                app.connect();
                assert(rig.sockets === 1, 'the reconnect scheduled before the answer opens nothing');
                const scheduled = dropSocket(app, 1006);
                assert(scheduled.length === 0,
                    `a late close schedules nothing, got ${JSON.stringify(scheduled)}`);
                assert(rig.fetched.length === 1, 'and asks nothing more');
            });
        } finally {
            resetAfterDrop(app);
            rig.restore();
        }
    },

    // A drop that is not the session ending reconnects as it always did: the
    // cloud's 502 while the system is away, a probe that cannot reach
    // anything, and a panel on the system's own address, which never asks.
    async remote_session_a_drop_that_is_not_the_end_reconnects() {
        clearAccessOverlay();
        const rig = remoteRig(reply(502, 'Agent not connected to tunnel'));
        const apps = [];
        try {
            await atPath('/tunnel/t1/panel/', async () => {
                for (const answer of [rig.answer, new Error('network down')]) {
                    rig.answer = answer;
                    rig.fetched.length = 0;
                    const app = mkApp();
                    apps.push(app);
                    app.connect();
                    const scheduled = dropSocket(app, 1006);
                    await rig.settle();
                    assert(rig.fetched.length === 1, `one question after the drop (${answer.status || answer})`);
                    assert(scheduled.includes(1000), `the reconnect is scheduled (${answer.status || answer})`);
                    assert(!document.getElementById('panel-access-overlay'),
                        `no ended screen (${answer.status || answer})`);
                    const before = rig.sockets;
                    app.connect();
                    assert(rig.sockets === before + 1, `the reconnect opens a socket (${answer.status || answer})`);
                }
            });
            await atPath('/panel/', async () => {
                rig.answer = reply(404, REMOTE_ACCESS_ENDED);
                rig.fetched.length = 0;
                const app = mkApp();
                apps.push(app);
                app.connect();
                const scheduled = dropSocket(app, 1006);
                await rig.settle();
                assert(rig.fetched.length === 0, "a panel on the system's own address asks nothing");
                assert(scheduled.includes(1000), 'and reconnects');
            });
        } finally {
            for (const app of apps) resetAfterDrop(app);
            rig.restore();
        }
    },

    // The system's own answer is JSON whatever else its content type says
    // (a charset), and never reads as the cloud ending the session.
    async remote_session_the_systems_json_with_a_charset_is_its_own_answer() {
        const app = mkApp();
        await atPath('/tunnel/t1/panel/', async () => {
            for (const status of [401, 403, 404]) {
                const res = reply(status, REMOTE_ACCESS_ENDED);
                res.headers = new window.Headers({ 'content-type': 'application/json; charset=utf-8' });
                assert(!(await app.access.isEndedRemoteSession(res)), `${status} JSON with a charset stands`);
            }
        });
    },

    // A dropped socket reconnects a little later each time, up to 30 s, and
    // says which attempt it is on; an opened socket and a refusal (4010) each
    // start the count again.
    async reconnect_backs_off_counts_its_attempts_and_starts_over() {
        clearAccessOverlay();
        const rig = remoteRig(reply(200, '{"access":"open"}', true));
        const info = document.createElement('div');
        info.id = 'reconnect-info';
        document.body.appendChild(info);
        const realSetTimeout = window.setTimeout;
        const scheduled = [];
        window.setTimeout = (fn, ms) => { scheduled.push({ fn, ms }); return 0; };
        const app = mkApp();
        const drop = (code) => {
            scheduled.length = 0;
            app.ws.onclose({ code });
            return scheduled.map((t) => t.ms);
        };
        try {
            app.connect();
            const delays = [];
            for (let i = 0; i < 12; i++) {
                delays.push(...drop(1006));
                scheduled[0].fn();
            }
            assert(JSON.stringify(delays) === JSON.stringify([1000, 1500, 2250, 3375, 5062.5, 7593.75,
                11390.625, 17085.9375, 25628.90625, 30000, 30000, 30000]),
            `each retry half as long again, up to 30 s, got ${JSON.stringify(delays)}`);
            assert(info.textContent === 'Reconnecting (attempt 12)...', `got "${info.textContent}"`);
            assert(rig.sockets === 13, `each retry opens a socket, got ${rig.sockets}`);

            app.ws.onopen();
            assert(info.textContent === '', 'an opened socket clears the attempt line');
            assert(JSON.stringify(drop(1006)) === '[1000]' && info.textContent === 'Reconnecting (attempt 1)...',
                `an opened socket starts the count again, got "${info.textContent}"`);

            drop(1006); drop(1006);
            assert(JSON.stringify(drop(4010)) === '[]', 'a refusal schedules no retry');
            await rig.settle();
            assert(rig.sockets === 14, `the refused page connects once admitted, got ${rig.sockets}`);
            assert(JSON.stringify(drop(1006)) === '[1000]' && info.textContent === 'Reconnecting (attempt 1)...',
                `a refusal starts the count again, got "${info.textContent}"`);
        } finally {
            window.setTimeout = realSetTimeout;
            info.remove();
            resetAfterDrop(app);
            rig.restore();
        }
    },

    // The check-in, the Remote Access probe and the admin password ask the
    // server every time, never a cache, and the password asks for JSON back.
    async access_requests_ask_the_server_not_a_cache() {
        clearAccessOverlay();
        const rig = accessRig([PENDING_ANSWER]);
        try {
            const app = mkApp();
            app.start();
            await rig.settle();
            const checkIn = rig.calls.find((c) => c.url && c.url.includes('/api/panel/access'));
            assert(checkIn && checkIn.init.cache === 'no-store', `the check-in, got ${JSON.stringify(checkIn)}`);
            const form = document.querySelector('.panel-access-claim');
            form.querySelector('input[name="password"]').value = 'x';
            form.dispatchEvent(new window.Event('submit', { cancelable: true }));
            await rig.settle();
            const claim = rig.calls.find((c) => c.url && c.url.endsWith('/api/panel/access/claim'));
            assert(claim && claim.init.cache === 'no-store' && claim.init.method === 'POST'
                && claim.init.headers.Accept === 'application/json',
            `the admin password, got ${JSON.stringify(claim && claim.init)}`);
        } finally {
            clearAccessOverlay();
            rig.restore();
        }
        const probes = [];
        const savedFetch = window.fetch;
        const savedWS = window.WebSocket;
        window.fetch = async (u, init) => { probes.push({ u: String(u), init }); return reply(502, 'away'); };
        window.WebSocket = FakeWS;
        const app = mkApp();
        try {
            await atPath('/tunnel/t1/panel/', async () => {
                app.connect();
                dropSocket(app, 1006);
                await Promise.resolve();
            });
            assert(probes.length === 1 && probes[0].init && probes[0].init.cache === 'no-store',
                `the Remote Access probe, got ${JSON.stringify(probes)}`);
        } finally {
            window.fetch = savedFetch;
            window.WebSocket = savedWS;
            resetAfterDrop(app);
        }
    },

    // A socket the server closes with 4013 (this interface is turned off) or
    // 4014 (Preview without a Programmer session) puts the reason in place of
    // the panel with a way to try again, and does not reconnect or ask
    // anything, even under Remote Access. The panel goes offline first, so
    // the screen is what shows, not the offline overlay.
    async access_a_4013_or_4014_close_says_why_and_offers_try_again() {
        const sentences = {
            4013: ['OpenAVC interface disabled', 'This interface is disabled in product management.'],
            4014: ['Preview sign-in required', 'Sign in to the Programmer to use Preview.'],
        };
        for (const code of [4013, 4014]) {
            clearAccessOverlay();
            const rig = remoteRig(reply(404, REMOTE_ACCESS_ENDED));
            const app = mkApp();
            const hold = window.setInterval(() => {}, 100000);
            try {
                await atPath('/tunnel/t1/panel/', async () => {
                    app.connect();
                    app.holdTimers = { held: hold };
                    const scheduled = dropSocket(app, code);
                    await rig.settle();
                    assert(scheduled.length === 0, `${code} schedules no reconnect, got ${JSON.stringify(scheduled)}`);
                    assert(app.reconnectAttempts === 0, `${code} is not a reconnect attempt`);
                    assert(rig.fetched.length === 0, `${code} asks nothing, got ${JSON.stringify(rig.fetched)}`);
                    assert(Object.keys(app.holdTimers).length === 0, `${code} ends every hold-repeat`);
                    const overlay = document.getElementById('panel-access-overlay');
                    assert(overlay, `${code} puts a screen up`);
                    assert(overlay.dataset.status === 'unavailable',
                        `${code} screen status, got ${overlay.dataset.status}`);
                    assert(overlay.querySelector('.panel-access-title').textContent === sentences[code][0],
                        `${code} title, got "${overlay.querySelector('.panel-access-title').textContent}"`);
                    assert(overlay.querySelector('.panel-access-message').textContent === sentences[code][1],
                        `${code} sentence, got "${overlay.querySelector('.panel-access-message').textContent}"`);
                    assert(overlay.querySelector('.panel-access-claim-link').hidden
                        && overlay.querySelector('.panel-access-claim').hidden,
                    `${code}: no admin password route`);
                    const retry = overlay.querySelectorAll('.panel-access-container > button.panel-access-submit');
                    assert(retry.length === 1 && retry[0].textContent === 'Try again',
                        `${code}: one Try again button, got ${retry.length}`);
                    assert(typeof retry[0].onclick === 'function', `${code}: Try again does something`);
                    const status = document.getElementById('connection-status');
                    assert(status.textContent === 'Disconnected' && status.classList.contains('hidden'),
                        `${code}: offline, with the pill hidden under the screen`);
                    assert(!document.getElementById('offline-overlay').classList.contains('visible'),
                        `${code}: the offline overlay is not over the screen`);
                    assert(app.root.style.pointerEvents === 'none', `${code}: the panel takes no touches`);
                });
            } finally {
                window.clearInterval(hold);
                resetAfterDrop(app);
                rig.restore();
            }
        }
        // The Remote Access ended screen is the only other one with its own
        // status, and nothing but these two closes carries a Try again button.
        clearAccessOverlay();
        const app = mkApp();
        app.access.showWaiting({ status: 'pending', code: '1', space: 'x' });
        assert(!document.querySelector('.panel-access-container > button.panel-access-submit'),
            'the waiting screen has no Try again button');
        clearAccessOverlay();
    },

    // The check-in reads an answer only when it is an object the server sent
    // with a success status; anything else, including no network, is no
    // answer. A screen whose zone cannot be read asks without one.
    async access_a_check_in_with_no_usable_answer_is_null() {
        const app = mkApp();
        const savedFetch = window.fetch;
        const savedIntl = window.Intl;
        const urls = [];
        const answer = (value) => { window.fetch = async (u) => { urls.push(String(u)); return value(); }; };
        try {
            answer(() => { throw new Error('network down'); });
            assert(await app.access.fetchAccess() === null, 'no network is no answer');
            answer(() => ({ ok: true, status: 200, json: async () => 'approved' }));
            assert(await app.access.fetchAccess() === null, 'a string is no answer');
            answer(() => ({ ok: true, status: 200, json: async () => null }));
            assert(await app.access.fetchAccess() === null, 'null is no answer');
            answer(() => ({ ok: true, status: 200, json: async () => { throw new Error('not JSON'); } }));
            assert(await app.access.fetchAccess() === null, 'a body that is not JSON is no answer');
            answer(() => ({ ok: false, status: 500, json: async () => ({ access: 'open' }) }));
            assert(await app.access.fetchAccess() === null, 'an error status is no answer, whatever it says');
            answer(() => ({ ok: true, status: 200, json: async () => ({ access: 'open' }) }));
            const got = await app.access.fetchAccess();
            assert(got && got.access === 'open', 'an object is the answer');

            urls.length = 0;
            window.Intl = { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: '' }) }) };
            await app.access.fetchAccess();
            window.Intl = { DateTimeFormat: () => { throw new Error('no Intl'); } };
            await app.access.fetchAccess();
            window.Intl = { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: 'America/New_York' }) }) };
            await app.access.fetchAccess();
            assert(JSON.stringify(urls) === JSON.stringify([
                '/api/panel/access', '/api/panel/access',
                '/api/panel/access?time_zone=America%2FNew_York',
            ]), `a screen with no zone asks without one, got ${JSON.stringify(urls)}`);
        } finally {
            window.fetch = savedFetch;
            window.Intl = savedIntl;
        }
    },

    // After a refusal the page keeps asking through no answer, a few seconds
    // apart; a denied or full system is asked again a minute apart; and the
    // first approval ends the wait. Two callers share the one loop.
    async access_after_a_refusal_it_keeps_asking_at_its_own_pace() {
        clearAccessOverlay();
        const rig = accessRig([null, { access: 'approved', status: 'denied' },
            { access: 'approved', status: 'unavailable' }, { access: 'approved', status: 'something-new' },
            PENDING_ANSWER, APPROVED_ANSWER]);
        const realSetTimeout = window.setTimeout;
        const waits = [];
        window.setTimeout = (fn, ms) => { waits.push(ms); return 0; };
        try {
            const app = mkApp();
            const first = app.access.waitForAccess({ afterRefusal: true });
            const second = app.access.waitForAccess();
            assert(first === second, 'a second caller joins the loop in flight');
            let done = false;
            first.then(() => { done = true; });
            const statuses = [];
            for (let i = 0; i < 5; i++) {
                await rig.settle();
                const overlay = document.getElementById('panel-access-overlay');
                statuses.push(overlay ? overlay.dataset.status : null);
                app.access.wake();
            }
            await rig.settle();
            // A status the panel does not know is drawn as waiting and asked
            // again a minute apart: the pace follows what the server said.
            assert(JSON.stringify(waits) === JSON.stringify([3000, 60000, 60000, 60000, 3000]),
                `no answer and pending wait 3 s, denied, full and unknown 60 s, got ${JSON.stringify(waits)}`);
            assert(JSON.stringify(statuses) === JSON.stringify([null, 'denied', 'unavailable', 'pending', 'pending']),
                `the screen follows each answer, got ${JSON.stringify(statuses)}`);
            assert(done, 'approval ends the wait');
            assert(!document.getElementById('panel-access-overlay'), 'and takes the screen down');
            const later = app.access.waitForAccess();
            assert(later !== first, 'the loop is gone once it ends: a later caller starts its own');
            await rig.settle();

            // While the waiting screen is up, no answer keeps it asking even
            // on the first load.
            waits.length = 0;
            rig.answers = [PENDING_ANSWER];
            const app2 = mkApp();
            app2.access.waitForAccess();
            await rig.settle();
            rig.answers = [null];
            app2.access.wake();
            await rig.settle();
            assert(JSON.stringify(waits) === JSON.stringify([3000, 3000]),
                `the screen stays and it asks again, got ${JSON.stringify(waits)}`);
            assert(document.getElementById('panel-access-overlay'), 'the waiting screen stays up');

            // Once a screen has come down it is gone: a later first load with
            // no answer goes on at once rather than waiting behind it.
            rig.answers = [APPROVED_ANSWER];
            app2.access.wake();
            await rig.settle();
            assert(!document.getElementById('panel-access-overlay'), 'approved, the screen is down');
            waits.length = 0;
            rig.answers = [null];
            let went = false;
            app2.access.waitForAccess().then(() => { went = true; });
            await rig.settle();
            assert(went && waits.length === 0,
                `no answer on a first load goes on at once, waited ${JSON.stringify(waits)}`);
        } finally {
            window.setTimeout = realSetTimeout;
            clearAccessOverlay();
            rig.restore();
        }
    },

    // A waiting screen the server named no space or code for still reads as
    // a sentence; an answer with any other status reads as waiting.
    access_the_waiting_screen_fills_in_what_the_answer_left_out() {
        clearAccessOverlay();
        const app = mkApp();
        try {
            app.access.showWaiting({ access: 'approved', status: 'pending' });
            let overlay = document.getElementById('panel-access-overlay');
            assert(overlay.querySelector('.panel-access-message').textContent
                === 'This panel needs approval before it can control this space. '
                    + 'Ask your AV administrator to approve code .',
            `got "${overlay.querySelector('.panel-access-message').textContent}"`);
            app.access.showWaiting({ access: 'approved', status: 'something-new', space: 7, code: 12 });
            overlay = document.getElementById('panel-access-overlay');
            assert(overlay.dataset.status === 'pending', 'an unknown status waits');
            assert(overlay.querySelector('.panel-access-title').textContent === 'Waiting for approval',
                'with the waiting title');
            assert(overlay.querySelector('.panel-access-space').textContent === '7'
                && overlay.querySelector('.panel-access-code').textContent === '12',
            'names and codes are drawn as text');
            // A form already opened stays open when the screen is refilled.
            overlay.querySelector('.panel-access-claim-link').click();
            app.access.showWaiting({ access: 'approved', status: 'denied' });
            assert(overlay.querySelector('.panel-access-claim-link').hidden
                && !overlay.querySelector('.panel-access-claim').hidden,
            'an opened form is not folded back behind its link');
            // A full system takes an opened form away too: nothing to approve.
            app.access.showWaiting({ access: 'approved', status: 'unavailable' });
            assert(overlay.querySelector('.panel-access-claim-link').hidden
                && overlay.querySelector('.panel-access-claim').hidden,
            'a full system hides an opened form');
            // A screen taken off the page by something else is drawn again,
            // not refilled where nobody can see it.
            overlay.remove();
            app.access.showWaiting({ access: 'approved', status: 'denied' });
            const again = document.getElementById('panel-access-overlay');
            assert(again && again !== overlay, 'a new screen goes up');
            assert(again.querySelector('.panel-access-title').textContent === 'Not approved',
                'and it says the answer');
        } finally {
            clearAccessOverlay();
        }
    },

    // The screen covers the loading text, and gives it back when it goes only
    // while nothing has been drawn: a panel that has its page keeps it.
    access_the_screen_takes_the_loading_text_and_gives_it_back_before_a_first_draw() {
        clearAccessOverlay();
        const loading = document.getElementById('loading-state');
        const before = loading.style.display;
        try {
            for (const [drawn, after] of [[false, 'flex'], [true, 'none']]) {
                const app = mkApp();
                app.snapshotReceived = drawn;
                loading.style.display = 'flex';
                app.access.showWaiting(PENDING_ANSWER);
                assert(loading.style.display === 'none', 'the loading text goes under the screen');
                app.access.hide();
                assert(loading.style.display === after,
                    `${drawn ? 'drawn' : 'not drawn yet'}: the loading text is ${loading.style.display}, want ${after}`);
            }
        } finally {
            loading.style.display = before;
            clearAccessOverlay();
        }
    },

    // What the panel says when the admin password does not approve it: the
    // server's own sentence where it gave one, else a plain one, and a
    // separate one when the system cannot be reached. One submit, one try.
    async access_the_admin_password_refusals_say_what_happened() {
        const app = mkApp();
        const savedFetch = window.fetch;
        const claim = async (res) => {
            window.fetch = async () => { if (res instanceof Error) throw res; return res; };
            return app.access.claimWithPassword('admin', 'x');
        };
        const json = (status, body) => ({ ok: status < 300, status, json: async () => body });
        const notJson = (status) => ({ ok: status < 300, status, json: async () => { throw new Error('html'); } });
        try {
            const cases = [
                [json(200, {}), { ok: true }],
                [json(401, { detail: 'Bad credentials' }), { ok: false, message: 'That password is not correct.' }],
                [notJson(401), { ok: false, message: 'That password is not correct.' }],
                [json(403, { detail: 'Panel approval is turned off.' }),
                    { ok: false, message: 'Panel approval is turned off.' }],
                [json(409, { detail: '' }), { ok: false, message: 'This panel could not be approved. Try again.' }],
                [json(409, { detail: { code: 'x' } }),
                    { ok: false, message: 'This panel could not be approved. Try again.' }],
                [notJson(500), { ok: false, message: 'This panel could not be approved. Try again.' }],
                [new Error('offline'), { ok: false, message: "Can't reach the system. Try again." }],
            ];
            for (const [res, want] of cases) {
                const got = await claim(res);
                assert(JSON.stringify(got) === JSON.stringify(want),
                    `${res.status || res} answered ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
            }

            // Enter and the button both fire: one attempt while one is out.
            clearAccessOverlay();
            let claims = 0;
            let release;
            window.fetch = async () => {
                claims++;
                await new Promise((r) => { release = r; });
                return json(401, {});
            };
            app.access.showWaiting(PENDING_ANSWER);
            const form = document.querySelector('.panel-access-claim');
            const submit = form.querySelector('button.panel-access-submit');
            form.dispatchEvent(new window.Event('submit', { cancelable: true }));
            form.dispatchEvent(new window.Event('submit', { cancelable: true }));
            await Promise.resolve();
            assert(claims === 1, `one claim while one is out, got ${claims}`);
            assert(submit.disabled, 'the button is off while it is out');
            release();
            for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
            assert(!submit.disabled, 'and back once it answered');
        } finally {
            window.fetch = savedFetch;
            clearAccessOverlay();
        }
    },

    // A held button presses again on a timer until it is let go. Every way a
    // press can lose its release ends that timer: the pointer leaving, the
    // panel going to the background, the socket dropping, a new page, and the
    // overlay it sits on closing (which ends that overlay's holds only).
    hold_repeat_presses_until_let_go_and_stops_when_the_release_cannot_come() {
        const realSet = window.setInterval;
        const realClear = window.clearInterval;
        const realSetTimeout = window.setTimeout;
        const timers = new Map();
        let next = 0;
        window.setInterval = (fn) => { timers.set(++next, fn); return next; };
        window.clearInterval = (id) => { timers.delete(id); };
        const tick = () => [...timers.values()].forEach((fn) => fn());
        const held = (id) => el(id, 'button', {
            bindings: { do: { press: [{ action: 'macro', macro: 'm1', mode: 'hold_repeat', hold_repeat_ms: 50 }] } },
        });
        const pages = ['home', 'help'].map(startupPage);
        pages[0].elements.push(held('h1'));
        pages[0].layouts[0].placements.h1 = { x: 0, y: 0, w: 10, h: 10 };
        pages[1].page_type = 'overlay';
        pages[1].elements.push(held('h2'));
        pages[1].layouts[0].placements.h2 = { x: 0, y: 0, w: 10, h: 10 };
        const app = openingPanel(pages);
        const sent = [];
        const recordSends = () => {
            app.ws.send = (raw) => {
                const msg = JSON.parse(raw);
                sent.push(`${msg.type}:${msg.element_id || msg.page_id}`);
                if (msg.type === 'ui.page') app.handleMessage({ type: 'ui.navigate', page_id: msg.page_id });
            };
        };
        app.ws = { readyState: 1 };
        recordSends();
        const button = (id) => document.querySelector(`[data-element-id="${id}"]`);
        const press = (id) => button(id).dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, cancelable: true }));
        const letGo = () => window.dispatchEvent(new window.MouseEvent('mouseup', { bubbles: true }));
        try {
            press('h1');
            tick(); tick();
            assert(sent.join() === 'ui.press:h1,ui.press:h1,ui.press:h1',
                `a press, then one per tick while held, got ${sent.join()}`);
            button('h1').dispatchEvent(new window.MouseEvent('mouseup', { bubbles: true, cancelable: true }));
            tick();
            assert(sent.join() === 'ui.press:h1,ui.press:h1,ui.press:h1,ui.release:h1',
                `letting go stops it, got ${sent.join()}`);
            assert(timers.size === 0 && !('h1' in app.holdTimers), 'no timer left');

            press('h1');
            button('h1').dispatchEvent(new window.MouseEvent('mouseleave'));
            assert(timers.size === 0, 'the pointer leaving ends the hold');
            letGo();

            press('h1');
            Object.defineProperty(document, 'hidden', { value: true, configurable: true });
            document.dispatchEvent(new window.Event('visibilitychange'));
            delete document.hidden;
            assert(timers.size === 0, 'going to the background ends the hold');
            letGo();

            press('h1');
            document.dispatchEvent(new window.Event('visibilitychange'));
            assert(timers.size === 1, 'coming back to the front is not a release');
            app.renderCurrentPage();
            assert(timers.size === 0, 'a new page ends the hold');
            letGo();

            press('h1');
            app.navigateToPage('help');
            press('h2');
            assert(timers.size === 2, 'two buttons held, one under the overlay');
            app.dismissOverlay();
            assert(timers.size === 1 && !('h2' in app.holdTimers) && ('h1' in app.holdTimers),
                'closing the overlay ends its own hold and leaves the page underneath alone');
            letGo();

            window.WebSocket = FakeWS;
            app.connect();
            recordSends();
            press('h1');
            dropSocket(app, 1006);
            assert(timers.size === 0, 'a dropped socket ends the hold');
            letGo();
        } finally {
            window.setInterval = realSet;
            window.clearInterval = realClear;
            window.setTimeout = realSetTimeout;
            resetAfterDrop(app);
            app.dismissAllOverlays();
        }
    },

    // The ended screen is drawn once and ends every hold-repeat.
    remote_session_ended_is_drawn_once_and_ends_the_holds() {
        clearAccessOverlay();
        const rig = remoteRig(reply(200, '{}', true));
        const app = mkApp();
        const hold = window.setInterval(() => {}, 100000);
        try {
            app.holdTimers = { held: hold };
            app.access.showRemoteSessionEnded();
            assert(Object.keys(app.holdTimers).length === 0, 'every hold-repeat ends');
            app.connect();
            assert(rig.sockets === 0, 'the panel does not connect once its session is over');
            const overlay = document.getElementById('panel-access-overlay');
            overlay.querySelector('.panel-access-title').textContent = 'touched';
            app.access.showRemoteSessionEnded();
            assert(overlay.querySelector('.panel-access-title').textContent === 'touched',
                'a second call draws nothing');
            assert(document.querySelectorAll('#panel-access-overlay').length === 1, 'one screen');
        } finally {
            window.clearInterval(hold);
            clearAccessOverlay();
            rig.restore();
        }
    },

    // Every address the panel builds for its own server keeps the path it
    // was opened under, so it works through Remote Access as it does on the
    // system's own address. Read when it is asked, not when the panel starts.
    async every_server_address_follows_the_path_the_panel_was_opened_under() {
        const savedFetch = window.fetch;
        const savedWS = window.WebSocket;
        const seen = [];
        window.fetch = async (u) => {
            seen.push(String(u));
            return { ok: false, status: 404, json: async () => ({}) };
        };
        class UrlWS extends FakeWS { constructor(url) { super(); seen.push(String(url)); } }
        UrlWS.OPEN = 1;
        window.WebSocket = UrlWS;
        const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        const addresses = async (app) => {
            seen.length = 0;
            app.uiSettings = { lock_enabled: true };
            app._pluginExtensions = {};
            await app._loadPluginExtensions();
            await app._fetchPluginExtToken('p 1');
            app.uiDef = { pages: [] };          // the designer's fetch draws nothing on its own
            app._fetchProjectAndRender();
            await Promise.resolve();
            app.connect();
            await app._checkLockCode('1234');
            await app.access.fetchAccess();
            await app.access.claimWithPassword('admin', 'x');
            app.currentTheme = null; app.inlineTheme = null;
            app.applyTheme({ theme_id: 'slate blue' });
            await app.access.probeRemoteSession();
            const built = [
                app._resolveAbsoluteUrl('/api/x.mp3'),
                app._resolveSoundUrl('chime 1'),
                assetUrl('assets://a b.png'),
                app.renderIcon('play').querySelector('use').getAttribute('href'),
                app.renderCustomElement({ id: 'c', type: 'custom', custom_file: 'map/index.html' })
                    ._pluginIframe.getAttribute('src'),
                app.renderPluginElement({ id: 'p', type: 'plugin', plugin_id: 'clock', plugin_type: 'face' })
                    ._pluginIframe.getAttribute('src'),
            ];
            return seen.concat(built);
        };
        const want = (base) => [
            `${base}/api/plugins/extensions`,
            `${base}/api/plugins/p%201/ext-token`,
            `${base}/api/ui/resolved`,
            `ws://localhost:8080${base}/ws?client=panel&namespaces=device,var,ui,system,plugin`,
            `${base}/api/panel/unlock`,
            `${base}/api/panel/access?time_zone=${encodeURIComponent(zone)}`,
            `${base}/api/panel/access/claim`,
            `${base}/api/themes/slate%20blue`,
            `${base}/api/panel/access`,
            // A custom control's frame asks for its own file first, to say
            // so when it is missing (modules/frames.js, the file check).
            `${base}/api/projects/default/ui/map/index.html`,
            `${base}/api/x.mp3`,
            `${base}/api/plugins/audio_player/files/sounds/chime%201.mp3`,
            `${base}/api/projects/default/assets/a%20b.png`,
            `${base}/panel/icons.svg#play`,
            `${base}/api/projects/default/ui/map/index.html`,
            `${base}/api/plugins/clock/panel/face.html`,
        ];
        const realWarn = window.console.warn;
        window.console.warn = () => {};
        try {
            for (const [path, base] of [['/tunnel/t1/panel/', '/tunnel/t1'], ['/panel', ''],
                ['/tunnel/t1/panel/index.html?x=1', '/tunnel/t1']]) {
                const got = await atPath(path, async () => addresses(mkApp()));
                assert(JSON.stringify(got) === JSON.stringify(want(base)),
                    `under ${path}:\n got  ${JSON.stringify(got)}\n want ${JSON.stringify(want(base))}`);
            }
        } finally {
            window.console.warn = realWarn;
            window.fetch = savedFetch;
            window.WebSocket = savedWS;
            for (const id of ['c', 'p']) document.querySelectorAll(`[data-element-id="${id}"]`).forEach((n) => n.remove());
        }
    },

    // the band carries failures of things people did. A frame
    // with no source_type is the connection refusing a message before anything
    // was read off it, so it cannot name what failed; "Rate limit exceeded" in
    // front of a room is a fact about our protocol nobody there can act on.
    q139_a_connection_level_refusal_is_not_put_on_the_glass() {
        const app = mkApp();
        clearFailureBand();

        app.handleMessage({ type: 'error', message: 'Rate limit exceeded' });
        assert(!document.getElementById('panel-failure-message'),
            'the protocol keeps its complaints to the console');

        // And the rule is narrow: a refusal of something somebody pressed
        // still says so.
        app.handleMessage({
            type: 'error', source_type: 'ui.press', element_id: 'btn_on',
            message: 'Ceiling Projector is not connected.',
        });
        const band = document.getElementById('panel-failure-message');
        assert(band && band.classList.contains('visible')
            && band.textContent === 'Ceiling Projector is not connected.',
            `a named failure is still drawn, got "${band && band.textContent}"`);
    },

    // --- A theme override has to reach the page, not just the elements ------

    // Every built-in theme paints the page with `var(panel_bg)`, and that
    // reference is resolved in JS rather than by CSS. Resolved against the
    // theme's OWN variables it could never see the project's override, so the
    // page stayed the theme's colour while every element on it moved.
    theme_override_moves_the_page_background() {
        const app = mkApp();
        app.inlineTheme = themeWithPageDefaults();
        try {
            renderProject(app, themedProject({ theme_overrides: { panel_bg: '#0e1216' } }));
            const surface = document.querySelector('#panel-root .panel-page');
            assert(surface.style.backgroundColor === 'rgb(14, 18, 22)',
                `the page paints the override, got ${surface.style.backgroundColor}`);
            assert(document.documentElement.style.getPropertyValue('--panel-bg') === '#0e1216',
                'the elements were already honouring it -- the page is what was not');
        } finally {
            document.documentElement.style.removeProperty('--panel-bg');
        }
    },

    // Guards the guard: with nothing overridden the reference still resolves,
    // or the test above would pass against a build that had lost page_defaults
    // altogether and simply painted whatever it was handed.
    theme_with_no_override_paints_its_own_page_colour() {
        const app = mkApp();
        app.inlineTheme = themeWithPageDefaults();
        try {
            renderProject(app, themedProject());
            const surface = document.querySelector('#panel-root .panel-page');
            assert(surface.style.backgroundColor === 'rgb(30, 42, 58)',
                `the theme's own panel_bg, got ${surface.style.backgroundColor}`);
        } finally {
            document.documentElement.style.removeProperty('--panel-bg');
        }
    },

    // Both ends of a page gradient go through the same resolver, so both have
    // to see the same variables -- and settings.accent_color is an override
    // too, applied after theme_overrides and on top of them.
    theme_page_defaults_references_read_one_set_of_variables() {
        const app = mkApp();
        app.inlineTheme = themeWithPageDefaults({
            background_gradient: { from: 'var(panel_bg)', to: 'var(accent)', angle: 90 },
        });
        try {
            renderProject(app, themedProject({
                theme_overrides: { panel_bg: '#0e1216', accent: '#ff0000' },
                accent_color: '#00e676',
            }));
            const layer = document.querySelector('#panel-root .panel-page-bg-gradient');
            assert(layer, 'the page drew a gradient layer');
            const painted = layer.style.background || layer.style.backgroundImage;
            assert(painted.includes('#0e1216'),
                `the from end takes the override, got ${painted}`);
            assert(painted.includes('#00e676'),
                `accent_color outranks the theme_override, got ${painted}`);
        } finally {
            document.documentElement.style.removeProperty('--panel-bg');
            document.documentElement.style.removeProperty('--panel-accent');
        }
    },

    // --- a look that goes inactive lets go of its colours ------------------
    //
    // applyStyle writes only what it is given, so a look whose inactive half
    // names no colour used to leave the active colour standing: an input
    // picker showed several inputs lit at once, and a Blank All button stayed
    // orange after the screens were restored.

    look_with_no_inactive_colour_lets_go_of_the_active_one() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'blank', type: 'button', label: 'Blank All',
                bindings: { show: { look: {
                    key: 'var.blanked', condition: { equals: true },
                    style_active: { bg_color: '#ff9800', text_color: '#000000' },
                } } },
            }],
            placements: { blank: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'var.blanked': false };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="blank"]');
        app.state['var.blanked'] = true;
        app.evaluateAllBindings(['var.blanked']);
        assert(el.style.backgroundColor === 'rgb(255, 152, 0)',
            `blanked: lit, got ${el.style.backgroundColor}`);
        app.state['var.blanked'] = false;
        app.evaluateAllBindings(['var.blanked']);
        assert(el.style.backgroundColor === '',
            `restored: the orange is dropped, got "${el.style.backgroundColor}"`);
        assert(el.style.color === '',
            `restored: and so is the text colour, got "${el.style.color}"`);
    },

    input_picker_lights_only_the_current_input() {
        const app = mkApp();
        const input = (n) => ({
            id: `in${n}`, type: 'button', label: `Input ${n}`,
            bindings: { show: { look: {
                key: 'var.input', condition: { equals: n },
                style_active: { bg_color: '#4caf50' },
            } } },
        });
        const proj = project({
            elements: [input(1), input(2), input(3)],
            placements: {
                in1: { x: 5, y: 5, w: 20, h: 10 },
                in2: { x: 30, y: 5, w: 20, h: 10 },
                in3: { x: 55, y: 5, w: 20, h: 10 },
            },
        });
        app.state = { 'var.input': 1 };
        renderProject(app, proj);
        const lit = () => [1, 2, 3].filter(n =>
            app.root.querySelector(`[data-element-id="in${n}"]`).style.backgroundColor
                === 'rgb(76, 175, 80)');
        for (const n of [1, 2, 3, 1]) {
            app.state['var.input'] = n;
            app.evaluateAllBindings(['var.input']);
            const on = lit();
            assert(on.length === 1 && on[0] === n,
                `input ${n} selected: exactly it is lit, got [${on}]`);
        }
    },

    state_naming_no_colour_drops_the_last_states_colour_and_opacity() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'pwr', type: 'button', label: 'Power',
                bindings: { show: { look: {
                    key: 'device.proj.power', default_state: 'off',
                    states: {
                        on: { label: 'Power ON', bg_color: '#4caf50' },
                        warming: { label: 'Warming', bg_color: '#ff9800', opacity: 0.6 },
                        off: { label: 'Power OFF' },
                    },
                } } },
            }],
            placements: { pwr: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'device.proj.connected': true, 'device.proj.power': 'off' };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="pwr"]');
        const go = (v) => {
            app.state['device.proj.power'] = v;
            app.evaluateAllBindings(['device.proj.power']);
        };
        go('on');
        assert(el.style.backgroundColor === 'rgb(76, 175, 80)', `on: green, got ${el.style.backgroundColor}`);
        go('off');
        assert(el.style.backgroundColor === '', `off: no colour of its own, got "${el.style.backgroundColor}"`);
        go('warming');
        assert(el.style.opacity === '0.6', `warming: dimmed, got "${el.style.opacity}"`);
        go('off');
        assert(el.style.backgroundColor === '' && el.style.opacity === '',
            `off again: neither colour nor dimming stays, got "${el.style.backgroundColor}" / "${el.style.opacity}"`);
        go('standby');
        assert(el.style.backgroundColor === '' && el.textContent.includes('Power OFF'),
            `an unlisted value falls to the default state, clean, got "${el.style.backgroundColor}"`);
    },

    // Most element types take their theme colour inline rather than from CSS,
    // so letting go of a state's colour has to put the theme's back.
    themed_element_returns_to_its_theme_colour() {
        const app = mkApp();
        app.themeElementDefaults = { camera_preset: { bg_color: '#2a2a4a', text_color: '#e0e0e0' } };
        const proj = project({
            elements: [{
                id: 'cam', type: 'camera_preset', label: 'Wide', preset_number: 1,
                bindings: { show: { look: {
                    key: 'var.preset', condition: { equals: 1 },
                    style_active: { bg_color: '#ff0000' },
                } } },
            }],
            placements: { cam: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'var.preset': 1 };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="cam"]');
        assert(el.style.backgroundColor === 'rgb(255, 0, 0)', `active: red, got ${el.style.backgroundColor}`);
        app.state['var.preset'] = 2;
        app.evaluateAllBindings(['var.preset']);
        assert(el.style.backgroundColor === 'rgb(42, 42, 74)',
            `inactive: back to the theme's colour, got ${el.style.backgroundColor}`);
    },

    label_look_lets_go_of_its_colour() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'st', type: 'label', text: 'Status',
                bindings: { show: { look: { key: 'var.s', states: {
                    alarm: { label: 'ALARM', text_color: '#ff0000', opacity: 0.5 },
                    ok: { label: 'OK' },
                } } } },
            }],
            placements: { st: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'var.s': 'alarm' };
        renderProject(app, proj);
        const el = app.root.querySelector('[data-element-id="st"]');
        assert(el.style.color === 'rgb(255, 0, 0)', `alarm: red, got ${el.style.color}`);
        app.state['var.s'] = 'ok';
        app.evaluateAllBindings(['var.s']);
        assert(el.style.color === '' && el.style.opacity === '',
            `ok: no colour or dimming left over, got "${el.style.color}" / "${el.style.opacity}"`);
    },

    // Which appearance a look picks for a state value, through every control
    // that has a look of its own. One table, five controls: the same value
    // must pick the same entry everywhere.
    look_picks_the_same_state_on_a_label() { checkLookTable('evaluateLabelLook', 'label'); },
    look_picks_the_same_state_on_an_image() { checkLookTable('evaluateImageLook', 'image'); },
    look_picks_the_same_state_on_a_page_nav() { checkLookTable('evaluateNavLook', 'page_nav'); },
    look_picks_the_same_state_on_a_container() { checkLookTable('evaluateGroupLook', 'group'); },
    look_picks_the_same_state_on_a_button() { checkLookTable('evaluateFeedback', 'button'); },

    // A device that cannot be reached claims no state: every look draws the
    // control's own look, whatever its key says. A page nav with no key has no
    // state to show and keeps the words it already had.
    a_look_shows_nothing_for_an_unreachable_device_or_a_missing_key() {
        const away = { key: 'device.amp.mute', default_state: 'off', states: { on: LOOK_ENTRIES.A, off: LOOK_ENTRIES.B } };
        const gone = { 'device.amp.connected': false };
        for (const [method, type] of [['evaluateLabelLook', 'label'], ['evaluateImageLook', 'image'],
            ['evaluateNavLook', 'page_nav'], ['evaluateGroupLook', 'group'], ['evaluateFeedback', 'button']]) {
            const r = runLook(method, type, away, 'on', { state: gone, live: true });
            assert(r.bg === LOOK_OWN_BG, `${type}, device away: its own colour, got ${r.bg}`);
            const live = runLook(method, type, away, 'on', { state: { 'device.amp.connected': true }, live: true });
            assert(live.bg === LOOK_BG.A, `${type}, device back: the state's colour, got ${live.bg}`);
        }
        const keyless = { default_state: 'off', states: { on: LOOK_ENTRIES.A, off: LOOK_ENTRIES.B } };
        const r = runLook('evaluateNavLook', 'page_nav', keyless, undefined);
        assert(r.bg === LOOK_OWN_BG && r.text === 'Own' && r.path === null,
            `page nav, no key: its own look and words, nothing recorded, got ${JSON.stringify(r)}`);
    },

    // The two-state form's own words: a label or page nav takes label_active /
    // label_inactive whenever one is set, an empty one included; a button takes
    // one only when it has words, and otherwise the state's own label.
    two_state_words_on_a_label_page_nav_and_button() {
        const binding = {
            key: 'var.v', condition: { equals: 'on' },
            style_active: { label: 'Style on' }, style_inactive: { label: 'Style off' },
            label_active: 'Word on', label_inactive: '',
        };
        const run = (method, type, value) => {
            const r = runLook(method, type, binding, value);
            return [r.text, r.path];
        };
        const same = (got, want, what) => assert(JSON.stringify(got) === JSON.stringify(want),
            `${what}: want ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
        const word = (w) => ['bindings', 'show', 'look', w];
        same(run('evaluateLabelLook', 'label', 'on'), ['Word on', word('label_active')], 'label, on');
        same(run('evaluateLabelLook', 'label', 'off'), ['', word('label_inactive')], 'label, off');
        same(run('evaluateNavLook', 'page_nav', 'on'), ['Word on', word('label_active')], 'nav, on');
        same(run('evaluateNavLook', 'page_nav', 'off'), ['', word('label_inactive')], 'nav, off');
        same(run('evaluateFeedback', 'button', 'on'), ['Word on', word('label_active')], 'button, on');
        same(run('evaluateFeedback', 'button', 'off'),
            ['Style off', [...word('style_inactive'), 'label']], 'button, off');
        // A button whose own style carries its words, under a state that
        // names none: the words are its own style's.
        const plain = { key: 'var.v', condition: { equals: 'on' }, style_active: { bg_color: '#110000' } };
        const own = runLook('evaluateFeedback', 'button', plain, 'on', { ownStyle: { label: 'Styled' } });
        same([own.text, own.path], ['Styled', ['style', 'label']], 'button, words in its own style');
    },

    // applyStyle decides background-image on every call (the gradient, or
    // nothing), and a button's own image lives in the same property, so every
    // look or toggle pass used to wipe it.
    looks_and_toggles_keep_the_buttons_own_image() {
        const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
        const mount = (def, state) => {
            const app = mkApp();
            renderProject(app, project({
                elements: [def], placements: { [def.id]: { x: 5, y: 5, w: 20, h: 10 } },
            }));
            app.state = state;
            app.evaluateAllBindings(Object.keys(state));
            return { app, el: app.root.querySelector(`[data-element-id="${def.id}"]`) };
        };
        const flip = (app, key, value) => {
            app.state[key] = value;
            app.evaluateAllBindings([key]);
        };
        const base = { type: 'button', label: 'Cam', display_mode: 'image_text', button_image: PIXEL };
        const look = { key: 'var.s', default_state: 'off', states: {
            on: { label: 'ON', bg_color: '#ff0000' }, off: { label: 'OFF' } } };
        const toggle = { do: { press: [{ action: 'state.set', key: 'var.on', value: true,
            mode: 'toggle', toggle_key: 'var.on', toggle_value: true }] } };

        let m = mount({ id: 'a', ...base, bindings: { show: { look } } }, { 'var.s': 'off' });
        flip(m.app, 'var.s', 'on');
        assert(m.el.style.backgroundImage.includes('url('), `look on: image kept, got "${m.el.style.backgroundImage}"`);
        flip(m.app, 'var.s', 'off');
        assert(m.el.style.backgroundImage.includes('url('), `look off: image kept, got "${m.el.style.backgroundImage}"`);

        m = mount({ id: 'b', ...base, bindings: toggle }, { 'var.on': false });
        flip(m.app, 'var.on', true);
        assert(m.el.style.backgroundImage.includes('url('), `toggle on: image kept, got "${m.el.style.backgroundImage}"`);
        flip(m.app, 'var.on', false);
        assert(m.el.style.backgroundImage.includes('url('), `toggle off: image kept, got "${m.el.style.backgroundImage}"`);

        m = mount({ id: 'c', ...base, frameless: true, bindings: toggle }, { 'var.on': false });
        flip(m.app, 'var.on', true);
        assert(m.el.style.backgroundImage.includes('url('),
            `frameless toggle: image kept, got "${m.el.style.backgroundImage}"`);

        m = mount({ id: 'd', ...base, style: { background_gradient: { from: '#e74c3c', to: '#8e44ad', angle: 180 } },
            bindings: { show: { look } } }, { 'var.s': 'off' });
        flip(m.app, 'var.s', 'on');
        const both = m.el.style.backgroundImage;
        assert(both.includes('url(') && both.includes('linear-gradient'),
            `gradient and image both survive a look, got "${both}"`);

        // A masked image is drawn as a coloured layer; with no state colour it
        // must take the text colour, not go transparent.
        m = mount({ id: 'e', ...base, display_mode: 'image', image_blend_mode: 'mask', bindings: toggle },
            { 'var.on': true });
        flip(m.app, 'var.on', false);
        const layer = m.el.querySelector('.panel-button-image-layer');
        assert(layer, 'masked toggle off: the image layer is still there');
        assert(layer.style.backgroundColor !== '',
            `masked toggle off: the layer keeps a fill, got "${layer.style.backgroundColor}"`);
    },

    // --- clearing a ui.<id>.* override hands the element back to its look ---
    //
    // What came back used to be a snapshot of the element as the panel first
    // drew it, so a Blank All button lit orange by its look, coloured by a
    // script, then uncoloured, went back to the grey it had before the
    // screens were blanked.

    cleared_override_returns_to_what_the_look_shows_now() {
        const app = mkApp();
        const proj = project({
            elements: [{
                id: 'blank', type: 'button', label: 'Blank All',
                bindings: { show: { look: {
                    key: 'var.blanked', condition: { equals: true },
                    style_active: { bg_color: '#ff9800', text_color: '#000000' },
                    label_active: 'Blanked',
                } } },
            }],
            placements: { blank: { x: 5, y: 5, w: 20, h: 10 } },
        });
        app.state = { 'var.blanked': false };
        renderProject(app, proj);
        app.evaluateAllBindings();
        const el = app.root.querySelector('[data-element-id="blank"]');
        const set = (key, value) => {
            if (value === undefined) delete app.state[key]; else app.state[key] = value;
            app.evaluateAllBindings([key]);
        };
        const ORANGE = 'rgb(255, 152, 0)';

        set('var.blanked', true);
        assert(el.style.backgroundColor === ORANGE, `blanked: lit, got ${el.style.backgroundColor}`);
        set('ui.blank.bg_color', '#ff0000');
        set('ui.blank.text_color', '#ffffff');
        set('ui.blank.label', 'Busy');
        assert(el.style.backgroundColor === 'rgb(255, 0, 0)', `override: red, got ${el.style.backgroundColor}`);
        set('ui.blank.bg_color', undefined);
        assert(el.style.backgroundColor === ORANGE,
            `override cleared: back to the look's orange, got "${el.style.backgroundColor}"`);
        assert(el.style.color === 'rgb(255, 255, 255)',
            `the text colour override still set is still drawn, got "${el.style.color}"`);
        assert(el.textContent.includes('Busy'),
            `the label override still set is still drawn, got "${el.textContent}"`);
        set('ui.blank.text_color', undefined);
        set('ui.blank.label', undefined);
        assert(el.style.color === 'rgb(0, 0, 0)',
            `text colour cleared: the look's black, got "${el.style.color}"`);
        assert(el.textContent.includes('Blanked'),
            `label cleared: the look's word, got "${el.textContent}"`);

        // The look moves while the override covers it: what comes back is
        // where the look is now, not where it was when the override was set.
        set('ui.blank.bg_color', '#ff0000');
        set('var.blanked', false);
        assert(el.style.backgroundColor === 'rgb(255, 0, 0)',
            `the override outranks the look, got ${el.style.backgroundColor}`);
        set('ui.blank.bg_color', undefined);
        assert(el.style.backgroundColor === '',
            `override cleared after restore: no colour of its own, got "${el.style.backgroundColor}"`);
        assert(el.textContent.includes('Blank All'),
            `and its own label, got "${el.textContent}"`);
    },

    // --- a PIN pad shows a dot per digit, and sends the digits ---------------

    masked_keypad_draws_dots_and_sends_the_digits() {
        const mount = (extra) => {
            const app = mkApp();
            const sent = [];
            app.send = (m) => sent.push(m);
            renderProject(app, project({
                elements: [Object.assign({ id: 'pin', type: 'keypad', label: 'PIN', digits: 4 }, extra)],
                placements: { pin: { x: 5, y: 5, w: 25, h: 60 } },
            }));
            const el = app.root.querySelector('[data-element-id="pin"]');
            const press = (k) => [...el.querySelectorAll('.keypad-key')]
                .find(b => b.getAttribute('aria-label') === k).click();
            return { el, sent, press, display: () => el.querySelector('.keypad-display').textContent };
        };

        let m = mount({ mask_display: true });
        for (const k of ['Key 1', 'Key 2', 'Key 3']) m.press(k);
        assert(m.display() === '•••',
            `masked: a dot per digit, got "${m.display()}"`);
        m.press('Clear');
        assert(m.display() === '', `masked: Clear empties it, got "${m.display()}"`);
        for (const k of ['Key 4', 'Key 2']) m.press(k);
        m.press('Enter');
        const submit = m.sent.find(s => s.type === 'ui.submit');
        assert(submit && submit.value === '42',
            `masked: the digits are what is sent, got ${JSON.stringify(m.sent)}`);
        assert(m.display() === '', `masked: empty after sending, got "${m.display()}"`);

        m = mount({});
        for (const k of ['Key 1', 'Key 2']) m.press(k);
        assert(m.display() === '12', `not masked: the digits, got "${m.display()}"`);
    },

    // --- every control takes a macro's overrides, and gives them back --------
    //
    // The picker offers ui.<id>.* for every control on every page, and the
    // panel answered them on ten of the nineteen types. On the captioned ones
    // it did answer, a label override added its words beside the caption, and
    // clearing it left the caption drawn twice.

    every_control_takes_its_overrides_and_gives_them_back() {
        const defs = [
            { type: 'button', label: 'Cap' }, { type: 'label', text: 'Cap' },
            { type: 'status_led', label: 'Cap' }, { type: 'slider', label: 'Cap' },
            { type: 'page_nav', label: 'Cap', target_page: 'main' },
            { type: 'select', label: 'Cap', options: [{ label: 'A', value: 'a' }] },
            { type: 'text_input', label: 'Cap' }, { type: 'image', label: 'Cap' },
            { type: 'camera_preset', label: 'Cap', preset_number: 1 },
            { type: 'list', label: 'Cap', items: [{ label: 'Row', value: 'r' }] },
            { type: 'matrix', label: 'Cap' }, { type: 'gauge', label: 'Cap' },
            { type: 'level_meter', label: 'Cap' }, { type: 'fader', label: 'Cap' },
            { type: 'group', label: 'Cap' }, { type: 'clock' }, { type: 'keypad', label: 'Cap' },
            { type: 'plugin', label: 'Cap' }, { type: 'custom', label: 'Cap' },
        ];
        // A control with no words of its own for the label override to replace.
        const wordless = new Set(['image', 'clock', 'plugin', 'custom']);
        const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
        for (const d of defs) {
            const app = mkApp();
            const id = `x_${d.type}`;
            renderProject(app, project({
                elements: [Object.assign({ id }, d)],
                placements: { [id]: { x: 5, y: 5, w: 30, h: 30 } },
            }));
            app.state = {};
            app.evaluateAllBindings();
            const el = app.root.querySelector(`[data-element-id="${id}"]`);
            const keys = ['bg_color', 'visible', 'label'].map(k => `ui.${id}.${k}`);
            const before = { bg: el.style.backgroundColor, display: el.style.display, words: text(el) };

            app.state[keys[0]] = '#ff0000';
            app.state[keys[1]] = false;
            app.state[keys[2]] = 'OVR';
            app.evaluateAllBindings(keys);
            assert(el.style.backgroundColor === 'rgb(255, 0, 0)',
                `${d.type}: the colour override is drawn, got "${el.style.backgroundColor}"`);
            assert(el.style.display === 'none', `${d.type}: the visible override hides it`);
            if (wordless.has(d.type)) {
                assert(text(el) === before.words,
                    `${d.type}: no words to replace, so none are added, got "${text(el)}"`);
            } else {
                assert(text(el).includes('OVR') && !text(el).includes('Cap'),
                    `${d.type}: the label override replaces its words, got "${text(el)}"`);
            }

            for (const k of keys) delete app.state[k];
            app.evaluateAllBindings(keys);
            assert(el.style.backgroundColor === before.bg && el.style.display === before.display,
                `${d.type}: cleared, its own colour and display come back, got "${el.style.backgroundColor}" / "${el.style.display}"`);
            assert(text(el) === before.words,
                `${d.type}: cleared, its own words come back once, got "${text(el)}" for "${before.words}"`);
        }
    },

    // A control with an icon keeps its words in a span beside it, and a label
    // drawn as rich text keeps some of them in <strong> and <em>. Clearing a
    // label override read only the text sitting directly in the control, so
    // the button came back as an icon with no words and the label lost its
    // bold run.
    a_cleared_label_override_gives_back_the_words_it_covered() {
        const defs = [
            { id: 'icon_left', type: 'button', label: 'Power', icon: 'power' },
            { id: 'icon_right', type: 'button', label: 'Next', icon: 'arrow-right', icon_position: 'right' },
            { id: 'icon_top', type: 'button', label: 'Mute', icon: 'volume-x', icon_position: 'top' },
            { id: 'nav', type: 'page_nav', label: 'Home', target_page: 'main', icon: 'house' },
            { id: 'cam', type: 'camera_preset', label: 'Wide', preset_number: 2, icon: 'camera' },
            { id: 'rich', type: 'label', text: '**Room** is *ready*', style: { white_space: 'pre-wrap' } },
        ];
        const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
        for (const d of defs) {
            const app = mkApp();
            renderProject(app, project({
                elements: [d],
                placements: { [d.id]: { x: 5, y: 5, w: 30, h: 30 } },
            }));
            app.state = {};
            app.evaluateAllBindings();
            const el = app.root.querySelector(`[data-element-id="${d.id}"]`);
            const drawn = el.innerHTML;
            // Where the icon sits against the words; bold and italic runs are words.
            const order = () => [...el.children].filter(c => !['STRONG', 'EM'].includes(c.tagName))
                .map(c => c.tagName.toLowerCase()).join(',');
            const layout = order();
            const key = `ui.${d.id}.label`;

            app.state[key] = 'OVR';
            app.evaluateAllBindings([key]);
            assert(text(el) === 'OVR', `${d.id}: the override is all the words, got "${text(el)}"`);
            assert(order() === layout,
                `${d.id}: the icon stays where it was drawn, got [${order()}] for [${layout}]`);

            delete app.state[key];
            app.evaluateAllBindings([key]);
            assert(el.innerHTML === drawn,
                `${d.id}: cleared, it draws what it drew before, got ${el.innerHTML} for ${drawn}`);
        }
    },

    // The unavailable mark is styled on the control's box (.panel-element), so
    // a mark on the part a binding draws into (an LED's dot, a slider's track,
    // a dropdown's list) was never drawn.
    the_unavailable_mark_goes_on_the_control_itself() {
        const app = mkApp();
        renderProject(app, project({
            elements: [
                { id: 'led', type: 'status_led', label: 'LED',
                    bindings: { show: { look: { key: 'device.d.power', states: { on: { color: '#4caf50' } } } } } },
                { id: 'sld', type: 'slider', label: 'Volume',
                    bindings: { show: { value: { key: 'device.d.volume' } } } },
                { id: 'sel', type: 'select', label: 'Input', options: [{ label: 'A', value: 'a' }],
                    bindings: { show: { value: { key: 'device.d.input' } } } },
                { id: 'txt', type: 'text_input', label: 'Title',
                    bindings: { show: { value: { key: 'device.d.title' } } } },
                { id: 'cam', type: 'camera_preset', label: 'Wide', preset_number: 1,
                    action_devices: ['d'],
                    bindings: { do: { press: [{ action: 'device.command', device: 'd', command: 'recall' }] } } },
            ],
            placements: {
                led: { x: 5, y: 5, w: 20, h: 10 }, sld: { x: 30, y: 5, w: 30, h: 10 },
                sel: { x: 5, y: 30, w: 30, h: 10 }, txt: { x: 5, y: 55, w: 30, h: 10 },
                cam: { x: 40, y: 30, w: 20, h: 10 },
            },
        }));
        app.state = { 'device.d.connected': false, 'device.d.volume': 40, 'device.d.input': 'a',
            'device.d.title': 'Lecture' };
        app.evaluateAllBindings();
        const box = (id) => app.root.querySelector(`[data-element-id="${id}"]`);
        for (const id of ['led', 'sld', 'sel', 'txt']) {
            assert(box(id).classList.contains('device-offline'),
                `${id}: the reading mark is on the control, got "${box(id).className}"`);
        }
        assert(box('cam').classList.contains('action-offline'),
            `cam: a preset whose camera is away is marked, got "${box('cam').className}"`);
        app.state['device.d.connected'] = true;
        app.evaluateAllBindings(['device.d.connected']);
        for (const id of ['led', 'sld', 'sel', 'txt', 'cam']) {
            assert(!box(id).classList.contains('device-offline') && !box(id).classList.contains('action-offline'),
                `${id}: back, and unmarked, got "${box(id).className}"`);
        }
    },

    // Preview draws the Builder's copy of the page, which never carries
    // action_devices: only the server's resolved definition does. So Preview
    // takes them from the ui.definition the socket brings, by element id, and
    // the button and the camera preset are marked as /panel marks them.
    preview_marks_a_press_the_server_says_cannot_land() {
        const app = mkApp();
        app.embedded = true;
        app.editMode = false;
        const press = { do: { press: [{ action: 'device.command', device: 'd', command: 'on' }] } };
        const builderCopy = project({
            elements: [
                { id: 'btn', type: 'button', label: 'Display On', bindings: press },
                { id: 'cam', type: 'camera_preset', label: 'Wide', preset_number: 1, bindings: press },
                { id: 'lbl', type: 'label', text: 'Room' },
            ],
            placements: {
                btn: { x: 5, y: 5, w: 20, h: 10 }, cam: { x: 30, y: 5, w: 20, h: 10 },
                lbl: { x: 5, y: 30, w: 20, h: 10 },
            },
        });
        renderProject(app, builderCopy);
        app.state = { 'device.d.connected': false };
        app.evaluateAllBindings();
        const box = (id) => app.root.querySelector(`[data-element-id="${id}"]`);
        assert(!box('btn').classList.contains('action-offline'),
            'nothing is known about the press before the server says');

        const served = (ids) => {
            const ui = JSON.parse(JSON.stringify(builderCopy.ui));
            for (const element of ui.pages[0].elements) {
                if (ids.includes(element.id)) element.action_devices = ['d'];
            }
            return ui;
        };
        app.handleMessage({ type: 'ui.definition', ui: served(['btn', 'cam']) });
        assert(app.uiDef === builderCopy.ui, "the Builder's copy is still the page drawn");
        for (const id of ['btn', 'cam']) {
            assert(box(id).classList.contains('action-offline'),
                `${id}: marked as soon as the server's definition arrives, got "${box(id).className}"`);
        }
        assert(!box('lbl').classList.contains('action-offline'), 'a label reaches nothing');

        app.state['device.d.connected'] = true;
        app.evaluateAllBindings(['device.d.connected']);
        assert(!box('btn').classList.contains('action-offline'), 'the device is back: unmarked');

        // The press is re-pointed in the Builder and saved: the next
        // definition says the button reaches nothing, and its mark goes.
        app.state['device.d.connected'] = false;
        app.evaluateAllBindings(['device.d.connected']);
        assert(box('btn').classList.contains('action-offline'), 'marked again');
        app.handleMessage({ type: 'ui.definition', ui: served(['cam']) });
        assert(!box('btn').classList.contains('action-offline'),
            `a button that now reaches nothing loses its mark, got "${box('btn').className}"`);
        assert(box('cam').classList.contains('action-offline'), 'the preset keeps its mark');
    },

    // The design canvas never marks a control (its handles and artwork stay
    // visible), whatever reaches it.
    the_design_canvas_takes_no_press_marks_from_the_server() {
        const app = mkApp();
        app.embedded = true;
        app.editMode = true;
        const builderCopy = project({
            elements: [{ id: 'btn', type: 'button', label: 'Display On',
                bindings: { do: { press: [{ action: 'device.command', device: 'd', command: 'on' }] } } }],
            placements: { btn: { x: 5, y: 5, w: 20, h: 10 } },
        });
        renderProject(app, builderCopy);
        app.state = { 'device.d.connected': false };
        const ui = JSON.parse(JSON.stringify(builderCopy.ui));
        ui.pages[0].elements[0].action_devices = ['d'];
        app.handleMessage({ type: 'ui.definition', ui });
        app.evaluateAllBindings();
        const btn = app.root.querySelector('[data-element-id="btn"]');
        assert(!btn.classList.contains('action-offline'), `the canvas is unmarked, got "${btn.className}"`);
        assert(!app._servedActionDevices, 'the canvas keeps nothing from the server');
    },

    // A signage player or a dashboard card that frames /panel gets an ordinary
    // panel. Only the Programmer's Preview and design canvas, which mark the
    // URL they open, are embedded (a preview socket, the Builder's definition).
    only_the_programmers_frames_are_embedded() {
        const previousUrl = window.location.href;
        // jsdom keeps `parent` on the window itself: put back exactly that.
        const ownParent = Object.getOwnPropertyDescriptor(window, 'parent');
        const opened = (framed, query) => {
            if (framed) {
                Object.defineProperty(window, 'parent', { value: { postMessage() {} }, configurable: true });
            }
            window.history.replaceState({}, '', '/panel/' + query);
            try {
                return mkApp();
            } finally {
                window.history.replaceState({}, '', previousUrl);
                if (framed) {
                    if (ownParent) Object.defineProperty(window, 'parent', ownParent);
                    else delete window.parent;
                }
            }
        };
        assert(!opened(true, '').embedded, 'a page framing /panel gets a panel');
        assert(!opened(true, '?page=main').embedded, 'a page link alone is a panel');
        assert(opened(true, '?preview=1&page=main').embedded, 'the Builder Preview is embedded');
        assert(opened(true, '?preview=1&page=main&edit=1').embedded, 'the design canvas is embedded');
        assert(opened(true, '?page=main&edit=1').embedded, 'a preview frame is embedded');
        assert(!opened(false, '?preview=1').embedded, 'a tab is never embedded');
        assert(window.parent === window, 'the harness window is its own parent again');
    },

    // Simulation stays on through a restart until somebody stops it, so the
    // panel marks it in a corner while the devices are on simulators.
    simulation_tag_shows_while_the_devices_are_simulated() {
        const shown = () => {
            const tag = document.getElementById('simulation-tag');
            return !!tag && tag.classList.contains('visible')
                && window.getComputedStyle(tag).display !== 'none';
        };
        const old = document.getElementById('simulation-tag');
        if (old) old.remove();
        const app = mkApp();
        app.handleMessage({ type: 'state.snapshot', state: {} });
        assert(!shown(), 'no tag while nothing is simulated');
        app.handleMessage({ type: 'state.update', changes: { 'system.simulation_active': true } });
        assert(shown(), 'the tag appears when simulation starts');
        const tag = document.getElementById('simulation-tag');
        assert(tag.textContent === 'Simulated', `tag reads "${tag.textContent}"`);
        assert(window.getComputedStyle(tag).pointerEvents === 'none',
            'a tap goes through the tag to the control under it');
        app.handleMessage({ type: 'state.update', changes: { 'system.simulation_active': false } });
        assert(!shown(), 'the tag goes when simulation stops');
        app.handleMessage({ type: 'state.snapshot', state: { 'system.simulation_active': true } });
        assert(shown(), 'a reconnect snapshot of a simulating system draws it');
        app.handleMessage({ type: 'state.delete', keys: ['system.simulation_active'] });
        assert(!shown(), 'a deleted key is not simulating');
        document.getElementById('simulation-tag').remove();
    },

    simulation_tag_is_never_drawn_in_the_ide_preview() {
        const old = document.getElementById('simulation-tag');
        if (old) old.remove();
        const previousUrl = window.location.href;
        const ownParent = Object.getOwnPropertyDescriptor(window, 'parent');
        Object.defineProperty(window, 'parent', { value: { postMessage() {} }, configurable: true });
        window.history.replaceState({}, '', '/panel/?preview=1&page=main');
        let app;
        try {
            app = mkApp();
        } finally {
            window.history.replaceState({}, '', previousUrl);
            if (ownParent) Object.defineProperty(window, 'parent', ownParent);
            else delete window.parent;
        }
        assert(app.embedded, 'the harness built an embedded preview');
        app.handleMessage({ type: 'state.snapshot', state: { 'system.simulation_active': true } });
        assert(!document.getElementById('simulation-tag'), 'the IDE says it in its own bar');
    },

    // --- what a style writes onto a node, branch by branch -----------------
    //
    // Pinned as the panel draws today, value and order (the style attribute
    // is compared whole, so a write that moves or goes missing fails here).
    // jsdom keeps every value below except a multi-layer background-position
    // and the -webkit- mask longhands; tests/e2e/test_button_background_layers.py
    // reads those in Chromium.

    paint_writes_each_style_key_as_it_does_today() {
        const rows = {
            nothing: null,
            empty: {},
            flat: { bg_color: '#111111' },
            gradient_default_angle: { background_gradient: { from: '#ff0000', to: '#0000ff' } },
            gradient_angle_zero_reads_180: { background_gradient: { from: '#ff0000', to: '#0000ff', angle: 0 } },
            gradient_angle_text_reads_180: { background_gradient: { from: '#ff0000', to: '#0000ff', angle: 'abc' } },
            gradient_angle_45_with_colour: { bg_color: '#222222', background_gradient: { from: '#ff0000', to: '#0000ff', angle: 45 } },
            gradient_needs_both_stops: { bg_color: '#333333', background_gradient: { from: '#ff0000' } },
            gradient_stops_sanitized: { background_gradient: { from: 'red; color: blue', to: 'url(x)' } },
            picture_over_gradient: { background_gradient: { from: '#ff0000', to: '#0000ff' },
                background_image: 'assets://a b.png', background_size: 'stretch', background_position: 'top' },
            picture_alone: { background_image: '/x.png' },
            picture_contain_left: { background_image: '/x.png', background_size: 'contain', background_position: 'left' },
            picture_opacity_text_reads_whole: { background_image: '/x.png', background_opacity: 'abc' },
            text: { text_color: '#00ff00', font_size: 1.2, font_weight: 700, border_radius: 0 },
            zeros_skipped: { font_size: 0, letter_spacing: 0, border_width: 0, font_weight: 0, line_height: 0 },
            zeros_written: { border_radius: 0, opacity: 0, transition_duration: 0, margin: 0, padding: 0 },
            align_left_top: { text_align: 'left', vertical_align: 'top' },
            align_right_bottom: { text_align: 'right', vertical_align: 'bottom' },
            align_unknown: { text_align: 'justify', vertical_align: 'middle' },
            align_center: { text_align: 'center', vertical_align: 'center' },
            border_default_style: { border_width: 0.2 },
            border_full: { border_width: 0.3, border_style: 'dashed', border_color: '#abcdef' },
            border_colour_alone_ignored: { border_color: '#abcdef' },
            shadow_none: { box_shadow: 'none' },
            shadow_sm: { box_shadow: 'sm' },
            shadow_md: { box_shadow: 'md' },
            shadow_lg: { box_shadow: 'lg' },
            shadow_inset: { box_shadow: 'inset' },
            shadow_glow_default: { box_shadow: 'glow' },
            shadow_glow_text_colour: { box_shadow: 'glow', text_color: '#ff00ff' },
            shadow_custom: { box_shadow: '1px 2px 3px red' },
            margin_all: { margin: 1 },
            margin_all_vertical: { margin: 1, margin_vertical: 2 },
            margin_all_horizontal: { margin: 1, margin_horizontal: 3 },
            margin_vertical: { margin_vertical: 2 },
            margin_horizontal: { margin_horizontal: 3 },
            margin_both_sides: { margin_vertical: 2, margin_horizontal: 3 },
            padding_all: { padding: 1 },
            padding_all_vertical: { padding: 1, padding_vertical: 2 },
            padding_all_horizontal: { padding: 1, padding_horizontal: 3 },
            padding_vertical: { padding_vertical: 2 },
            padding_horizontal: { padding_horizontal: 3 },
            padding_both_sides: { padding_vertical: 2, padding_horizontal: 3 },
            typography: { text_transform: 'uppercase', letter_spacing: 0.1, line_height: 1.3,
                white_space: 'pre-wrap', transition_duration: 250, overflow: 'hidden', opacity: 0.4 },
            accents: { accent_color: '#123456', track_color: '#654321' },
            // Every key at once, so the order they are written in is pinned too.
            every_key: { bg_color: '#111111', background_gradient: { from: '#ff0000', to: '#0000ff', angle: 90 },
                background_image: '/x.png', text_color: '#222222', font_size: 1, font_weight: 600,
                border_radius: 0.5, text_align: 'right', vertical_align: 'top', border_width: 0.1,
                border_style: 'dotted', border_color: '#333333', box_shadow: 'md', margin: 1,
                margin_vertical: 0, padding_horizontal: 2, text_transform: 'lowercase', letter_spacing: 0.2,
                line_height: 1.1, white_space: 'nowrap', transition_duration: 100, overflow: 'auto',
                opacity: 0.9, accent_color: '#444444', track_color: '#555555' },
        };
        const expected = PAINT_PINS.style;
        const got = {};
        for (const [name, style] of Object.entries(rows)) {
            const node = document.createElement('div');
            paintStyle(node, style);
            got[name] = node.getAttribute('style');
        }
        comparePins('style', got, expected);
    },

    // A faded background picture is a layer of its own, reused on every call
    // and kept under the content, without unplacing anything already placed.
    paint_draws_a_faded_picture_as_one_reused_layer() {
        const node = document.createElement('div');
        node.style.position = 'absolute';
        const plain = node.appendChild(document.createElement('span'));
        const stacked = node.appendChild(document.createElement('span'));
        stacked.style.zIndex = '3';
        const placed = node.appendChild(document.createElement('span'));
        placed.style.position = 'absolute';
        const style = { background_image: 'assets://a.png', background_opacity: 0.5,
            background_size: 'stretch', background_position: 'bottom' };
        paintStyle(node, style);
        paintStyle(node, { ...style, background_opacity: 0 });
        const got = {
            node: node.getAttribute('style'),
            layers: node.querySelectorAll(':scope > .panel-bg-image-layer').length,
            first: node.firstElementChild.className,
            layer: node.querySelector('.panel-bg-image-layer').getAttribute('style'),
            plain: plain.getAttribute('style'),
            stacked: stacked.getAttribute('style'),
            placed: placed.getAttribute('style'),
        };
        const fresh = document.createElement('div');
        paintStyle(fresh, style);
        got.fresh_node = fresh.getAttribute('style');
        paintStyle(fresh, { ...style, background_size: 'contain', background_opacity: 0.3 });
        got.fresh_contain = fresh.querySelector('.panel-bg-image-layer').getAttribute('style');
        comparePins('faded', got, PAINT_PINS.faded);
    },

    // A button's picture: on the button itself, or on an effect layer for a
    // mask, a blend or an opacity, always over the gradient rather than
    // instead of it.
    paint_draws_a_button_picture_in_each_mode() {
        const GRAD = { background_gradient: { from: '#ff0000', to: '#0000ff' } };
        const rows = {
            no_picture: ['', {}, null],
            plain: ['/p.png', {}, null],
            plain_over_gradient: ['/p.png', {}, GRAD],
            plain_fill: ['/p.png', { fit: 'fill' }, null],
            plain_contain: ['/p.png', { fit: 'contain' }, null],
            asset: ['assets://a b.png', {}, null],
            normal_blend_is_plain: ['/p.png', { blend: 'normal' }, null],
            none_blend_is_plain: ['/p.png', { blend: 'none' }, null],
            faded: ['/p.png', { opacity: 0.5 }, null],
            faded_text_opacity: ['/p.png', { opacity: '0.25' }, GRAD],
            mask: ['/p.png', { blend: 'mask', tintColor: '#00ff00' }, null],
            mask_no_tint: ['/p.png', { blend: 'mask' }, GRAD],
            multiply: ['/p.png', { blend: 'multiply', tintColor: '#00ff00', fit: 'fill' }, null],
            screen_no_tint: ['/p.png', { blend: 'screen' }, GRAD],
        };
        const got = {};
        for (const [name, [ref, opts, style]] of Object.entries(rows)) {
            const node = document.createElement('button');
            node.appendChild(document.createTextNode('Words'));
            node.style.maskImage = 'url("/old.png")';
            node.style.isolation = 'isolate';
            if (style) paintStyle(node, style);
            paintPicture(node, ref, opts);
            // A second call replaces the layer rather than stacking one.
            paintPicture(node, ref, opts);
            const layers = node.querySelectorAll(':scope > .panel-button-image-layer');
            got[name] = `${node.getAttribute('style')} | layers=${layers.length}`
                + (layers[0] ? ` | ${layers[0].getAttribute('style')}` : '');
        }
        comparePins('picture', got, PAINT_PINS.picture);
    },

    // Retint touches only a layer whose fill is the tint (a mask or a blend).
    paint_retints_only_a_mask_or_blend_layer() {
        const got = {};
        for (const [name, opts] of Object.entries({
            none: null, mask: { blend: 'mask', tintColor: '#111111' },
            multiply: { blend: 'multiply', tintColor: '#111111' }, faded: { opacity: 0.5 },
        })) {
            const node = document.createElement('button');
            if (opts) paintPicture(node, '/p.png', opts);
            paintTint(node, '#ff0000');
            const layer = node.querySelector('.panel-button-image-layer');
            got[name] = layer ? layer.getAttribute('style') : `no layer: ${node.getAttribute('style')}`;
        }
        comparePins('tint', got, PAINT_PINS.tint);
    },

    // Frameless strips the chrome; a picture drawn after it still composes
    // with a gradient the style named (as it draws today).
    paint_frameless_and_the_state_colour_clear() {
        const got = {};
        let node = document.createElement('button');
        paintStyle(node, { bg_color: '#111111', border_width: 0.2, box_shadow: 'sm',
            background_gradient: { from: '#ff0000', to: '#0000ff' } });
        paintFrameless(node);
        got.frameless = node.getAttribute('style');
        paintPicture(node, '/p.png', {});
        got.frameless_then_picture = node.getAttribute('style');

        const clears = {
            rest_names_nothing: {},
            rest_names_colours: { bg_color: '#010101', text_color: '#020202', opacity: 0.5 },
            rest_names_gradient: { background_gradient: { from: '#ff0000', to: '#0000ff' } },
            rest_opacity_zero: { opacity: 0 },
        };
        for (const [name, rest] of Object.entries(clears)) {
            node = document.createElement('div');
            node.style.backgroundColor = '#aaaaaa';
            node.style.color = '#bbbbbb';
            node.style.opacity = '0.7';
            node.style.fontSize = '2rem';
            clearColours(node, rest);
            got[`clear_${name}`] = node.getAttribute('style');
        }
        clearColours({}, {});   // something with no style is left alone

        // A gradient the node no longer has is not drawn under a later picture.
        node = document.createElement('button');
        paintStyle(node, { background_gradient: { from: '#ff0000', to: '#0000ff' } });
        paintStyle(node, { bg_color: '#222222' });
        paintPicture(node, '/p.png', {});
        got.gradient_gone_then_picture = node.getAttribute('style');
        paintPicture(node, '/p.png', { blend: 'mask' });
        got.gradient_gone_then_layer = node.getAttribute('style');
        comparePins('frameless', got, PAINT_PINS.frameless);
    },

    paint_sanitizes_and_resolves_as_it_does_today() {
        const app = mkApp();
        const got = {
            value_number: sanitizeValue(12),
            value_null: sanitizeValue(null),
            value_undefined: sanitizeValue(undefined),
            value_tricks: sanitizeValue('expression(x) javascript:y behavior:z @import /*c*/ image-set(a) \\e\r\nf'),
            url_number: sanitizeUrl(5),
            url_ftp: sanitizeUrl('ftp://x/y.png'),
            url_vbscript: sanitizeUrl(' VBScript:x'),
            url_javascript_spaced: sanitizeUrl('  javascript:alert(1)'),
            url_data_image: sanitizeUrl('data:image/png;base64,AAA='),
            url_upper_http: sanitizeUrl('HTTP://x/y (1).png'),
            url_relative: sanitizeUrl("a b'c\\d.png"),
            asset_empty: assetUrl(''),
            asset_null: assetUrl(null),
            asset_plain: assetUrl('http://x/y.png'),
            asset_ref: assetUrl('assets://a b#c.png'),
        };
        assert(app, 'built');
        comparePins('sanitize', got, PAINT_PINS.sanitize);
    },

    // A page's own background: its colour, its picture as a faded layer, and
    // its gradient as a layer over the picture.
    paint_draws_a_page_background_as_it_does_today() {
        const rows = {
            colour_only: { color: '#101010' },
            picture: { image: 'assets://a b.png', image_size: 'contain', image_position: 'top' },
            picture_faded: { image: '/p.png', image_opacity: 0.4 },
            picture_opacity_zero: { image: '/p.png', image_opacity: 0 },
            picture_opacity_text: { image: '/p.png', image_opacity: 'abc' },
            gradient_default_angle: { gradient: { from: '#ff0000', to: '#0000ff' } },
            gradient_angle_zero: { gradient: { from: '#ff0000', to: '#0000ff', angle: 0 } },
            gradient_angle_45: { gradient: { from: '#00ff00', to: '#0000ff', angle: 45 } },
            gradient_needs_both: { color: '#202020', gradient: { from: '#ff0000' } },
            everything: { color: '#303030', image: '/p.png', image_opacity: 0.5,
                gradient: { from: '#ff0000', to: '#0000ff', angle: 90 } },
        };
        const got = {};
        for (const [name, background] of Object.entries(rows)) {
            const app = mkApp();
            renderProject(app, project({ elements: [], background }));
            const grid = app.root.querySelector('.panel-page-bg-image, .panel-page-bg-gradient')?.parentElement
                || app.root.querySelector('[style*="background-color"]');
            const layers = grid ? [...grid.children]
                .filter((c) => /panel-page-bg-/.test(c.className))
                .map((c) => `${c.className}: ${c.getAttribute('style')}`) : [];
            got[name] = [grid ? `grid: ${grid.getAttribute('style')}` : 'no grid', ...layers];
        }
        comparePins('page', got, PAINT_PINS.page);
    },

    // --- a button's face after its look or toggle, every branch ------------

    button_face_after_feedback_and_toggle_looks() {
        const PIXEL = '/p.png';
        const mount = (def, state) => {
            const app = mkApp();
            renderProject(app, project({
                elements: [def], placements: { [def.id]: { x: 5, y: 5, w: 20, h: 10 } },
            }));
            app.state = state;
            app.evaluateAllBindings(Object.keys(state));
            return { app, el: app.root.querySelector(`[data-element-id="${def.id}"]`) };
        };
        const set = (m, key, value) => { m.app.state[key] = value; m.app.evaluateAllBindings([key]); };
        const face = (el) => {
            const layer = el.querySelector(':scope > .panel-button-image-layer');
            const kids = [...el.children].map((c) => `${c.tagName.toLowerCase()}.${c.getAttribute('class') || ''}`
                + (c.tagName === 'svg' ? `[${c.querySelector('use')?.getAttribute('href')}|${c.getAttribute('stroke')}]` : '')
                + (c.classList.contains('panel-label-span') ? `"${c.textContent}"` : ''));
            return `${el.getAttribute('style')} | text="${el.textContent}" | ${kids.join(',')}`
                + (layer ? ` | layer: ${layer.getAttribute('style')}` : '')
                + ` | src=${JSON.stringify(el.getAttribute('data-avc-text-src'))}`
                + ` | class="${el.className}" aria-current=${el.getAttribute('aria-current')}`;
        };
        const down = { 'device.d1.connected': false };
        const up = { 'device.d1.connected': true };
        const states = { key: 'device.d1.mode', default_state: 'a', states: {
            a: { label: 'A', bg_color: '#ff0000', icon: 'play', icon_color: '#00ff00' },
            b: { text_color: '#0000ff', button_image: '/b.png' },
        } };
        const legacy = (extra) => ({ key: 'device.d1.mode', condition: { equals: 'on' },
            style_active: { bg_color: '#ff0000', label: 'From style' }, style_inactive: {}, ...extra });
        const rows = [
            ['multi_framed_icon', { type: 'button', label: 'Own', icon: 'stop', frameless: true,
                bindings: { show: { look: states } } }, ['a', 'b', 'zzz']],
            ['multi_suppressed', { type: 'button', label: 'Own', display_mode: 'icon_only', icon: 'stop',
                bindings: { show: { look: states } } }, ['a', 'b']],
            ['multi_image_mode', { type: 'button', label: 'Own', display_mode: 'image', button_image: PIXEL,
                image_blend_mode: 'mask', bindings: { show: { look: states } } }, ['a', 'b']],
            ['multi_no_own_label', { type: 'button', label: '',
                bindings: { show: { look: states } } }, ['b', 'a']],
            ['legacy_words', { type: 'button', label: 'Own', frameless: true, icon: 'stop',
                bindings: { show: { look: legacy({ label_active: 'On!', label_inactive: 'Off!' }) } } }, ['on', 'off']],
            ['legacy_state_icon', { type: 'button', label: 'Own',
                bindings: { show: { look: legacy({ style_active: { icon: 'play', icon_color: '#00ff00' } }) } } },
                ['on', 'off']],
            ['legacy_style_label', { type: 'button', label: 'Own',
                bindings: { show: { look: legacy({}) } } }, ['on', 'off']],
            ['legacy_style_label_rest', { type: 'button', label: 'Own', style: { label: 'Rest' },
                bindings: { show: { look: legacy({}) } } }, ['off', 'on']],
            ['legacy_style_label_empty_and_zero', { type: 'button', label: 'Own',
                bindings: { show: { look: legacy({ style_active: { label: '' }, style_inactive: { label: 0 } }) } } },
                ['on', 'off']],
            ['legacy_suppressed', { type: 'button', label: 'Own', display_mode: 'image', button_image: PIXEL,
                image_blend_mode: 'multiply', bindings: { show: { look: legacy({ label_active: 'On!' }) } } }, ['on', 'off']],
            ['offline_full', { type: 'button', label: 'Own', icon: 'stop', frameless: true, button_image: PIXEL,
                image_blend_mode: 'mask', style: { bg_color: '#123456' }, bindings: { show: { look: states } } }, ['a']],
            ['offline_suppressed', { type: 'button', label: 'Own', display_mode: 'image', button_image: PIXEL,
                bindings: { show: { look: states } } }, ['b']],
            ['offline_no_label', { type: 'button', bindings: { show: { look: states } } }, ['a']],
            ['offline_label_zero', { type: 'button', label: 0, bindings: { show: { look: states } } }, ['a']],
            ['multi_icon_only_no_icon', { type: 'button', label: 'Own', display_mode: 'icon_only',
                bindings: { show: { look: states } } }, ['a', 'b']],
            ['state_label_null', { type: 'button', label: 'Own', bindings: { show: { look: { ...states, states: {
                ...states.states, d: { label: null, bg_color: '#00ff00' } } } } } }, ['d', 'a']],
            ['offline_retints_a_state_layer', { type: 'button', label: 'Own', image_blend_mode: 'mask',
                style: { bg_color: '#123456' }, bindings: { show: { look: { ...states, states: {
                    ...states.states, c: { bg_color: '#ff0000', button_image: '/c.png' } } } } } }, ['c']],
            ['state_artwork_layer_goes', { type: 'button', label: 'Own', image_blend_mode: 'multiply',
                bindings: { show: { look: states } } }, ['b', 'a']],
            ['nav_lit_icon', { type: 'page_nav', label: 'Go', target_page: 'main', highlight_current: true,
                bindings: { show: { look: states } } }, ['a', 'b', 'zzz']],
            ['nav_unlit_own_icon', { type: 'page_nav', target_page: 'elsewhere', icon: 'stop', highlight_current: true,
                style: { accent_color: '#00ffff' }, bindings: { show: { look: states } } }, ['b', 'a']],
            ['nav_no_words', { type: 'page_nav', bindings: { show: { look: states } } }, ['b']],
            ['offline_nav', { type: 'page_nav', label: 'Go', target_page: 'main', highlight_current: true,
                bindings: { show: { look: states } } }, ['a']],
        ];
        const got = {};
        for (const [name, def, values] of rows) {
            const m = mount({ id: name, ...def }, { ...up, 'device.d1.mode': values[0] });
            const seen = [face(m.el)];
            for (const v of values.slice(1)) { set(m, 'device.d1.mode', v); seen.push(face(m.el)); }
            if (name.startsWith('offline')) { set(m, 'device.d1.connected', false); seen.push(face(m.el)); }
            got[name] = seen;
        }
        const toggle = (extra) => ({ do: { press: [{ action: 'state.set', key: 'var.on', value: true,
            mode: 'toggle', toggle_key: 'var.on', toggle_value: true, ...extra }] } });
        const toggles = [
            ['toggle_words_icon', { type: 'button', label: 'Own', icon: 'stop', bindings: toggle({ on_label: 'ON', off_label: '' }) }],
            ['toggle_no_own_label', { type: 'button', bindings: toggle({ on_label: '', off_label: 'OFF' }) }],
            ['toggle_suppressed', { type: 'button', label: 'Own', display_mode: 'icon_only', icon: 'stop',
                bindings: toggle({ on_label: 'ON' }) }],
            ['toggle_frameless_picture', { type: 'button', label: 'Own', frameless: true, display_mode: 'image_text',
                button_image: PIXEL, image_blend_mode: 'mask', bindings: toggle({}) }],
            ['toggle_image_mode_words', { type: 'button', label: 'Own', display_mode: 'image', button_image: PIXEL,
                bindings: toggle({ on_label: 'ON', off_label: 'OFF' }) }],
            ['toggle_own_label_zero', { type: 'button', label: 0, bindings: toggle({ on_label: 'ON', off_label: '' }) }],
        ];
        for (const [name, def] of toggles) {
            const m = mount({ id: name, ...def }, { 'var.on': false });
            const seen = [face(m.el)];
            set(m, 'var.on', true); seen.push(face(m.el));
            set(m, 'var.on', false); seen.push(face(m.el));
            got[name] = seen;
        }
        // Plain buttons, as renderButton draws them with no look: each display
        // mode, with and without an icon, and a tinted picture.
        const plain = { type: 'button', label: 'Own', button_image: PIXEL, image_blend_mode: 'mask',
            style: { bg_color: '#123456' } };
        for (const mode of ['text', 'image', 'image_text', 'icon_only', 'bogus']) {
            for (const icon of [null, 'stop']) {
                const def = { id: `plain_${mode}_${icon || 'no_icon'}`, ...plain, display_mode: mode };
                if (icon) def.icon = icon;
                got[def.id] = [face(mount(def, {}).el)];
            }
        }

        // A theme value CSS ignores: what a state or a toggle drew is dropped
        // against the button's own style, so it does not outlive the state.
        const badTheme = { button: { text_color: 'not-a-colour', bg_color: 'nope', opacity: 'x' } };
        const themed = (def, state) => {
            const app = mkApp();
            renderProject(app, project({
                elements: [def], placements: { [def.id]: { x: 5, y: 5, w: 20, h: 10 } },
            }));
            app.themeElementDefaults = badTheme;
            app.state = state;
            app.evaluateAllBindings(Object.keys(state));
            return { app, el: app.root.querySelector(`[data-element-id="${def.id}"]`) };
        };
        let t = themed({ id: 'offline_ignored_theme_colour', type: 'button', label: 'Own', bindings: { show: { look: {
            key: 'device.d1.mode', default_state: 'a',
            states: { a: { text_color: '#ff0000', bg_color: '#00ff00', opacity: 0.5 } } } } } },
            { ...up, 'device.d1.mode': 'a' });
        got.offline_ignored_theme_colour = [face(t.el)];
        set(t, 'device.d1.connected', false);
        got.offline_ignored_theme_colour.push(face(t.el));
        t = themed({ id: 'toggle_ignored_theme_colour', type: 'button', label: 'Own', bindings: toggle({}) },
            { 'var.on': false });
        got.toggle_ignored_theme_colour = [face(t.el)];
        set(t, 'var.on', true); got.toggle_ignored_theme_colour.push(face(t.el));
        set(t, 'var.on', false); got.toggle_ignored_theme_colour.push(face(t.el));

        // A toggle binding reached directly, with no `_labelBase` on it.
        const m = mount({ id: 'direct', type: 'button', label: 'Own',
            bindings: toggle({ on_label: 'ON' }) }, { 'var.on': false });
        const b = m.app.bindings.find((x) => x.elementDef?.id === 'direct' && x.type === 'toggle_look');
        if (b) {
            delete b.binding._labelBase;
            m.app.state['var.on'] = true;
            m.app.editMode = true;      // where the words' source is recorded
            m.app.evaluateToggleLook(b);
            got.toggle_without_label_base = [face(m.el)];
        } else {
            got.toggle_without_label_base = ['no toggle_look binding'];
        }
        assert(down, 'unused');
        comparePins('face', got, PAINT_PINS.face);
    },

    // What a slider draws, follows and sends, across its settings: the scale
    // (range, step, response, output range and dead space), the readout, the
    // design sample, the unknown form, the drag and the rate it streams at.
    level_slider_draws_follows_and_sends_as_today() {
        const S = levelDef('slider');
        const cases = [
            [S('plain'), LIVE_WITH_READING],
            [S('vertical_log', {
                orientation: 'vertical', response: 'logarithmic', response_db_range: 40,
                min: -80, max: 0, step: 0.5, unit: 'dB', display_decimals: 2, thumb_size: 3,
                label: 'Gain', style: { show_value: true },
            }), LIVE_WITH_READING],
            [S('log_default_range', { response: 'logarithmic', response_db_range: 0, style: { show_value: true } }), LIVE_WITH_READING],
            [S('scaled', { output_min: 0, output_max: 255, style: { show_value: true } }), LIVE_WITH_READING],
            [S('scaled_flat_output', { output_min: 7, output_max: 7, style: { show_value: true } }), LIVE_WITH_READING],
            [S('dead_space', { output_min: 20, output_max: 80, scale_to_full: false, style: { show_value: true } }), LIVE_WITH_READING],
            [S('dead_space_log', {
                output_min: -60, output_max: -10, scale_to_full: false, min: -80, max: 0,
                response: 'logarithmic', style: { show_value: true },
            }), LIVE_WITH_READING],
            [S('dead_space_design', { output_min: 20, output_max: 80, scale_to_full: false, style: { show_value: true } }), { edit: true }],
            [S('sample_design', { min: -10, max: 30, unit: 'V', style: { show_value: true } }), { edit: true }],
            [S('unreported', { unit: '%', style: { show_value: true } }), LIVE_NO_READING],
            [S('fraction', { min: 0, max: 1, step: 0.1, style: { show_value: true } }), LIVE_WITH_READING],
            [S('no_step', { min: 0, max: 1, step: 0, style: { show_value: true } }), LIVE_WITH_READING],
            [S('on_release', { send_on_release: true, send_throttle_ms: 0 }), LIVE_WITH_READING],
            [S('every_move', { send_throttle_ms: 0 }), LIVE_WITH_READING],
            [S('rate_text', { send_throttle_ms: '30' }), LIVE_WITH_READING],
            [S('backwards', { min: 100, max: 0, style: { show_value: true } }), LIVE_WITH_READING],
            [S('flat', { min: 5, max: 5, style: { show_value: true } }), LIVE_WITH_READING],
            [S('flat_log_overflow', {
                min: 5, max: 5, response: 'logarithmic', response_db_range: 10000, style: { show_value: true },
            }), LIVE_WITH_READING],
            [S('text_range', { min: '10', max: '20', style: { show_value: true } }), LIVE_WITH_READING],
            [S('decimals_text', { display_decimals: '3', unit: '%', style: { show_value: true } }), LIVE_WITH_READING],
            [S('unbound', { bindings: {}, style: { show_value: true } }), LIVE_WITH_READING],
            [S('unbound_design', { bindings: {}, style: { show_value: true } }), { edit: true }],
            [S('nonsense_range', { min: 'low', style: { show_value: true } }), LIVE_WITH_READING],
            [S('unbound_range', { bindings: {}, min: -40, max: 40, style: { show_value: true } }), LIVE_WITH_READING],
            [S('nothing_in_state', { min: -40, max: 40, style: { show_value: true } }), {}],
            [S('text_range_scaled', { min: '10', max: '20', output_min: 0, output_max: 100, style: { show_value: true } }), LIVE_WITH_READING],
            [S('text_range_design', { min: '10', max: '20', style: { show_value: true } }), { edit: true }],
            [S('log_vanishing_range', { response: 'logarithmic', response_db_range: 1e-300, style: { show_value: true } }), LIVE_WITH_READING],
        ];
        const got = {};
        for (const [def, opts] of cases) {
            withFakeClock((advance) => levelCase(got, def, opts, (m) => {
                const input = m.node().querySelector('input');
                const steps = Number(input.max);
                const put = (pos, type) => {
                    input.value = String(pos);
                    input.dispatchEvent(new window.Event(type));
                };
                // A drag: three moves inside the rate, an echo mid-drag, the rate passing, the release.
                input.dispatchEvent(new window.Event('pointerdown'));
                put(steps * 0.25, 'input'); m.look(); advance(10);
                put(steps * 0.5, 'input'); m.look(); advance(10);
                m.set(LEVEL_KEY, 10); m.look();
                m.set('device.d.connected', false); m.look();
                m.set('device.d.connected', true); m.look();
                put(steps * 0.75, 'input'); m.look();
                advance(500); m.look();
                put(steps * 0.75, 'change');
                input.dispatchEvent(new window.Event('pointerup'));
                m.look();
                m.set(LEVEL_KEY, 12); m.look();
                // Two moves 60 ms apart: inside the slider's default rate, outside the fader's.
                put(steps * 0.4, 'input'); advance(60);
                put(steps * 0.6, 'input'); m.look();
                advance(500); put(steps * 0.6, 'change'); m.look();
                // A tap, and a release that differs from what the stream sent.
                put(1, 'input'); put(1, 'change'); m.look();
                put(2, 'input'); put(3, 'change'); m.look();
                // Travel to both ends, past any output limit.
                put(0, 'input'); advance(500); put(steps, 'input'); advance(500); m.look();
                // Each way a drag ends lets a reading through again.
                ['pointercancel', 'blur', 'touchend', 'touchcancel'].forEach((end, i) => {
                    input.dispatchEvent(new window.Event('pointerdown'));
                    m.set(LEVEL_KEY, 20 + 2 * i);
                    input.dispatchEvent(new window.Event(end));
                    m.set(LEVEL_KEY, 21 + 2 * i);
                    m.look();
                });
                // A page redrawn while a move is held: the held move never goes out.
                advance(500);
                put(steps * 0.3, 'input'); advance(5);
                put(steps * 0.7, 'input');
                m.app.renderCurrentPage();
                advance(500);
                m.steps.push({ s: m.sent.splice(0).map(frameLine) });
            }));
        }
        comparePins('level_slider', got, LEVEL_PINS.level_slider);
    },

    // The reading a slider or fader is first drawn with, before any binding
    // has been evaluated: its device's, the design sample, or none.
    level_controls_draw_their_first_reading_as_today() {
        const got = {};
        const defs = [
            levelDef('slider')('slider_scaled', { min: -40, max: 40, output_min: 0, output_max: 255, style: { show_value: true } }),
            levelDef('slider')('slider_text_range', { min: '10', max: '20', style: { show_value: true } }),
            levelDef('fader')('fader_scaled', { min: -80, max: 0, unit: 'dB', output_min: 0, output_max: 100 }),
            levelDef('fader')('fader_dead_space', { output_min: 20, output_max: 80, scale_to_full: false }),
        ];
        for (const def of defs) {
            for (const [name, state, edit] of [
                ['reading', { [LEVEL_KEY]: 30 }, false], ['none', {}, false], ['design', {}, true],
            ]) {
                const app = mkApp();
                app.editMode = edit;
                app.state = state;
                const { node } = drawnLevel(app, def);
                const input = node.querySelector('input');
                got[`${def.id}/${name}`] = { c: nodeSnapshot(node), p: input ? input.value : null };
            }
        }
        comparePins('level_first_draw', got, LEVEL_PINS.level_first_draw);
    },

    // What a fader draws, follows and sends, the same way, plus its scale
    // marks, its dead-space bands, the drag by mouse and touch, the arrow keys,
    // a drag on an unreachable device and a page redrawn mid-drag.
    level_fader_draws_follows_and_sends_as_today() {
        const F = levelDef('fader');
        const cases = [
            [F('plain'), LIVE_WITH_READING],
            [F('db_log', {
                min: -80, max: 0, step: 0.5, unit: 'dB', response: 'logarithmic',
                response_db_range: 40, label: 'Gain',
            }), LIVE_WITH_READING],
            [F('log_default_range', { response: 'logarithmic', response_db_range: -3 }), LIVE_WITH_READING],
            [F('horizontal', { orientation: 'horizontal', display_decimals: 0, unit: '%' }), LIVE_WITH_READING],
            [F('scaled', { output_min: 0, output_max: 255 }), LIVE_WITH_READING],
            [F('scaled_flat_output', { output_min: 7, output_max: 7 }), LIVE_WITH_READING],
            [F('dead_space', { output_min: 20, output_max: 80, scale_to_full: false }), LIVE_WITH_READING],
            [F('dead_space_horizontal', {
                orientation: 'horizontal', output_min: 20, output_max: 80, scale_to_full: false,
            }), LIVE_WITH_READING],
            [F('dead_space_log', {
                output_min: -60, output_max: -10, scale_to_full: false, min: -80, max: 0,
                response: 'logarithmic',
            }), LIVE_WITH_READING],
            [F('dead_space_top_only', { output_min: 0, output_max: 60, scale_to_full: false }), LIVE_WITH_READING],
            [F('dead_space_design', { output_min: 20, output_max: 80, scale_to_full: false }), { edit: true }],
            [F('sample_design', { min: -10, max: 30, unit: 'V' }), { edit: true }],
            [F('unreported', { unit: 'dB', min: -80, max: 0 }), LIVE_NO_READING],
            [F('fraction', { min: 0, max: 1, step: 0.1 }), LIVE_WITH_READING],
            [F('no_step', { min: 0, max: 1, step: 0 }), LIVE_WITH_READING],
            [F('on_release', { send_on_release: true, send_throttle_ms: 0 }), LIVE_WITH_READING],
            [F('every_move', { send_throttle_ms: 0 }), LIVE_WITH_READING],
            [F('backwards', { min: 100, max: 0 }), LIVE_WITH_READING],
            [F('flat', { min: 5, max: 5 }), LIVE_WITH_READING],
            [F('text_range', { min: '-20', max: '20' }), LIVE_WITH_READING],
            [F('no_value_no_scale', { style: { show_value: false, show_scale: false } }), LIVE_WITH_READING],
            [F('unbound', { bindings: {} }), LIVE_WITH_READING],
            [F('odd_range', { min: -73, max: 7 }), LIVE_WITH_READING],
            [F('wide_db', { min: -120, max: 0, unit: 'dB' }), LIVE_WITH_READING],
            [F('off_step_range', { min: 0.3, max: 10.3, step: 1 }), LIVE_WITH_READING],
            [F('nothing_in_state', { min: -40, max: 40 }), {}],
            [F('nonsense_range', { min: 'low' }), LIVE_WITH_READING],
            [F('log_vanishing_range', { response: 'logarithmic', response_db_range: 1e-300 }), LIVE_WITH_READING],
        ];
        const got = {};
        for (const [def, opts] of cases) {
            withFakeClock((advance) => levelCase(got, def, opts, (m) => {
                const wrap = m.node().querySelector('.fader-track-wrap');
                const handle = m.node().querySelector('.fader-handle');
                wrap.getBoundingClientRect = () => ({ left: 10, top: 20, width: 200, height: 100, right: 210, bottom: 120 });
                const mouse = (target, type, x, y) => target.dispatchEvent(new window.MouseEvent(type,
                    { clientX: x, clientY: y, bubbles: true, cancelable: true }));
                const touch = (target, type, x, y) => {
                    const e = new window.Event(type, { bubbles: true, cancelable: true });
                    if (x !== undefined) e.touches = [{ clientX: x, clientY: y }];
                    target.dispatchEvent(e);
                };
                const key = (k) => handle.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, cancelable: true }));
                // An arrow key with no reading starts from the bottom of the range.
                m.set(LEVEL_KEY, null); key('ArrowUp'); m.look(); advance(100);
                // A mouse drag: moves inside the rate, an echo mid-drag, past the track, the release.
                mouse(wrap, 'mousedown', 60, 45); m.look(); advance(10);
                mouse(document, 'mousemove', 110, 70); m.look(); advance(10);
                m.set(LEVEL_KEY, -3); m.look();
                mouse(document, 'mousemove', 300, 0); m.look();
                advance(500); m.look();
                mouse(document, 'mousemove', 300, 200); m.look(); advance(500);
                mouse(document, 'mouseup', 300, 200); m.look();
                mouse(document, 'mousemove', 50, 50); m.look();
                m.set(LEVEL_KEY, 12); m.look();
                // A touch drag to the far corner.
                touch(wrap, 'touchstart', 20, 110); m.look(); advance(5);
                touch(document, 'touchmove', 150, 30); m.look(); advance(500);
                touch(document, 'touchend'); m.look();
                // The arrow keys, then a run to the top and back down past the floor.
                for (const k of ['ArrowUp', 'ArrowRight', 'ArrowDown', 'ArrowLeft', 'Enter']) { key(k); m.look(); advance(100); }
                for (let i = 0; i < 40; i++) key('ArrowUp');
                advance(1000); m.look();
                for (let i = 0; i < 300; i++) key('ArrowDown');
                advance(1000); m.look();
                // The device going away and back while the handle is held draws nothing under the finger.
                mouse(wrap, 'mousedown', 80, 50); m.look();
                m.set('device.d.connected', false); m.look();
                m.set('device.d.connected', true); m.look();
                mouse(document, 'mouseup', 80, 50); m.look();
                // A drag on a device that has gone away puts the readout back to unknown.
                m.set('device.d.connected', false);
                mouse(wrap, 'mousedown', 100, 60); mouse(document, 'mouseup', 100, 60); m.look();
                m.set('device.d.connected', true);
                // The page redrawn mid-drag: the release goes out, the old drag listens no more.
                mouse(wrap, 'mousedown', 150, 40);
                m.app.renderCurrentPage();
                mouse(document, 'mousemove', 30, 30); mouse(document, 'mouseup', 30, 30);
                m.steps.push({ s: m.sent.splice(0).map(frameLine) });
                // A page redrawn with no drag in progress sends nothing.
                m.app.renderCurrentPage();
                m.steps.push({ s: m.sent.splice(0).map(frameLine) });
            }));
        }
        comparePins('level_fader', got, LEVEL_PINS.level_fader);
    },

    // The frame pins: a custom control and a plugin panel drawn, opened,
    // spoken to and redrawn, every case compared whole with
    // panel_frame_pins.json (FRAME_PINS above says how they were recorded).
    async frames_custom_control_draws_opens_and_bridges_as_today() {
        const got = {};
        const themed = document.documentElement.style;
        themed.setProperty('--panel-bg', '#123456');
        themed.setProperty('--panel-border-radius', '6px');
        try {
            await frameCase(got, 'granted', customDef('cc', { grant: FRAME_GRANT, custom_config: { room: '204' }, style: { bg_color: '#222' } }));
            await frameCase(got, 'no_grant', customDef('cc'));
            await frameCase(got, 'odd_grant', customDef('cc', { grant: FRAME_ODD_GRANT }));
            await frameCase(got, 'designer', customDef('cc', { grant: FRAME_GRANT }), { edit: true });
            await frameCase(got, 'saved_version', customDef('cc', { custom_file: 'a b/c#d.html' }), {
                before: (app) => { app._uiFilesVersion = 'v 7'; },
            });
            for (const [i, file] of ['', '/x.html', '../x.html', 'a\\b.html', 'x..y.html', null].entries()) {
                await frameCase(got, `bad_file_${i}`, customDef('cc', { custom_file: file, style: { bg_color: '#222' } }));
            }
            await frameCase(got, 'missing_file', customDef('cc'), { answer: () => ({ ok: false, status: 404 }) });
            await frameCase(got, 'unreachable_file', customDef('cc'), { answer: () => { throw new Error('offline'); } });
            await frameCase(got, 'custom_page', null, {
                page: {
                    id: 'pg', name: 'P', page_type: 'page', render_mode: 'custom', custom_file: 'dialog/index.html',
                    custom_config: { a: 1 }, grant: { devices: ['dsp1'], navigate: true }, elements: [],
                    layouts: [{ id: 'landscape', orientation: 'landscape', primary: true, placements: {}, hidden: [] }],
                },
            });
        } finally {
            themed.removeProperty('--panel-bg');
            themed.removeProperty('--panel-border-radius');
        }
        comparePins('frames_custom', got, FRAME_PINS.frames_custom);
    },

    async frames_plugin_panel_draws_opens_and_bridges_as_today() {
        const got = {};
        const withExt = (answer) => ({ before: (app) => { app._pluginExtensions = PLUGIN_EXTENSIONS; }, answer });
        await frameCase(got, 'plain', pluginDef('pl', { grant: FRAME_GRANT, plugin_config: { a: 1 }, style: { bg_color: '#222' } }));
        await frameCase(got, 'no_grant', pluginDef('pl'));
        await frameCase(got, 'extended', pluginDef('pl', { grant: FRAME_GRANT }), withExt(
            (url) => ({ ok: true, status: 200, json: async () => (url.endsWith('/ext-token') ? { token: 'tok-1' } : {}) })));
        await frameCase(got, 'one_feature', pluginDef('pl'), {
            before: (app) => { app._pluginExtensions = { p1: { panel: { allow_features: ['fullscreen'] } } }; },
        });
        // The token is asked of the panel, so whatever answers there is what the frame gets.
        await frameCase(got, 'token_from_the_panel', pluginDef('pl'), {
            before: (app) => {
                app._pluginExtensions = PLUGIN_EXTENSIONS;
                app._fetchPluginExtToken = async (id) => `panel-token-${id}`;
            },
        });
        await frameCase(got, 'token_refused', pluginDef('pl'), withExt(() => ({ ok: false, status: 401, json: async () => ({}) })));
        await frameCase(got, 'token_unreachable', pluginDef('pl'), withExt(() => { throw new Error('offline'); }));
        await frameCase(got, 'token_empty', pluginDef('pl'), withExt(() => ({ ok: true, status: 200, json: async () => ({ token: '' }) })));
        await frameCase(got, 'token_after_removal', pluginDef('pl'), {
            before: (app) => { app._pluginExtensions = PLUGIN_EXTENSIONS; },
            answer: () => new Promise((resolve) => { pendingToken = resolve; }),
        }, {
            async afterLoad(r) {
                r.window(false);
                pendingToken({ ok: true, status: 200, json: async () => ({ token: 'late' }) });
                await settle();
                r.window();
            },
        });
        await frameCase(got, 'state_during_token', pluginDef('pl', { grant: FRAME_GRANT }), {
            before: (app) => { app._pluginExtensions = PLUGIN_EXTENSIONS; },
            answer: (url) => (url.endsWith('/ext-token')
                ? new Promise((resolve) => { pendingToken = resolve; })
                : { ok: true, status: 200, json: async () => ({}) }),
        }, {
            async afterLoad(r) {
                r.app.state = { 'device.dsp1.mute': 'during', 'plugin.p1.x': 'during' };
                pendingToken({ ok: true, status: 200, json: async () => ({ token: 'held' }) });
                await settle();
            },
        });
        await frameCase(got, 'designer', pluginDef('pl', { grant: FRAME_GRANT, style: { bg_color: '#222' } }), { edit: true });
        for (const [i, ids] of [{ plugin_id: 'p 1' }, { plugin_type: 'pa/nel' }, { plugin_id: '' }, { plugin_type: null }].entries()) {
            await frameCase(got, `unconfigured_${i}`, pluginDef('pl', { ...ids, style: { bg_color: '#222' } }));
        }
        // The theme's defaults for each type reach the frame and its placeholders.
        const themed = mkApp();
        themed.themeElementDefaults = {
            custom: { bg_color: '#0a0b0c', border_radius: 3 }, plugin: { bg_color: '#0d0e0f', opacity: 0.9 },
        };
        const designer = () => {
            themed.editMode = true;
            try { return themed.renderPluginElement(pluginDef('p2', { style: { text_color: '#fff' } })); } finally { themed.editMode = false; }
        };
        got.themed = [
            themed.renderCustomElement(customDef('c1', { style: { text_color: '#fff' } })),
            themed.renderCustomElement(customDef('c2', { custom_file: '' })),
            themed.renderPluginElement(pluginDef('p1')),
            designer(),
        ].map(nodeSnapshot);
        // The extension table loaded at start: which entries it keeps, and a
        // failed load leaving what was there.
        const app = mkApp();
        const saved = { fetch: window.fetch, warn: window.console.warn };
        const warned = [];
        window.console.warn = (...a) => warned.push(a.map(String).join(' '));
        const answers = [
            async () => ({ ok: true, json: async () => ({ extensions: { panel_elements: [
                { plugin_id: 'p1', type: 'panel', ext_auth: true }, { plugin_id: 'p1', type: 'mini' },
                { plugin_id: 'p2', type: 'panel', sandbox_permissions: ['allow-forms'] },
                { type: 'orphan' }, { plugin_id: 'p3' }, { plugin_id: 'p1', type: 'panel', allow_features: ['camera'] },
            ] } }) }),
            async () => ({ ok: false, json: async () => ({ extensions: { panel_elements: [{ plugin_id: 'x', type: 'y' }] } }) }),
            async () => { throw new Error('offline'); },
            async () => ({ ok: true, json: async () => ({}) }),
            async () => ({ ok: true, json: async () => ({ extensions: {} }) }),
        ];
        try {
            for (const [i, answer] of answers.entries()) {
                const urls = [];
                window.fetch = (url) => { urls.push(String(url)); return answer(); };
                await app._loadPluginExtensions();
                got[`extensions_${i}`] = { urls, table: JSON.parse(JSON.stringify(app._pluginExtensions)), warned: warned.splice(0) };
            }
        } finally {
            window.fetch = saved.fetch;
            window.console.warn = saved.warn;
        }
        comparePins('frames_plugin', got, FRAME_PINS.frames_plugin);
    },

    // Where the browser reports a frame's own status, the file check reads it
    // instead of asking again; a status of 0 still asks.
    async frames_read_the_status_of_their_own_request_as_today() {
        const got = {};
        for (const status of [0, 200, 399, 400, 404, 500]) {
            await withStatusTimeline(async (deliver) => {
                await frameCase(got, `status_${status}`, customDef('cc'), {
                    answer: () => ({ ok: false, status: 410 }),
                }, {
                    async afterLoad(r, node, iframe) {
                        const src = iframe.src;
                        deliver([
                            { initiatorType: 'img', name: src, responseStatus: 404 },
                            { initiatorType: 'iframe', name: `${src}?other`, responseStatus: 404 },
                            { initiatorType: 'iframe', name: src, responseStatus: status },
                        ]);
                        await settle();
                        deliver([{ initiatorType: 'iframe', name: src, responseStatus: 404 }]);
                        await settle();
                        got[`status_${status}_strip`] = node.querySelector('.panel-iframe-fault')?.textContent ?? null;
                    },
                });
            });
        }
        await withStatusTimeline(async () => {
            await frameCase(got, 'no_status_on_the_timeline', customDef('cc'), { answer: () => ({ ok: false, status: 404 }) });
        }, { noStatus: true });
        await withStatusTimeline(async (deliver) => {
            await frameCase(got, 'redrawn_before_the_status', customDef('cc'), { answer: () => ({ ok: true, status: 200 }) }, {
                async afterLoad(r, node, iframe) {
                    const src = iframe.src;
                    r.app.renderCurrentPage();
                    await settle();
                    r.take();
                    deliver([{ initiatorType: 'iframe', name: src, responseStatus: 404 }]);
                    await settle();
                    got.redrawn_strips = [node, r.node()].map((n) => n.querySelector('.panel-iframe-fault')?.textContent ?? null);
                },
            });
        });
        await withStatusTimeline(async (deliver) => {
            await frameCase(got, 'observer_refused', customDef('cc'), { answer: () => ({ ok: false, status: 404 }) });
        }, { throwsOnObserve: true });
        comparePins('frames_status', got, FRAME_PINS.frames_status);
    },

    // The choice pins: a list, a dropdown and a text field drawn on a page,
    // taken through readings, the device going away and back, a person's
    // picks and typing, a refused press and a page redraw, compared whole with
    // panel_choice_pins.json (CHOICE_PINS below says how they were recorded).
    choices_draw_show_and_send_as_today() {
        const got = {};
        const K = CHOICE_KEY;
        const opts = [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Bravo' }, { value: 'c' }];
        const look = { key: K, style_map: { a: { bg_color: '#ff0000', text_color: '#ffffff' }, c: { bg_color: '#00ff00' }, b: {} } };
        const pick = (m, value, ms) => {
            const sel = m.node().querySelector('select');
            sel.value = value;
            sel.dispatchEvent(new window.Event('change'));
            m.advance(ms);
        };
        const type = (m, text, ms) => {
            const input = m.node().querySelector('input');
            input.value = text;
            input.dispatchEvent(new window.Event('input'));
            m.advance(ms);
        };
        const tap = (m, index) => {
            const row = m.node().querySelectorAll('.list-item')[index];
            if (row) row.click();
            m.look();
        };
        const cases = [
            ['select_plain', { type: 'select', label: 'Input', options: opts, bindings: { show: { value: { key: K } } } }, (m) => {
                pick(m, 'b', 50); pick(m, 'c', 100); m.look();
                pick(m, 'a', 99); m.look(); m.advance(1); m.look();
                pick(m, 'b', 20); m.app.renderCurrentPage(); m.advance(200); m.look();
                m.set(K, 'c'); m.app._redrawElement('x'); m.look();
            }],
            ['select_look', { type: 'select', options: opts, bindings: { show: { value: { key: K }, look } } }, (m) => {
                pick(m, 'a', 100); m.look();
            }],
            ['select_look_no_key', { type: 'select', options: opts, bindings: { show: { look: { style_map: look.style_map } } } }, () => {}],
            ['select_look_only', { type: 'select', options: opts, bindings: { show: { look: { key: 'var.scene', style_map: look.style_map } } } }, (m) => {
                for (const v of ['a', 'b', 'zz', null, 'c']) { m.set('var.scene', v); m.look(); }
            }],
            ['select_empty', { type: 'select', bindings: { show: { value: { key: K } } } }, () => {}],
            ['text_plain', { type: 'text_input', label: 'Name', placeholder: 'Type here', bindings: { show: { value: { key: K } } } }, (m) => {
                type(m, 'he', 100); type(m, 'hey', 300); m.look();
                type(m, 'x', 299); m.look(); m.advance(1); m.look();
                type(m, 'y', 10); m.app.renderCurrentPage(); m.advance(400); m.look();
                const input = m.node().querySelector('input');
                input.focus(); m.set(K, 'while typing'); m.look();
                input.blur(); m.set(K, 'after'); m.look();
            }],
            ['text_bare', { type: 'text_input' }, (m) => { type(m, 'q', 300); m.look(); }],
            ['list_selectable', {
                type: 'list', label: 'Rooms', item_height: 2,
                items: [{ value: 'a', label: 'A' }, { value: 0, label: 'Zero' }, { value: '' }, { label: 'NoValue' }, { value: 7 }],
                bindings: { show: { value: { key: K } } },
            }, (m) => { tap(m, 0); tap(m, 4); tap(m, 1); m.set(K, 'a'); m.look(); tap(m, 0); m.app._redrawElement('x'); m.look(); }],
            ['list_multi', {
                type: 'list', list_style: 'multi_select', items: [{ value: 'a' }, { value: 'b' }, { value: 'c' }],
                bindings: { show: { value: { key: K } } },
            }, (m) => { tap(m, 0); tap(m, 1); tap(m, 0); m.set(K, 'c'); m.look(); }],
            ['list_static', {
                type: 'list', list_style: 'static', items: [{ value: 'a' }, { value: 'b' }],
                bindings: { show: { value: { key: K } } },
            }, (m) => { tap(m, 0); m.set(K, 'b'); m.look(); }],
            ['list_action', { type: 'list', list_style: 'action', options: [{ value: 'go', label: 'Go' }] }, (m) => { tap(m, 0); tap(m, 0); }],
            ['list_pattern', {
                type: 'list', items: [{ value: 'none', label: 'None' }],
                bindings: { show: { items: { source: 'state', key_pattern: 'var.zone.*' }, value: { key: K } } },
            }, (m) => {
                m.set('var.zone.one', 'First'); m.look();
                m.set('var.zone.two', 'Second'); m.look();
                m.set(K, 'two'); m.look();
                tap(m, 0);
                m.set('var.zone.one', 'First'); m.look();
                m.app._redrawElement('x'); m.look();
                m.set(K, 'one'); m.look();
                m.set('var.zone.one', 'First'); m.look();
                m.set(K, null); m.set('var.zone.three', 'Third'); m.look();
                m.set('var.zone.', 'Empty'); m.look();
                m.set('var.zone.one', undefined); m.set('var.zone.two', undefined); m.look();
            }],
            ['list_action_pattern', {
                type: 'list', list_style: 'action', bindings: { show: { items: { source: 'state', key_pattern: 'var.act.*' } } },
            }, (m) => { m.set('var.act.a', 'A'); m.set('var.act.b', 'B'); m.look(); tap(m, 0); m.set('var.act.c', 'C'); m.look(); }],
            ['list_multi_pattern', {
                type: 'list', list_style: 'multi_select', bindings: { show: { items: { source: 'state', key_pattern: 'var.mm.*' } } },
            }, (m) => {
                m.set('var.mm.a', 'A'); m.set('var.mm.b', 'B'); m.look();
                tap(m, 0); tap(m, 1); tap(m, 0); m.set('var.mm.c', 'C'); m.look();
            }],
            ['list_static_pattern', {
                type: 'list', list_style: 'static',
                bindings: { show: { items: { source: 'state', key_pattern: 'var.st.*' }, value: { key: K } } },
            }, (m) => { m.set(K, 'a'); m.look(); m.set('var.st.a', 'A'); m.set('var.st.b', 'B'); m.look(); tap(m, 1); }],
            ['list_static_preselected', {
                type: 'list', list_style: 'static', items: [{ value: 'a' }, { value: 'b' }],
                bindings: { show: { value: { key: K } } },
            }, () => {}, { [K]: 'a' }],
            ['list_exact_pattern', {
                type: 'list', bindings: { show: { items: { source: 'state', key_pattern: 'var.solo' } } },
            }, (m) => { m.set('var.solo', 'Only'); m.look(); m.set('var.solo', 5); m.look(); }],
            ['list_items_no_pattern', {
                type: 'list', items: [{ value: 'a' }], bindings: { show: { items: { key: 'var.list' } } },
            }, (m) => { m.set('var.list', 'x'); m.look(); m.set('', 'blank'); m.set('var.list', 'y'); m.look(); }],
            ['list_bare', { type: 'list' }, (m) => { tap(m, 0); }],
        ];
        const scrolled = [];
        const realScroll = window.Element.prototype.scrollIntoView;
        window.Element.prototype.scrollIntoView = function (arg) { scrolled.push(`${this.textContent}|${JSON.stringify(arg)}`); };
        try {
            for (const [name, def, gestures, mountState] of cases) {
                withFakeClock((advance) => {
                    let m;
                    try {
                        m = levelMount({ id: 'x', ...def }, { state: { 'device.d.connected': true, ...mountState } });
                        m.advance = advance;
                        const look = m.look;
                        m.look = () => {
                            look();
                            const step = m.steps[m.steps.length - 1];
                            const sel = m.node().querySelector('select');
                            if (sel) step.v = `${sel.selectedIndex}|${sel.value}`;
                            if (scrolled.length) step.si = scrolled.splice(0);
                            if (m.steps.length === 1) step.bt = m.app.bindings.filter((b) => b.elementDef?.id === 'x').map((b) => b.type);
                        };
                        choiceReadings(m, K);
                        gestures(m);
                    } catch (e) {
                        (m ? m.steps : []).push({ threw: String(e && e.message) });
                    }
                    got[name] = m ? m.steps : [{ threw: 'mount' }];
                });
            }
            // The theme's defaults for each type, on controls drawn on their own.
            const themed = mkApp();
            themed.themeElementDefaults = {
                select: { bg_color: '#010203' }, text_input: { border_color: '#040506' },
                list: { item_bg: '#070809', item_active_bg: '#0a0b0c', bg_color: '#0d0e0f' },
            };
            themed.state = { [K]: 'b' };
            got.themed = [
                themed.renderSelect({ id: 's', type: 'select', options: opts, style: { text_color: '#fff' } }),
                themed.renderTextInput({ id: 't', type: 'text_input', style: { font_size: 12 } }),
                themed.renderList({ id: 'l', type: 'list', items: opts, style: { item_bg: '#123123' }, bindings: { show: { value: { key: K } } } }),
                themed.renderList({ id: 'l2', type: 'list', items: opts }),
            ].map(nodeSnapshot);
            got.themed_scrolled = scrolled.splice(0);
        } finally {
            window.Element.prototype.scrollIntoView = realScroll;
        }
        comparePins('choices', got, CHOICE_PINS.choices);
    },
};

// --- The frames (custom controls and plugin panels), as a page draws them ---
//
// The frame pins drive a custom control or a plugin panel the way a page and
// the page inside the frame do: drawn on a page, the frame's load, messages
// from the frame, state pushed to it, a file that is not there. They record
// what the panel did in reply -- what it drew, what it told the frame, what it
// sent the server, what it warned, whether the person counted as active, which
// listeners it added and removed -- and compare it with panel_frame_pins.json,
// recorded from the code before the frames moved, the way the paint and level
// pins are.
const FRAME_PINS = JSON.parse(fs.readFileSync(path.join(__dirname, 'panel_frame_pins.json'), 'utf8'));

const FRAME_STATE = {
    'device.dsp1.mute': true, 'device.dsp10.mute': false, 'device.dsp1.zone.2.level': -10,
    'device.7.power': 'on', 'var.vol': 5, 'var.vol_max': 9, 'var.3': 'z',
    'plugin.p1.x': 1, 'plugin.p10.x': 2, 'plugin.p1': 3, 'system.time': 't', 'ui.b1.label': 'L',
};
const FRAME_GRANT = { devices: ['dsp1', 7], variables: ['vol', 3], macros: true, navigate: true };
const FRAME_ODD_GRANT = { devices: 'dsp1', variables: null, macros: 'true', navigate: 1 };

/** Every message a frame sends in the pins, in order: the bridge's whole vocabulary and its edges. */
const FRAME_MESSAGES = [
    null, {}, { type: '' }, 'text', { type: 'openavc:unknown' },
    { type: 'openavc:activity' },
    { type: 'openavc:action' },
    { type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'mute', params: { ch: 1 } },
    { type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'mute' },
    { type: 'openavc:action', action: 'device.command', device: 7, command: 'on' },
    { type: 'openavc:action', action: 'device.command', device: 'dsp10', command: 'on' },
    { type: 'openavc:action', action: 'device.command', device: 'dsp1' },
    { type: 'openavc:action', action: 'device.command', command: 'on' },
    { type: 'openavc:action', action: 'state.set', key: 'var.vol', value: 3 },
    { type: 'openavc:action', action: 'state.set', key: 'var.vol_max', value: 3 },
    { type: 'openavc:action', action: 'state.set', key: 'var.3', value: 'x' },
    { type: 'openavc:action', action: 'state.set', key: 'plugin.p1.x', value: 1 },
    { type: 'openavc:action', action: 'state.set', key: 'plugin.p10.x', value: 1 },
    { type: 'openavc:action', action: 'state.set', key: 'plugin.p1', value: 1 },
    { type: 'openavc:action', action: 'state.set', key: 'device.dsp1.mute', value: true },
    { type: 'openavc:action', action: 'state.set', value: 1 },
    { type: 'openavc:action', action: 'state.set', key: '', value: 1 },
    { type: 'openavc:action', action: 'state.set', key: ['var.vol'], value: 1 },
    { type: 'openavc:action', action: 'macro.run', macro: 'system_on' },
    { type: 'openavc:action', action: 'macro.run', macro: 42 },
    { type: 'openavc:action', action: 'macro.run' },
    { type: 'openavc:action', action: 'ui.press', element: 'x' },
    { type: 'openavc:navigate', page: 'admin' },
    { type: 'openavc:navigate' },
    { type: 'openavc:navigate', page: '' },
    { type: 'openavc:error', message: 'ReferenceError: x' },
    { type: 'openavc:error' },
    { type: 'openavc:error', message: 12345 },
    { type: 'openavc:error', message: 'y'.repeat(250) },
];

/**
 * A frame element on a page of its own, live unless `edit`, with a recorder.
 * `answer(url, init)` is what a fetch gets back (a value, a promise, or a throw);
 * `before(app)` runs before the page is drawn. `take()` returns, and clears,
 * everything the panel did since the last take.
 */
function frameMount(def, { edit, answer, before, page } = {}) {
    const r = { ws: [], warn: [], posted: [], toParent: [], fetched: [], listen: [], rejected: [], idle: 0 };
    const onRejection = (e) => r.rejected.push(String(e && e.message));
    process.on('unhandledRejection', onRejection);
    const saved = {
        warn: window.console.warn, fetch: window.fetch,
        add: window.addEventListener, remove: window.removeEventListener,
    };
    window.console.warn = (...a) => r.warn.push(a.map(String).join(' '));
    window.fetch = async (url, init) => {
        // An earlier scenario's access check-in can still be polling; it is
        // not the frame's request, and whether it lands here is timing.
        if (String(url).includes('/api/panel/access')) return saved.fetch(url, init);
        r.fetched.push(init === undefined ? String(url) : `${url} ${JSON.stringify(init)}`);
        return answer ? answer(String(url), init) : { ok: true, status: 200, json: async () => ({}) };
    };
    window.addEventListener = function (type, fn, ...rest) {
        if (type === 'message') r.listen.push(`+${r.handlerName(fn)}`);
        return saved.add.call(this, type, fn, ...rest);
    };
    window.removeEventListener = function (type, fn, ...rest) {
        if (type === 'message') r.listen.push(`-${r.handlerName(fn)}`);
        return saved.remove.call(this, type, fn, ...rest);
    };
    const names = new Map();
    r.handlerName = (fn) => {
        if (!names.has(fn)) names.set(fn, `h${names.size + 1}`);
        return names.get(fn);
    };
    r.restore = () => {
        process.off('unhandledRejection', onRejection);
        window.console.warn = saved.warn;
        window.fetch = saved.fetch;
        window.addEventListener = saved.add;
        window.removeEventListener = saved.remove;
    };
    const app = mkApp();
    app.editMode = !!edit;
    app.ws = { readyState: 1, send: (m) => r.ws.push(JSON.parse(m)) };
    app._postToParent = (m) => r.toParent.push(m);
    app.resetIdleTimer = () => { r.idle += 1; };
    app.state = { ...FRAME_STATE };
    if (before) before(app);
    if (page) {
        app.uiDef = { settings: {}, master_elements: [], pages: [page] };
        app.uiSettings = {};
        app.currentPage = page.id;
        app.snapshotReceived = true;
        app.renderCurrentPage();
    } else {
        renderProject(app, project({
            elements: [def, { id: 'lbl', type: 'label', text: 'x' }],
            placements: { [def.id]: { x: 5, y: 5, w: 40, h: 40 }, lbl: { x: 50, y: 5, w: 20, h: 10 } },
        }));
    }
    r.app = app;
    r.node = () => app.root.querySelector(`[data-element-id="${page ? page.id : def.id}"]`);
    r.take = () => {
        const out = {};
        for (const k of ['ws', 'warn', 'posted', 'toParent', 'fetched', 'listen', 'rejected']) {
            if (r[k].length) out[k] = r[k].splice(0);
        }
        if (r.idle) { out.idle = r.idle; r.idle = 0; }
        if (app._lastTouchedElementId != null) { out.touched = app._lastTouchedElementId; app._lastTouchedElementId = null; }
        return out;
    };
    /** Point the frame's window at the recorder (or at nothing, `null`). */
    r.window = (present = true) => {
        const iframe = r.node()?.querySelector('iframe');
        if (!iframe) return null;
        const value = present ? { postMessage: (m, origin) => r.posted.push([m, origin]) } : null;
        Object.defineProperty(iframe, 'contentWindow', { value, configurable: true });
        return value;
    };
    return r;
}

// --- The choices (a list, a dropdown, a text field), as a page draws them ---
//
// Each is drawn on a page by levelMount's recorder, taken through
// choiceReadings and then its own gestures, and compared with
// panel_choice_pins.json, recorded from the code before they moved.
const CHOICE_PINS = JSON.parse(fs.readFileSync(path.join(__dirname, 'panel_choice_pins.json'), 'utf8'));
const CHOICE_KEY = 'device.d.v';

/** The readings every choice pin case is taken through, the device going away and back. */
function choiceReadings(m, key) {
    m.look();
    for (const value of ['b', null, 'zz', null]) { m.set(key, value); m.look(); }
    m.set('device.d.connected', false); m.look();
    m.set(key, 'a'); m.look();
    m.set('device.d.connected', true); m.look();
    m.set(key, undefined); m.look();
    for (const value of [0, '', 'a', true, 7]) { m.set(key, value); m.look(); }
}

/** Resolves the token fetch a case holds open (token_after_removal). */
let pendingToken = () => {};

/** Microtasks and the promise chains a fetch starts, run out. */
async function settle() {
    for (let i = 0; i < 6; i++) await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
}

/** One frame pin case, end to end; a throw is pinned as its last step. */
async function frameCase(got, name, def, opts = {}, extra) {
    const steps = [];
    let r;
    try {
        r = frameMount(def, opts);
        const node = r.node();
        steps.push({ drawn: nodeSnapshot(node), ...r.take() });
        const id = def ? def.id : opts.page.id;
        steps.push({
            mapped: r.app.elementMap[id]?.el === node && (!def || r.app.elementMap[id]?.elementDef === def),
            grant: node._grant ?? null, own: node._ownNamespace ?? null, config: node._pluginConfig ?? null,
            bridge: typeof node._pluginMessageHandler, held: r.app._pluginMessageHandlers.has(node._pluginMessageHandler),
            handlers: r.app._pluginMessageHandlers.size,
        });
        const iframe = node.querySelector('iframe');
        if (iframe) {
            await settle();
            steps.push({ fileCheck: r.take(), drawn: nodeSnapshot(node) });
            const win = r.window();
            iframe.dispatchEvent(new window.Event('load'));
            await settle();
            steps.push({ load: r.take(), drawn: nodeSnapshot(node) });
            if (extra?.afterLoad) { await extra.afterLoad(r, node, iframe); steps.push({ afterLoad: r.take() }); }
            const fire = (data, source = win) => node._pluginMessageHandler({ source, data });
            for (const data of FRAME_MESSAGES) {
                fire(data);
                steps.push({ msg: JSON.stringify(data)?.slice(0, 60) ?? 'undefined', ...r.take() });
            }
            fire({ type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'x' }, {});
            steps.push({ wrongSource: r.take() });
            // Focus: a message counts as activity only while the frame has it.
            iframe.focus();
            fire({ type: 'openavc:activity' });
            steps.push({ focused: document.activeElement === iframe, ...r.take() });
            for (const data of [null, {}, { type: '' }]) fire(data);
            steps.push({ focusedTypeless: r.take() });
            Object.defineProperty(document, 'activeElement', { get() { throw new Error('no focus here'); }, configurable: true });
            try { fire({ type: 'openavc:activity' }); } finally { delete document.activeElement; }
            steps.push({ focusThrows: r.take() });
            iframe.blur();
            // The grant is read off the element when a message arrives.
            const placed = node._grant;
            node._grant = { devices: ['dsp10'], variables: [], macros: false, navigate: false };
            fire({ type: 'openavc:action', action: 'device.command', device: 'dsp10', command: 'on' });
            fire({ type: 'openavc:action', action: 'device.command', device: 'dsp1', command: 'on' });
            node._grant = undefined;
            fire({ type: 'openavc:action', action: 'device.command', device: 'dsp10', command: 'on' });
            fire({ type: 'openavc:navigate', page: 'p' });
            steps.push({ regranted: r.take() });
            node._grant = { devices: ['dsp10'], variables: ['vol_max'], macros: false, navigate: true };
            r.app.handleMessage({ type: 'state.update', changes: { 'device.dsp10.mute': 'r', 'device.dsp1.mute': 'r', 'var.vol_max': 'r', 'var.vol': 'r' } });
            fire({ type: 'openavc:request-init' });
            await settle();
            steps.push({ regrantedReads: r.take() });
            node._grant = placed;
            // State pushed to the frame: the same scope as its opening snapshot.
            r.app.handleMessage({ type: 'state.update', changes: Object.fromEntries(Object.keys(FRAME_STATE).map((k) => [k, `${k}!`])) });
            r.app.handleMessage({ type: 'state.delete', keys: Object.keys(FRAME_STATE) });
            steps.push({ pushed: r.take() });
            // Asking for the opening message again, after the panel's state was
            // replaced whole, as a reconnect's snapshot does.
            r.app.state = { 'device.dsp1.mute': 'replaced', 'var.vol': 9, 'plugin.p1.x': 'replaced', 'device.other.z': 1 };
            fire({ type: 'openavc:request-init' });
            await settle();
            steps.push({ reinit: r.take(), drawn: nodeSnapshot(node) });
            // A frame that has gone away is told nothing.
            r.window(false);
            r.app.handleMessage({ type: 'state.update', changes: { 'var.vol': 1 } });
            fire({ type: 'openavc:request-init' }, null);
            await settle();
            steps.push({ gone: r.take() });
            r.window();
        }
        // The page redrawn: every frame's listener goes, the new frames listen again.
        r.app.renderCurrentPage();
        await settle();
        steps.push({ redrawn: r.take(), handlers: r.app._pluginMessageHandlers.size });
    } catch (e) {
        steps.push({ threw: String(e && e.message), at: (e && e.stack || '').split('\n')[1]?.trim() });
    } finally {
        if (r) r.restore();
    }
    got[name] = steps;
}

/** A fake resource timeline that reports a frame's status, installed before the app is made. */
function withStatusTimeline(run, { throwsOnObserve, noStatus } = {}) {
    const delivered = [];
    window.PerformanceResourceTiming = function PerformanceResourceTiming() {};
    if (!noStatus) window.PerformanceResourceTiming.prototype.responseStatus = 0;
    window.PerformanceObserver = class {
        constructor(cb) { this.cb = cb; delivered.push(this); }
        observe(opts) { if (throwsOnObserve) throw new Error('not here'); this.opts = opts; }
    };
    const deliver = (entries) => {
        for (const o of delivered) o.cb({ getEntries: () => entries });
    };
    return Promise.resolve(run(deliver, delivered)).finally(() => {
        delete window.PerformanceResourceTiming;
        delete window.PerformanceObserver;
    });
}

const customDef = (id, extra) => ({ id, type: 'custom', custom_file: 'map/index.html', ...extra });
const pluginDef = (id, extra) => ({ id, type: 'plugin', plugin_id: 'p1', plugin_type: 'panel', ...extra });
const PLUGIN_EXTENSIONS = {
    p1: { panel: {
        sandbox_permissions: ['allow-forms', 'allow-scripts', 'allow-forms', 'allow-popups'],
        allow_features: ['camera', 'microphone'], ext_auth: true,
    } },
};

// --- The panel's own time, moved by hand -------------------------------------
//
// The gauge and meter pins, the clock's and the keypad's read the wall clock
// (`new Date()`, `Date.now()`) and the timers (`setTimeout`, `setInterval`).
// withFakeTime replaces all of them on the panel's window for one run, starting
// at `start` (a UTC instant) in the time zone `tz`, so a pin reads the same on
// any machine. `advance(ms)` runs every timer and interval that falls due, in
// time order.

function withFakeTime(run, { start = Date.UTC(2026, 0, 15, 14, 5, 9, 250), tz = 'UTC' } = {}) {
    const RealDate = window.Date;
    const real = {
        setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
        setInterval: window.setInterval, clearInterval: window.clearInterval,
    };
    const realTz = process.env.TZ;
    process.env.TZ = tz;
    let now = start;
    let seq = 0;
    const timers = new Map();
    class FakeDate extends RealDate {
        constructor(...args) { if (args.length === 0) super(now); else super(...args); }
        static now() { return now; }
    }
    const schedule = (fn, ms, every) => {
        seq += 1;
        const step = Math.max(0, Number(ms) || 0);
        timers.set(seq, { at: now + step, fn, every: every ? Math.max(1, step) : 0 });
        return seq;
    };
    window.Date = FakeDate;
    window.setTimeout = (fn, ms) => schedule(fn, ms, false);
    window.setInterval = (fn, ms) => schedule(fn, ms, true);
    window.clearTimeout = (id) => { timers.delete(id); };
    window.clearInterval = (id) => { timers.delete(id); };
    const advance = (ms) => {
        const until = now + ms;
        for (;;) {
            const due = [...timers].filter(([, t]) => t.at <= until)
                .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
            if (!due) break;
            const [id, t] = due;
            now = t.at;
            if (t.every) t.at += t.every; else timers.delete(id);
            t.fn();
        }
        now = until;
    };
    try {
        return run(advance, { pending: () => timers.size });
    } finally {
        window.Date = RealDate;
        Object.assign(window, real);
        if (realTz === undefined) delete process.env.TZ; else process.env.TZ = realTz;
    }
}

// --- The gauge and the level meter, as a page draws them ---------------------
//
// Each drawn on a page by levelMount's recorder (theme defaults or the design
// canvas where a case says), taken through meterReadings with the clock moving
// between readings (the meter's peak hold reads it), then a page redraw, a
// refusal redraw and the peak hold's window; compared with
// panel_meter_pins.json, recorded from the code before they moved.

const METER_PINS = JSON.parse(fs.readFileSync(path.join(__dirname, 'panel_meter_pins.json'), 'utf8'));
const METER_KEY = 'device.d.v';

function meterReadings(m, key, advance) {
    m.look();
    for (const value of [-30, -6, -1, 5, -70, 60, 250, -5, 0, null, 33.333, 0.05, -0.04, 72.6]) {
        m.set(key, value); advance(400); m.look();
    }
    m.set('device.d.connected', false); m.look();
    m.set(key, 30); m.look();
    m.set('device.d.connected', true); m.look();
    m.set(key, undefined); m.look();
    for (const value of ['55', '-20', ' 7 ', true, false, '', 'abc', 1e21, -1e-9, 42]) {
        m.set(key, value); advance(400); m.look();
    }
}

function meterGestures(m, key, advance) {
    m.app.renderCurrentPage(); m.look();
    m.set(key, -12); m.look();
    m.app._redrawElement(m.id); m.look();
    m.set(key, -10); m.look();
    advance(100); m.set(key, -40); m.look();
    advance(100); m.set(key, -40.5); m.look();
    advance(1500); m.set(key, -41); m.look();
    advance(10); m.set(key, -50); m.look();
    advance(5000); m.app._redrawElement(m.id); m.look();
    // The default hold's edge: inside it, exactly at it, just past it.
    advance(5000); m.set(key, -5); m.look();
    advance(1450); m.set(key, -45); m.look();
    advance(50); m.set(key, -46); m.look();
    advance(1); m.set(key, -47); m.look();
}

Object.assign(tests, {
    meters_draw_and_show_readings_as_today() {
        const got = {};
        const bound = { bindings: { show: { value: { key: METER_KEY } } } };
        const gauge = (id, extra) => ({ id, type: 'gauge', label: 'Level', ...bound, ...extra });
        const meter = (id, extra) => ({ id, type: 'level_meter', label: 'Mic', ...bound, ...extra });
        const live = { 'device.d.connected': true };
        const themeDefaults = (app) => {
            app.themeElementDefaults = {
                gauge: { gauge_color: '#0a0a0a', gauge_bg_color: '#1b1b1b', tick_count: 3, show_value: false, text_color: '#2c2c2c' },
                level_meter: { meter_segments: 6, show_peak: false, green_to: -30, yellow_to: -10, peak_hold_ms: 200 },
            };
        };
        const cases = [
            [gauge('g_plain', { min: 0, max: 100, unit: '%' })],
            [gauge('g_defaults', { label: undefined })],
            [gauge('g_zones', {
                min: 0, max: 100, display_decimals: 2,
                zones: [{ from: 0, to: 30, color: '#00ff00' }, { from: 30, to: 70, color: '#ffff00' }, { from: 60, to: 100, color: '#ff0000' }],
            })],
            [gauge('g_zones_empty', { zones: [] })],
            [gauge('g_zones_object', { zones: { from: 0, to: 100, color: '#ff0000' } })],
            [gauge('g_negative', { min: -20, max: 20, arc_angle: 180, display_decimals: 0, unit: ' dB' })],
            [gauge('g_flat', { min: 50, max: 50 })],
            [gauge('g_inverted', { min: 100, max: 0 })],
            [gauge('g_text_range', { min: '9', max: '10' })],
            [gauge('g_null_range', { min: null, max: null, unit: null, arc_angle: null })],
            [gauge('g_style', { style: {
                gauge_color: '#123456', gauge_bg_color: '#654321', gauge_width: 4,
                show_value: false, show_ticks: false, text_color: '#abcdef',
            } })],
            [gauge('g_one_tick', { style: { tick_count: 1 } })],
            [gauge('g_seven_ticks', { style: { tick_count: 7, gauge_width: 0, show_value: true } })],
            [gauge('g_full_circle', { arc_angle: 360 })],
            [gauge('g_no_arc', { arc_angle: 0 })],
            [gauge('g_decimals_text', { display_decimals: 'x' })],
            [gauge('g_decimals_big', { display_decimals: 25 })],
            [gauge('g_decimals_negative', { display_decimals: -3, unit: 'V' })],
            [gauge('g_unbound', { bindings: undefined })],
            [gauge('g_themed', { style: { gauge_width: 10 } }), { before: themeDefaults }],
            [gauge('g_design', { min: 0, max: 10 }), { edit: true }],
            [meter('m_plain', {})],
            [meter('m_horizontal', { orientation: 'horizontal', style: { meter_segments: 10, green_to: -20, yellow_to: -6 } })],
            [meter('m_no_peak', { style: { show_peak: false } })],
            [meter('m_whole_steps', { min: -24, max: 0, style: { meter_segments: 25 } })],
            [meter('m_one_segment', { style: { meter_segments: 1 } })],
            [meter('m_flat', { min: 0, max: 0 })],
            [meter('m_inverted', { min: 0, max: -60 })],
            [meter('m_text_range', { min: '-60', max: '0' })],
            [meter('m_null_range', { min: null, max: null, orientation: null })],
            [meter('m_hold', { style: { peak_hold_ms: 500 } })],
            [meter('m_positive', { min: 0, max: 100, style: { green_to: 50, yellow_to: 80, meter_segments: 8 } })],
            [meter('m_odd_orientation', { orientation: 'diagonal', label: '' })],
            [meter('m_unbound', { bindings: undefined })],
            [meter('m_themed', { style: { meter_segments: 3 } }), { before: themeDefaults }],
            [meter('m_design', {}), { edit: true }],
        ];
        for (const [def, opts] of cases) {
            withFakeTime((advance) => {
                let m;
                try {
                    m = levelMount(def, { state: live, ...opts });
                    m.id = def.id;
                    meterReadings(m, METER_KEY, advance);
                    meterGestures(m, METER_KEY, advance);
                } catch (e) {
                    (m ? m.steps : []).push({ threw: String(e && e.message) });
                }
                got[def.id] = m ? m.steps : [{ threw: 'mount' }];
            });
        }
        comparePins('meters', got, METER_PINS.meters);
    },
});

const results = {};
// Awaited, so a scenario that returns a promise reports its failure instead of
// passing vacuously the moment it is called.
(async () => {
    await loadPanel(panelPath);
    patchedFetch = window.fetch;
    PanelApp = window.__openavcPanel.constructor;
    for (const [name, fn] of Object.entries(tests)) {
        try { await fn(); results[name] = { pass: true }; }
        catch (e) { results[name] = { pass: false, error: String(e && e.message), stack: (e && e.stack || '').split('\n').slice(0, 4).join(' | ') }; }
    }
    // Exit explicitly once stdout is flushed so jsdom/lingering timers from the
    // scenarios don't keep the process alive.
    if (process.env.PAINT_PINS_RECORD) {
        fs.writeFileSync(process.env.PAINT_PINS_RECORD, JSON.stringify(recordedPins, null, 2) + '\n');
    }
    process.stdout.write(JSON.stringify(results, null, 2), () => process.exit(0));
})();
