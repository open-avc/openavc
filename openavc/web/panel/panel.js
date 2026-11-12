/**
 * OpenAVC Panel UI — Phase 1
 *
 * Connects to the backend via WebSocket, renders the touch panel UI
 * from JSON definitions, and sends user interactions back to the server.
 */

import { NOT_APPROVED_CLOSE, PanelAccess } from './modules/access.js';
import { assetUrl, basePath } from './modules/base_path.js';
import { pickLook, withTwoStateWords } from './modules/looks.js';
import {
    applyFrameless, applyImageEffect, applyStyle, clearStateColours, drawFadedPicture,
    gradientCss, updateImageTint,
} from './modules/paint.js';
import {
    clearMatrixOverlays, createMatrix, endMatrixGestures,
} from './modules/matrix.js';
import { FaderControl, SliderControl, endFaderDrags } from './modules/level.js';

// --- Programmer session bridge ---------------------------------------------
// When the panel is embedded as an iframe inside the Programmer IDE (the UI
// Builder's canvas and its Preview), the IDE holds its session token in
// sessionStorage. The iframe is same-origin and unsandboxed, so it shares the
// tab's sessionStorage, and the token is what lets the iframe's /api fetches
// and its WebSocket handshake pass on an instance with a password: without it
// every call answers 401, which makes the browser pop its native sign-in
// dialog inside the iframe, and the panel gate refuses the socket for want of
// an approved cookie. The stored value is the bare token, URL-safe base64, so
// it is valid as it is both in a header and as a subprotocol. See
// openavc/web/programmer/src/api/auth.ts for the parent half.
(function installProgrammerSessionBridge() {
    const STORAGE_KEY = 'openavc.programmer.session';

    function getSessionToken() {
        try {
            const token = sessionStorage.getItem(STORAGE_KEY);
            return token || null;
        } catch (_) {
            return null;
        }
    }

    function getAuthHeader() {
        const token = getSessionToken();
        return token ? `Bearer ${token}` : null;
    }

    // Mirrors getAuthSubprotocols() in the Programmer SPA. The server reads it
    // in check_ws_auth().
    function getAuthSubprotocol() {
        const token = getSessionToken();
        return token ? `auth.bearer.${token}` : null;
    }

    // Exposed so the PanelApp WebSocket constructor can pull the subprotocol.
    window.__openavcGetAuthSubprotocol = getAuthSubprotocol;

    function isApiUrl(url) {
        // /api, /api/..., or /tunnel/<id>/api/...
        return /(^|\/)api(\/|$|\?)/.test(url);
    }

    // Patch fetch unconditionally; the header is only attached when a session
    // is actually present. A panel opened on its own holds none and this
    // patch is a no-op.
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
        let url;
        if (typeof input === 'string') {
            url = input;
        } else if (input instanceof URL) {
            url = input.toString();
        } else {
            url = input.url;
        }
        const auth = getAuthHeader();
        if (auth && isApiUrl(url)) {
            const headers = new Headers(
                init?.headers ??
                    (input instanceof Request ? input.headers : undefined),
            );
            if (!headers.has('Authorization')) {
                headers.set('Authorization', auth);
            }
            init = { ...(init || {}), headers };
        }
        return originalFetch(input, init);
    };
})();
// ---------------------------------------------------------------------------

// Every stored measurement is rem, and the root type scale makes 1rem equal
// 14px at the 1280x800 reference. Runtime defaults are written as
// <old px> / REM_BASE_PX so they still land on the pixel they always did.
const REM_BASE_PX = 14;

// How long a macro this panel started stays this panel's to report a failure
// for, when nothing ever tells us its run ended. Matches the macro engine's own
// ceiling for a queued start, which is the longest a press can legitimately sit
// before it runs.
const MACRO_CLAIM_MAX_AGE_MS = 5 * 60 * 1000;

// A button's display modes that draw no words, and those that draw its own picture.
const WORDLESS_MODES = new Set(['image', 'icon_only']);
const PICTURE_MODES = new Set(['image', 'image_text']);

// Overlay and sidebar boxes, as percentages of the viewport. These are the
// old hardcoded pixel defaults (400x300 dialog, 320-wide sidebar) measured
// against the 1280x800 reference, so an overlay that never set a size keeps
// the proportions it had.
const OVERLAY_DEFAULTS = {
    dialogWidth: 31.25,
    dialogHeight: 37.5,
    sidebarWidth: 25,
};

// The snap increment a page falls back to: the old 12x8 grid's spacing.
const SNAP_FALLBACK = { x: 100 / 12, y: 100 / 8 };

// How long the first draw waits for the theme before going ahead without it.
// The fetch is same-origin and answers in milliseconds; this only exists so a
// server that accepts the request and never answers cannot leave a panel with
// nothing on it. Past this the page draws unthemed -- what it used to draw
// first anyway -- and repaints when the theme finally lands.
const FIRST_THEME_WAIT_MS = 1000;

// The `device.<id>.*` properties the PLATFORM maintains rather than the device
// reports. They describe the device -- whether it is reachable, what it is
// called, whether it is enabled -- so they stay true while it is unreachable
// and must keep rendering. `connected` above all: an LED bound to it IS the
// offline report, and blanking that would hide the one honest thing on the
// page. Everything else under `device.` came from the far end of a wire that
// is no longer there. Kept in step with what core/device_manager.py writes.
const DEVICE_PLATFORM_PROPS = new Set([
    'connected', 'enabled', 'name', 'offline_detail', 'offline_reason',
    'orphan_reason', 'orphaned', 'paused', 'paused_for', 'reconnect_attempt',
    'reconnect_failed', 'restarting', 'asleep', 'wake_command',
]);

// The same set, for a child entity's reserved props. A child carries the
// platform's own account of itself under `device.<id>.<type>.<local>.<prop>`,
// and those four keep telling the truth while the parent is away exactly as
// the device's own pair does -- so a label bound to one must keep rendering.
//
// Until this existed the split rule was accidental rather than decided: the
// device's `offline_reason` is three segments and was exempt, its child's is
// five and was not, so a panel bound to a child's fault drew `--` at precisely
// the moment the child had something to say.
const CHILD_PLATFORM_PROPS = new Set([
    'online', 'offline_reason', 'offline_detail', 'label',
]);

class PanelApp {
    constructor() {
        const params = new URLSearchParams(window.location.search);
        // Edit mode — iframe embedded in the UI Builder design canvas. No WS, no
        // binding sends, no idle/lock, no transitions. Definition arrives via
        // postMessage from the parent programmer window.
        this.editMode = params.get('edit') === '1';
        // Embedded — the Programmer's Preview or design canvas, which mark the
        // URL they open (preview=1, edit=1). The parent is authoritative for the
        // project definition; WS is used only for live device state. Any other
        // page that frames the panel (a signage player, a dashboard's card)
        // gets an ordinary panel, as a standalone tab does: the server's
        // definitions, the panel check-in, a panel connection.
        const framed = !!(window.parent && window.parent !== window);
        this.embedded = framed && (params.get('preview') === '1' || this.editMode);
        // Type scales with the panel via vmin, which resolves against the
        // viewport. That is right on real glass and inside overlays, but wrong
        // when the panel is embedded in a box smaller than the screen it stands
        // for -- the builder previewing a 400px overlay would render text at a
        // third of runtime size. An embedder pins the scale by handing us the
        // preset's vmin in px.
        this._applyVminOverride(params.get('vmin'));
        this.ws = null;
        this.state = {};
        this.uiDef = null;
        this.uiSettings = {};
        // Without an explicit page link, wait for the definition and use its
        // home page. A page named "main" can exist without being home.
        this.currentPage = params.get('page') || '';
        // The way round the arrangement currently being drawn is. Set per
        // render; seeded here so a renderer reached before any page is drawn
        // still has an answer rather than `undefined`.
        this._drawnOrientation = 'landscape';
        this.locked = false;
        this.snapshotReceived = false;
        this.idleTimer = null;
        this.root = document.getElementById('panel-root');
        this.statusEl = document.getElementById('connection-status');
        this.bindings = [];          // Active bindings to evaluate on state change
        this.elementMap = {};        // element_id -> {el, elementDef} for ui.* overrides
        // Preview only: element_id -> the devices its press reaches, from the
        // server's ui.definition (the Builder's copy never carries them).
        this._servedActionDevices = null;
        // Preview only: element_id -> the devices its press reaches through a
        // command that works while they are offline (`wake_devices`).
        this._servedWakeDevices = null;
        // The in-place text edit in flight, or null. Edit mode only. Held here
        // rather than per-element so the freeze in `_setLabelText` is one
        // comparison, and so opening a second edit closes the first.
        this._textEdit = null;
        // Bound once: add/removeEventListener only match on identity, and a
        // fresh arrow per edit would leak a listener onto every node touched.
        this._onTextEditKey = (e) => {
            const edit = this._textEdit;
            if (!edit) return;
            // Mid-composition Enter belongs to the IME, not to us.
            if (e.isComposing) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                this._finishTextEdit(false);
                return;
            }
            if (e.key === 'Enter') {
                // A label may hold several lines, so Enter alone commits and
                // shift-Enter breaks the line -- the same bargain every editor
                // of this shape makes. Single-line types commit on either.
                if (edit.multiline && e.shiftKey) return;
                e.preventDefault();
                e.stopPropagation();
                this._finishTextEdit(true);
                return;
            }
            // Tab out rather than inserting one: a caret in a control's caption
            // has nowhere to put a tab, and browsers insert a literal one.
            if (e.key === 'Tab') {
                e.preventDefault();
                this._finishTextEdit(true);
                return;
            }
            // A text-only button holds its words in the <button> itself, and a
            // button's own answer to the space bar is "press me" -- so a label
            // typed as "Mic Mute" committed as "MicMute" and the space was
            // gone with no sign it had been dropped. Insert it ourselves.
            if (e.key === ' ' && edit.host.closest('button')) {
                e.preventDefault();
                this._insertTextAtCaret(' ');
            }
        };
        this._onTextEditBlur = () => this._finishTextEdit(true);
        this.holdTimers = {};        // element_id -> interval for hold-repeat mode
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) this._endAllHolds();
        });
        this.debounceTimers = [];    // Track all debounce timeouts for cleanup
        this._pluginMessageHandlers = new Set(); // Track all plugin iframe message handlers
        this._clockElements = [];    // All clock update functions for batched interval
        this._clockInterval = null;  // Single global clock interval
        this._pendingBindingKeys = null; // Batched binding keys for rAF
        this._bindingRafId = null;       // requestAnimationFrame ID
        this.overlayStack = [];      // Stack of overlay page IDs (newest on top)
        this.pageHistory = [];       // Stack of previously-visited regular pages (newest on top) for $back
        this._previewPageId = null;  // Page the builder's preview last asked for (see _showPageAsRuntimeWould)
        this._navigatingBack = false; // Skip history push when navigateToPage is recursing for $back
        // Keyed by macro id, which is whatever the author named it. No
        // prototype, so an id like `constructor` reads as not running and
        // `__proto__` is an ordinary key rather than Object.prototype.
        this._runningMacros = Object.create(null);    // macro_id -> { description, step_index, total_steps }
        this._startedMacros = Object.create(null);    // macro_id -> { at, reported, running } for macros THIS panel started
        this._macroRuns = Object.create(null);        // macro_id -> how many runs of it are in flight (see _endMacroRun)
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000; // matches the backoff cap used in onclose
        this.reconnectAttempts = 0;
        this._offline = false;           // true while the WS is disconnected
        this._lastTouchedElementId = null; // control the next failure is about
        this._errorMessageTimer = null;    // auto-dismiss for the failure band
        this._lockInitialized = false;   // lock screen shown once per session, not on every reconnect
        this.access = new PanelAccess({
            hidePageStatus: () => this._hidePageStatus(),
            showPageStatus: () => this._showPageStatus(),
            endHolds: () => this._endAllHolds(),
        });
        this._meetingStartTimes = {};    // element_id -> meeting start Date (survives re-render)
        this.themeElementDefaults = {};
        // The variables the applied theme is actually drawing from, which is
        // not theme.variables: see _applyThemeData.
        this.themeVariables = {};
        this.currentTheme = null;
        this._themeApplyInProgress = false;
        // The first draw waits for the theme -- see _drawWhenThemeArrives.
        this._firstThemeSettled = false;
        this._awaitingFirstTheme = false;
        // Audio playback (driven by plugin.audio_player.* state)
        this._audioUnlocked = false;
        this._lastAudioRequestId = null;
        this._activeAudio = new Set();
        // Plugin panel_elements lookup: pluginId -> {[type]: extension}.
        // Populated once at startup via /api/plugins/extensions so per-element
        // iframe sandbox / allow attributes can apply the plugin's declared
        // permissions instead of always defaulting to allow-scripts only.
        this._pluginExtensions = {};
        // Frames waiting to hear what their own request answered: absolute url
        // -> Set of callbacks. See _watchIframeStatuses.
        this._iframeStatusWaiters = new Map();
        this._iframeStatusWatch = false;
        this._watchIframeStatuses();
    }

    /** Watch what every iframe's OWN request answered, so nothing has to ask twice.
     *
     *  A frame pointed at a missing file has to say so. The server does its own
     *  half -- api/static_files.py answers a missing page with a small HTML
     *  document rather than a JSON error, because the body of that response is
     *  what the room sees -- but that only reaches whoever is looking at the
     *  frame. The strip out here is what names the FILE, and what tells the
     *  Builder which element failed (openavc:element-error), which is the half
     *  an author needs. Finding out used to cost a second request per frame, on
     *  the one path in the panel that never caches (the ui/ route is no-cache by
     *  design, so an author saving a file sees it), which meant every custom
     *  control was two round trips forever.
     *
     *  The parent's own resource timeline already carries the answer: it reports
     *  responseStatus for the frame's navigation even though the frame is
     *  sandboxed into an opaque origin. The frame's `load` event does NOT --
     *  it fires for a 404 exactly as it does for a 200, so the status is the
     *  whole signal and the event is worthless on its own.
     *
     *  An observer rather than a read of performance.getEntriesByName(): the
     *  timeline stops recording at 250 entries, which a wall panel reaches in
     *  days, and past that the lookup finds nothing while an observer still
     *  gets delivered. The lookup would have silently stopped working in the
     *  field, which is the failure this is here to remove. The cost is that the
     *  observer fires just after `load`, so it has to be the trigger.
     */
    _watchIframeStatuses() {
        // Old WebViews have no responseStatus, and there the second request is
        // still the only way to know. Detect it rather than assume it.
        const readable = typeof PerformanceObserver !== 'undefined'
            && typeof PerformanceResourceTiming !== 'undefined'
            && 'responseStatus' in PerformanceResourceTiming.prototype;
        if (!readable) return;
        try {
            new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (entry.initiatorType !== 'iframe') continue;
                    const waiting = this._iframeStatusWaiters.get(entry.name);
                    if (!waiting) continue;
                    this._iframeStatusWaiters.delete(entry.name);
                    for (const fn of waiting) {
                        try { fn(entry.responseStatus); } catch (_) { /* one frame */ }
                    }
                }
            }).observe({ type: 'resource', buffered: true });
            this._iframeStatusWatch = true;
        } catch (_) {
            // Nothing to read -- the fallback in _reportIframeFileFailure asks.
        }
    }

    /** Say so in the element's box if the control's file never arrived.
     *
     *  Reads the status off the frame's own request where the browser will tell
     *  us (no extra traffic), and only asks for it where it will not.
     */
    _reportIframeFileFailure(el, iframe, opts) {
        const failed = (status) => this._showIframeFault(
            el,
            status
                ? `${opts.fileLabel} could not be loaded (${status})`
                : `${opts.fileLabel} could not be loaded`,
        );
        // The second request, kept for the cases where the frame's own status
        // is not knowable. `no-store` because its whole purpose is to find out
        // what is there right now.
        const ask = () => {
            fetch(opts.src, { cache: 'no-store' }).then(res => {
                if (!res.ok) failed(res.status);
            }).catch(() => failed(0));
        };

        if (this._iframeStatusWatch) {
            // The timeline names resources absolutely; `iframe.src` reflects
            // the resolved URL, which is the same string.
            const url = iframe.src;
            let waiting = this._iframeStatusWaiters.get(url);
            if (!waiting) {
                waiting = new Set();
                this._iframeStatusWaiters.set(url, waiting);
            }
            waiting.add((status) => {
                // A status of 0 is not a verdict -- it is the browser declining
                // to give one. A request that never got an answer reads that
                // way, and so does one the browser will not report on. Asking
                // is right for both: the frame either really is broken, in
                // which case the round trip is the least of it, or it drew fine
                // and an accusation would have been wrong.
                if (status === 0) ask();
                else if (status >= 400) failed(status);
            });
            return;
        }

        ask();
    }

    async _loadPluginExtensions() {
        try {
            const res = await fetch(`${basePath()}/api/plugins/extensions`);
            if (!res.ok) return;
            const data = await res.json();
            const elements = (data.extensions || {}).panel_elements || [];
            for (const ext of elements) {
                if (!ext.plugin_id || !ext.type) continue;
                const byType = this._pluginExtensions[ext.plugin_id] || {};
                byType[ext.type] = ext;
                this._pluginExtensions[ext.plugin_id] = byType;
            }
        } catch (err) {
            console.warn('[panel] failed to load plugin extensions:', err);
        }
    }

    // Fetch a plugin-scoped token for a panel_elements iframe that declared
    // ext_auth. Uses the patched fetch so the request is authenticated; the
    // token is forwarded to the iframe via openavc:init so it can reach its
    // plugin's /api/plugins/<id>/ext/* routes. Returns undefined when the
    // instance is open (empty token) or on any error.
    async _fetchPluginExtToken(pluginId) {
        try {
            const res = await fetch(
                `${basePath()}/api/plugins/${encodeURIComponent(pluginId)}/ext-token`
            );
            if (!res.ok) return undefined;
            const data = await res.json();
            return data && data.token ? data.token : undefined;
        } catch (err) {
            console.warn('[panel] failed to fetch plugin ext token:', err);
            return undefined;
        }
    }

    async start() {
        // Any embedded iframe accepts project updates from the parent programmer
        // window. Preview mode embeds the iframe but still opens WS for live state;
        // edit mode embeds it and skips WS entirely.
        if (this.embedded) {
            this._setupEditModeListener();
        }
        if (this.editMode) {
            this._hideLoadingState();
            if (this.statusEl) {
                this.statusEl.style.display = 'none';
                this.statusEl.remove();
                this.statusEl = null;
            }
            const offline = document.getElementById('offline-overlay');
            if (offline) offline.style.display = 'none';
            console.log('[panel-edit] start: edit mode, waiting for editor-init from parent');
            this._fetchProjectAndRender();
            this._postToParent({ type: 'openavc:editor-ready' });
            return;
        }
        // Whether this device may connect is settled before anything else is
        // fetched: a device that is not approved sees the waiting screen and
        // nothing more. The Programmer's Preview is parent-driven and carries
        // the Programmer's own session on its socket, so it skips the check-in.
        if (!this.embedded) {
            await this.access.waitForAccess();
        }
        // Load plugin extensions before the first render so per-element
        // iframe sandbox / allow attributes can be applied correctly.
        // Best-effort: if the fetch fails or hangs, we proceed with defaults.
        await this._loadPluginExtensions();
        this.setupIdleListeners();
        this._setupAudioUnlock();
        this.connect();
    }

    _fetchProjectAndRender() {
        // The RESOLVED ui, not /api/project. A matrix's sources and destinations
        // are expanded on the server (matrix plan D6) and this renderer has no
        // expander, so the authoring copy would draw an empty matrix here.
        fetch(`${basePath()}/api/ui/resolved`)
            .then(r => r.ok ? r.json() : null)
            .then(proj => {
                if (!proj || !proj.ui) {
                    console.warn('[panel-edit] no project available from /api/ui/resolved');
                    return;
                }
                // If the parent already pushed a definition, don't clobber it.
                if (this.uiDef) {
                    console.log('[panel-edit] fetched project but parent already provided one; skipping');
                    return;
                }
                console.log('[panel-edit] rendering from fetched project');
                this.uiDef = proj.ui;
                this.uiSettings = proj.ui.settings || {};
                this.state = {};
                this.snapshotReceived = true;
                this._setupViewportListener();
                this.renderCurrentPage();
            })
            .catch(err => console.warn('[panel-edit] fetch failed:', err));
    }

    _postToParent(msg) {
        // Only to a parent on this panel's own origin. The Programmer always is
        // (through the cloud too, where the difference is a path), and any page
        // can frame the panel with ?preview=1, so '*' would hand that page the
        // editor messages: text, element errors, file names.
        if (this.embedded) {
            try { window.parent.postMessage(msg, window.location.origin); } catch (_) { /* ignore */ }
        }
    }

    _setupEditModeListener() {
        window.addEventListener('message', (event) => {
            // Only accept messages from the parent programmer window, which is
            // on this panel's own origin. Any page can frame /panel?preview=1,
            // and is then the parent too.
            if (event.source !== window.parent) return;
            if (event.origin !== window.location.origin) return;
            const msg = event.data;
            if (!msg || typeof msg !== 'object') return;
            switch (msg.type) {
                case 'openavc:editor-init':
                case 'openavc:editor-project': {
                    const ui = msg.project?.ui || msg.ui || null;
                    if (!ui) return;
                    this.uiDef = ui;
                    this.uiSettings = ui.settings || {};
                    if (msg.pageId) this.currentPage = msg.pageId;
                    if (typeof msg.showGrid === 'boolean') this._editShowGrid = msg.showGrid;
                    // Bumped by the Builder every time a file lands in ui/. It
                    // rides on the src of every custom control so a save is
                    // visible in the canvas without reloading the IDE.
                    if (Object.prototype.hasOwnProperty.call(msg, 'uiFilesVersion')) {
                        this._uiFilesVersion = msg.uiFilesVersion;
                    }
                    // The builder sizes an overlay preview to the overlay box,
                    // not the screen, so it hands us the preset's vmin to keep
                    // preview type the size it will be at runtime.
                    if (Object.prototype.hasOwnProperty.call(msg, 'vmin')) {
                        this._applyVminOverride(msg.vmin);
                    }
                    // The Theme Studio sends a live working-copy theme so edits
                    // apply within a frame (no fetch). When omitted, fall back to
                    // the normal /api/themes/<id> fetch path.
                    if (msg.inlineTheme && typeof msg.inlineTheme === 'object') {
                        this.inlineTheme = msg.inlineTheme;
                    } else if (Object.prototype.hasOwnProperty.call(msg, 'inlineTheme')) {
                        // Explicitly null/undefined cleared from parent — drop any prior inline theme
                        this.inlineTheme = null;
                    }
                    // Edit mode has no WS, so the parent supplies state (or none).
                    // Preview mode has a WS that manages state — don't clobber it.
                    if (this.editMode) {
                        this.state = msg.demoState && typeof msg.demoState === 'object'
                            ? { ...msg.demoState }
                            : {};
                    }
                    this.snapshotReceived = true;
                    this._setupViewportListener();
                    if (this.editMode) {
                        this.renderCurrentPage();
                    } else {
                        // Preview: an overlay page has to go through the
                        // navigation path or it draws as a flat page.
                        this._showPageAsRuntimeWould(msg.pageId || this.currentPage);
                        this._reconcileLockOnDefinition();
                        this.resetIdleTimer();
                    }
                    this._postToParent({ type: 'openavc:editor-ready' });
                    break;
                }
                case 'openavc:editor-edit-text': {
                    // A double-click on the Builder's overlay. The iframe takes
                    // no pointer events in edit mode, so the element arrives by
                    // id rather than by the click landing in here.
                    if (this.editMode && msg.elementId) {
                        this._beginTextEdit(String(msg.elementId));
                    }
                    break;
                }
                case 'openavc:editor-cancel-text': {
                    if (this._textEdit) this._finishTextEdit(false);
                    break;
                }
                case 'openavc:editor-placements': {
                    // A drag in progress. The builder commits geometry to its
                    // store once, on pointer-up, so between those it just tells
                    // us where the boxes are and we move the nodes we already
                    // have -- four inline styles instead of rebuilding a page
                    // sixty times a second.
                    this._applyLivePlacements(msg.placements);
                    break;
                }
                case 'openavc:editor-page': {
                    // A page change can move the preview between a full-screen
                    // page and an overlay box, which is exactly when the vmin
                    // the builder wants us to use changes.
                    let rerender = false;
                    if (Object.prototype.hasOwnProperty.call(msg, 'vmin')) {
                        this._applyVminOverride(msg.vmin);
                        rerender = true;
                    }
                    if (!this.editMode) {
                        // Preview always rebuilds through the runtime's own
                        // path, so a dialog stays a dialog. Going through
                        // renderCurrentPage instead would tear it down: that
                        // function opens by dismissing every overlay, so a bare
                        // vmin nudge used to close an open dialog.
                        const want = msg.pageId || this._previewPageId || this.currentPage;
                        if (rerender || want !== this._previewPageId) {
                            this._showPageAsRuntimeWould(want);
                        }
                        break;
                    }
                    if (msg.pageId && msg.pageId !== this.currentPage) {
                        this.currentPage = msg.pageId;
                        rerender = true;
                    }
                    if (rerender) this.renderCurrentPage();
                    break;
                }
            }
        });
    }

    // --- WebSocket ---

    connect() {
        // A Remote Access session that ended does not come back on a retry:
        // the reconnect scheduled before the probe answered stops here.
        if (this.access.ended) return;
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${location.host}${basePath()}/ws?client=${this.embedded ? "preview" : "panel"}&namespaces=device,var,ui,system,plugin`;

        // When embedded in the Programmer IDE, attach the cached programmer
        // password as a Sec-WebSocket-Protocol so the handshake authenticates
        // without prompting. Standalone panels with no cached credentials fall
        // back to the plain ctor (browser cache / open server).
        const authProto = typeof window.__openavcGetAuthSubprotocol === 'function'
            ? window.__openavcGetAuthSubprotocol()
            : null;
        this.ws = authProto ? new WebSocket(url, [authProto]) : new WebSocket(url);

        this.ws.onopen = () => {
            this.reconnectDelay = 1000;
            this.reconnectAttempts = 0;
            this.setConnectionStatus(true);
            // Clear reconnection info on successful connect
            const retryEl = document.getElementById('reconnect-info');
            if (retryEl) retryEl.textContent = '';
        };

        this.ws.onclose = (event) => this._socketClosed(event);

        this.ws.onerror = () => {
            this.ws.close();
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                this.handleMessage(msg);
            } catch (e) {
                console.error('Invalid message:', e);
            }
        };
    }

    /** What a closed socket means: a refusal with a reason says so in place
     *  of the panel; a device not approved asks again before connecting; and
     *  anything else reconnects, a little later each time. */
    _socketClosed(event) {
        if (this.access.ended) return;
        const code = event.code;
        this.setConnectionStatus(false);
        this._endAllHolds();
        if (this.access.showRefusal(code)) return;
        if (code === NOT_APPROVED_CLOSE) {
            // The server took the handshake and then refused this device:
            // not approved, or approval was just revoked. There is nothing
            // to reconnect to until that changes, so ask instead of
            // retrying; the waiting screen shows a new code after a revoke.
            this.reconnectDelay = 1000;
            this.reconnectAttempts = 0;
            this.access.waitForAccess({ afterRefusal: true }).then(() => this.connect());
            return;
        }
        this.access.checkSessionAfterDrop();
        this.reconnectAttempts++;
        const retryEl = document.getElementById('reconnect-info');
        if (retryEl) {
            retryEl.textContent = `Reconnecting (attempt ${this.reconnectAttempts})...`;
        }
        // Always retry with exponential backoff (capped at maxReconnectDelay)
        setTimeout(() => this.connect(), this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, this.maxReconnectDelay);
    }

    send(msg) {
        // Edit mode: no WS, bindings must not fire even if pointer events leak through
        if (this.editMode) return;
        // Which control a failure coming back is about. The error frame carries
        // the sentence and the interaction, not the element, and does not need
        // to: one finger presses one thing at a time, and the only thing this
        // is used for is keeping the message off the control just pressed.
        if (msg && msg.element_id) this._lastTouchedElementId = msg.element_id;
        this._claimMacros(msg);
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(msg));
        }
    }

    setConnectionStatus(connected) {
        if (this._statusHideTimer) {
            clearTimeout(this._statusHideTimer);
            this._statusHideTimer = null;
        }

        this.statusEl.textContent = connected ? 'Connected' : 'Disconnected';
        this.statusEl.className = connected ? 'connected' : 'disconnected';

        if (connected && this.embedded) {
            // The Builder's Preview reconnects every time it opens, and the
            // IDE shows its own connection state: no flash over the page.
            this.statusEl.classList.add('hidden');
        } else if (connected) {
            this._statusHideTimer = setTimeout(() => {
                this.statusEl.classList.add('hidden');
            }, 3000);
        }

        // Offline overlay
        const overlay = document.getElementById('offline-overlay');
        if (overlay) {
            overlay.classList.toggle('visible', !connected);
        }
        // Disable panel interaction when offline
        if (this.root) {
            this.root.style.pointerEvents = connected ? '' : 'none';
        }
        // Open overlays/sidebars live on document.body, outside this.root, so
        // toggle their interactivity too — otherwise taps on an already-open
        // overlay (matrix lock/mute, keypad) still flip local optimistic UI
        // while the command is silently dropped offline.
        document.querySelectorAll('.panel-overlay').forEach(o => {
            o.style.pointerEvents = connected ? '' : 'none';
        });

        this._offline = !connected;
        if (!connected) {
            // Don't let a previously-scheduled idle timer fire while offline —
            // it would navigate the dead panel and stack a lock screen over the
            // offline overlay. resetIdleTimer re-arms it on reconnect.
            if (this.idleTimer) {
                clearTimeout(this.idleTimer);
                this.idleTimer = null;
            }
            // Stop any in-flight notification audio; stale chimes on reconnect
            // are worse than missed ones, and this bounds _activeAudio.
            this._stopAllAudio();
        }
    }

    // --- Message Handling ---

    handleMessage(msg) {
        switch (msg.type) {
            case 'ping':
                this.send({ type: 'pong' });
                break;

            case 'state.snapshot':
                this.state = msg.state || {};
                this.snapshotReceived = true;
                this._hideLoadingState();
                this.evaluateAllBindings();
                this._updateSimulationTag();
                // Seed audio dedupe id from current state so we don't replay
                // the most recent sound when (re)connecting.
                this._seedAudioDedupeFromSnapshot();
                break;

            case 'state.update':
                Object.assign(this.state, msg.changes || {});
                this._scheduleBindingEvaluation(Object.keys(msg.changes || {}));
                if ('system.simulation_active' in (msg.changes || {})) {
                    this._updateSimulationTag();
                }
                // Notify plugin iframes of state changes
                for (const [k, v] of Object.entries(msg.changes || {})) {
                    this._notifyPluginIframes(k, v);
                    if (k === 'plugin.audio_player.play_request') {
                        this._handleAudioPlayRequest(v);
                    }
                }
                break;

            case 'state.delete':
                if (Array.isArray(msg.keys) && msg.keys.length > 0) {
                    for (const key of msg.keys) {
                        delete this.state[key];
                    }
                    this._scheduleBindingEvaluation(msg.keys);
                    if (msg.keys.includes('system.simulation_active')) {
                        this._updateSimulationTag();
                    }
                    // Plugin iframes get a null value notification — preserves
                    // their existing contract (they saw value=null before the
                    // server emitted explicit state.delete messages).
                    for (const key of msg.keys) {
                        this._notifyPluginIframes(key, null);
                    }
                }
                break;

            case 'ui.definition':
                // Embedded iframes take their definition from the parent
                // programmer window via postMessage — ignore server-pushed
                // definitions so in-flight builder edits aren't clobbered by
                // the last-saved project. All Preview keeps of it is which
                // devices each control's press reaches, which only the server
                // can work out (`ui/action_devices.py`).
                if (this.embedded) {
                    if (!this.editMode) {
                        this._servedActionDevices = this._actionDevicesById(msg.ui);
                        this._servedWakeDevices = this._actionDevicesById(msg.ui, 'wake_devices');
                        this.evaluateActionAvailability();
                    }
                    break;
                }
                this.uiDef = msg.ui;
                this.uiSettings = msg.ui?.settings || {};
                this._setupViewportListener();
                if (this.snapshotReceived) {
                    // The first definition is the cold start, and the theme
                    // that decides what every element looks like is still one
                    // fetch away -- so wait for it here rather than draw the
                    // whole page and draw it again when it lands. Only here:
                    // renderCurrentPage() itself still draws when called, which
                    // is what every other caller (and every test) expects.
                    if (this._firstThemeSettled) {
                        this.renderCurrentPage();
                    } else if (!this._awaitingFirstTheme) {
                        this._awaitingFirstTheme = true;
                        this._drawWhenThemeArrives();
                    }
                }
                this._reconcileLockOnDefinition();
                this.resetIdleTimer();
                break;

            case 'ui.files':
                // A file in the project's ui/ tree changed. Nothing about the
                // project moved, so no ui.definition follows and this page
                // would keep drawing the control the browser already has --
                // for as long as nobody navigated away and back. The version
                // rides onto each frame's src, so a re-render re-fetches.
                //
                // Embedded is the Builder's canvas, which runs its own counter
                // over postMessage and would fight this one. Same reason
                // ui.definition is ignored there.
                if (this.embedded) break;
                this._uiFilesVersion = msg.version;
                if (this.snapshotReceived && this._pageRunsAuthorMarkup()) {
                    this.renderCurrentPage();
                }
                break;

            case 'ui.navigate':
                if (msg.page_id) {
                    this.navigateToPage(msg.page_id);
                }
                break;

            case 'macro.started':
                this._macroRuns[msg.macro_id] = (this._macroRuns[msg.macro_id] || 0) + 1;
                // A claim made before this frame is a press waiting for its
                // run; now it has one. Until it does, an ending run belongs to
                // somebody else and must not take the claim with it.
                if (this._startedMacros[msg.macro_id]) {
                    this._startedMacros[msg.macro_id].running = true;
                }
                this._runningMacros[msg.macro_id] = {
                    description: '',
                    step_index: 0,
                    total_steps: msg.total_steps || 0,
                };
                this._updateMacroBusyState(msg.macro_id);
                this._updateMacroProgressBindings(msg.macro_id);
                break;

            case 'macro.progress': {
                // Only a step's own message says which step is running. A Wait
                // Until's waiting / satisfied / timeout, a group command's result
                // and a condition's answer carry none of it, and overwriting
                // from them read "Step 1 of 0" for as long as the wait lasted.
                const run = this._runningMacros[msg.macro_id];
                if (run) {
                    if ('description' in msg) run.description = msg.description || '';
                    if (typeof msg.step_index === 'number') run.step_index = msg.step_index;
                    if (typeof msg.total_steps === 'number') run.total_steps = msg.total_steps;
                }
                this._updateMacroProgressBindings(msg.macro_id);
                break;
            }

            case 'macro.step_error':
                this._reportMacroFailure(msg);
                break;

            case 'macro.completed':
            case 'macro.error':
            case 'macro.cancelled':
                this._endMacroRun(msg.macro_id);
                this._updateMacroBusyState(msg.macro_id);
                this._updateMacroProgressBindings(msg.macro_id);
                break;

            case 'error':
                // source_type is absent for connection-level errors (rate
                // limit, malformed JSON) — printing it unconditionally put a
                // literal "undefined" in front of every one of those.
                console.warn(msg.source_type
                    ? `[WS Error] ${msg.source_type}: ${msg.message}`
                    : `[WS Error] ${msg.message}`);
                // The band only ever carries a failure of something somebody
                // did. A frame with no source_type is the connection refusing a
                // message before anything was read off it -- a rate limit, a
                // frame that was not JSON -- and it cannot name what failed
                // because nothing was parsed. "Rate limit exceeded" on a wall
                // panel is a fact about our protocol in front of a room that
                // can do nothing with it, and it displaces the sentence that
                // would have been useful. The revert still runs, which is the
                // half that matters there: the control stops showing a value
                // the device never took.
                if (msg.source_type) this.showFailureMessage(msg.message);
                this._revertRefusedInteraction(msg.element_id);
                break;

            case 'command.ack':
            case 'state.set.ack':
                // A control somebody wrote themselves sends its commands and
                // its writes straight down this socket, and so does the lock on
                // a matrix row, so those failures come back on an ack rather
                // than as an error frame. Same sentence either way, and nobody
                // standing in the room can tell which kind of control they just
                // pressed.
                if (msg.success === false) {
                    this.showFailureMessage(msg.error);
                    this._revertRefusedInteraction(null);
                }
                break;
        }
    }

    /**
     * Remember that a macro was started FROM THIS PANEL.
     *
     * A macro failure is broadcast to every panel on the instance, because a
     * running macro is a fact about the room rather than about one screen.
     * That is right for the busy state on a button and wrong for a message:
     * a band in a space where nobody touched anything is about somebody
     * else's press, and the only panel that can act on it is the one it was
     * made from. There is nothing on the wire that says who started a macro,
     * and nothing needs to be -- the panel that sent it already knows.
     *
     * Both doors go through send(), so this is one hook rather than one per
     * interaction: a preset and a custom control send `macro.execute`
     * themselves, and a binding sends `ui.<interaction>` and lets the server
     * look up what that control runs.
     */
    _claimMacros(msg) {
        if (!msg || typeof msg.type !== 'string') return;
        if (msg.type === 'macro.execute' && msg.macro_id) {
            this._claimMacro(String(msg.macro_id));
            return;
        }
        if (!msg.type.startsWith('ui.') || !msg.element_id) return;
        const def = this.elementMap[msg.element_id]?.elementDef;
        if (!def) return;
        // Every macro this control can run, not just the one this interaction
        // would have reached: a finger landed on it, so a failure from any
        // macro it runs is that person's. Which slot fired is the server's
        // business and it is not worth a second copy of the dispatch rules
        // here -- an off_action, a hold and a value_map entry are all
        // reachable and all theirs.
        //
        // matrix_config as well as the do bindings, because a destination row
        // may carry its OWN route action list that runs instead of the
        // element's. Nothing under `show` is walked: a label bound to a
        // macro's progress is WATCHING it, which is not the same as having
        // started it.
        for (const macroId of this._macrosReachableFrom([
            def.bindings?.do, def.matrix_config,
        ])) {
            this._claimMacro(macroId);
        }
    }

    _claimMacro(macroId) {
        // A fresh press re-arms the report: pressing again after a failure is
        // somebody asking again, and silence would read as having fixed it.
        this._startedMacros[macroId] = { at: Date.now(), reported: false, running: false };
    }

    /**
     * One run of a macro ended. Forget it only when they all have.
     *
     * A macro is a fact about the room, so every panel sees every run of it --
     * and nothing on the wire says which run a frame belongs to. Keyed by macro
     * id alone, the first ending run cleared the busy state and, worse, threw
     * away the claim: press "System On" while a schedule is already running it,
     * that run finishes, and the failure of the run somebody is standing there
     * waiting for arrives to a panel that has forgotten it ever asked. Silence,
     * which is what this whole surface exists to end.
     *
     * Counting the starts is what tells them apart without a run id: every run
     * announces itself on the same socket, so what is in flight is knowable
     * even though which-is-which is not. A start that never happens -- the
     * macro's own overlap guard refused it -- leaves the claim to age out, the
     * case MACRO_CLAIM_MAX_AGE_MS was always for.
     */
    _endMacroRun(macroId) {
        const left = Math.max(0, (this._macroRuns[macroId] || 0) - 1);
        if (left) {
            this._macroRuns[macroId] = left;
            return;
        }
        delete this._macroRuns[macroId];
        delete this._runningMacros[macroId];
        // Only a claim that has seen its run start. One made a moment ago is a
        // press whose run has not been announced yet, and the frame that just
        // arrived is somebody else's.
        const claim = this._startedMacros[macroId];
        if (claim && claim.running) delete this._startedMacros[macroId];
    }

    _macrosReachableFrom(node, found) {
        found = found || new Set();
        if (Array.isArray(node)) {
            for (const entry of node) this._macrosReachableFrom(entry, found);
            return found;
        }
        if (!node || typeof node !== 'object') return found;
        if (node.action === 'macro' && node.macro) found.add(String(node.macro));
        for (const value of Object.values(node)) {
            if (value && typeof value === 'object') this._macrosReachableFrom(value, found);
        }
        return found;
    }

    /**
     * A step of a macro this panel started failed. Say so, once.
     *
     * ONE message per run, the first: a macro that cannot reach a device on
     * step 2 usually cannot reach it on step 3 either, and three sentences
     * swapping through one band in a tenth of a second is unreadable. The
     * first names the thing to go and fix.
     */
    _reportMacroFailure(msg) {
        // Anything this failure happened INSIDE, not just the macro the step
        // belongs to: press "System On", which calls "Projector On", and the
        // frame names the sub-macro nobody at the panel has heard of.
        const chain = Array.isArray(msg.call_chain) && msg.call_chain.length
            ? msg.call_chain
            : [msg.macro_id];
        const claims = [];
        for (const macroId of chain) {
            const claim = this._startedMacros[macroId];
            if (!claim) continue;
            // A claim is normally cleared when its run ends. This covers the
            // one that never sees an end -- the start was throttled, or the
            // socket dropped mid-run -- so a schedule firing the same macro
            // hours later cannot draw a message nobody asked for.
            if (Date.now() - claim.at > MACRO_CLAIM_MAX_AGE_MS) {
                delete this._startedMacros[macroId];
                continue;
            }
            claims.push(claim);
        }
        if (!claims.length) return;
        if (claims.some(claim => claim.reported)) return;
        for (const claim of claims) claim.reported = true;
        this.showFailureMessage(msg.message || msg.error);
        // A step that never reached its device is the same failure as a refused
        // command, arriving on a different frame: the fader that ran the macro
        // is still showing the number somebody dragged it to. Nothing on a
        // macro frame says which control started it, so this takes the
        // last-touched fallback -- which is what claimed the macro in the first
        // place.
        this._revertRefusedInteraction(null);
    }

    /**
     * The "Simulated" tag in a corner while the devices are connected to
     * simulators instead of the real equipment. Simulation stays on through a
     * restart until somebody stops it, so a panel in a space left simulating
     * says so to whoever is standing at it. Never in the IDE's own previews:
     * the IDE says it across the top of every view.
     */
    _updateSimulationTag() {
        if (this.embedded) return;
        let tag = document.getElementById('simulation-tag');
        if (this.state['system.simulation_active'] !== true) {
            if (tag) tag.classList.remove('visible');
            return;
        }
        if (!tag) {
            tag = document.createElement('div');
            tag.id = 'simulation-tag';
            tag.textContent = 'Simulated';
            document.body.appendChild(tag);
        }
        tag.classList.add('visible');
    }

    /**
     * Say why the last thing somebody pressed did not work.
     *
     * The reason has always existed -- the server answers a failed interaction
     * with a sentence written for a person -- and it used to go to a browser
     * console, on a panel screwed to a wall, in kiosk mode, with no keyboard.
     * Everything about how this draws is for that room rather than for a
     * desktop: a band across the whole width instead of a corner card, text
     * sized like the controls around it, and about five seconds to read it.
     *
     * One message at a time, replaced rather than stacked: somebody pressing a
     * dead button four times has one problem, not four, and a wall of cards is
     * how a panel stops being usable at the exact moment it is failing.
     */
    showFailureMessage(text) {
        // One project-level switch, on by default. Off is for a room that draws
        // its own status and would rather we kept quiet -- not a per-element
        // setting, which would be authoring work for something that only ever
        // happens when something is already wrong.
        if (this.uiSettings && this.uiSettings.show_error_messages === false) return;
        const message = String(text == null ? '' : text).trim();
        if (!message) return;

        let band = document.getElementById('panel-failure-message');
        if (!band) {
            band = document.createElement('div');
            band.id = 'panel-failure-message';
            // Announced rather than only drawn: a panel is the whole interface,
            // so somebody using a screen reader on one has nowhere else to find
            // out that the press did nothing.
            band.setAttribute('role', 'alert');
            band.addEventListener('click', () => this.dismissFailureMessage());
            document.body.appendChild(band);
        }
        band.textContent = message;
        band.classList.toggle('at-top', this._lastTouchedIsLow());
        band.classList.add('visible');

        clearTimeout(this._errorMessageTimer);
        this._errorMessageTimer = setTimeout(() => this.dismissFailureMessage(), 5000);
    }

    dismissFailureMessage() {
        clearTimeout(this._errorMessageTimer);
        this._errorMessageTimer = null;
        const band = document.getElementById('panel-failure-message');
        if (band) band.classList.remove('visible');
    }

    /**
     * Put the control a refused command came from back to the value the panel
     * knows.
     *
     * A control moves the instant it is touched, before the server has said
     * anything -- that is what makes a panel feel like hardware, and it is
     * right for every command that works. A refused one changes nothing, so
     * there is no state update coming to overwrite the move: the band
     * explaining the failure is gone in five seconds and the control is not,
     * so what survives is the lie. Somebody who walks up afterwards reads a
     * confident, plausible, wrong number -- and on a fader it is a number they
     * act on. Only a reload heals it, and a wall tablet is the one browser
     * that is never reloaded.
     *
     * The rule: after a refusal the control shows exactly what it would show
     * if it had never been touched. That is a re-evaluation against current
     * state, with the memo forgotten first -- the state did not move, so every
     * memoised renderer would decide it has nothing to do and leave the
     * operator's value standing, which is the whole defect.
     *
     * `elementId` comes off the error frame when the server knows which
     * interaction failed. It does not for a connection-level refusal (a rate
     * limit), on a result ack, or on a macro step error, and those fall back to
     * the control last touched -- the same correlation the failure band has
     * always used, and sound for the same reason: every one of them answers a
     * message this panel has just sent.
     *
     * A control the operator still has hold of is left where it is, and there
     * is deliberately no test for that here: each value renderer already
     * refuses to overwrite a live gesture (`handle._dragging`, a slider's
     * `_dragging`, a cursor in a text box) and a second copy of that judgement
     * in this loop is the one that would drift. Nothing is lost by waiting:
     * the release sends again, so a refusal of that send is what puts the
     * control back. A numeric control retaining keyboard focus after release
     * is no longer an active gesture.
     */
    _revertRefusedInteraction(elementId) {
        const id = elementId || this._lastTouchedElementId;
        if (!id) return;
        this._redrawElement(id);
    }

    /**
     * Draw every binding of one element again from current state, forgetting
     * what each last drew (a renderer that remembers its last value would
     * otherwise skip the write). Asked by the refusal revert and by a cleared
     * `ui.<id>.*` override, both of which need the element as current state
     * says it should look, whatever is on screen.
     */
    _redrawElement(id) {
        for (const b of this.bindings) {
            if (b.elementDef?.id !== id) continue;
            for (const k of Object.keys(b)) {
                if (k.startsWith('_last')) delete b[k];
            }
            try { this._evaluateBinding(b); }
            catch (e) { console.error('Binding error:', e); }
        }
    }

    /**
     * Is the control that was just pressed in the bottom half of the screen?
     *
     * The band sits at the bottom, and moves to the top when the answer is yes.
     * A message drawn over the button somebody still has a finger on is the one
     * place it must not be: they cannot read it, and it looks like the button
     * changed under them. Unknown control -- nothing pressed yet, or it has
     * been re-rendered since -- keeps the default.
     */
    _lastTouchedIsLow() {
        const entry = this._lastTouchedElementId
            ? this.elementMap[this._lastTouchedElementId]
            : null;
        const el = entry && entry.el;
        if (!el || !el.getBoundingClientRect) return false;
        const box = el.getBoundingClientRect();
        if (!box.height && !box.width) return false;
        return (box.top + box.height / 2) > (window.innerHeight / 2);
    }

    _hideLoadingState() {
        const loading = document.getElementById('loading-state');
        if (loading) loading.style.display = 'none';
    }

    /** The page's own status (the loading text, the connection pill and the
     *  offline overlay) goes while an access screen covers the panel. */
    _hidePageStatus() {
        this._hideLoadingState();
        if (this.statusEl) this.statusEl.classList.add('hidden');
        const offline = document.getElementById('offline-overlay');
        if (offline) offline.classList.remove('visible');
    }

    /** ...and comes back when it goes: the pill, and the loading text while
     *  nothing has been drawn yet. */
    _showPageStatus() {
        if (this.statusEl) this.statusEl.classList.remove('hidden');
        if (!this.snapshotReceived) {
            const loading = document.getElementById('loading-state');
            if (loading) loading.style.display = 'flex';
        }
    }

    // --- Navigation ---

    navigateToPage(pageId) {
        // $dismiss — overlay only, no page-history fallback
        if (pageId === '$dismiss') {
            this.dismissOverlay();
            return;
        }
        // $back — phone-style: if an overlay is open, dismiss it;
        // otherwise pop the page-history stack and go there.
        if (pageId === '$back') {
            if (this.overlayStack.length > 0) {
                this.dismissOverlay();
                return;
            }
            const prev = this.pageHistory.pop();
            if (!prev) return; // No history → no-op
            this._navigatingBack = true;
            try { this.navigateToPage(prev); }
            finally { this._navigatingBack = false; }
            return;
        }

        const pages = this.uiDef?.pages || [];
        const targetPage = pages.find(p => p.id === pageId);
        if (!targetPage) return;

        // The message names a press on the page being left, and the control it
        // was avoiding is about to be gone.
        this.dismissFailureMessage();

        const pageType = targetPage.page_type || 'page';

        if (pageType === 'overlay' || pageType === 'sidebar') {
            // Push onto overlay stack
            this.overlayStack.push(pageId);
            this.renderOverlay(targetPage);
        } else {
            // Regular page — push current onto history (so $back can return to
            // it), close all overlays, and switch. Skip the push when we're
            // recursing for $back, and when the target is the same page.
            if (!this._navigatingBack && this.currentPage && this.currentPage !== pageId) {
                this.pageHistory.push(this.currentPage);
                if (this.pageHistory.length > 50) this.pageHistory.shift();
            }
            this.dismissAllOverlays();
            this.currentPage = pageId;
            this.renderCurrentPage();
        }
    }

    dismissOverlay() {
        if (this.overlayStack.length === 0) return;
        const dismissed = this.overlayStack.pop();
        // Closing it in preview means the builder's page selection no longer
        // matches what is on screen, so selecting that page again should reopen
        // it rather than read as "already showing".
        if (this._previewPageId === dismissed) this._previewPageId = null;
        // Remove the topmost overlay DOM element
        const overlayEl = document.querySelector(`.panel-overlay[data-page-id="${dismissed}"]`);
        if (overlayEl) {
            overlayEl.querySelectorAll('[data-element-id]').forEach(el => {
                const eid = el.dataset.elementId;
                if (eid) this._endHold(eid);
            });
            // Clean up clock update closures for overlay elements. Clocks share
            // a single global interval (this._clockInterval) and register their
            // update fn in this._clockElements, so removing the element alone
            // would leave a dead closure running every second forever.
            overlayEl.querySelectorAll('.panel-clock').forEach(el => {
                if (el._clockUpdate) {
                    const idx = this._clockElements.indexOf(el._clockUpdate);
                    if (idx !== -1) this._clockElements.splice(idx, 1);
                }
            });
            // Remove window 'message' listeners registered by plugin iframes in
            // this overlay — otherwise each open/dismiss cycle leaks one stale
            // listener (retaining the removed iframe's closure).
            overlayEl.querySelectorAll('.panel-plugin, .panel-custom').forEach(el => {
                if (el._pluginMessageHandler) {
                    window.removeEventListener('message', el._pluginMessageHandler);
                    this._pluginMessageHandlers.delete(el._pluginMessageHandler);
                }
            });
            overlayEl.classList.add('dismissing');
            overlayEl.addEventListener('transitionend', () => overlayEl.remove(), { once: true });
            // Fallback in case transitionend doesn't fire (e.g., no transition defined)
            setTimeout(() => { if (overlayEl.parentNode) overlayEl.remove(); }, 500);
        }
        // Clean up bindings from the dismissed overlay
        this.bindings = this.bindings.filter(b => {
            const elId = b.elementDef?.id;
            if (!elId) return true;
            return !overlayEl?.querySelector(`[data-element-id="${elId}"]`);
        });
        this._refreshPageNavs();
    }

    dismissAllOverlays() {
        while (this.overlayStack.length > 0) {
            this.overlayStack.pop();
        }
        document.querySelectorAll('.panel-overlay').forEach(el => {
            el.remove();
        });
    }

    renderOverlay(page) {
        const overlay = page.overlay || {};
        const pageType = page.page_type || 'overlay';
        const backdrop = overlay.backdrop || 'dim';
        const animation = overlay.animation || 'fade';
        const dismissOnBackdrop = overlay.dismiss_on_backdrop !== false;

        // Container
        const container = document.createElement('div');
        container.className = `panel-overlay panel-overlay-${animation}`;
        container.dataset.pageId = page.id;

        // Backdrop
        const backdropEl = document.createElement('div');
        backdropEl.className = `overlay-backdrop overlay-backdrop-${backdrop}`;
        if (dismissOnBackdrop) {
            backdropEl.addEventListener('click', (e) => {
                e.stopPropagation();
                this.dismissOverlay();
            });
        }
        container.appendChild(backdropEl);

        // Content panel
        const content = document.createElement('div');

        // The box itself is a percentage of the viewport now, so an overlay
        // scales with the glass instead of being the one part of a panel still
        // pinned to hard pixels.
        if (pageType === 'sidebar') {
            const side = overlay.side || 'right';
            const width = this._pct(overlay.width, OVERLAY_DEFAULTS.sidebarWidth);
            content.className = `overlay-content overlay-sidebar overlay-sidebar-${side}`;
            content.style.width = width + '%';
        } else {
            const width = this._pct(overlay.width, OVERLAY_DEFAULTS.dialogWidth);
            const height = this._pct(overlay.height, OVERLAY_DEFAULTS.dialogHeight);
            const position = overlay.position || 'center';
            content.className = `overlay-content overlay-dialog overlay-pos-${position}`;
            content.style.width = width + '%';
            content.style.height = height + '%';
        }

        // The overlay's surface runs the same renderer as a full page. Its
        // elements are percentages of the overlay box, exactly like a
        // container's children.
        const surface = document.createElement('div');
        surface.className = 'panel-page';
        surface.style.width = '100%';
        surface.style.height = '100%';

        // A dialog can be hand-written too -- it is the same frame, sized to the
        // overlay box instead of the screen, so refusing it here would be a
        // special case to explain rather than one to write.
        const customFrame = this._isCustomPage(page) ? this._renderCustomPageFrame(page) : null;
        if (customFrame) surface.appendChild(customFrame);
        else this._renderPageElements(page, surface);

        this._applyPageBackground(surface, page.background);
        content.appendChild(surface);
        container.appendChild(content);

        // Append to root (on top of everything)
        document.body.appendChild(container);
        this._applyCoordinateSpaces(surface);

        // Keep a newly-opened overlay non-interactive while offline; commands
        // would be silently dropped but local optimistic UI would still flip.
        // setConnectionStatus re-enables overlays on reconnect.
        if (this._offline) container.style.pointerEvents = 'none';

        // Trigger animation
        requestAnimationFrame(() => container.classList.add('active'));

        // Evaluate bindings for new elements
        this.evaluateAllBindings();
    }

    // --- Layout ---

    /** A number, or the fallback when the value isn't one. */
    _pct(value, fallback) {
        const n = typeof value === 'number' ? value : parseFloat(value);
        return Number.isFinite(n) ? n : fallback;
    }

    /** Which way the glass is turned right now. */
    _viewportOrientation() {
        const w = window.innerWidth || document.documentElement.clientWidth || 0;
        const h = window.innerHeight || document.documentElement.clientHeight || 0;
        return w >= h ? 'landscape' : 'portrait';
    }

    /**
     * Move already-rendered elements to new boxes, without a re-render.
     *
     * Design-time only: this is what the builder sends while a control is
     * actually under the pointer. The node to move is the placement box when
     * the element is aspect-locked (that box is what holds the position) and
     * the element itself otherwise.
     */
    _applyLivePlacements(placements) {
        if (!this.editMode || !placements || typeof placements !== 'object') return;
        for (const [id, p] of Object.entries(placements)) {
            if (!p) continue;
            const node = document.querySelector(
                `[data-placement-for="${CSS.escape(id)}"]`,
            ) || document.querySelector(`[data-element-id="${CSS.escape(id)}"]`);
            if (!node) continue;
            node.style.left = `${this._pct(p.x, 0)}%`;
            node.style.top = `${this._pct(p.y, 0)}%`;
            node.style.width = `${this._pct(p.w, 100)}%`;
            node.style.height = `${this._pct(p.h, 100)}%`;
        }
    }

    /**
     * Pin the type scale to a fixed vmin instead of the live viewport.
     * Pass nothing to hand it back to the viewport.
     */
    _applyVminOverride(value) {
        const root = document.documentElement;
        if (value == null || value === '') {
            root.style.removeProperty('--panel-vmin');
            return;
        }
        const px = parseFloat(value);
        if (!Number.isFinite(px) || px <= 0) return;
        root.style.setProperty('--panel-vmin', `${px}px`);
    }

    /**
     * The arrangement this page should render in at this viewport.
     *
     * Pick the layout whose orientation matches the screen, fall back to the
     * primary when none does, then fold the `inherits` chain down so a
     * secondary layout only has to say what moved -- anything it leaves alone
     * follows the layout it inherits from.
     */
    _selectLayout(page) {
        const layouts = Array.isArray(page?.layouts) ? page.layouts : [];
        const empty = { placements: {}, hidden: new Set(), id: null, orientation: null };
        if (!layouts.length) return empty;

        const primary = layouts.find(l => l && l.primary) || layouts[0];
        const wanted = this._viewportOrientation();
        const chosen = layouts.find(l => l && l.orientation === wanted) || primary;
        if (!chosen) return empty;

        // Walk to the root of the inherits chain, then apply from the base
        // down so the chosen layout's own placements win. The seen-set is a
        // cycle guard: a hand-edited project can point two layouts at each
        // other, and the panel still has to draw something.
        const chain = [];
        const seen = new Set();
        let cursor = chosen;
        while (cursor && !seen.has(cursor.id)) {
            seen.add(cursor.id);
            chain.unshift(cursor);
            cursor = cursor.inherits ? layouts.find(l => l && l.id === cursor.inherits) : null;
        }

        const placements = {};
        const hidden = new Set();
        for (const layout of chain) {
            Object.assign(placements, layout.placements || {});
            for (const id of (layout.hidden || [])) hidden.add(id);
        }
        return { placements, hidden, id: chosen.id, orientation: chosen.orientation };
    }

    /**
     * A master element's box for this viewport. Masters carry their own
     * orientation-keyed placements because they have to be valid on every page
     * they appear on, whatever those pages are arranged like.
     */
    _masterPlacement(master) {
        const placements = master?.placements || {};
        return placements[this._viewportOrientation()]
            || placements.landscape
            || placements.portrait
            || Object.values(placements)[0]
            || null;
    }

    /**
     * Which of a master's orientation-keyed boxes _masterPlacement resolved to.
     *
     * The same fallback chain, returning the KEY instead of the box. A tile
     * wall inside a master has to be drawn the shape that key was reviewed
     * against -- review_master_element measures each key against its own
     * screen, so answering "landscape" here for a box picked from `portrait`
     * would publish a floor for a wall nobody draws.
     */
    _masterOrientation(master) {
        const placements = master?.placements || {};
        const viewport = this._viewportOrientation();
        if (placements[viewport]) return viewport;
        if (placements.landscape) return 'landscape';
        if (placements.portrait) return 'portrait';
        return Object.keys(placements)[0] || 'landscape';
    }

    /**
     * The ratio an element holds under a stretch, or null to stretch freely.
     *
     * Deliberately no per-type default here. A renderer that locked, say,
     * every status LED to 1:1 would re-shape elements in a project that was
     * migrated, not authored, against the promise that a migrated panel looks
     * exactly like it used to. The default belongs where the element is
     * created, so a *new* LED is locked and an existing one is left alone.
     */
    _aspectLockFor(element) {
        if (!element || element.aspect_lock == null) return null;
        const n = parseFloat(element.aspect_lock);
        return Number.isFinite(n) && n > 0 ? n : null;
    }

    /**
     * THE placement path. Page elements, container children, overlay contents
     * and master elements all get their box from here: four percentages of
     * whatever they sit inside. That is what makes a panel drawn once the same
     * panel on any screen of the same shape.
     *
     * Returns the node to append, which is the element itself unless it is
     * aspect-locked -- CSS can only hold a ratio when one axis is free, and
     * "shrink to fit, stay centred" needs a box to shrink inside, so a locked
     * element gets a placement box and sits within it.
     */
    _placeElement(el, placement, element) {
        const p = placement || {};
        const x = this._pct(p.x, 0);
        const y = this._pct(p.y, 0);
        const w = this._pct(p.w, 100);
        const h = this._pct(p.h, 100);

        // The author's own classes, for the project stylesheet to target. Every
        // element type sets its own className, so this is the one place both
        // page elements and master elements pass through. It lands on the
        // element itself rather than the aspect-lock wrapper below, because the
        // wrapper is the placement box and the element is what was styled.
        this._applyCssClass(el, element);

        const ratio = this._aspectLockFor(element);
        if (ratio) {
            const box = document.createElement('div');
            box.className = 'panel-placement';
            if (element?.id) box.dataset.placementFor = element.id;
            box.style.position = 'absolute';
            box.style.left = `${x}%`;
            box.style.top = `${y}%`;
            box.style.width = `${w}%`;
            box.style.height = `${h}%`;
            el.style.aspectRatio = String(ratio);
            box.appendChild(el);
            return box;
        }

        el.style.position = 'absolute';
        el.style.left = `${x}%`;
        el.style.top = `${y}%`;
        el.style.width = `${w}%`;
        el.style.height = `${h}%`;
        return el;
    }

    _applyCssClass(el, element) {
        const raw = element?.css_class;
        if (!el || typeof raw !== 'string') return;
        // Space-separated, like the class attribute it becomes. Tokens are
        // filtered rather than handed straight to classList, which throws on an
        // empty or whitespace-bearing name and would take the whole page down
        // over one stray space in a hand-written field.
        for (const name of raw.split(/\s+/)) {
            if (!name) continue;
            try {
                el.classList.add(name);
            } catch {
                console.warn(`[panel] ignoring invalid css_class '${name}' on element '${element?.id}'`);
            }
        }
    }

    /**
     * The project stylesheet (ui.custom_css), paired with element.css_class.
     *
     * Appended to the head so it comes after panel.css and panel-elements.css,
     * and then every declaration in it is re-set as !important -- see
     * _raiseCustomCssPriority for why that is the right call rather than a
     * heavy hand.
     *
     * One style node, reused, and only rewritten when the text actually changes:
     * this runs on every page render, and reassigning textContent re-parses the
     * sheet and forces a restyle even when nothing differs. The priority pass
     * only has to run when the text is new, because CSSOM edits live on the
     * parsed sheet and survive until it is re-parsed.
     */
    _applyCustomCss(css) {
        const text = typeof css === 'string' ? css : '';
        let node = this._customCssNode;
        if (!node || !node.isConnected) {
            node = document.getElementById('panel-custom-css');
        }
        if (!text) {
            if (node) node.remove();
            this._customCssNode = null;
            return;
        }
        if (!node) {
            node = document.createElement('style');
            node.id = 'panel-custom-css';
            document.head.appendChild(node);
        }
        this._customCssNode = node;
        if (node.textContent !== text) {
            node.textContent = text;
            this._raiseCustomCssPriority(node);
        }
    }

    /**
     * Give the project stylesheet the last word, without making the author ask.
     *
     * applyStyle writes colours, corner radius, borders, shadows and text sizes
     * straight onto each control -- from the THEME as much as from anything the
     * author set on that one element -- and an inline style beats a stylesheet
     * rule that does not say !important. So the plain, obvious rule somebody
     * writes first ('.brand-button { background: #8AB493 }') did nothing at all,
     * silently, which is the worst possible introduction to a feature.
     *
     * Making them type !important was never protecting anything: the properties
     * it applies to are exactly the properties they are trying to change. It was
     * a tax on the common case, paid in a confused afternoon.
     *
     * So the panel adds it. The project keeps the author's text verbatim -- the
     * sheet is theirs, raising its priority is ours -- and the parsing is the
     * browser's own, through the CSSOM, rather than a regex over CSS that would
     * be wrong about comments, strings and nesting within the week.
     *
     * The one thing it must not touch is @keyframes: !important inside a
     * keyframe is ignored by the spec, and writing it there can drop the
     * declaration outright, so an animation would break rather than win.
     *
     * What this deliberately does NOT solve is a control that changes its own
     * look to show state -- a button that goes green when the projector is on
     * writes that colour inline at runtime, and now a class rule for the same
     * property outranks it, so the button stops reporting. That conflict exists
     * either way (an author who typed !important got the same result), so it is
     * answered where it can be answered clearly: the Builder warns when a class
     * on an element sets a property that element's own feedback also sets.
     */
    _raiseCustomCssPriority(node) {
        const sheet = node.sheet;
        if (!sheet) return;
        // CSSRule.KEYFRAMES_RULE, with the literal as a fallback for anything
        // that doesn't expose the constant.
        const KEYFRAMES = (window.CSSRule && window.CSSRule.KEYFRAMES_RULE) || 7;
        const raise = (rules) => {
            for (const rule of rules) {
                if (rule.type === KEYFRAMES) continue;
                if (rule.style) this._raiseDeclarations(rule.style);
                // @media / @supports and friends hold rules of their own.
                if (rule.cssRules) raise(rule.cssRules);
            }
        };
        try {
            raise(sheet.cssRules);
        } catch {
            // A sheet the browser won't let us read is a sheet we leave alone;
            // the author's CSS still applies, just at its normal priority.
        }
    }

    /**
     * Re-set one rule's declarations as important.
     *
     * This works on the declaration TEXT rather than property by property, and
     * that is not a style preference -- it is the only thing that survives a
     * custom property inside a shorthand. `background: var(--brand)` cannot be
     * expanded into longhands until the variable is substituted, so the browser
     * lists background-color and friends but hands back an empty string for
     * every one of them. Setting each of those "values" doesn't raise the
     * declaration, it DELETES it: the first version of this shipped a worked
     * example whose green never appeared, and the rule in the sheet had simply
     * lost its background line.
     *
     * The text comes from the browser's own serializer, so it is already
     * normalized -- no comments, no surprises -- and splitting it needs to
     * respect only two things: a `;` inside a quoted string, and a `;` inside
     * parentheses (a data: URI carries one).
     *
     * If the rebuilt text doesn't parse back to the same number of
     * declarations, the original goes back untouched. A stylesheet at normal
     * priority is a disappointment; a stylesheet with rules silently missing is
     * a bug hunt.
     */
    _raiseDeclarations(decls) {
        const original = decls.cssText;
        if (!original) return;
        const parts = this._splitDeclarations(original);
        if (parts.length === 0) return;
        const raised = parts
            .map(d => (/!\s*important\s*$/i.test(d) ? d : `${d} !important`))
            .join('; ') + ';';
        decls.cssText = raised;
        if (this._splitDeclarations(decls.cssText).length !== parts.length) {
            decls.cssText = original;
        }
    }

    /** Split a declaration list on the semicolons that actually separate it. */
    _splitDeclarations(text) {
        const parts = [];
        let buffer = '';
        let quote = null;
        let depth = 0;
        for (let i = 0; i < text.length; i++) {
            const ch = text[i];
            if (ch === '\\') {
                // An escape carries its next character verbatim, quote included.
                buffer += ch + (text[i + 1] || '');
                i++;
                continue;
            }
            if (quote) {
                buffer += ch;
                if (ch === quote) quote = null;
                continue;
            }
            if (ch === '"' || ch === "'") {
                quote = ch;
                buffer += ch;
                continue;
            }
            if (ch === '(') depth++;
            else if (ch === ')') depth = Math.max(0, depth - 1);
            else if (ch === ';' && depth === 0) {
                parts.push(buffer);
                buffer = '';
                continue;
            }
            buffer += ch;
        }
        parts.push(buffer);
        return parts.map(p => p.trim()).filter(Boolean);
    }

    /**
     * Make a container's box and its contents' coordinate space the same box.
     *
     * A child's percentages are OF its container, and CSS resolves an absolutely
     * positioned box against its ancestor's PADDING box -- which is the border
     * box minus the border. So a container with the theme's 1px frame quietly
     * moves everything inside it in by a pixel and shrinks it by two, while the
     * builder measures against the rectangle it drew. The two disagree, and a
     * control shuffles a little every time it moves in or out of a container.
     *
     * An outline draws the same rectangle and takes up no space at all, so the
     * frame stays exactly where it was and the coordinate space becomes the
     * whole box. Only containers need this; a leaf element's border is nobody's
     * frame of reference.
     *
     * A themed or authored border arrives inline, but a stylesheet is just as
     * capable of putting one there -- panel-elements.css does it today, and
     * element css_class plus a project stylesheet is reserved (see the power-
     * user hooks) -- and a border this never saw would shift every child inside
     * it. So the inline value is tried first and the browser is asked second.
     * Asking needs the node in the document, which is why this runs as a pass
     * after the page is attached: computed style on a detached node is empty.
     */
    _applyCoordinateSpaces(root) {
        if (!root || typeof root.querySelectorAll !== 'function') return;
        for (const el of root.querySelectorAll('[data-coordinate-space]')) {
            this._makeCoordinateSpace(el);
        }
    }

    _makeCoordinateSpace(el) {
        // Inline first, and verbatim: it is a CSS expression -- max(1px, Xrem)
        // -- and re-emitting it keeps the frame scaling with the panel the way
        // every other measurement does. Resolving it to px here would pin it
        // until the next re-render.
        let width = el.style.borderWidth;
        let style = el.style.borderStyle;
        let color = el.style.borderColor;
        if (!width || width === '0' || width === '0px') {
            const computed = (typeof getComputedStyle === 'function' && el.isConnected)
                ? getComputedStyle(el)
                : null;
            if (!computed) return;
            const px = Math.max(...[
                computed.borderTopWidth, computed.borderRightWidth,
                computed.borderBottomWidth, computed.borderLeftWidth,
            ].map(v => parseFloat(v) || 0));
            if (!px) return;
            // Sides are uniform in every border the platform itself writes, so
            // the widest is THE frame. An asymmetric one draws as a uniform
            // frame rather than shifting its contents: the coordinate space is
            // the invariant, the exact frame is cosmetic.
            width = `${px}px`;
            style = computed.borderTopStyle;
            color = computed.borderTopColor;
        }
        el.style.outline = `${width} ${style || 'solid'} ${color || 'currentColor'}`;
        el.style.outlineOffset = `calc(0px - ${width})`;
        el.style.borderWidth = '0';
    }

    /**
     * Render one element, and anything that names it as a parent, into `host`.
     *
     * A container is a real parent now: its children are percentages *of it*,
     * so moving or resizing the container carries them along and one
     * visible_when on the container hides the whole group.
     */
    _renderElementTree(element, ctx, host) {
        if (!element || !element.id) return null;
        // Guard against an element listed twice, or a parent cycle a
        // hand-edited project could introduce.
        if (ctx.rendered.has(element.id)) return null;
        ctx.rendered.add(element.id);
        if (ctx.layout.hidden.has(element.id)) return null;

        const el = this.renderElement(element);
        if (!el) return null;
        el.dataset.elementType = element.type;

        const children = ctx.byParent.get(element.id) || [];
        if (children.length) {
            // Containers do not clip. A child nudged past the edge stays
            // visible -- the builder's out-of-bounds badge is what says so,
            // consistent with free positioning warning rather than preventing.
            el.style.overflow = 'visible';
            el.style.pointerEvents = 'auto';
            // Converted to an outline once the page is attached and the browser
            // has resolved every border that reaches this box, inline or not.
            el.dataset.coordinateSpace = '1';
            for (const child of children) {
                this._renderElementTree(child, ctx, el);
            }
        }

        const node = this._placeElement(el, ctx.layout.placements[element.id], element);
        if (ctx.entryAnimation && ctx.entryAnimation !== 'none') {
            this._applyEntryAnimation(node, ctx);
        }
        this.registerVisibleWhen(node, element);
        host.appendChild(node);
        return node;
    }

    _applyEntryAnimation(node, ctx) {
        const index = ctx.order++;
        node.style.opacity = '0';
        const staggerStyle = this.uiSettings.element_stagger_style || 'fade-up';
        const animClass = ctx.entryAnimation === 'stagger'
            ? `element-entry-${staggerStyle}`
            : `element-entry-${ctx.entryAnimation}`;
        setTimeout(() => {
            node.style.opacity = '';
            node.classList.add(animClass);
        }, ctx.staggerMs * index);
    }

    /**
     * Render every element of a page onto a surface, in the arrangement this
     * viewport calls for. Shared by full pages and by overlays/sidebars, which
     * used to carry their own copy of the placement math -- and were the last
     * part of a panel still measured in hard pixels because of it.
     */
    _renderPageElements(page, surface, opts = {}) {
        const layout = this._selectLayout(page);
        // Which way round the arrangement being drawn is. A tile wall's grid
        // follows it (matrixTileGridShape in modules/matrix.js), and it is the LAYOUT's orientation
        // rather than the viewport's on purpose: a page with no portrait
        // arrangement falls back to its landscape one, and the review measured
        // that arrangement as landscape. The two have to agree.
        this._drawnOrientation = layout.orientation || 'landscape';
        const elements = Array.isArray(page.elements) ? page.elements : [];

        // Group by parent. A parent id that names nothing on this page is
        // treated as page-level, so an orphaned child still draws.
        const ids = new Set(elements.map(e => e && e.id).filter(Boolean));
        const byParent = new Map();
        for (const element of elements) {
            if (!element || !element.id) continue;
            const key = (element.parent && ids.has(element.parent)) ? element.parent : null;
            if (!byParent.has(key)) byParent.set(key, []);
            byParent.get(key).push(element);
        }

        const ctx = {
            layout,
            byParent,
            rendered: new Set(),
            order: 0,
            entryAnimation: opts.entryAnimation || 'none',
            staggerMs: opts.staggerMs || 30,
        };
        for (const element of (byParent.get(null) || [])) {
            this._renderElementTree(element, ctx, surface);
        }
        return layout;
    }

    /**
     * Re-render when the glass changes orientation, so a page with a portrait
     * layout picks it up. Nothing else needs a re-render: percentage geometry
     * reflows on its own, and redrawing on every resize tick would restart
     * entry animations for no reason.
     */
    _setupViewportListener() {
        if (this._viewportListenerSetup) return;
        this._viewportListenerSetup = true;
        let last = this._viewportOrientation();
        const onChange = () => {
            const now = this._viewportOrientation();
            if (now === last) return;
            last = now;
            if (this.uiDef) this.renderCurrentPage();
        };
        window.addEventListener('resize', onChange);
        window.addEventListener('orientationchange', onChange);
    }

    // --- Rendering ---

    /**
     * Show a page the way the runtime would, when an embedder names one.
     *
     * The builder hands the preview a page id, and for a regular page setting
     * `currentPage` and re-rendering IS what the runtime does. An overlay is
     * the exception: at runtime it arrives through navigateToPage, which is the
     * only place `page_type` is read, so assigning currentPage instead rendered
     * a dialog as a flat page with no backdrop -- and left `overlayStack` empty,
     * so its Cancel button (`$back`) found nothing to dismiss and silently did
     * nothing. The dialog worked on real glass and looked broken in preview,
     * which is the worst way for a test surface to be wrong.
     *
     * An overlay needs something behind it, so a regular page is drawn first.
     * That page is also where `$back` lands, exactly as it would in the field.
     * Edit mode keeps the flat render: authoring a dialog means seeing its
     * contents, not a backdrop.
     */
    _showPageAsRuntimeWould(pageId) {
        const pages = this.uiDef?.pages || [];
        const target = pages.find(p => p.id === pageId);
        const type = target?.page_type || 'page';
        // What the embedder last asked for, which is NOT `currentPage` once an
        // overlay is open -- that holds the page drawn behind it. Without this
        // the next editor-page for the same dialog reads as a move and reopens
        // it, so a vmin nudge or an unrelated edit makes it flicker.
        this._previewPageId = pageId;
        if (!target || (type !== 'overlay' && type !== 'sidebar')) {
            this.currentPage = pageId;
            this.renderCurrentPage();
            return;
        }
        const behind = pages.find(p => p.id === this.currentPage && (p.page_type || 'page') === 'page')
            || pages.find(p => (p.page_type || 'page') === 'page');
        if (behind) {
            this.currentPage = behind.id;
            this.renderCurrentPage();
        }
        this.navigateToPage(pageId);
    }

    renderCurrentPage() {
        if (!this.uiDef) return;
        // Everything below is about to be replaced, including whatever node an
        // in-place edit was holding. Let go of it first: a commit from a
        // detached node would write text the author can no longer see.
        if (this._textEdit) {
            this._textEdit = null;
            this._postToParent({ type: 'openavc:editor-text-closed' });
        }

        const pages = this.uiDef.pages || [];
        let page = pages.find(p => p.id === this.currentPage);
        if (!page) {
            if (pages.length > 0) {
                page = pages.find(p => (p.page_type || 'page') === 'page') || pages[0];
                this.currentPage = page.id;
            } else {
                this.root.textContent = '';
                const emptyMsg = document.createElement('div');
                emptyMsg.style.cssText = 'padding:2rem;text-align:center;color:var(--panel-text);opacity:0.5;';
                emptyMsg.textContent = 'No panels configured';
                this.root.appendChild(emptyMsg);
                return;
            }
        }

        // Clean up overlays
        this.dismissAllOverlays();

        this._endAllHolds();
        for (const t of this.debounceTimers) clearTimeout(t);
        this.debounceTimers = [];
        endFaderDrags(this.root);
        endMatrixGestures(this.root);
        // Clean up global clock interval
        if (this._clockInterval) {
            clearInterval(this._clockInterval);
            this._clockInterval = null;
        }
        this._clockElements = [];

        // Page transition settings — disabled in edit mode so the designer isn't
        // fighting re-entry animations on every live-preview rebuild.
        const settings = this.uiSettings || {};
        const pageTransition = this.editMode ? 'none' : (settings.page_transition || 'none');
        const transitionDuration = settings.page_transition_duration || 200;
        const entryAnimation = this.editMode ? 'none' : (settings.element_entry || 'none');
        const staggerMs = settings.element_stagger_ms || 30;

        // Set transition duration CSS variable
        this.root.style.setProperty('--page-transition-duration', transitionDuration + 'ms');

        // If page transition is enabled and there's existing content, animate out
        const oldGrid = this.root.querySelector('.panel-page');
        if (oldGrid && pageTransition !== 'none') {
            oldGrid.classList.add(`page-exit-${pageTransition}`);
            oldGrid.style.position = 'absolute';
            oldGrid.style.inset = '0';
            setTimeout(() => oldGrid.remove(), transitionDuration);
        } else {
            this.root.innerHTML = '';
        }

        // Clean up all plugin iframe message handlers
        for (const handler of this._pluginMessageHandlers) {
            window.removeEventListener('message', handler);
        }
        this._pluginMessageHandlers.clear();

        clearMatrixOverlays();

        this.bindings = [];
        this.elementMap = {};
        // Frames from the page being replaced are about to be discarded, so
        // drop what they were waiting to hear -- a callback held here keeps the
        // element it draws into alive. Every frame this render builds registers
        // again below.
        this._iframeStatusWaiters.clear();

        // Apply theme
        this.applyTheme(this.uiDef.settings || {});

        // The project stylesheet goes on after the theme, so an author's rule
        // can override what a theme variable produced.
        this._applyCustomCss(this.uiDef.custom_css);

        // Make root relative for absolute positioning during transitions
        this.root.style.position = 'relative';
        this.root.style.overflow = 'hidden';

        // The page surface. Elements are absolutely positioned inside it in
        // percentages, so the same design fills any screen of the same shape.
        const surface = document.createElement('div');
        surface.className = 'panel-page';

        // Apply per-page background
        this._applyPageBackground(surface, page.background);

        // Apply page enter animation
        if (pageTransition !== 'none') {
            surface.classList.add(`page-enter-${pageTransition}`);
        }

        // A custom page is the author's own markup filling the screen, so there
        // is no ruler to draw on and nothing to snap to.
        const customFrame = this._isCustomPage(page) ? this._renderCustomPageFrame(page) : null;
        if (customFrame) {
            // BEFORE the master elements, not after: every child of .panel-page
            // gets the same z-index, so the later sibling wins, and a full-box
            // frame appended last would paint over every master element on the
            // page. A master nav bar is how somebody gets off a custom page.
            surface.appendChild(customFrame);
        } else {
            // Design-time snap overlay. The grid is a ruler now rather than a
            // container, so this only draws where things will snap to -- showing
            // or hiding it moves nothing.
            this._renderSnapOverlay(page, surface);
        }

        // Master elements come before the page's own, so DOM order alone keeps
        // them behind. They used to carry an inline z-index for that, which
        // also dropped them behind the page's gradient layer. On a custom page
        // that puts them AFTER the frame, which is the other half of the same
        // rule: they draw over the author's markup, and are the way off it.
        const masterElements = this.uiDef.master_elements || [];
        for (const mEl of masterElements) {
            const mPages = mEl.pages;
            const showOnPage = mPages === '*' || (Array.isArray(mPages) && mPages.includes(page.id));
            if (!showOnPage || mEl.hidden) continue;
            this._drawnOrientation = this._masterOrientation(mEl);
            const el = this.renderElement(mEl);
            if (!el) continue;
            el.dataset.elementType = mEl.type;
            const node = this._placeElement(el, this._masterPlacement(mEl), mEl);
            this.registerVisibleWhen(node, mEl);
            surface.appendChild(node);
        }

        if (!customFrame) {
            this._renderPageElements(page, surface, { entryAnimation, staggerMs });
        }

        // A page nobody has put anything on says so, in the room's own terms.
        // Deliberately says nothing about the Programmer: this same document is
        // what a wall panel shows, and on a kiosk display the reader is an end
        // user who has no business being sent to the IDE. Not in edit mode --
        // the Builder canvas embeds this page with `edit=1`, and an empty page
        // there is an author one drop away from their first control.
        if (!this.editMode && this._pageDrawsNothing(page)) {
            const notice = document.createElement('div');
            notice.className = 'panel-page-empty';
            notice.textContent = 'This page has no controls yet.';
            surface.appendChild(notice);
        }

        this.root.appendChild(surface);
        this._applyCoordinateSpaces(surface);
        this.evaluateAllBindings();

        // Theme Studio direct manipulation — click any element in the preview
        // to jump to its section in the editor. Hover shows an outline + type label.
        if (this.editMode) {
            this._setupThemeStudioInteraction(surface);
            this._reportTextDefaults();
        }
    }

    /**
     * Tell the Builder what size each element's text STARTS at: the value the
     * cascade gives it before the element's own font_size, if it has one, is
     * applied inline. The Style panel shows that as the Font Size placeholder,
     * so the number a person reads there is the number on the canvas, whatever
     * the stylesheet, the theme or a project class decided it. The Builder
     * keeps no copy of the panel's default; this message is how it knows.
     *
     * Read by clearing the inline size, asking, and putting it back. There is
     * no other way to ask the cascade what it would have said, and nothing
     * transitions font-size, so the read is exact and nothing flickers.
     *
     * In rem, the unit a project stores and the Builder converts for display.
     * Pixels here would be the canvas preset's pixels, not the panel's.
     */
    _reportTextDefaults() {
        if (window.parent === window) return;
        const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize);
        if (!rootPx) return;
        const fontSizeRem = {};
        // Which elements have text an author can edit in place. Reported rather
        // than derived from a table of types, because the renderer is the only
        // thing that knows: it is per ELEMENT, not per type -- an icon-only
        // button draws no words, a bound label draws a format string, and a
        // button whose label a script is overriding has no authored source at
        // all. Runs after evaluateAllBindings, so the records have settled.
        const editableText = [];
        const editableTypes = PanelApp.IN_PLACE_TEXT_TYPES;
        const drawnTypes = this._drawnElementTypes();
        // Elements drawing words that are NOT the author's to edit here, and
        // why. Without this the Builder has only "editable or not" and a
        // double-click on the second kind is a silent no-op -- which is how a
        // button whose label a script is setting would look simply broken.
        // Only cases worth a sentence are listed: a control that draws no text
        // at all says nothing, because nothing is expected of it.
        const textRefusals = {};
        for (const el of this.root.querySelectorAll('[data-element-id]')) {
            const id = el.dataset.elementId;
            const offerable = editableTypes.has(drawnTypes[id]);
            if (offerable && el.hasAttribute('data-avc-text-src')) {
                editableText.push(id);
            } else if (offerable
                       && this.state[`ui.${id}.label`] !== undefined
                       && this.state[`ui.${id}.label`] !== null) {
                textRefusals[id] = `This text is being set by a script or macro `
                    + `through ui.${id}.label, not by the element's Label. Change the `
                    + `Label in Properties, or clear that key to show it again.`;
            }
            const own = el.style.fontSize;
            if (own) el.style.fontSize = '';
            const px = parseFloat(getComputedStyle(el).fontSize);
            if (own) el.style.fontSize = own;
            // Four decimals, the precision a project stores measurements at, so
            // a size that IS the default compares equal to it in the Builder.
            if (Number.isFinite(px)) fontSizeRem[el.dataset.elementId] = Math.round(px / rootPx * 1e4) / 1e4;
        }
        this._postToParent({
            type: 'openavc:editor-text-defaults', fontSizeRem, editableText,
            textRefusals,
        });
    }

    /**
     * Draw the snap increment behind the page while designing. Two repeating
     * gradients rather than a div per cell: the increment is a float now
     * (8.3333% is the old 12-column spacing), and a background-size in percent
     * lands on it exactly at any page size.
     */
    _renderSnapOverlay(page, surface) {
        if (!this.editMode || this._editShowGrid === false) return;
        // The ruler draws whenever the builder asks for it, snapping on or
        // off — the grid toggle controls what you SEE, the snap toggle what
        // pulls. Keying this off snap.enabled made the grid button do nothing
        // on a page with snapping switched off.
        const snap = page.snap || {};
        const stepX = this._pct(snap.x, SNAP_FALLBACK.x);
        const stepY = this._pct(snap.y, SNAP_FALLBACK.y);
        if (!(stepX > 0) || !(stepY > 0)) return;

        const overlay = document.createElement('div');
        overlay.className = 'panel-page-snap-overlay';
        const line = 'rgba(255,255,255,0.18)';
        overlay.style.cssText = [
            'position: absolute',
            'inset: 0',
            'pointer-events: none',
            'z-index: 0',
            `background-image: linear-gradient(to right, ${line} 1px, transparent 1px),`
                + ` linear-gradient(to bottom, ${line} 1px, transparent 1px)`,
            `background-size: ${stepX}% ${stepY}%`,
        ].join(';');
        surface.appendChild(overlay);
    }

    _setupThemeStudioInteraction(grid) {
        const TYPE_LABELS = {
            button: 'Button', label: 'Label', slider: 'Slider', fader: 'Fader',
            select: 'Select', text_input: 'Text Input', status_led: 'Status LED',
            gauge: 'Gauge', level_meter: 'Level Meter', list: 'List', matrix: 'Matrix',
            group: 'Group', image: 'Image', clock: 'Clock',
            page_nav: 'Page Nav', camera_preset: 'Camera Preset', keypad: 'Keypad',
        };

        const tooltip = document.createElement('div');
        tooltip.style.cssText = [
            'position: fixed', 'padding: 2px 6px', 'font-size: 10px', 'font-weight: 600',
            'background: rgba(0,0,0,0.8)', 'color: #fff', 'border-radius: 3px',
            'pointer-events: none', 'z-index: 9999', 'display: none', 'white-space: nowrap',
        ].join(';');
        document.body.appendChild(tooltip);

        let hoveredEl = null;

        const findPanelElement = (target) => {
            let el = target;
            while (el && el !== grid) {
                if (el.dataset && el.dataset.elementType) return el;
                el = el.parentElement;
            }
            return null;
        };

        grid.addEventListener('mousemove', (e) => {
            const panelEl = findPanelElement(e.target);
            if (panelEl === hoveredEl) {
                if (panelEl) {
                    const r = panelEl.getBoundingClientRect();
                    tooltip.style.left = (r.left + r.width / 2 - tooltip.offsetWidth / 2) + 'px';
                    tooltip.style.top = (r.top - 22) + 'px';
                }
                return;
            }
            if (hoveredEl) {
                hoveredEl.style.outline = '';
                hoveredEl.style.outlineOffset = '';
            }
            hoveredEl = panelEl;
            if (panelEl) {
                panelEl.style.outline = '2px solid rgba(33,150,243,0.7)';
                panelEl.style.outlineOffset = '-2px';
                const type = panelEl.dataset.elementType;
                tooltip.textContent = TYPE_LABELS[type] || type;
                tooltip.style.display = 'block';
                const r = panelEl.getBoundingClientRect();
                tooltip.style.left = (r.left + r.width / 2 - tooltip.offsetWidth / 2) + 'px';
                tooltip.style.top = (r.top - 22) + 'px';
            } else {
                tooltip.style.display = 'none';
            }
        });

        grid.addEventListener('mouseleave', () => {
            if (hoveredEl) {
                hoveredEl.style.outline = '';
                hoveredEl.style.outlineOffset = '';
                hoveredEl = null;
            }
            tooltip.style.display = 'none';
        });

        grid.addEventListener('click', (e) => {
            const panelEl = findPanelElement(e.target);
            if (!panelEl) return;
            e.preventDefault();
            e.stopPropagation();
            if (hoveredEl) {
                hoveredEl.style.outline = '';
                hoveredEl.style.outlineOffset = '';
                hoveredEl = null;
            }
            tooltip.style.display = 'none';
            const elType = panelEl.dataset.elementType;
            this._postToParent({
                type: 'openavc:theme-element-click',
                elementType: elType,
                elementId: panelEl.dataset.elementId,
            });
        }, true);
    }

    /**
     * Register a visible_when binding for an element if it has one.
     * Call this after renderElement() for every element placed on a page.
     */
    registerVisibleWhen(el, element) {
        const vw = element.bindings?.show?.visible_when;
        if (!vw) return;

        // Single condition, compound AND (all:[...]), or compound OR (any:[...])
        const conditions = vw.all || vw.any || [vw];
        const mode = vw.any ? 'any' : 'all';
        const keys = conditions.map(c => c.key).filter(Boolean);

        this.bindings.push({
            type: 'visible_when',
            element: el,
            elementDef: element,
            binding: { conditions, mode, _keys: keys },
        });
    }

    renderElement(element) {
        const el = this._renderElementOfType(element);
        // Every control is in the map: a macro or script can override any of
        // them (ui.<id>.*), and the same entry is where the unavailable marks,
        // a running macro's busy state and a press's failure claim find it.
        if (el && element.id) this.elementMap[element.id] = { el, elementDef: element };
        return el;
    }

    _renderElementOfType(element) {
        switch (element.type) {
            case 'button':        return this.renderButton(element);
            case 'label':         return this.renderLabel(element);
            case 'status_led':    return this.renderStatusLed(element);
            case 'slider':        return this.renderSlider(element);
            case 'page_nav':      return this.renderPageNav(element);
            case 'select':        return this.renderSelect(element);
            case 'text_input':    return this.renderTextInput(element);
            case 'image':         return this.renderImage(element);
            case 'camera_preset': return this.renderCameraPreset(element);
            case 'list':          return this.renderList(element);
            case 'matrix':        return this.renderMatrix(element);
            case 'gauge':         return this.renderGauge(element);
            case 'level_meter':   return this.renderLevelMeter(element);
            case 'fader':         return this.renderFader(element);
            case 'group':         return this.renderGroup(element);
            case 'clock':         return this.renderClock(element);
            case 'keypad':        return this.renderKeypad(element);
            case 'plugin':        return this.renderPluginElement(element);
            case 'custom':        return this.renderCustomElement(element);
            default:
                console.warn('Unknown element type:', element.type);
                return null;
        }
    }

    /** End a button's hold-repeat: a held button sends its press on a timer
     *  until it is released. */
    _endHold(elementId) {
        if (this.holdTimers[elementId]) {
            clearInterval(this.holdTimers[elementId]);
            delete this.holdTimers[elementId];
        }
    }

    /** End every hold-repeat. A press can lose its release: the panel goes to
     *  the background, its socket drops (and the panel stops taking
     *  touches), a new page is drawn, or a screen covers the panel. A timer
     *  left running would go on pressing the button. */
    _endAllHolds() {
        for (const t of Object.values(this.holdTimers)) clearInterval(t);
        this.holdTimers = {};
    }

    renderButton(element) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'panel-element panel-button';
        el.textContent = element.label || '';
        el.dataset.elementId = element.id;
        this._recordTextSource(el, ['label']);
        el._baseAccessibleName = element.label || element.id;
        el.setAttribute('aria-label', el._baseAccessibleName);

        // Apply static styles (theme defaults merged)
        const themedStyle = this.getThemedStyle('button', element.style);
        applyStyle(el, themedStyle);

        // Frameless: hide chrome so the image acts as the button
        if (element.frameless) applyFrameless(el);

        const displayMode = element.display_mode || 'text';
        const showImage = this._buttonShowsOwnPicture(element) && element.button_image;

        // Clear label text for image-only/icon-only modes BEFORE content/layer rendering
        if (!this._buttonShowsWords(element)) {
            el.textContent = '';
            // The label still exists in data and is still the accessible name,
            // but nothing is on screen to put a caret in.
            this._recordTextSource(el, null);
        }
        if (displayMode === 'icon_only' && !element.icon_position) {
            element.icon_position = 'center';
        }

        // Render icon+text content first (may call el.textContent = '' internally to rebuild).
        // Image layer must be prepended AFTER this so content rendering can't wipe it.
        this.renderElementContent(el, element);

        // Image effect last so its DOM layer isn't removed by other content rendering paths
        if (showImage) {
            applyImageEffect(el, element.button_image, this._pictureOptions(element, themedStyle.bg_color));
            if (displayMode === 'image_text') {
                el.style.textShadow = '0 1px 0.2143rem rgba(0,0,0,0.8)';
            }
        }

        // Button mode: tap (default), toggle, hold_repeat, tap_hold
        // Press binding is an array of actions; mode properties come from the first action
        const pressActions = element.bindings?.do?.press || [];
        const pressBinding = (Array.isArray(pressActions) ? pressActions[0] : pressActions) || {};
        const mode = pressBinding.mode || 'tap';
        const holdRepeatMs = pressBinding.hold_repeat_ms || 200;
        const holdThresholdMs = pressBinding.hold_threshold_ms || 500;

        // Toggle without toggle_key falls back to tap mode
        const effectiveMode = (mode === 'toggle' && !pressBinding.toggle_key) ? 'tap' : mode;

        let pressTime = 0;
        let pressActive = false;

        const onPress = (e) => {
            e.preventDefault();
            el.classList.add('pressing');
            pressTime = Date.now();
            pressActive = true;

            // The release must be un-missable: kiosk WebViews and mobile
            // browsers sometimes swallow the element-level touchend (gesture
            // interception, system dialogs), and a hold-repeat interval that
            // outlives the physical press fires its action forever. Window-
            // level one-shot fallbacks end the press no matter where (or
            // whether) the browser delivers the release event.
            const winEnd = (ev) => {
                winCleanup();
                onRelease(ev);
            };
            const winCleanup = () => {
                window.removeEventListener('mouseup', winEnd);
                window.removeEventListener('touchend', winEnd);
                window.removeEventListener('touchcancel', winEnd);
                window.removeEventListener('blur', winEnd);
            };
            window.addEventListener('mouseup', winEnd);
            window.addEventListener('touchend', winEnd, { passive: true });
            window.addEventListener('touchcancel', winEnd, { passive: true });
            window.addEventListener('blur', winEnd);

            if (effectiveMode === 'hold_repeat') {
                this.send({ type: 'ui.press', element_id: element.id });
                // Clear any existing timer before starting a new one
                this._endHold(element.id);
                this.holdTimers[element.id] = setInterval(() => {
                    this.send({ type: 'ui.press', element_id: element.id });
                }, holdRepeatMs);
            } else if (effectiveMode === 'tap') {
                this.send({ type: 'ui.press', element_id: element.id });
            } else if (effectiveMode === 'toggle') {
                const toggleKey = pressBinding.toggle_key;
                const toggleValue = pressBinding.toggle_value;
                const stateValue = this.state[toggleKey];
                const isActive = stateValue !== undefined && toggleValue !== undefined &&
                    String(stateValue).toLowerCase() === String(toggleValue).toLowerCase();
                if (isActive) {
                    this.send({ type: 'ui.toggle_off', element_id: element.id });
                } else {
                    this.send({ type: 'ui.press', element_id: element.id });
                }
            }
            // tap_hold: nothing on press — decided on release
        };
        const onRelease = (e) => {
            // Element handler and window fallback both route here; only the
            // first one for a given press does anything.
            if (!pressActive) return;
            pressActive = false;
            if (e && e.cancelable && e.preventDefault) e.preventDefault();
            el.classList.remove('pressing');

            if (effectiveMode === 'hold_repeat') {
                this._endHold(element.id);
            } else if (effectiveMode === 'tap_hold') {
                const held = Date.now() - pressTime;
                if (held >= holdThresholdMs) {
                    this.send({ type: 'ui.hold', element_id: element.id });
                } else {
                    this.send({ type: 'ui.press', element_id: element.id });
                }
            }

            this.send({ type: 'ui.release', element_id: element.id });
        };

        el.addEventListener('mousedown', onPress);
        el.addEventListener('mouseup', onRelease);
        el.addEventListener('mouseleave', () => {
            // Hold ends when the pointer leaves; the press cycle itself is
            // closed by the window-level mouseup fallback.
            el.classList.remove('pressing');
            this._endHold(element.id);
        });
        el.style.touchAction = 'none';
        el.addEventListener('touchstart', onPress);
        el.addEventListener('touchend', onRelease);
        el.addEventListener('touchcancel', onRelease);

        // Appearance (state-driven look) binding
        if (element.bindings?.show?.look) {
            this.bindings.push({
                type: 'feedback',
                element: el,
                elementDef: element,
                binding: element.bindings.show.look,
            });
        } else if (effectiveMode === 'toggle') {
            // A toggle button's own indication. Registered ONLY when the
            // element has no look binding of its own: that binding is the
            // author saying what this button looks like, and two bindings
            // writing one element's colour and label is how the two drift
            // into disagreeing.
            this.bindings.push({
                type: 'toggle_look',
                element: el,
                elementDef: element,
                binding: {
                    key: pressBinding.toggle_key,
                    value: pressBinding.toggle_value,
                    on_label: pressBinding.on_label,
                    off_label: pressBinding.off_label,
                    // Where those two words are authored, so an in-place edit
                    // writes the one the button is actually showing. The press
                    // binding is an array whose first action carries the mode.
                    _labelBase: Array.isArray(pressActions)
                        ? ['bindings', 'do', 'press', 0]
                        : ['bindings', 'do', 'press'],
                },
            });
        }

        return el;
    }

    renderLabel(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-label';
        el.dataset.elementId = element.id;

        const text = element.text || '';
        const whiteSpace = element.style?.white_space;
        if (whiteSpace) {
            el.innerHTML = this._formatRichText(text);
        } else {
            el.textContent = text;
        }
        // Rich or not, what the author owns is the source string -- markup and
        // all -- so the path is the same and the editor reveals `**bold**`.
        this._recordTextSource(el, ['text']);

        applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.renderElementContent(el, element);

        // Text binding (the label's value)
        if (element.bindings?.show?.value) {
            const textBinding = element.bindings.show.value;
            if (textBinding.source === 'macro_progress') {
                // Macro progress label: show step descriptions while macro runs
                this.bindings.push({
                    type: 'macro_progress',
                    element: el,
                    elementDef: element,
                    binding: textBinding,
                });
                // Set initial idle text
                el.textContent = textBinding.idle_text || '';
                this._recordTextSource(el, ['bindings', 'show', 'value', 'idle_text']);
            } else {
                this.bindings.push({
                    type: 'text',
                    element: el,
                    elementDef: element,
                    binding: textBinding,
                });
            }
        }

        // Appearance (state-driven look) binding, same as a button's. A label
        // showing a device's status wants the words and the colour to track the
        // state -- ONLINE in green, OFFLINE in red -- and before this the only
        // ways to get that were two stacked labels with opposite visible_when,
        // or a button dressed up to look like a label.
        if (element.bindings?.show?.look) {
            this.bindings.push({
                type: 'label_look',
                element: el,
                elementDef: element,
                binding: element.bindings.show.look,
            });
        }

        return el;
    }

    renderStatusLed(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-status-led';
        el.dataset.elementId = element.id;

        // Apply theme element_defaults so wrapper bg / border / radius pick
        // up the theme. Without this, status_led ignored theme styling.
        applyStyle(el, this.getThemedStyle('status_led', element.style));

        const dot = document.createElement('div');
        dot.className = 'led-dot';
        el.appendChild(dot);

        if (element.label) {
            const label = document.createElement('label');
            label.className = 'led-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }

        // Color binding (status LED look)
        if (element.bindings?.show?.look) {
            this.bindings.push({
                type: 'color',
                element: dot,
                elementDef: element,
                binding: element.bindings.show.look,
            });
        }

        return el;
    }

    renderSlider(element) {
        const slider = new SliderControl(element, this._levelHost(element.id));
        const el = document.createElement('div');
        el.className = 'panel-element panel-slider' + (slider.vertical ? ' panel-slider-vertical' : '');
        el.dataset.elementId = element.id;

        // The theme's element_defaults style the wrapper: background, border,
        // radius, shadow.
        const themedSliderStyle = this.getThemedStyle('slider', element.style);
        applyStyle(el, themedSliderStyle);

        // Thumb size: per-element value wins, otherwise theme element_default, otherwise 44.
        el.style.setProperty(
            '--thumb-size',
            (element.thumb_size ?? themedSliderStyle.thumb_size ?? 44 / REM_BASE_PX) + 'rem',
        );

        if (element.label) {
            const label = document.createElement('label');
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }
        slider.mount(el);

        // Value binding (read; two-way when show.value.write_back)
        const valueBinding = element.bindings?.show?.value;
        if (valueBinding) {
            this.bindings.push({
                type: 'slider_value',
                element: slider.input,
                elementDef: element,
                binding: valueBinding,
                _slider: slider,
            });
        }

        return el;
    }

    /**
     * What a slider or fader borrows from the panel, read when called: the
     * state object is replaced on a full update, the timer list on a page
     * redraw. `bindingOf` gives the control's value binding once it is
     * registered; a slider never asks whether its device is reachable.
     */
    _levelHost(elementId, bindingOf = () => null) {
        return {
            read: (key) => this.state[key],
            sendValue: (value) => this.send({ type: 'ui.change', element_id: elementId, value }),
            keepTimer: (timer) => this.debounceTimers.push(timer),
            design: () => this.editMode,
            offline: () => {
                const binding = bindingOf();
                return !!binding && this._bindingOffline(binding);
            },
        };
    }

    renderPageNav(element) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'panel-element panel-page-nav';
        el.dataset.elementId = element.id;

        if (element.target_page) {
            el.textContent = element.label || element.target_page;
            el.setAttribute('aria-label', `Navigate to ${element.label || element.target_page}`);
            this._recordTextSource(el, ['label']);
            el.addEventListener('click', () => {
                // The server replies with ui.navigate to this panel only.
                // Moving here too opens overlays twice and consumes two Back
                // steps. All moves use the reply handler, including $back.
                this.send({ type: 'ui.page', page_id: element.target_page });
            });
        } else {
            el.textContent = element.label || 'No Target';
            this._recordTextSource(el, ['label']);
            el.disabled = true;
            el.style.opacity = '0.5';
        }

        applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.renderElementContent(el, element);

        // Appearance, and lit while its page is showing. One binding and one
        // evaluator for both, because both write the same colours.
        if (element.target_page) {
            if (element.bindings?.show?.look) {
                this.bindings.push({
                    type: 'nav_look',
                    element: el,
                    elementDef: element,
                    binding: element.bindings.show.look,
                });
            } else if (element.highlight_current) {
                // Nothing to read from state: drawn when the page changes and
                // never on a state batch, which an empty key list matches none of.
                this.bindings.push({
                    type: 'nav_look',
                    element: el,
                    elementDef: element,
                    binding: { _keys: [] },
                });
            }
        }
        return el;
    }

    /**
     * Whether a Page Nav set to light up has its page on this panel's screen:
     * the page itself, or an overlay or sidebar open over it.
     *
     * From this panel's own `currentPage` and overlay stack, never from state.
     * State reaches every panel in the building, and one tablet changing page
     * must not light the tabs on another.
     */
    _navShowsItsPage(elementDef) {
        if (!elementDef.highlight_current) return false;
        const target = elementDef.target_page;
        // `$back` and `$dismiss` are moves, not places.
        if (!target || target.startsWith('$')) return false;
        return this.currentPage === target || this.overlayStack.includes(target);
    }

    /**
     * Redraw every Page Nav after an overlay closes. Opening one evaluates
     * everything, and a page change renders everything again, but closing one
     * re-renders nothing under it, so a tab to the overlay would stay lit.
     */
    _refreshPageNavs() {
        for (const b of this.bindings) {
            if (b.type !== 'nav_look') continue;
            try { this._evaluateBinding(b); }
            catch (e) { console.error('Binding error:', e); }
        }
        this.evaluateUiOverrides();
    }

    renderSelect(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-select';
        el.dataset.elementId = element.id;

        if (element.label) {
            const label = document.createElement('label');
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }

        const select = document.createElement('select');
        const options = element.options || [];
        // Per-option styling (show.look.style_map, authored in the UI
        // Builder's Appearance card). Option colors show in the open list
        // where the browser supports styling native options.
        const lookBinding = element.bindings?.show?.look;
        const styleMap = lookBinding && lookBinding.style_map ? lookBinding.style_map : null;
        for (const opt of options) {
            const option = document.createElement('option');
            option.value = opt.value;
            option.textContent = opt.label;
            const optStyle = styleMap && styleMap[opt.value];
            if (optStyle) {
                if (optStyle.bg_color) option.style.backgroundColor = optStyle.bg_color;
                if (optStyle.text_color) option.style.color = optStyle.text_color;
            }
            select.appendChild(option);
        }

        let changeTimeout = null;
        select.addEventListener('change', () => {
            if (changeTimeout) clearTimeout(changeTimeout);
            changeTimeout = setTimeout(() => {
                this.send({
                    type: 'ui.change',
                    element_id: element.id,
                    value: select.value,
                });
            }, 100);
            this.debounceTimers.push(changeTimeout);
        });

        el.appendChild(select);
        applyStyle(el, this.getThemedStyle(element.type, element.style));

        // Value binding (read; two-way when show.value.write_back)
        const valueBinding = element.bindings?.show?.value;
        if (valueBinding) {
            this.bindings.push({
                type: 'select_value',
                element: select,
                elementDef: element,
                binding: valueBinding,
            });
        }

        // Appearance binding: the control takes the colors of the option
        // matching the bound key's current value.
        if (lookBinding && lookBinding.key && styleMap) {
            this.bindings.push({
                type: 'select_look',
                element: el,
                select,
                elementDef: element,
                binding: lookBinding,
            });
        }

        return el;
    }

    renderTextInput(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-text-input';
        el.dataset.elementId = element.id;

        if (element.label) {
            const label = document.createElement('label');
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }

        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = element.placeholder || '';

        let changeTimeout = null;
        input.addEventListener('input', () => {
            if (changeTimeout) clearTimeout(changeTimeout);
            changeTimeout = setTimeout(() => {
                this.send({
                    type: 'ui.change',
                    element_id: element.id,
                    value: input.value,
                });
            }, 300);
            this.debounceTimers.push(changeTimeout);
        });

        el.appendChild(input);
        applyStyle(el, this.getThemedStyle(element.type, element.style));

        const valueBinding = element.bindings?.show?.value;
        if (valueBinding) {
            this.bindings.push({
                type: 'text_input_value',
                element: input,
                elementDef: element,
                binding: valueBinding,
            });
        }

        return el;
    }

    renderImage(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-image';
        el.dataset.elementId = element.id;

        this._showImagePicture(el, element, element.src);
        applyStyle(el, this.getThemedStyle(element.type, element.style));

        // Appearance: a picture per state, so a variable a script sets (the
        // season, the room's mode) changes what the Image shows. The pictures
        // are named in the look itself, never read out of a variable, so the
        // asset check still sees every one of them.
        if (element.bindings?.show?.look) {
            this.bindings.push({
                type: 'image_look',
                element: el,
                elementDef: element,
                binding: element.bindings.show.look,
            });
            this._preloadStatePictures(element.bindings.show.look);
        }

        return el;
    }

    /**
     * Show one picture in an Image, or say it could not be loaded.
     *
     * The Image keeps ONE <img> and at most one "Image not found" note for its
     * whole life, because a state look swaps the picture many times: a new
     * note per failure would stack them, and a picture that loads after one
     * that failed has to take the note away again. No picture at all removes
     * both, so an Image whose pictures all live in its states draws nothing
     * until one of them is chosen.
     */
    _showImagePicture(el, elementDef, src) {
        if (el._pictureSrc === src) return;
        el._pictureSrc = src;
        let img = el._pictureImg;
        if (!src) {
            img?.remove();
            el._pictureImg = null;
            el._pictureMissing?.remove();
            el._pictureMissing = null;
            return;
        }
        if (!img) {
            img = document.createElement('img');
            img.alt = elementDef.label || 'Panel image';
            img.loading = 'lazy';
            if (elementDef.object_fit) img.style.objectFit = elementDef.object_fit;
            img.onload = () => {
                img.style.display = '';
                el._pictureMissing?.remove();
                el._pictureMissing = null;
            };
            img.onerror = () => {
                img.style.display = 'none';
                let note = el._pictureMissing;
                if (!note) {
                    note = document.createElement('div');
                    note.textContent = 'Image not found';
                    note.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--panel-text);opacity:0.5;font-size:0.8571rem;';
                    el.appendChild(note);
                    el._pictureMissing = note;
                }
                note.title = el._pictureSrc || '';
            };
            el.prepend(img);
            el._pictureImg = img;
        }
        img.src = assetUrl(src);
    }

    /**
     * Fetch every picture a look can show, once, so a state change swaps the
     * picture at once instead of leaving the Image blank while it downloads.
     */
    _preloadStatePictures(look) {
        const pictures = new Set();
        const take = (appearance) => {
            if (appearance && typeof appearance === 'object' && appearance.button_image) {
                pictures.add(String(appearance.button_image));
            }
        };
        if (look.states && typeof look.states === 'object') {
            Object.values(look.states).forEach(take);
        }
        take(look.style_active);
        take(look.style_inactive);
        for (const ref of pictures) {
            const probe = new Image();
            probe.src = assetUrl(ref);
        }
    }

    renderCameraPreset(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-button';
        el.dataset.elementId = element.id;

        let content = element.label || 'Preset';
        if (element.preset_number != null) {
            content = element.preset_number + '\n' + content;
            // Preserve the newline so the preset number sits on its own line.
            el.style.whiteSpace = 'pre-line';
            el.style.lineHeight = '1.15';
        }
        el.textContent = content;

        applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.renderElementContent(el, element);

        const onPress = (e) => {
            e.preventDefault();
            el.classList.add('pressing');
            this.send({ type: 'ui.press', element_id: element.id });
        };
        const onRelease = (e) => {
            e.preventDefault();
            el.classList.remove('pressing');
            this.send({ type: 'ui.release', element_id: element.id });
        };

        el.addEventListener('mousedown', onPress);
        el.addEventListener('mouseup', onRelease);
        el.addEventListener('mouseleave', () => el.classList.remove('pressing'));
        el.style.touchAction = 'none';
        el.addEventListener('touchstart', onPress);
        el.addEventListener('touchend', onRelease, { passive: false });

        if (element.bindings?.show?.look) {
            this.bindings.push({
                type: 'feedback',
                element: el,
                elementDef: element,
                binding: element.bindings.show.look,
            });
        }

        return el;
    }

    // --- List ---

    renderList(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-list';
        el.dataset.elementId = element.id;

        const listStyle = element.list_style || 'selectable';
        const itemHeight = element.item_height || 44 / REM_BASE_PX;
        // Merge theme element_defaults so `item_bg` / `item_active_bg` from
        // the theme actually drive list row colors. Reading raw element.style
        // here was a long-standing bug — theme edits looked dead because
        // only per-element overrides won.
        const style = this.getThemedStyle('list', element.style);
        const itemBg = style.item_bg || '#2a2a4e';
        const itemActiveBg = style.item_active_bg || '#42a5f5';

        applyStyle(el, style);

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'list-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }

        const scrollArea = document.createElement('div');
        scrollArea.className = 'list-scroll';
        el.appendChild(scrollArea);

        // Track selected state
        const selectedValues = new Set();

        let _lastItemsJson = '';
        let _lastSelVal = undefined;
        const renderItems = (items) => {
            // Skip full re-render if items and selection haven't changed
            const selBinding = element.bindings?.show?.value;
            const selKey = selBinding?.key;
            const currentSelVal = selKey ? this.state[selKey] : undefined;
            const itemsJson = JSON.stringify(items);
            if (itemsJson === _lastItemsJson && currentSelVal === _lastSelVal) return;
            _lastItemsJson = itemsJson;
            _lastSelVal = currentSelVal;
            scrollArea.innerHTML = '';
            // Read selected value from state
            const selVal = selKey ? this.state[selKey] : undefined;
            if (selVal !== undefined && selVal !== null) {
                selectedValues.clear();
                selectedValues.add(String(selVal));
            }

            for (const item of items) {
                const row = document.createElement('div');
                row.className = 'list-item';
                row.style.minHeight = itemHeight + 'rem';
                row.style.backgroundColor = itemBg;
                row.textContent = item.label || item.value || '';
                row.dataset.value = item.value || '';

                const isActive = selectedValues.has(String(item.value));
                if (isActive && listStyle !== 'static') {
                    row.style.backgroundColor = itemActiveBg;
                    row.classList.add('active');
                }

                if (listStyle !== 'static') {
                    row.addEventListener('click', () => {
                        if (listStyle === 'selectable') {
                            selectedValues.clear();
                            selectedValues.add(String(item.value));
                        } else if (listStyle === 'multi_select') {
                            if (selectedValues.has(String(item.value))) {
                                selectedValues.delete(String(item.value));
                            } else {
                                selectedValues.add(String(item.value));
                            }
                        }
                        // The list's action binding slot is `select` (see the UI
                        // Builder). Emit ui.select so that authored action fires.
                        // Previously selectable/multi_select sent ui.change and
                        // action sent ui.press, neither of which the engine maps
                        // to the `select` binding, so the configured action was a
                        // silent no-op on the end-user panel.
                        this.send({ type: 'ui.select', element_id: element.id, value: item.value });
                        // Re-render items to update selection visuals
                        renderItems(items);
                    });
                }

                scrollArea.appendChild(row);
            }
            // Scroll selected item into view
            const activeRow = scrollArea.querySelector('.list-item.active');
            if (activeRow) activeRow.scrollIntoView({ block: 'nearest' });
        };

        // Initial items from static list
        const staticItems = element.items || element.options || [];
        renderItems(staticItems);


        // State-driven items binding
        const itemsBinding = element.bindings?.show?.items;
        if (itemsBinding) {
            this.bindings.push({
                type: 'list_items',
                element: el,
                elementDef: element,
                binding: itemsBinding,
                _list: { renderItems, scrollArea, staticItems, itemBg, itemActiveBg, listStyle, selectedValues },
            });
        }

        // Selection binding (the list's value)
        const selBinding = element.bindings?.show?.value;
        if (selBinding) {
            this.bindings.push({
                type: 'list_selected',
                element: el,
                elementDef: element,
                binding: selBinding,
                _list: { scrollArea, itemBg, itemActiveBg, selectedValues },
            });
        }

        return el;
    }

    evaluateListItems(b) {
        const { renderItems, staticItems } = b._list;
        const binding = b.binding;
        const keyPattern = binding.key_pattern || '';

        if (keyPattern) {
            // Collect items from state matching pattern (glob with *). Every
            // other character is literal: escaped first, so a bracket or a
            // plus in the pattern neither throws nor matches as a regex.
            const escaped = keyPattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const regex = new RegExp('^' + escaped.replace(/\\\*/g, '(.+)') + '$');
            const items = [];
            for (const [key, val] of Object.entries(this.state)) {
                const match = key.match(regex);
                if (match) {
                    items.push({ label: String(val), value: match[1] || String(val) });
                }
            }
            if (items.length > 0) {
                const hash = JSON.stringify(items);
                if (b._lastItemsHash === hash) return;
                b._lastItemsHash = hash;
                renderItems(items);
                return;
            }
        }
        // Fallback to static items
        const hash = JSON.stringify(staticItems);
        if (b._lastItemsHash === hash) return;
        b._lastItemsHash = hash;
        renderItems(staticItems);
    }

    evaluateListSelected(b) {
        const { scrollArea, itemBg, itemActiveBg, selectedValues } = b._list;
        const value = this.state[b.binding.key];
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        const paint = () => scrollArea.querySelectorAll('.list-item').forEach(item => {
            const isActive = selectedValues.has(item.dataset.value);
            item.style.backgroundColor = isActive ? itemActiveBg : itemBg;
            item.classList.toggle('active', isActive);
        });
        if (offline) {
            // Nothing is selected while the device is unreachable. Left alone
            // this is the quietest of the wrong answers: the row stays lit and
            // nothing about it says the selection is a memory.
            selectedValues.clear();
            paint();
            return;
        }
        if (value !== undefined && value !== null) {
            selectedValues.clear();
            selectedValues.add(String(value));
            paint();
        }
    }

    // --- Matrix ---

    /**
     * A routing matrix (modules/matrix.js draws and updates it). The panel
     * puts the element and its caption down, lends the matrix a host, and
     * registers one binding for every key the matrix reads.
     */
    renderMatrix(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-matrix';
        el.dataset.elementId = element.id;
        // Merge theme element_defaults so crosspoint colors come from the
        // theme, not just per-element overrides.
        const style = this.getThemedStyle('matrix', element.style);
        const matrix = createMatrix(element, style, this._matrixHost(element));
        applyStyle(el, style);

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'matrix-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }
        matrix.mount(el);

        // The keys are listed by name rather than as a glob. The incremental
        // state.update filter matches on a prefix, and a concrete key is its
        // own prefix.
        const watched = matrix.watchedKeys();
        if (watched.length) {
            this.bindings.push({
                type: 'matrix_routes',
                element: el,
                elementDef: element,
                binding: { _patterns: watched },
                _matrix: matrix,
            });
        }
        return el;
    }

    /**
     * What a matrix borrows from the panel. The functions read and send at
     * call time, never captured: the state object is replaced wholesale on a
     * full update. The two values are the drawing's, fixed when it is drawn.
     */
    _matrixHost(element) {
        return {
            read: (key) => this.state[key],
            send: (frame) => this.send(frame),
            // A lock's state.set and a preset's macro.execute name no element,
            // so this is the only place that knows which control a refusal of
            // either would be about -- same rule as the custom-control bridge.
            sendOwn: (frame) => {
                this._lastTouchedElementId = element.id;
                this.send(frame);
            },
            offline: (key) => this._keyDeviceOffline(key),
            orientation: this._drawnOrientation,
            remPx: REM_BASE_PX,
        };
    }

    evaluateMatrixRoutes(b) {
        const matrix = b._matrix;
        const destOffline = matrix.offlineDestinations();
        this._markMatrixAvailability(matrix, destOffline);
        matrix.update(destOffline);
    }

    // --- Gauge ---

    renderGauge(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-gauge';
        el.dataset.elementId = element.id;

        const min = element.min ?? 0;
        const max = element.max ?? 100;
        const unit = element.unit || '';
        const arcAngle = element.arc_angle ?? 240;
        // Merge theme element_defaults so gauge_color / gauge_bg_color come
        // from the theme.
        const style = this.getThemedStyle('gauge', element.style);
        const gaugeColor = style.gauge_color || '#4CAF50';
        const gaugeBgColor = style.gauge_bg_color || '#333333';
        const gaugeWidth = style.gauge_width || 8;
        const showValue = style.show_value !== false;
        const showTicks = style.show_ticks !== false;
        const tickCount = style.tick_count || 5;
        const zones = element.zones || null;
        const displayDecimals = this._displayDecimals(element);

        // SVG gauge
        const size = 120;
        const cx = size / 2, cy = size / 2;
        const radius = (size - gaugeWidth * 2) / 2;
        const startAngle = (270 - arcAngle / 2) * Math.PI / 180;
        const endAngle = (270 + arcAngle / 2) * Math.PI / 180;

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
        svg.style.width = '100%';
        svg.style.height = '100%';
        svg.style.maxWidth = '100%';
        svg.style.maxHeight = '100%';

        // Helper: polar to cartesian
        const polarToCart = (angle, r) => ({
            x: cx + r * Math.cos(angle),
            y: cy + r * Math.sin(angle)
        });

        // Helper: create arc path
        const arcPath = (startA, endA, r) => {
            const s = polarToCart(startA, r);
            const e = polarToCart(endA, r);
            const sweep = endA - startA;
            const largeArc = sweep > Math.PI ? 1 : 0;
            return `M ${s.x} ${s.y} A ${r} ${r} 0 ${largeArc} 1 ${e.x} ${e.y}`;
        };

        // Background arc
        const bgPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        bgPath.setAttribute('d', arcPath(startAngle, endAngle, radius));
        bgPath.setAttribute('fill', 'none');
        bgPath.setAttribute('stroke', gaugeBgColor);
        bgPath.setAttribute('stroke-width', gaugeWidth);
        bgPath.setAttribute('stroke-linecap', 'round');
        svg.appendChild(bgPath);

        // Foreground arc placeholder (updated via binding)
        const fgPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        fgPath.setAttribute('fill', 'none');
        fgPath.setAttribute('stroke', gaugeColor);
        fgPath.setAttribute('stroke-width', gaugeWidth);
        fgPath.setAttribute('stroke-linecap', 'round');
        fgPath.dataset.role = 'gauge-fg';
        svg.appendChild(fgPath);

        // Tick marks
        if (showTicks && tickCount > 1) {
            for (let i = 0; i <= tickCount; i++) {
                const frac = i / tickCount;
                const angle = startAngle + frac * (endAngle - startAngle);
                const inner = polarToCart(angle, radius - gaugeWidth);
                const outer = polarToCart(angle, radius + gaugeWidth / 2);
                const tick = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                tick.setAttribute('x1', inner.x);
                tick.setAttribute('y1', inner.y);
                tick.setAttribute('x2', outer.x);
                tick.setAttribute('y2', outer.y);
                tick.setAttribute('stroke', '#666');
                tick.setAttribute('stroke-width', '1');
                svg.appendChild(tick);
            }
        }

        // Value text in center
        const valueText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        valueText.setAttribute('x', cx);
        valueText.setAttribute('y', cy + 4);
        valueText.setAttribute('text-anchor', 'middle');
        valueText.setAttribute('fill', style.text_color || '#ffffff');
        valueText.setAttribute('font-size', '16');
        valueText.setAttribute('font-weight', '600');
        valueText.textContent = showValue ? `--${unit}` : '';
        valueText.dataset.role = 'gauge-value';
        svg.appendChild(valueText);

        el.appendChild(svg);

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'gauge-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }

        applyStyle(el, this.getThemedStyle(element.type, element.style));

        // Value binding
        if (element.bindings?.show?.value) {
            this.bindings.push({
                type: 'gauge_value',
                element: el,
                elementDef: element,
                binding: element.bindings.show.value,
                _svg: { fgPath, valueText, startAngle, endAngle, radius, cx, cy, min, max, unit, gaugeColor, zones, showValue, displayDecimals, arcPath: arcPath, polarToCart },
            });
        }

        return el;
    }

    evaluateGaugeValue(b) {
        const raw = this.state[b.binding.key];
        const offline = this._bindingOffline(b);
        // Memoize: skip if unchanged (also short-circuits the undefined steady state)
        if (b._lastGaugeRaw === raw && b._lastGaugeOffline === offline) return;
        b._lastGaugeRaw = raw;
        b._lastGaugeOffline = offline;
        this._markBindingAvailability(b, offline);
        const { fgPath, valueText, startAngle, endAngle, radius, min, max, unit, gaugeColor, zones, showValue, displayDecimals, arcPath: arcPathFn } = b._svg;
        if (offline || raw === undefined || raw === null) {
            // Nothing to draw — the key was deleted, or the device it comes
            // from is unreachable. Either way the no-data placeholder, never a
            // frozen last reading and never an arc at zero.
            fgPath.setAttribute('d', '');
            if (showValue) valueText.textContent = `--${unit}`;
            return;
        }
        const value = Math.max(min, Math.min(max, Number(raw)));
        const frac = max > min ? (value - min) / (max - min) : 0;
        const valAngle = startAngle + frac * (endAngle - startAngle);

        if (frac > 0.001) {
            fgPath.setAttribute('d', arcPathFn(startAngle, valAngle, radius));
        } else {
            fgPath.setAttribute('d', '');
        }

        // Zone coloring
        let color = gaugeColor;
        if (zones && zones.length) {
            for (const z of zones) {
                if (value >= z.from && value <= z.to) {
                    color = z.color;
                    break;
                }
            }
        }
        fgPath.setAttribute('stroke', color);

        if (showValue) {
            // Unset means one decimal with trailing zeros dropped — exactly what
            // a gauge has always drawn, so no panel built before this moves. An
            // explicit setting is a fixed width instead, the way the slider and
            // fader readouts already behave.
            const shown = displayDecimals != null
                ? value.toFixed(displayDecimals)
                : String(Math.round(value * 10) / 10);
            valueText.textContent = `${shown}${unit}`;
        }
    }

    // --- Level Meter ---

    renderLevelMeter(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-level-meter';
        el.dataset.elementId = element.id;

        const orientation = element.orientation || 'vertical';
        const min = element.min ?? -60;
        const max = element.max ?? 0;
        // Merge theme element_defaults so green_to / yellow_to thresholds
        // (now editable per theme) actually drive the meter zones.
        const style = this.getThemedStyle('level_meter', element.style);
        const segments = style.meter_segments || 20;
        const showPeak = style.show_peak !== false;
        const greenTo = style.green_to ?? -12;
        const yellowTo = style.yellow_to ?? -3;

        el.classList.add(orientation === 'horizontal' ? 'horizontal' : 'vertical');

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'meter-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }

        const bar = document.createElement('div');
        bar.className = 'meter-bar';

        // Create segments (for vertical: bottom=min, top=max).
        // Colors come from CSS using --panel-success / --panel-warning / --panel-danger
        // via [data-zone] selectors, so themes can recolor zones without code changes.
        for (let i = 0; i < segments; i++) {
            const seg = document.createElement('div');
            seg.className = 'meter-segment';
            const segFrac = segments > 1 ? i / (segments - 1) : 0;
            const segValue = min + segFrac * (max - min);
            if (segValue >= yellowTo) {
                seg.dataset.zone = 'red';
            } else if (segValue >= greenTo) {
                seg.dataset.zone = 'yellow';
            } else {
                seg.dataset.zone = 'green';
            }
            bar.appendChild(seg);
        }

        el.appendChild(bar);

        applyStyle(el, this.getThemedStyle(element.type, element.style));

        // Value binding
        if (element.bindings?.show?.value) {
            this.bindings.push({
                type: 'level_meter_value',
                element: el,
                elementDef: element,
                binding: element.bindings.show.value,
                _meter: { segments, min, max, bar, showPeak, peakValue: -Infinity, peakTime: 0, peakHoldMs: style.peak_hold_ms || 1500 },
            });
        }

        return el;
    }

    evaluateLevelMeterValue(b) {
        const raw = this.state[b.binding.key];
        const offline = this._bindingOffline(b);
        if (b._lastMeterRaw === raw && b._lastMeterOffline === offline) return;
        b._lastMeterRaw = raw;
        b._lastMeterOffline = offline;
        this._markBindingAvailability(b, offline);
        const { segments, min, max, bar, showPeak, peakHoldMs } = b._meter;
        const segs = bar.querySelectorAll('.meter-segment');
        if (offline || raw === undefined || raw === null) {
            // Key deleted, or the device is unreachable — clear the meter
            // rather than freezing the level it last reported.
            b._meter.peakValue = -Infinity;
            for (const s of segs) { s.classList.remove('lit'); s.classList.remove('peak'); }
            return;
        }
        const value = Math.max(min, Math.min(max, Number(raw)));
        const span = max - min;
        const frac = span > 0 ? (value - min) / span : 0;
        const litCount = Math.round(frac * segments);

        // Peak hold
        const now = Date.now();
        if (value > b._meter.peakValue || now - b._meter.peakTime > peakHoldMs) {
            b._meter.peakValue = value;
            b._meter.peakTime = now;
        }
        const peakFrac = span > 0 ? (b._meter.peakValue - min) / span : 0;
        const peakIdx = segments > 1 ? Math.round(peakFrac * (segments - 1)) : 0;

        // Toggle CSS classes; backgrounds come from theme tokens via panel-elements.css.
        for (let i = 0; i < segs.length; i++) {
            segs[i].classList.toggle('lit', i < litCount);
            segs[i].classList.toggle('peak', showPeak && i === peakIdx && i >= litCount);
        }
    }

    // --- Fader ---

    renderFader(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-fader';
        el.dataset.elementId = element.id;

        // The theme's element_defaults style the wrapper (bg_color, border,
        // padding, shadow) and say whether it shows its value and its scale.
        const style = this.getThemedStyle('fader', element.style);
        // Filled in below when the fader has a value binding: the end of a
        // drag asks whether that binding's device is reachable.
        let faderBinding = null;
        const fader = new FaderControl(element, style, this._levelHost(element.id, () => faderBinding));
        el.classList.add(fader.horizontal ? 'horizontal' : 'vertical');
        applyStyle(el, style);

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'fader-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }
        fader.mount(el);

        // Value binding for state updates
        const valueBinding = element.bindings?.show?.value;
        if (valueBinding) {
            faderBinding = {
                type: 'fader_value',
                element: el,
                elementDef: element,
                binding: valueBinding,
                _fader: fader,
            };
            this.bindings.push(faderBinding);
        }

        return el;
    }

    // The fixed decimal count an element asked for in its readout, or null when
    // the author said nothing (each element type decides its own default from
    // that). Clamped to the range toFixed accepts, so a stray project value
    // can't throw part-way through a render pass and leave the page half drawn.
    _displayDecimals(elementDef) {
        const raw = elementDef?.display_decimals;
        if (raw == null) return null;
        const n = Number(raw);
        if (!Number.isFinite(n)) return null;
        return Math.max(0, Math.min(20, Math.round(n)));
    }

    evaluateFaderValue(b) {
        const raw = this.state[b.binding.key];
        const offline = this._bindingOffline(b);
        // Connectivity is part of the memo: the value can be identical either
        // side of a device going away, and what it is worth is not.
        if (b._lastFaderRaw === raw && b._lastFaderOffline === offline) return;
        b._lastFaderRaw = raw;
        b._lastFaderOffline = offline;
        this._markBindingAvailability(b, offline);
        b._fader.show(raw, offline);
    }

    // --- Group ---

    renderGroup(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-group';
        el.dataset.elementId = element.id;

        const labelPos = element.label_position || 'top-left';

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'group-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);

            // Position
            if (labelPos.startsWith('top')) {
                label.style.top = '0';
            } else {
                label.style.bottom = '0';
            }
            if (labelPos.endsWith('left')) {
                label.style.left = '0.5714rem';
            } else if (labelPos.endsWith('center')) {
                label.style.left = '50%';
                label.style.transform = 'translateX(-50%)';
            } else if (labelPos.endsWith('right')) {
                label.style.right = '0.5714rem';
            }

            el.appendChild(label);
        }

        applyStyle(el, this.getThemedStyle(element.type, element.style));

        // Appearance: the frame's colour or background picture per state (a
        // divisible space in use, an alarm zone gone red).
        if (element.bindings?.show?.look) {
            this.bindings.push({
                type: 'group_look',
                element: el,
                elementDef: element,
                binding: element.bindings.show.look,
            });
            this._preloadStatePictures(element.bindings.show.look);
        }
        return el;
    }

    // --- Clock ---

    renderClock(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-clock';
        el.dataset.elementId = element.id;

        const display = document.createElement('div');
        display.className = 'clock-display';
        el.appendChild(display);

        applyStyle(el, this.getThemedStyle(element.type, element.style));

        const mode = element.clock_mode || 'time';
        const defaultFormats = { time: 'h:mm A', date: 'MMM D, YYYY', datetime: 'MMM D, YYYY h:mm A', countdown: 'HH:mm:ss', elapsed: 'HH:mm:ss', meeting: 'mm:ss' };
        // "12" / "24" are documented shorthands for 12- and 24-hour time, not
        // literal token strings; map them before the token replacer runs.
        const formatShortcuts = { '12': 'h:mm A', '24': 'HH:mm' };
        const rawFormat = element.format || defaultFormats[mode] || 'h:mm A';
        const format = formatShortcuts[rawFormat] || rawFormat;
        const timezone = element.timezone || undefined;
        const durationMin = element.duration_minutes || 60;

        const updateClock = () => {
            const now = new Date();
            let text = '';

            switch (mode) {
                case 'time':
                    text = this._formatDateTime(now, format, timezone);
                    break;
                case 'date':
                    text = this._formatDateTime(now, format, timezone);
                    break;
                case 'datetime':
                    text = this._formatDateTime(now, format, timezone);
                    break;
                case 'countdown': {
                    // A live state key takes priority over a static target_time,
                    // matching the builder help text. Both are validated as dates
                    // so a non-ISO / garbage value renders the placeholder, not
                    // NaN:NaN:NaN.
                    const key = element.bindings?.show?.value?.key || element.start_key;
                    const stateVal = key ? this.state[key] : null;
                    const targetStr = (stateVal !== undefined && stateVal !== null && stateVal !== '')
                        ? stateVal
                        : element.target_time;
                    const target = targetStr != null ? new Date(targetStr) : null;
                    if (target && !isNaN(target.getTime())) {
                        const diff = Math.max(0, target - now);
                        text = this._formatDuration(diff);
                    } else {
                        text = '--:--:--';
                    }
                    break;
                }
                case 'elapsed': {
                    const key = element.start_key;
                    const stateVal = key ? this.state[key] : null;
                    const start = stateVal != null ? new Date(stateVal) : null;
                    if (start && !isNaN(start.getTime())) {
                        const diff = Math.max(0, now - start);
                        text = this._formatDuration(diff);
                    } else {
                        text = '00:00:00';
                    }
                    break;
                }
                case 'meeting': {
                    // Anchor the meeting start on the app instance keyed by element
                    // id so navigating away and back (or a theme/idle re-render)
                    // doesn't restart the countdown from its full duration.
                    let meetingStartTime = this._meetingStartTimes[element.id];
                    if (!meetingStartTime) {
                        meetingStartTime = now;
                        this._meetingStartTimes[element.id] = meetingStartTime;
                    }
                    const elapsed = now - meetingStartTime;
                    const totalMs = durationMin * 60 * 1000;
                    const remaining = totalMs - elapsed;
                    if (remaining > 0) {
                        text = this._formatDuration(remaining);
                    } else {
                        text = '-' + this._formatDuration(Math.abs(remaining));
                    }
                    break;
                }
                default:
                    text = this._formatDateTime(now, format, timezone);
            }

            display.textContent = text;
        };

        updateClock();
        // Register with global clock interval instead of per-element interval.
        // Stash the closure on the element so dismissOverlay can unregister it.
        el._clockUpdate = updateClock;
        this._clockElements.push(updateClock);
        if (!this._clockInterval) {
            this._clockInterval = setInterval(() => {
                for (const fn of this._clockElements) fn();
            }, 1000);
        }

        return el;
    }

    _formatDateTime(date, format, timezone) {
        let d = date;
        if (timezone) {
            try {
                // Use Intl to get components in the target timezone
                const opts = { timeZone: timezone, hour12: false };
                const parts = new Intl.DateTimeFormat('en-US', {
                    ...opts, year: 'numeric', month: '2-digit', day: '2-digit',
                    hour: '2-digit', minute: '2-digit', second: '2-digit',
                    weekday: 'short',
                }).formatToParts(date);
                const get = (type) => parts.find(p => p.type === type)?.value || '';
                const tzDate = {
                    year: parseInt(get('year')),
                    month: parseInt(get('month')),
                    day: parseInt(get('day')),
                    hour: parseInt(get('hour')),
                    minute: parseInt(get('minute')),
                    second: parseInt(get('second')),
                    weekday: get('weekday'),
                };
                d = tzDate;

                return this._applyFormat(d, format, true);
            } catch (e) {
                // Fall through to local time
            }
        }
        const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        return this._applyFormat({
            year: d.getFullYear(), month: d.getMonth() + 1, day: d.getDate(),
            hour: d.getHours(), minute: d.getMinutes(), second: d.getSeconds(),
            weekday: days[d.getDay()],
        }, format, true);
    }

    _applyFormat(d, format) {
        const h24 = d.hour;
        const h12 = h24 % 12 || 12;
        const ampm = h24 < 12 ? 'AM' : 'PM';
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const fullMonths = ['January','February','March','April','May','June','July','August','September','October','November','December'];
        const fullDays = { Sun: 'Sunday', Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday' };

        return format
            .replace(/dddd/g, fullDays[d.weekday] || d.weekday || '')
            .replace(/ddd/g, d.weekday || '')
            .replace(/HH/g, String(h24).padStart(2, '0'))
            .replace(/\bH\b/g, String(h24))
            .replace(/hh/g, String(h12).padStart(2, '0'))
            .replace(/\bh\b/g, String(h12))
            .replace(/mm/g, String(d.minute).padStart(2, '0'))
            .replace(/ss/g, String(d.second).padStart(2, '0'))
            .replace(/\bA\b/g, ampm)
            .replace(/\ba\b/g, ampm.toLowerCase())
            .replace(/YYYY/g, String(d.year))
            .replace(/MMMM/g, fullMonths[d.month - 1] || '')
            .replace(/MMM/g, months[d.month - 1] || '')
            .replace(/MM/g, String(d.month).padStart(2, '0'))
            .replace(/\bM\b/g, String(d.month))
            .replace(/DD/g, String(d.day).padStart(2, '0'))
            .replace(/\bD\b/g, String(d.day));
    }

    _formatDuration(ms) {
        const totalSec = Math.floor(ms / 1000);
        const h = Math.floor(totalSec / 3600);
        const m = Math.floor((totalSec % 3600) / 60);
        const s = totalSec % 60;
        if (h > 0) {
            return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        }
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    _formatRichText(text) {
        // Sanitize HTML entities first — all user content is escaped before any tags are added
        let html = String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
        // Bold: **text**
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Italic: *text*
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
        // Defense-in-depth: strip any tags that aren't our whitelisted strong/em
        html = html.replace(/<(?!\/?(?:strong|em)>)[^>]*>/gi, '');
        return html;
    }

    // --- Keypad ---

    renderKeypad(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-keypad';
        el.dataset.elementId = element.id;

        const digits = element.digits ?? 4;
        const autoSend = element.auto_send ?? false;
        const autoSendDelay = element.auto_send_delay_ms ?? 1500;
        const keypadStyle = element.keypad_style || 'numeric';
        const showDisplay = element.show_display !== false;
        // A PIN pad: the display shows a dot per digit typed. Only what is
        // drawn changes; the entry submitted is the digits themselves.
        const maskDisplay = element.mask_display === true;

        applyStyle(el, this.getThemedStyle(element.type, element.style));

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'keypad-label';
            label.textContent = element.label;
            this._recordCaption(el, label, ['label']);
            el.appendChild(label);
        }

        // Display
        let displayEl = null;
        let buffer = '';
        if (showDisplay) {
            displayEl = document.createElement('div');
            displayEl.className = 'keypad-display';
            displayEl.textContent = '';
            el.appendChild(displayEl);
        }

        // Button grid
        const grid = document.createElement('div');
        grid.className = 'keypad-grid';

        let keys;
        if (keypadStyle === 'phone') {
            keys = ['1','2','3','4','5','6','7','8','9','*','0','#'];
        } else {
            keys = ['1','2','3','4','5','6','7','8','9','C','0','enter'];
        }

        let autoSendTimer = null;

        const updateDisplay = () => {
            if (!displayEl) return;
            displayEl.textContent = maskDisplay ? '•'.repeat(buffer.length) : buffer;
        };

        const doSubmit = () => {
            if (buffer) {
                this.send({ type: 'ui.submit', element_id: element.id, value: buffer });
                buffer = '';
                updateDisplay();
            }
        };

        for (const key of keys) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'keypad-key';
            // Enter is the panel's own arrow, never a font's return symbol.
            // Few fonts carry U+23CE, so each device drew a different
            // stand-in at a different width, and that one key set the
            // keypad's minimum width. The stylesheet sizes the arrow.
            if (key === 'enter') btn.appendChild(this.renderIcon('corner-down-left'));
            else btn.textContent = key;
            btn.setAttribute('aria-label', key === 'C' ? 'Clear' : key === 'enter' ? 'Enter' : key === '*' ? 'Star' : key === '#' ? 'Hash' : `Key ${key}`);

            if (key === 'C') btn.classList.add('keypad-clear');
            if (key === 'enter') btn.classList.add('keypad-enter');

            btn.addEventListener('click', (e) => {
                e.preventDefault();
                if (key === 'C') {
                    buffer = '';
                    if (autoSendTimer) { clearTimeout(autoSendTimer); autoSendTimer = null; }
                } else if (key === 'enter') {
                    doSubmit();
                } else {
                    if (buffer.length < digits) {
                        buffer += key;
                        if (autoSend && buffer.length >= digits) {
                            doSubmit();
                        } else if (autoSend) {
                            if (autoSendTimer) clearTimeout(autoSendTimer);
                            autoSendTimer = setTimeout(doSubmit, autoSendDelay);
                            this.debounceTimers.push(autoSendTimer);
                        }
                    }
                }
                updateDisplay();
            });

            grid.appendChild(btn);
        }

        el.appendChild(grid);
        return el;
    }

    // --- Iframe elements: plugin panels and custom controls ---
    //
    // Two element types render an author's own web page inside one element's
    // box, and they are the same machinery: a sandboxed iframe, an init message
    // carrying config + theme + a scoped state snapshot, live state pushes, and
    // an action bridge back. What differs is only where the page comes from,
    // what state it may see, and (for a plugin) an auth token for its own
    // routes. One renderer, two callers -- a second copy of the bridge is how
    // one of them quietly loses a guard.

    renderPluginElement(element) {
        const pluginId = element.plugin_id;
        const pluginType = element.plugin_type;

        // Validate pluginId/pluginType format (alphanumeric, underscores, hyphens only)
        const validIdPattern = /^[a-zA-Z0-9_-]+$/;
        if (!pluginId || !pluginType || !validIdPattern.test(pluginId) || !validIdPattern.test(pluginType)) {
            return this._renderIframePlaceholder(element, 'plugin', 'Plugin element (unconfigured)');
        }

        // Sandbox + allow attributes are 'allow-scripts' / none by default.
        // A plugin's panel_elements entry can opt into extra tokens via
        // `sandbox_permissions` and `allow_features`; the server has already
        // filtered both lists against per-field whitelists, so we trust
        // whatever comes back from /api/plugins/extensions.
        const extDef = this._pluginExtensions[pluginId]?.[pluginType];
        const sandboxTokens = ['allow-scripts'];
        for (const t of (extDef?.sandbox_permissions || [])) {
            if (!sandboxTokens.includes(t)) sandboxTokens.push(t);
        }

        return this._renderIframeElement(element, {
            className: 'panel-plugin',
            styleType: 'plugin',
            src: `${basePath()}/api/plugins/${encodeURIComponent(pluginId)}/panel/${encodeURIComponent(pluginType)}.html`,
            sandboxTokens,
            allowFeatures: extDef?.allow_features || [],
            config: element.plugin_config || {},
            loadingText: 'Loading plugin...',
            placeholderLabel: 'Plugin',
            placeholderDetail: `${pluginId} / ${pluginType}`,
            pluginId,
            extAuth: !!extDef?.ext_auth,
            // A plugin has a server-side half publishing into its own
            // namespace, and reaches that with no grant at all -- it is the
            // plugin's own data. Anything beyond it is granted per element,
            // exactly like a custom control's.
            ownNamespace: `plugin.${pluginId}.`,
        });
    }

    renderCustomElement(element) {
        // The file is a relative path inside the project's ui/ tree, written by
        // the Builder from what is actually on disk. Reject anything that isn't
        // one rather than building a URL out of it.
        const file = String(element.custom_file || '');
        if (!file || file.startsWith('/') || file.includes('..') || file.includes('\\')) {
            return this._renderIframePlaceholder(element, 'custom', 'Custom control (no file chosen)');
        }
        const src = file.split('/').map(encodeURIComponent).join('/');
        // The designer re-renders this page whenever the author saves a file
        // into ui/, and the browser would otherwise hand back the copy it
        // already has. The version rides in from the Builder; at runtime there
        // is none and the URL is the plain one.
        const bust = this._uiFilesVersion ? `?v=${encodeURIComponent(this._uiFilesVersion)}` : '';

        return this._renderIframeElement(element, {
            className: 'panel-custom',
            styleType: 'custom',
            src: `${basePath()}/api/projects/default/ui/${src}${bust}`,
            // The one iframe type that runs in the designer. It is the control
            // the integrator is writing right now, so a grey box is the wrong
            // answer: it draws, it is themed, and it is the size they gave it.
            // Nothing it sends reaches the room -- send() refuses in edit mode,
            // and edit mode has no socket to refuse into.
            liveInEditMode: true,
            // Named so a failure message can say which file, not which element.
            fileLabel: file,
            // Nothing but allow-scripts, and no opt-in. The per-plugin escape
            // hatch exists because a plugin is reviewed code with a manifest the
            // server has filtered; a file dropped into ui/ is neither.
            sandboxTokens: ['allow-scripts'],
            allowFeatures: [],
            config: element.custom_config || {},
            loadingText: 'Loading...',
            placeholderLabel: 'Custom control',
            placeholderDetail: file,
            pluginId: null,
            extAuth: false,
            // A custom control has no namespace of its own: everything it sees
            // and everything it sends comes from the grant on the element.
            ownNamespace: null,
        });
    }

    /** Is the person actually in this frame right now?
     *
     *  The browser is the only thing that can answer: clicking or tapping into
     *  an iframe moves `document.activeElement` to it out here, even though
     *  nothing about the event itself crosses. That is the whole guard on the
     *  idle reset above -- a message from a frame the user is not in must not
     *  count, or a control left running in a corner keeps a wall panel awake
     *  and unlocked all night. Wrapped because `document.activeElement` is not
     *  guaranteed to exist in every embedding host. */
    _frameHasFocus(iframe) {
        try {
            return !!iframe && document.activeElement === iframe;
        } catch {
            return false;
        }
    }

    /** Whether this page hands the whole screen to markup the author wrote.
     *
     *  A page that says `custom` but names no file is not one: it would draw a
     *  blank screen indistinguishable from a page with nothing on it, and the
     *  elements it still carries are the better thing to show while somebody is
     *  half way through setting it up. */
    _isCustomPage(page) {
        return !!page && page.render_mode === 'custom' && !!page.custom_file;
    }

    /** Will this page draw nothing at all?
     *
     *  Three things draw: the page's own elements, a master element that names
     *  the page, and a custom page's frame. None of the three is a black
     *  screen, and a black screen reads as a broken system rather than an
     *  unbuilt one -- which is what the seed project's single empty page looked
     *  like to anyone opening /panel to see what their client sees.
     *
     *  The same three are what `api/routes/setup.py::_panel_has_content` counts
     *  before an appliance display leaves the setup screen for /panel. That one
     *  asks the question of the whole project; this asks it of one page, so
     *  they are not interchangeable, but they must agree on what "draws".
     *
     *  The master test is copied from the render loop rather than simplified:
     *  a master element with no `pages` draws nowhere, and guessing otherwise
     *  here would suppress the notice on a page that really is blank. */
    _pageDrawsNothing(page) {
        if (!page) return false;
        if (this._isCustomPage(page)) return false;
        const elements = Array.isArray(page.elements) ? page.elements : [];
        if (elements.length) return false;
        return !(this.uiDef?.master_elements || []).some(mEl => {
            if (!mEl || mEl.hidden) return false;
            const on = mEl.pages;
            return on === '*' || (Array.isArray(on) && on.includes(page.id));
        });
    }

    /** Is anything on screen right now drawn from a file in `ui/`?
     *
     *  Asked before re-rendering on a ui.files push, so a panel showing a page
     *  with no author markup on it does not redraw -- a redraw is cheap but not
     *  free, and it is visible: it restarts page-enter animations and drops any
     *  transient state a control was holding. A master element can be a custom
     *  control too, and one draws on every page it names, so a panel sitting on
     *  a plain page still redraws when the logo strip's markup changes. */
    _pageRunsAuthorMarkup() {
        const pages = this.uiDef?.pages || [];
        const page = pages.find(p => p.id === this.currentPage);
        if (this._isCustomPage(page)) return true;
        if ((page?.elements || []).some(el => el?.type === 'custom')) return true;
        return (this.uiDef?.master_elements || []).some(mEl => {
            if (mEl?.type !== 'custom') return false;
            const on = mEl.pages;
            return on === '*' || (Array.isArray(on) && page && on.includes(page.id));
        });
    }

    /** A custom PAGE is a custom control sized to the page.
     *
     *  Same frame, same sandbox, same bridge, same grant -- the only difference
     *  is the box, and `_placeElement` with no placement is already the full
     *  box. The synthetic element carries the page's own id, so the state
     *  pushes, the failure strip and `openavc:init`'s elementId all name the
     *  page. A second renderer here is how one of the two quietly loses a
     *  guard, which is the reason the plugin and control paths were
     *  collapsed into one to begin with. */
    _renderCustomPageFrame(page) {
        const asElement = {
            id: page.id,
            type: 'custom',
            custom_file: page.custom_file,
            custom_config: page.custom_config || {},
            grant: page.grant,
        };
        const el = this.renderCustomElement(asElement);
        if (!el) return null;
        el.dataset.elementType = 'custom';
        // Fills the screen, so it keeps square corners -- the rounded radius
        // every element carries would clip the author's own page.
        el.classList.add('panel-custom-page');
        return this._placeElement(el, null, asElement);
    }

    /** The grant on an element, normalised so the checks below never have to
     *  ask what shape it is. An element with no grant reaches nothing, which is
     *  what every element in every project written before grants existed has. */
    _elementGrant(element) {
        const g = (element && element.grant) || {};
        return {
            devices: Array.isArray(g.devices) ? g.devices.map(String) : [],
            variables: Array.isArray(g.variables) ? g.variables.map(String) : [],
            macros: g.macros === true,
            navigate: g.navigate === true,
        };
    }

    /** One rule for what state an iframe element may see, asked by both the
     *  opening snapshot and every live push. Device grants match on the prefix
     *  INCLUDING the trailing dot -- a grant on `dsp1` must not also hand over
     *  `dsp10` -- which is also what makes child entities work: their keys are
     *  `device.<parent>.<child>.<id>.<prop>`, so the parent's grant covers them
     *  without the integrator listing keys that only exist at runtime. A
     *  variable is matched exactly, so `room_volume` is not `room_volume_max`. */
    _grantAllowsRead(grant, ownNamespace, key) {
        const k = String(key);
        if (ownNamespace && k.startsWith(ownNamespace)) return true;
        for (const id of grant.devices) {
            if (k.startsWith(`device.${id}.`)) return true;
        }
        for (const id of grant.variables) {
            if (k === `var.${id}`) return true;
        }
        return false;
    }

    /** The box an iframe element draws when it cannot render for real:
     *  unconfigured, or the design canvas where author code does not run. */
    _renderIframePlaceholder(element, styleType, label, detail) {
        const el = document.createElement('div');
        el.className = `panel-element panel-${styleType}`;
        el.dataset.elementId = element.id;
        el.style.display = 'flex';
        el.style.flexDirection = 'column';
        el.style.alignItems = 'center';
        el.style.justifyContent = 'center';
        el.style.gap = '4px';
        el.style.color = 'var(--panel-text)';
        el.style.textAlign = 'center';
        el.style.padding = '4px';
        // The sizes go on the text, in em, and never on `el`: it is a
        // .panel-element, so its own font-size is the panel's text default (or
        // the author's font_size, applied below) and these are proportions of
        // that, like every other piece of text inside a control.
        if (detail === undefined) {
            // Unconfigured: one line, no border — it is not a stand-in for
            // something that would otherwise draw.
            el.style.opacity = '0.5';
            const line = document.createElement('div');
            line.textContent = label;
            line.style.fontSize = '0.8571em';
            el.appendChild(line);
            return el;
        }
        el.style.border = '1px dashed var(--panel-text, rgba(255,255,255,0.3))';
        el.style.borderRadius = '4px';
        el.style.opacity = '0.4';
        const title = document.createElement('div');
        title.textContent = label;
        title.style.fontWeight = '600';
        title.style.fontSize = '0.7857em';
        const sub = document.createElement('div');
        sub.textContent = detail;
        sub.style.opacity = '0.8';
        sub.style.fontSize = '0.7143em';
        el.appendChild(title);
        el.appendChild(sub);
        applyStyle(el, this.getThemedStyle(styleType, element.style));
        return el;
    }

    /** Say, in the element's own box, that the page in it failed.
     *
     *  A control that breaks is otherwise a blank rectangle with the answer in
     *  a console nobody has open -- and on a wall panel there is no console at
     *  all. The strip sits over the frame rather than replacing it, so a page
     *  that half-drew still shows what it managed. */
    _showIframeFault(el, message) {
        let strip = el._faultStrip;
        if (!strip) {
            strip = document.createElement('div');
            strip.className = 'panel-iframe-fault';
            strip.style.cssText = 'position:absolute;left:0;right:0;bottom:0;z-index:2;'
                + 'padding:3px 5px;font-size:10px;line-height:1.3;text-align:left;'
                + 'background:var(--panel-danger, #b3261e);color:#fff;'
                + 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            el.appendChild(strip);
            el._faultStrip = strip;
        }
        strip.textContent = message;
        strip.title = message;
        // The designer can put it in front of the author, which the panel
        // itself cannot: it has no place to show a message that is not a box.
        this._postToParent({
            type: 'openavc:element-error',
            elementId: el.dataset.elementId,
            message,
        });
    }

    _renderIframeElement(element, opts) {
        // Edit mode: a plugin element stays a placeholder -- it is somebody
        // else's shipped code and the author is not editing it, so there is
        // nothing to see and no reason to run it while they drag. A custom
        // control is the opposite: it is the thing being written.
        if (this.editMode && !opts.liveInEditMode) {
            return this._renderIframePlaceholder(
                element, opts.styleType, opts.placeholderLabel, opts.placeholderDetail,
            );
        }

        const el = document.createElement('div');
        el.className = `panel-element ${opts.className}`;
        el.dataset.elementId = element.id;

        const iframe = document.createElement('iframe');
        iframe.src = opts.src;
        // setAttribute rather than `iframe.sandbox = ...`: identical in a
        // browser, but the property assignment leaves the attribute unset under
        // jsdom, and the sandbox attribute is the entire isolation story here —
        // it has to be assertable.
        iframe.setAttribute('sandbox', opts.sandboxTokens.join(' '));
        if (opts.allowFeatures.length) {
            iframe.setAttribute('allow', opts.allowFeatures.join('; '));
        }
        iframe.style.cssText = 'width:100%; height:100%; border:none; border-radius:inherit;';
        // NOT loading="lazy". A panel page is sized to fill the screen, so
        // every frame on it is in the viewport already and lazy defers nothing
        // -- it only delays the fetch behind a layout pass, on the path a
        // person is waiting at. It also costs the one thing that makes the
        // second request below unnecessary: Chromium zeroes responseStatus and
        // transferSize on a lazily-loaded frame's timing entry, so the parent
        // could no longer tell a 404 from a control that drew perfectly well.
        el.style.overflow = 'hidden';
        el.appendChild(iframe);

        // Is the page actually there? Registered before the frame is in the
        // document, so the answer cannot arrive before something is listening
        // for it.
        if (opts.fileLabel) {
            this._reportIframeFileFailure(el, iframe, opts);
        }

        // Loading indicator
        const loadingIndicator = document.createElement('div');
        loadingIndicator.textContent = opts.loadingText;
        loadingIndicator.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--panel-text);opacity:0.5;font-size:0.8571rem;position:absolute;inset:0;z-index:1;';
        el.style.position = 'relative';
        el.appendChild(loadingIndicator);

        // Store reference for state updates. Same {el, elementDef} shape as every
        // other renderer — this one used to store the bare node, which quietly cost
        // it everything that reads elementDef off an entry (ui.* label overrides,
        // macro-busy). Anything needing the frame's own bits reads them off entry.el.
        this.elementMap[element.id] = { el, elementDef: element };
        el._pluginIframe = iframe;
        el._pluginId = opts.pluginId;
        el._pluginConfig = opts.config;
        // The grant this element was placed with, and the namespace it owns
        // outright (a plugin's own `plugin.<id>.`, nothing for a custom
        // control). Both checks below read these off the element rather than
        // re-deriving them, so the opening snapshot, the live pushes and the
        // action bridge cannot end up answering differently.
        el._grant = this._elementGrant(element);
        el._ownNamespace = opts.ownNamespace;
        const stateFilter = (key) => this._grantAllowsRead(el._grant, el._ownNamespace, key);
        el._stateFilter = stateFilter;

        // postMessage API: send initial config + theme + state snapshot when the
        // iframe loads. Also re-run on demand ('openavc:request-init' from the
        // frame) so a long-lived plugin iframe can recover a fresh ext token
        // after its TTL expires — wall panels outlive the token lifetime by
        // design.
        const sendInit = async () => {
            loadingIndicator.remove();
            const themeVars = {};
            const root = document.documentElement;
            for (const prop of ['--panel-bg', '--panel-text', '--panel-accent',
                '--panel-button-bg', '--panel-button-text', '--panel-button-border',
                '--panel-surface', '--panel-surface-border',
                '--panel-danger', '--panel-success', '--panel-warning',
                '--panel-border-radius']) {
                themeVars[prop] = getComputedStyle(root).getPropertyValue(prop).trim();
            }
            const stateSnapshot = {};
            for (const [key, value] of Object.entries(this.state || {})) {
                if (stateFilter(key)) stateSnapshot[key] = value;
            }
            // Plugins that call their own /ext/* routes declare ext_auth. Fetch
            // a plugin-scoped token (our fetch is already authenticated) and pass
            // it in — the sandboxed iframe can't carry our credentials itself, so
            // it presents this token instead.
            let extToken;
            if (opts.extAuth) {
                extToken = await this._fetchPluginExtToken(opts.pluginId);
            }
            if (!iframe.contentWindow) return;  // element removed during await
            iframe.contentWindow.postMessage({
                type: 'openavc:init',
                config: opts.config,
                theme: themeVars,
                state: stateSnapshot,
                elementId: element.id,
                ext_token: extToken,
                // True in the Builder's design canvas: the control is drawing
                // for its author, with whatever sample state the Builder sent
                // and no way to reach the room. A control can use this to show
                // representative content instead of empty readouts.
                edit: !!this.editMode,
                // What it was granted, so a control can adapt to it -- hide a
                // button for a device it cannot command instead of sending a
                // message that is silently dropped.
                grant: {
                    devices: [...el._grant.devices],
                    variables: [...el._grant.variables],
                    macros: el._grant.macros,
                    navigate: el._grant.navigate,
                },
            }, '*');  // sandboxed iframe has opaque origin; source check provides security
        };
        iframe.addEventListener('load', sendInit);

        // Listen for messages from the iframe
        const who = opts.pluginId ? `plugin '${opts.pluginId}'` : `custom control '${element.id}'`;
        const handler = (event) => {
            if (event.source !== iframe.contentWindow) return;
            const msg = event.data;
            if (!msg || !msg.type) return;
            // Read off the element, not the closure: a re-render replaces the
            // grant, and a stale copy captured here would keep answering for a
            // grant the integrator has already taken away.
            const grant = el._grant || { devices: [], variables: [], macros: false, navigate: false };

            // Somebody using this frame is using the panel. Nothing inside a
            // cross-origin sandboxed frame reaches the document listeners that
            // reset the idle timer, so without this a person working a custom
            // page is invisible to it: the panel navigates to the idle page and
            // re-shows the lock screen under their hands. Any message from the
            // frame counts, so a control that already does something needs no
            // extra line -- but only while the frame HAS FOCUS, which is what
            // stops a frame nobody is touching from holding a panel unlocked
            // forever by posting in a loop.
            if (this._frameHasFocus(iframe)) this.resetIdleTimer();

            switch (msg.type) {
                case 'openavc:activity':
                    // Nothing more to do -- the reset above is the whole
                    // message. It exists for the control that draws rather than
                    // acts (a room map, a dashboard), which would otherwise
                    // send nothing at all while somebody stands in front of it.
                    break;
                case 'openavc:action': {
                    // A failure coming back from any of these is about the
                    // control that asked, the same as a finger on a button --
                    // none of these frames carries an element, so this is the
                    // only place that knows which one it was.
                    this._lastTouchedElementId = el.dataset.elementId;
                    // This bridge carries the panel's WS authority, so gate it
                    // against the grant the integrator set when they placed the
                    // element: a command reaches a device only if that device is
                    // on the list, a write reaches a variable only if that
                    // variable is. A plugin's own plugin.<id>.* namespace needs
                    // no grant -- it is the plugin's own data. Anything else is
                    // a confused-deputy write and is dropped.
                    //
                    // THIS IS THE ENFORCEMENT POINT, AND IT CANNOT BE ANYWHERE
                    // ELSE. The server's WS handler takes a device_id from any
                    // authenticated panel socket and has no idea which element
                    // sent it -- a WS frame carries no element identity, so the
                    // grant is unenforceable server-side by construction. What
                    // makes it a real boundary is the iframe: sandboxed with an
                    // opaque origin, it cannot reach this document's memory or
                    // its socket, and can only ask the panel to act for it. The
                    // panel is trusted because it already holds the credential.
                    // Do not assume the server double-checks. It does not.
                    if (msg.action === 'device.command' && msg.device && msg.command) {
                        if (!grant.devices.includes(String(msg.device))) {
                            console.warn(`[panel] ${who} attempted a command on device '${msg.device}', which it was not granted`);
                            break;
                        }
                        // Through send(), not straight at the socket: send() is the
                        // one place that refuses to talk to the room while the
                        // designer is authoring. Reaching past it worked only
                        // because there is no socket in edit mode, which is luck
                        // rather than a rule -- and the design canvas is about to
                        // start running these iframes for real.
                        this.send({
                            type: 'command',
                            device_id: msg.device,
                            command: msg.command,
                            params: msg.params || {},
                        });
                    } else if (msg.action === 'state.set' && msg.key) {
                        const key = String(msg.key);
                        const ownNamespace = !!el._ownNamespace && key.startsWith(el._ownNamespace);
                        const grantedVariable = grant.variables.some(id => key === `var.${id}`);
                        if (!ownNamespace && !grantedVariable) {
                            console.warn(`[panel] ${who} attempted to write '${key}', which it was not granted`);
                            break;
                        }
                        this.send({
                            type: 'state.set',
                            key: msg.key,
                            value: msg.value,
                        });
                    } else if (msg.action === 'macro.run' && msg.macro) {
                        if (!grant.macros) {
                            console.warn(`[panel] ${who} attempted to run macro '${msg.macro}' without the macro switch`);
                            break;
                        }
                        this.send({
                            type: 'macro.execute',
                            macro_id: String(msg.macro),
                        });
                    }
                    break;
                }
                case 'openavc:error': {
                    // The frame is sandboxed with an opaque origin, so nothing
                    // out here can see a script error inside it. The one line
                    // the docs give an author (window.onerror -> this message)
                    // is what makes a broken control say so instead of drawing
                    // an empty box.
                    const text = String(msg.message || 'error').slice(0, 200);
                    console.warn(`[panel] ${who} reported an error: ${text}`);
                    this._showIframeFault(el, text);
                    break;
                }
                case 'openavc:request-init': {
                    // Re-send openavc:init (with a freshly-fetched ext token
                    // when the plugin declares ext_auth). A plugin iframe
                    // calls this when an /ext/* request starts returning 401
                    // mid-session — its token expired.
                    sendInit();
                    break;
                }
                case 'openavc:navigate':
                    // Ungated until grants existed, so any plugin iframe could
                    // move the panel out from under whoever was using it.
                    if (!grant.navigate) {
                        console.warn(`[panel] ${who} attempted to change pages without the navigation switch`);
                        break;
                    }
                    // Navigation does not go through send(), so it needs its
                    // own authoring guard: in the designer the author is on the
                    // page they are editing, and a control must not move them
                    // off it.
                    if (this.editMode) break;
                    if (!msg.page) break;
                    // Use the same server round trip as a page-nav button:
                    // emit ui.page.<id>, then navigate once on its reply.
                    this.send({ type: 'ui.page', page_id: msg.page });
                    break;
            }
        };
        window.addEventListener('message', handler);
        el._pluginMessageHandler = handler;
        this._pluginMessageHandlers.add(handler);

        applyStyle(el, this.getThemedStyle(opts.styleType, element.style));
        return el;
    }

    // ──── Audio Playback (driven by Audio Player plugin state) ────

    // Modern browsers block audio until the user has interacted with the page.
    // We attach a one-time gesture listener that "unlocks" playback by
    // priming a silent <audio> element. After that, subsequent .play() calls
    // succeed silently. Edit/embedded modes don't need this — they don't
    // receive live state.
    _setupAudioUnlock() {
        if (this.editMode) return;
        const unlock = () => {
            if (this._audioUnlocked) return;
            const silent = new Audio(
                'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA='
            );
            silent.volume = 0;
            silent.play().then(() => {
                this._audioUnlocked = true;
                document.removeEventListener('pointerdown', unlock);
                document.removeEventListener('keydown', unlock);
                document.removeEventListener('touchstart', unlock);
            }).catch(() => {
                // Another gesture will retry — keep listener attached
            });
        };
        document.addEventListener('pointerdown', unlock);
        document.addEventListener('keydown', unlock);
        document.addEventListener('touchstart', unlock);
    }

    // Capture the current play_request id at snapshot time so reconnects
    // don't replay the last sound.
    _seedAudioDedupeFromSnapshot() {
        const raw = this.state['plugin.audio_player.play_request'];
        if (!raw) return;
        try {
            const req = JSON.parse(raw);
            if (req && req.id) this._lastAudioRequestId = req.id;
        } catch {
            // Ignore — bad JSON means nothing to dedupe against
        }
    }

    _handleAudioPlayRequest(rawValue) {
        if (!rawValue) return;
        let req;
        try {
            req = JSON.parse(rawValue);
        } catch {
            return;
        }
        if (!req || typeof req !== 'object') return;
        // Dedupe — every fresh request gets a new id
        if (req.id && req.id === this._lastAudioRequestId) return;
        if (req.id) this._lastAudioRequestId = req.id;

        if (req.stop) {
            this._stopAllAudio();
            return;
        }
        // Honor global mute
        if (this.state['plugin.audio_player.muted']) return;
        // Compute final volume = master × request
        const master = Number(this.state['plugin.audio_player.master_volume'] ?? 1.0);
        const reqVol = Number(req.volume ?? 1.0);
        const finalVol = Math.max(0, Math.min(1, (isFinite(master) ? master : 1) * (isFinite(reqVol) ? reqVol : 1)));
        if (finalVol <= 0) return;
        // Prefer the URL the plugin resolved (knows file extensions);
        // fall back to building one from the sound id for forward compat
        // with plugins that don't include url, and for assets:// references.
        const url = req.url ? this._resolveAbsoluteUrl(req.url) : this._resolveSoundUrl(req.sound);
        if (!url) return;
        this._playSound(url, finalVol);
    }

    _resolveAbsoluteUrl(url) {
        if (!url || typeof url !== 'string') return null;
        if (url.startsWith('http://') || url.startsWith('https://')) return url;
        if (!url.startsWith('/')) return url;
        return basePath() + url;
    }

    _resolveSoundUrl(soundId) {
        if (!soundId || typeof soundId !== 'string') return null;
        if (soundId.startsWith('assets://')) {
            return assetUrl(soundId);
        }
        if (soundId.startsWith('http://') || soundId.startsWith('https://') || soundId.startsWith('/')) {
            return this._resolveAbsoluteUrl(soundId);
        }
        // Last-resort fallback for sounds the plugin didn't resolve a URL for.
        // Assumes .mp3 by convention; works for plugins that follow it.
        return `${basePath()}/api/plugins/audio_player/files/sounds/${encodeURIComponent(soundId)}.mp3`;
    }

    _playSound(url, volume) {
        if (!url) return;
        // Prune elements that finished but never fired 'ended' (looping/streamed
        // sounds) and cap concurrency so _activeAudio can't accumulate detached
        // HTMLAudioElements over a multi-week kiosk uptime.
        for (const a of this._activeAudio) {
            if (a.ended) this._activeAudio.delete(a);
        }
        while (this._activeAudio.size >= 8) {
            const oldest = this._activeAudio.values().next().value;
            if (!oldest) break;
            try { oldest.pause(); } catch { /* element may be in a bad state */ }
            this._activeAudio.delete(oldest);
        }
        const audio = new Audio(url);
        audio.volume = volume;
        this._activeAudio.add(audio);
        const cleanup = () => this._activeAudio.delete(audio);
        audio.addEventListener('ended', cleanup);
        audio.addEventListener('error', () => {
            console.warn(`[panel-audio] failed to load: ${url}`);
            cleanup();
        });
        audio.play().catch((err) => {
            // Most common cause: browser autoplay policy hasn't been satisfied
            // yet. Drop the sound — stale notifications are worse than missed.
            console.warn(`[panel-audio] play() rejected for ${url}: ${err && err.message}`);
            cleanup();
        });
    }

    _stopAllAudio() {
        for (const audio of this._activeAudio) {
            try {
                audio.pause();
                audio.currentTime = 0;
            } catch {
                // Ignore — element may already be in a non-resettable state
            }
        }
        this._activeAudio.clear();
    }

    // Send state update to plugin iframes
    _notifyPluginIframes(key, value) {
        for (const [id, entry] of Object.entries(this.elementMap)) {
            const el = entry?.el;
            if (!el?._pluginIframe?.contentWindow) continue;
            // Scope live updates to what this frame may see, by asking the same
            // filter the init snapshot used. Broadcasting every key let any
            // plugin passively observe all device/var/ui/system/other-plugin
            // state it was never granted, making the scoped init snapshot moot —
            // and the two must agree, or a frame gets live updates for state its
            // opening snapshot said it could not see.
            if (!el._stateFilter || !el._stateFilter(String(key))) continue;
            el._pluginIframe.contentWindow.postMessage({
                type: 'openavc:state',
                key,
                value,
            }, '*');  // sandboxed iframe has opaque origin; source check provides security
        }
    }

    // --- Bindings ---

    _updateMacroBusyState(macroId) {
        // Apply or remove busy state on buttons whose press binding triggers this macro
        for (const [elemId, entry] of Object.entries(this.elementMap)) {
            const pressActions = entry.elementDef?.bindings?.do?.press;
            if (!pressActions) continue;
            const actions = Array.isArray(pressActions) ? pressActions : [pressActions];
            const referencesMacro = actions.some(a => a.action === 'macro' && a.macro === macroId);
            if (!referencesMacro) continue;
            const isRunning = macroId in this._runningMacros;
            if (isRunning) {
                entry.el.classList.add('macro-busy');
                entry.el.setAttribute('data-macro-busy', macroId);
            } else {
                entry.el.classList.remove('macro-busy');
                entry.el.removeAttribute('data-macro-busy');
            }
        }
    }

    _updateMacroProgressBindings(macroId) {
        // Update any text bindings with source: "macro_progress" for this macro
        for (const b of this.bindings) {
            if (b.type !== 'macro_progress') continue;
            if (b.binding.macro !== macroId) continue;
            const running = this._runningMacros[macroId];
            // A run with no description and no step count to show (one whose
            // steps are not known yet) keeps the idle text, never "of 0".
            if (running && (running.description || running.total_steps > 0)) {
                const text = running.description || `Step ${running.step_index + 1} of ${running.total_steps}`;
                b.element.textContent = text;
                // The step description comes from the macro, not the author.
                this._recordTextSource(b.element, null);
            } else {
                b.element.textContent = b.binding.idle_text || '';
                this._recordTextSource(b.element,
                    ['bindings', 'show', 'value', 'idle_text']);
            }
        }
    }

    _scheduleBindingEvaluation(keys) {
        // Batch multiple state updates into a single rAF evaluation
        if (!this._pendingBindingKeys) {
            this._pendingBindingKeys = new Set(keys);
            this._bindingRafId = requestAnimationFrame(() => {
                const batchedKeys = [...this._pendingBindingKeys];
                this._pendingBindingKeys = null;
                this._bindingRafId = null;
                this.evaluateAllBindings(batchedKeys);
            });
        } else {
            for (const k of keys) this._pendingBindingKeys.add(k);
        }
    }

    /**
     * The device a state key reports FROM, or null when it names none.
     *
     * `device.<id>.<prop>` is the whole rule, and a child key carries the same
     * second segment (`device.<id>.<type>.<local>.<prop>`), so one split covers
     * both. A key naming one of the platform-maintained props is deliberately
     * not attributed to the device: those keep telling the truth when it is
     * gone, and a panel bound to them is reporting the fault, not hiding it.
     */
    _deviceIdForKey(key) {
        if (typeof key !== 'string' || !key.startsWith('device.')) return null;
        const parts = key.split('.');
        if (parts.length < 3) return null;
        if (parts.length === 3 && DEVICE_PLATFORM_PROPS.has(parts[2])) return null;
        // `device.<id>.<type>.<local>.<prop>` — the child's own reserved props
        // are the platform's account of the child, not a reading from the far
        // end of the wire, so they are exempt for the same reason.
        if (parts.length === 5 && CHILD_PLATFORM_PROPS.has(parts[4])) return null;
        return parts[1] || null;
    }

    /** Every device a binding reads from, or null. Cached: bindings are rebuilt with the page. */
    _bindingDeviceIds(b) {
        if (b._deviceIds !== undefined) return b._deviceIds;
        const bd = b.binding || {};
        const keys = [];
        if (bd.key) keys.push(bd.key);
        if (Array.isArray(bd._keys)) keys.push(...bd._keys);
        if (Array.isArray(bd._patterns)) keys.push(...bd._patterns);
        if (bd.key_pattern) keys.push(bd.key_pattern);
        const ids = new Set();
        for (const k of keys) {
            const id = this._deviceIdForKey(k);
            if (id) ids.add(id);
        }
        b._deviceIds = ids.size ? [...ids] : null;
        return b._deviceIds;
    }

    /**
     * Whether a device is known to be unreachable right now.
     *
     * Strictly `=== false`: an absent `connected` key means nobody has said
     * either way -- a panel drawn before its first snapshot -- and that must
     * render normally rather than as a page full of dead controls.
     *
     * NEVER in the design canvas. Everything below this is a RUNTIME
     * condition, not a property of the page being drawn, and the canvas is fed
     * the IDE's live state -- so on a bench where the gear is not plugged in
     * yet, every bound control would draw dimmed and dashed and the author
     * could not see the colours, the handle or the artwork they are placing.
     * The treatment REPLACES the design, where a live value merely fills it
     * in, which is what makes this different from showing real state on the
     * canvas at all. The Preview button is the honest answer for "what does
     * the room see": it runs the same renderer over a real WebSocket, so an
     * author who wants to check how a page reads with a device down has a
     * place to go, and the runtime panel is untouched either way.
     */
    _deviceOffline(deviceId) {
        if (this.editMode) return false;
        return this.state[`device.${deviceId}.connected`] === false;
    }

    /** True when a key's device is known to be unreachable right now. */
    _keyDeviceOffline(key) {
        const id = this._deviceIdForKey(key);
        return id ? this._deviceOffline(id) : false;
    }

    /**
     * Whether this binding's reading can be believed.
     *
     * One exception: a device put to sleep on purpose (`device.<id>.asleep`)
     * keeps the readings of a control whose press wakes it. That press goes
     * out with the device off the network (`wake_devices`, resolved on the
     * server from the drivers' `available_offline`), and its feedback is the
     * true "off", so the Power On button stays live while every other control
     * bound to the device greys out.
     */
    _bindingOffline(b) {
        const ids = this._bindingDeviceIds(b);
        if (!ids) return false;
        const wakes = this._elementWakeDevices(b.elementDef);
        return ids.some(id => this._deviceOffline(id) && !(wakes.includes(id) && this._deviceAsleep(id)));
    }

    /** True while the device is asleep on purpose (never in the design canvas). */
    _deviceAsleep(deviceId) {
        if (this.editMode) return false;
        return this.state[`device.${deviceId}.asleep`] === true;
    }

    /** The devices an element's press reaches through a command that works offline. */
    _elementWakeDevices(elementDef) {
        const list = this._servedWakeDevices
            ? this._servedWakeDevices.get(elementDef?.id)
            : elementDef?.wake_devices;
        return Array.isArray(list) ? list : [];
    }

    /**
     * Mark the element a binding draws into as unavailable, or clear it.
     *
     * Tallied per element rather than toggled, because one element can carry
     * several bindings (a select's value and its look) which may name
     * different devices -- the control is unavailable while ANY of them is.
     */
    _markBindingAvailability(b, offline) {
        const host = this.elementMap[b.elementDef?.id]?.el || b.element;
        if (!host || !host.classList) return;
        const tally = host._offlineBindings || (host._offlineBindings = new Set());
        if (offline) tally.add(b); else tally.delete(b);
        host.classList.toggle('device-offline', tally.size > 0);
    }

    /**
     * Mark controls whose ACTIONS cannot land, which is a different statement
     * from a reading that cannot be trusted and gets a lighter treatment.
     *
     * The two axes, and why they are not the same mark:
     *
     * - A `show` binding on an unreachable device means "this number is not
     *   worth believing": the value is masked and the element is drained of
     *   colour, because a confident-looking reading is the lie.
     * - A `do` action on an unreachable device means "pressing this will be
     *   refused": the label, the colour and the artwork are all still correct,
     *   and only the availability is not. It gets the same dashed edge and
     *   keeps everything else, which is what stops a page going grey because
     *   one device is rebooting.
     *
     * ANY vs ALL is the other difference, and it matters most on the button
     * somebody actually built the page around. A reading is spoiled by ONE bad
     * source, so `_bindingOffline` marks on any. An action still does
     * something as long as one target is there -- a System On macro touching
     * eight devices is not unavailable because a display is off -- so this
     * marks only when NOTHING it can reach is up.
     *
     * `action_devices` is resolved on the server (`ui/action_devices.py`);
     * following a macro into a group is not something the renderer can do.
     * Preview draws the Builder's copy of the page, which never carries it, so
     * there it comes from the server's own definition, matched by element id
     * (`_servedActionDevices`): it follows an edit once the Builder saves it.
     */
    evaluateActionAvailability() {
        for (const [id, entry] of Object.entries(this.elementMap)) {
            const el = entry?.el;
            if (!el || !el.classList) continue;
            const devices = this._servedActionDevices
                ? this._servedActionDevices.get(id)
                : entry?.elementDef?.action_devices;
            if (!Array.isArray(devices) || !devices.length) {
                // Preview re-reads this without a re-render when the server's
                // definition moves, so a control that now reaches nothing has
                // to lose a mark it was drawn with.
                el.classList.remove('action-offline');
                continue;
            }
            const stranded = devices.every(id => this._deviceOffline(id));
            // Never both: an element already carrying the reading mark has
            // been drained, and adding a second dashed edge over it would draw
            // the border twice.
            el.classList.toggle(
                'action-offline',
                stranded && !el.classList.contains('device-offline'),
            );
        }
    }

    /** Element id -> the devices its press reaches, from a resolved definition. */
    _actionDevicesById(ui, key = 'action_devices') {
        const byId = new Map();
        const pages = Array.isArray(ui?.pages) ? ui.pages : [];
        const elements = pages.flatMap(p => (Array.isArray(p?.elements) ? p.elements : []))
            .concat(Array.isArray(ui?.master_elements) ? ui.master_elements : []);
        for (const element of elements) {
            if (element?.id && Array.isArray(element[key])) {
                byId.set(element.id, element[key]);
            }
        }
        return byId;
    }

    /**
     * The same unavailable mark, one destination at a time. A matrix is the
     * one control that can be half true, so it is marked per row rather than
     * per element: every node of a destination's row takes the mark.
     */
    _markMatrixAvailability(matrix, destOffline) {
        for (const [index, nodes] of matrix.destinationRows()) {
            const offline = !!destOffline[index];
            for (const node of nodes) node.classList.toggle('device-offline', offline);
        }
    }

    /**
     * Draw the artwork a button should show in its current state, after
     * applyStyle has decided background-image for the gradient.
     *
     * applyStyle sets background-image on every call (the gradient, or empty),
     * and the button's own image lives in the same property, so every look or
     * toggle pass wiped it unless something drew it again. A state's own
     * artwork wins; otherwise the button's, where its display mode draws one.
     * The tint falls back to the text colour inside applyImageEffect, so a
     * masked image stays visible when the state carries no colour.
     */
    _reapplyButtonImage(element, elementDef, stateImage, tint) {
        const image = stateImage
            || (this._buttonShowsOwnPicture(elementDef) ? elementDef.button_image : null);
        if (!image) {
            // A previous state's artwork on an effect layer goes with it.
            element.querySelector(':scope > .panel-button-image-layer')?.remove();
            return;
        }
        applyImageEffect(element, image, this._pictureOptions(elementDef, tint));
    }

    /**
     * Whether a button's display mode draws its words (not `image` or
     * `icon_only`) and its own picture (`image` or `image_text`). A picture a
     * state names shows in any mode: the Builder offers it on every button.
     */
    _buttonShowsWords(elementDef) {
        return !WORDLESS_MODES.has(elementDef.display_mode || 'text');
    }

    _buttonShowsOwnPicture(elementDef) {
        return PICTURE_MODES.has(elementDef.display_mode || 'text');
    }

    /** How a button draws its picture: its fit, blend and opacity, tinted with `tint`. */
    _pictureOptions(elementDef, tint) {
        return {
            fit: elementDef.image_fit,
            blend: elementDef.image_blend_mode,
            opacity: elementDef.image_opacity,
            tintColor: tint,
        };
    }

    /**
     * Draw a button's words, or take them off where its display mode draws
     * none. Only the text goes: the picture layer and an icon are children of
     * their own. `words` is [text, where an edit of it goes], or null to leave
     * the words as they are.
     */
    _drawButtonWords(element, elementDef, words) {
        if (!this._buttonShowsWords(elementDef)) {
            this._removeTextNodes(element);
            this._recordTextSource(element, null);
        } else if (words) {
            this._setLabelText(element, ...words);
        }
    }

    /**
     * Draw again the icon a look names, else the control's own, beside the
     * words: renderElementContent rebuilds the icon and text layout from the
     * words on the control, so they are written first, and draws nothing on a
     * control with no icon. With no look (`appearance` null) the control's own
     * definition is drawn as it is.
     */
    _drawLookIcon(element, elementDef, appearance) {
        this.renderElementContent(element,
            appearance ? this._withLookIcon(elementDef, appearance) : elementDef);
    }

    /** The control with the icon and icon colour a look names in place of its own. */
    _withLookIcon(elementDef, appearance) {
        return {
            ...elementDef,
            icon: appearance.icon || elementDef.icon,
            icon_color: appearance.icon_color || elementDef.icon_color,
        };
    }

    evaluateAllBindings(changedKeys = null) {
        // Which devices just changed reachability. A binding reading one of
        // them has to be re-evaluated even though its OWN key did not move:
        // the value is the same and what it is worth has changed.
        let flipped = null;
        if (changedKeys) {
            for (const k of changedKeys) {
                // `asleep` too: it lands just after `connected` drops, and it
                // decides whether a wake control's reading still counts.
                if (typeof k === 'string' && k.startsWith('device.')
                    && (k.endsWith('.connected')
                        || (k.endsWith('.asleep') && k.split('.').length === 3))) {
                    (flipped || (flipped = new Set())).add(k.split('.')[1]);
                }
            }
        }
        for (const b of this.bindings) {
            try {
                // Skip bindings not affected by changed keys
                const ids = flipped && this._bindingDeviceIds(b);
                const reachChanged = ids ? ids.some(id => flipped.has(id)) : false;
                if (changedKeys && !reachChanged) {
                    const bKey = b.binding?.key;
                    const bKeys = b.binding?._keys;        // visible_when: array of keys
                    // matrix: the concrete keys it reads, one per entry. A concrete
                    // key is its own prefix, so the test below matches it exactly.
                    const bPatterns = b.binding?._patterns;
                    const bPattern = b.binding?.key_pattern;
                    if (bKeys && !bKeys.some(k => changedKeys.includes(k))) continue;
                    if (bKey && !bKeys && !changedKeys.includes(bKey)) continue;
                    if (bPatterns) {
                        const hit = bPatterns.some(p => {
                            const prefix = p.replace(/\*.*$/, '');
                            return changedKeys.some(k => k.startsWith(prefix));
                        });
                        if (!hit) continue;
                    } else if (bPattern) {
                        const prefix = bPattern.replace(/\*.*$/, '');
                        if (!changedKeys.some(k => k.startsWith(prefix))) continue;
                    }
                    if (!bKey && !bPattern && !bPatterns) { /* safety: evaluate anyway */ }
                }
                this._evaluateBinding(b);
            } catch (e) {
                console.error('Binding error:', e);
            }
        }

        // Controls whose ACTIONS cannot land. Not a binding, so it is not in
        // the loop above: an action-only button pushes nothing onto
        // this.bindings, which is exactly why it used to carry no mark at all.
        this.evaluateActionAvailability();

        // Apply ui.* state overrides (set by macros/scripts)
        // These take priority over feedback bindings for direct control.
        this.evaluateUiOverrides();
    }

    /**
     * Draw one binding from current state.
     *
     * Shared with the refusal revert, which needs exactly this and none of the
     * incremental filtering around it. Each renderer protects active gestures.
     */
    _evaluateBinding(b) {
        switch (b.type) {
            case 'visible_when':
                this.evaluateVisibleWhen(b);
                break;
            case 'feedback':
                this.evaluateFeedback(b);
                break;
            case 'toggle_look':
                this.evaluateToggleLook(b);
                break;
            case 'label_look':
                this.evaluateLabelLook(b);
                break;
            case 'image_look':
                this.evaluateImageLook(b);
                break;
            case 'nav_look':
                this.evaluateNavLook(b);
                break;
            case 'group_look':
                this.evaluateGroupLook(b);
                break;
            case 'text':
                this.evaluateText(b);
                break;
            case 'color':
                this.evaluateColor(b);
                break;
            case 'slider_value':
                this.evaluateSliderValue(b);
                break;
            case 'select_value':
                this.evaluateSelectValue(b);
                break;
            case 'select_look':
                this.evaluateSelectLook(b);
                break;
            case 'text_input_value':
                this.evaluateTextInputValue(b);
                break;
            case 'gauge_value':
                this.evaluateGaugeValue(b);
                break;
            case 'level_meter_value':
                this.evaluateLevelMeterValue(b);
                break;
            case 'fader_value':
                this.evaluateFaderValue(b);
                break;
            case 'matrix_routes':
                this.evaluateMatrixRoutes(b);
                break;
            case 'list_items':
                this.evaluateListItems(b);
                break;
            case 'list_selected':
                this.evaluateListSelected(b);
                break;
        }
    }

    evaluateUiOverrides() {
        for (const [elementId, entry] of Object.entries(this.elementMap)) {
            const el = entry.el;
            const elementDef = entry.elementDef;
            if (!el || !el.style) continue;
            const prefix = `ui.${elementId}.`;

            // Lazily snapshot the rendered base so an override can be reverted
            // when its state key is later deleted. Without this, a script/macro
            // that sets ui.<id>.* and then clears it can't visually revert the
            // element until a full page re-render (one-way-invariant violation).
            if (!entry._uiBase) {
                const words = this._overrideWordsNode(el, elementDef);
                // A label drawn as rich text is put back from its source, so
                // its **bold** comes back bold rather than as asterisks.
                const rich = words === el && elementDef?.type === 'label'
                    && !!elementDef.style?.white_space && !this._labelSpan(el);
                entry._uiBase = {
                    backgroundColor: el.style.backgroundColor,
                    color: el.style.color,
                    opacity: el.style.opacity,
                    display: el.style.display,
                    words,
                    label: !words ? null
                        : rich ? String(elementDef.text ?? '') : this._drawnWords(words),
                    labelRich: rich,
                    labelPath: elementDef?.type === 'label' ? ['text'] : ['label'],
                };
                entry._uiApplied = new Set();
            }
            const base = entry._uiBase;
            const applied = entry._uiApplied;
            const isSet = (v) => v !== undefined && v !== null;
            const labelOverride = this.state[prefix + 'label'];
            const bgOverride = this.state[prefix + 'bg_color'];
            const textColorOverride = this.state[prefix + 'text_color'];
            const opacityOverride = this.state[prefix + 'opacity'];
            const visibleOverride = this.state[prefix + 'visible'];

            // First, put back what a cleared override was covering.
            let cleared = false;
            if (!isSet(labelOverride) && applied.has('label')) {
                this._writeOverrideWords(el, base.words, base.label, base.labelPath, base.labelRich);
                applied.delete('label');
                cleared = true;
            }
            if (!isSet(bgOverride) && applied.has('bg')) {
                el.style.backgroundColor = base.backgroundColor;
                applied.delete('bg');
                cleared = true;
            }
            if (!isSet(textColorOverride) && applied.has('text')) {
                el.style.color = base.color;
                applied.delete('text');
                cleared = true;
            }
            if (!isSet(opacityOverride) && applied.has('opacity')) {
                el.style.opacity = base.opacity;
                applied.delete('opacity');
                cleared = true;
            }
            if (!isSet(visibleOverride) && applied.has('visible')) {
                // Hand display back to a visible_when binding if one governs
                // this element (the redraw below re-asserts it); otherwise
                // restore the rendered base.
                el.style.display = elementDef?.bindings?.show?.visible_when ? '' : base.display;
                applied.delete('visible');
                cleared = true;
            }
            // The snapshot is the element as first drawn, and a look or a
            // toggle may have drawn something else since: a Blank All button
            // lit orange, coloured by a script, then uncoloured, went back to
            // the grey it had before the screens were blanked. So the
            // element's own bindings draw again from current state, over the
            // snapshot, and what shows is what it would show had the override
            // never been set. The snapshot stays the answer only for what no
            // binding draws.
            if (cleared) this._redrawElement(elementId);

            // Then every override still set, over whatever was drawn.
            if (isSet(labelOverride) && base.words) {
                // Runtime words with no authored home. The editor refuses these
                // and says which key is setting them: writing the element's own
                // label here would be a silent no-op, because this override
                // would still be covering it. Preserves the image layer and
                // other element children.
                this._writeOverrideWords(el, base.words, String(labelOverride), null);
                applied.add('label');
            }
            if (isSet(bgOverride)) {
                el.style.backgroundColor = String(bgOverride);
                applied.add('bg');
            }
            if (isSet(textColorOverride)) {
                el.style.color = String(textColorOverride);
                applied.add('text');
            }
            if (isSet(opacityOverride)) {
                el.style.opacity = String(opacityOverride);
                applied.add('opacity');
            }
            if (isSet(visibleOverride)) {
                el.style.display = (visibleOverride === false || visibleOverride === 'false')
                    ? 'none' : '';
                applied.add('visible');
            }
        }
    }

    /**
     * Where a control's words are, for a `ui.<id>.label` override: the caption
     * on a control that carries one (a slider, a dropdown, a meter, a keypad),
     * the control itself where its words are the control (a button, a page
     * nav, a camera preset, a label). Null for a control with no words to
     * replace -- an image, a clock, a frame, or a captioned control given no
     * caption -- and the override draws nothing there.
     */
    _overrideWordsNode(el, elementDef) {
        if (el._captionNode) return el._captionNode;
        return PanelApp.OWN_WORDS_TYPES.has(elementDef?.type) ? el : null;
    }

    static get OWN_WORDS_TYPES() {
        return new Set(['button', 'page_nav', 'camera_preset', 'label']);
    }

    /** The span a control with an icon keeps its words in (`renderElementContent`). */
    _labelSpan(node) {
        return node.querySelector(':scope > .panel-label-span');
    }

    /**
     * The words a node draws, leaving icons and image layers out: the span
     * beside an icon when there is one, else the text sitting in the node.
     */
    _drawnWords(node) {
        const span = this._labelSpan(node);
        if (span) return span.textContent;
        let text = '';
        for (const child of node.childNodes) {
            if (child.nodeType === Node.TEXT_NODE) text += child.nodeValue;
        }
        return text;
    }

    /**
     * Write a control's words for an override, unless an edit holds them.
     *
     * Beside an icon the words go in its span, so the icon stays on the side
     * it was drawn. A rich-text label's bold and italic runs are words too,
     * so they go with the rest, and `rich` draws the text as rich text again.
     */
    _writeOverrideWords(el, node, text, path, rich = false) {
        if (this._textEdit && this._textEdit.el === el) return;
        const span = this._labelSpan(node);
        if (span) {
            this._setLabelText(span, text, path);
            if (node.tagName === 'BUTTON') {
                node.setAttribute('aria-label', text !== '' ? text : (node._baseAccessibleName || ''));
            }
            return;
        }
        node.querySelectorAll(':scope > strong, :scope > em').forEach((n) => n.remove());
        this._setLabelText(node, rich ? '' : text, path);
        if (rich) node.insertAdjacentHTML('beforeend', this._formatRichText(text));
    }

    evaluateVisibleWhen(b) {
        const { element, binding } = b;
        const conditions = binding.conditions || [];
        const check = (cond) => {
            const actual = this.state[cond.key];
            return this._evalConditionOp(cond.operator || 'eq', actual, cond.value);
        };
        const visible = binding.mode === 'any'
            ? conditions.some(check)
            : conditions.every(check);
        element.style.display = visible ? '' : 'none';
    }

    /** Evaluate a condition operator (shared by visible_when). */
    _evalConditionOp(op, actual, target) {
        switch (op) {
            case 'eq': case 'equals': case '==': return actual == target;
            case 'ne': case 'not_equals': case '!=': return actual != target;
            case 'gt': case '>': return actual != null && target != null && actual > target;
            case 'lt': case '<': return actual != null && target != null && actual < target;
            case 'gte': case '>=': return actual != null && target != null && actual >= target;
            case 'lte': case '<=': return actual != null && target != null && actual <= target;
            case 'truthy': return !!actual;
            case 'falsy': return !actual;
            default: return false;
        }
    }

    /**
     * A label's state-driven look: colour from the matching state, and its
     * words too when that state names them.
     *
     * Deliberately NOT evaluateFeedback, which is the button's. That one also
     * re-applies frameless chrome, retints an image layer, swaps a per-state
     * button_image and rebuilds an icon+text layout -- none of which a label
     * has, and all of which would become properties a label is documented as
     * reading. This does the two things a label wants and nothing else.
     *
     * When a state names no label of its own, the text is left exactly as it
     * was. A button falls back to its own `label` field there, but a label
     * draws `text` and may also carry a show.value binding that owns what it
     * says -- so the fallback would either write a field this element never
     * draws, or clobber a bound value a moment after it arrived. Colour still
     * tracks the state in that case.
     */
    evaluateLabelLook(b) {
        const { element, elementDef, binding } = b;
        const stateValue = this.state[binding.key];
        const baseStyle = elementDef.style || {};

        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline) {
            // Back to the label's own colours and its own words, asserting no
            // state. Where a `show.value` binding owns the text instead, it is
            // left alone: that binding has its own unknown form and writing
            // here would clobber it a moment after it arrived.
            clearStateColours(element, baseStyle);
            applyStyle(element, this.getThemedStyle(elementDef.type, baseStyle));
            if (!elementDef.bindings?.show?.value && elementDef.text !== undefined) {
                this._setLabelText(element, String(elementDef.text), ['text']);
            }
            return;
        }

        const { appearance, wordsPath } = withTwoStateWords(binding, pickLook(binding, stateValue));

        clearStateColours(element, this.getThemedStyle(elementDef.type, baseStyle));
        applyStyle(element, this.getThemedStyle(elementDef.type, { ...baseStyle, ...appearance }));

        if (appearance.label !== undefined) {
            this._setLabelText(element, String(appearance.label), wordsPath);
        }
        // No `else`: when a state names no label the text is left exactly as it
        // was, and so is the record of where it came from. That is precisely
        // why the source is recorded on write rather than re-derived -- nothing
        // here could work out what is on screen.
    }

    /**
     * An Image's state-driven look: the matching state's picture, and its
     * colours.
     *
     * Its own evaluator rather than the button's, which also re-applies
     * frameless chrome, retints an artwork layer and redraws words and an
     * icon -- none of which an Image has. This does the two things an Image
     * wants and nothing else.
     *
     * A state that names no picture shows the Image's own `src`, and so does
     * a device that cannot be reached: no state is claimed while the device
     * behind the key is away, the rule every other look follows.
     */
    evaluateImageLook(b) {
        const { element, elementDef, binding } = b;
        const rest = this.getThemedStyle(elementDef.type, elementDef.style || {});

        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);

        const appearance = offline ? {} : pickLook(binding, this.state[binding.key]).appearance;

        clearStateColours(element, rest);
        applyStyle(element, { ...rest, ...appearance });
        this._showImagePicture(element, elementDef, appearance.button_image || elementDef.src);
    }

    /**
     * A Page Nav's look: its Appearance state, and lit while its page is on
     * this panel's screen.
     *
     * Its own evaluator rather than the button's, which also redraws artwork,
     * display modes and frameless chrome that a Page Nav does not have. Lit
     * is the accent a lit toggle wears, drawn over the state's colours; the
     * words and the icon still come from the state. A device that cannot be
     * reached takes the state away and leaves lit alone: which page is
     * showing is still true.
     */
    evaluateNavLook(b) {
        const { element, elementDef, binding } = b;
        const rest = this.getThemedStyle(elementDef.type, elementDef.style || {});
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);

        const { appearance, wordsPath } = binding.key && !offline
            ? withTwoStateWords(binding, pickLook(binding, this.state[binding.key]))
            : { appearance: {}, wordsPath: ['label'] };

        const lit = this._navShowsItsPage(elementDef);
        clearStateColours(element, rest);
        applyStyle(element, {
            ...rest, ...appearance, ...(lit ? this._toggleOnStyle(elementDef) : {}),
        });
        element.classList.toggle('nav-current', lit);
        if (lit) element.setAttribute('aria-current', 'page');
        else element.removeAttribute('aria-current');

        // Words and icon only where a look can change them. A nav that only
        // lights keeps what it drew, so a `ui.<id>.label` a script set is not
        // written over on every page change.
        if (!binding.key) return;
        const named = appearance.label !== undefined;
        this._setLabelText(element,
            named ? String(appearance.label) : (elementDef.label || elementDef.target_page || ''),
            named ? wordsPath : ['label']);
        this._drawLookIcon(element, elementDef, appearance);
    }

    /**
     * A Container's look: the matching state's colours, and its picture drawn
     * as the frame's background the way the Container's own background
     * picture is (its size, position and opacity apply).
     *
     * A device that cannot be reached puts the Container back on its own look
     * and claims no state, the rule every other look follows. It does NOT take
     * the unavailable mark the other controls take: that mark drains colour
     * with a filter, and on a container the filter reaches every control
     * inside it, including ones bound to a variable that is still true.
     */
    evaluateGroupLook(b) {
        const { element, elementDef, binding } = b;
        const rest = this.getThemedStyle(elementDef.type, elementDef.style || {});

        const appearance = this._bindingOffline(b)
            ? {}
            : pickLook(binding, this.state[binding.key]).appearance;

        const style = { ...rest, ...appearance };
        if (appearance.button_image) style.background_image = appearance.button_image;
        clearStateColours(element, rest);
        applyStyle(element, style);
        // A faded picture is a layer of its own, which applyStyle reuses but
        // never takes away: a state with no picture removes it here.
        if (!style.background_image) {
            element.querySelector(':scope > .panel-bg-image-layer')?.remove();
        }
    }

    evaluateFeedback(b) {
        const { element, elementDef, binding } = b;
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline) {
            this._drawButtonClaimingNoState(element, elementDef);
            return;
        }

        const look = pickLook(binding, this.state[binding.key]);
        const rest = this.getThemedStyle(elementDef.type, elementDef.style || {});
        const style = { ...rest, ...look.appearance };
        clearStateColours(element, rest);
        applyStyle(element, style);
        // Again after the state's colours, which would bring the chrome back.
        if (elementDef.frameless) applyFrameless(element);
        this._reapplyButtonImage(element, elementDef, look.appearance.button_image, style.bg_color);
        this._drawButtonWords(element, elementDef, this._feedbackWords(elementDef, binding, look, style));
        this._drawLookIcon(element, elementDef, look.appearance);
    }

    /**
     * No state is asserted while the device is unreachable: the button goes
     * back to its own look, its own words, its own icon and picture. This is
     * the mute button that was photographed drawing its not-muted face over a
     * muted amplifier -- a null child key resolves to `default_state`, so
     * "unknown" was being rendered as whatever state was nominated as the
     * default.
     *
     * A state's colours go first: applyStyle only ever writes a property it has
     * a value for, so a MUTED button staying red for an amplifier nobody can
     * reach would be the same fault, one property further down. And a state's
     * WORD is a claim as much as its colour: a button with no name of its own
     * is left blank rather than still reading MUTED.
     */
    _drawButtonClaimingNoState(element, elementDef) {
        const own = elementDef.style || {};
        clearStateColours(element, own);
        applyStyle(element, this.getThemedStyle(elementDef.type, own));
        if (elementDef.frameless) applyFrameless(element);
        if (own.bg_color) updateImageTint(element, own.bg_color);
        this._drawButtonWords(element, elementDef, [elementDef.label || '', ['label']]);
        this._drawLookIcon(element, elementDef, null);
        if (elementDef.button_image) {
            applyImageEffect(element, elementDef.button_image, this._pictureOptions(elementDef, own.bg_color));
        }
    }

    /**
     * The words a button's look draws, with where an edit of them goes: what
     * the look's form names (`_stateWords`, `_twoStateWords`), else the
     * button's own label. Null leaves the words as they are.
     */
    _feedbackWords(elementDef, binding, look, style) {
        const named = binding.states ? this._stateWords(look) : this._twoStateWords(binding, look, style);
        return named || (elementDef.label ? [elementDef.label, ['label']] : null);
    }

    /** A state's own words, when it names any. */
    _stateWords({ appearance, wordsPath }) {
        return appearance.label !== undefined ? [String(appearance.label), wordsPath] : null;
    }

    /**
     * The two-state form's words: the active or inactive label, else a label
     * in the style, where whichever half supplied it is the half an edit writes.
     */
    _twoStateWords(binding, { appearance, wordsPath, isActive }, style) {
        const half = isActive ? 'label_active' : 'label_inactive';
        if (binding[half]) return [binding[half], ['bindings', 'show', 'look', half]];
        if (style.label === undefined) return null;
        return [style.label, appearance.label !== undefined ? wordsPath : ['style', 'label']];
    }

    /**
     * A toggle button's own indication: whether the thing it toggles is on.
     *
     * Toggle mode used to be a DISPATCH rule and never a display rule --
     * `toggle_key` was read in the press handler, to pick between `ui.press`
     * and `ui.toggle_off`, and nowhere else. So a mute button configured
     * exactly the way the Builder invites ("Toggle: on/off based on current
     * state", a state key, a value that means on, and a live "Current: true
     * ON" badge under it) muted the amplifier every time and drew the same
     * pixels either way. A mute, power or mic toggle with no indication is
     * the commonest control on an AV panel, and the wall is where the
     * indication matters more than the press.
     *
     * Two halves, and the first is a documented contract rather than a new
     * idea: `on_label` / `off_label` are written up in the project format and
     * in the UI Builder guide as changing the button text per state, and
     * nothing here read them. The second is the look, for the toggle that
     * names no labels -- which is nearly all of them, since the Builder only
     * offered those two fields to control surfaces.
     *
     * What it draws:
     *   on      -- the accent, filled, with a text colour computed to read on
     *              it, plus `on_label` where one is set
     *   off     -- the button's own look and its own label (or `off_label`)
     *   offline -- the same as off, asserting nothing, per the rule the rest
     *              of this renderer follows: an unreachable device's last
     *              known state is not a state to draw
     *
     * A `show.look` binding takes the whole job instead (see renderButton) --
     * this never runs beside one.
     */
    evaluateToggleLook(b) {
        const { element, elementDef, binding } = b;
        const baseStyle = elementDef.style || {};

        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);

        const stateValue = this.state[binding.key];
        const on = !offline && stateValue != null && binding.value !== undefined &&
            String(stateValue).toLowerCase() === String(binding.value).toLowerCase();

        // applyStyle only ever writes a property it has a value for, so going
        // off has to drop the lit colours first or a button whose own style
        // names none stays lit for good.
        clearStateColours(element, baseStyle);
        const style = this.getThemedStyle(elementDef.type, on
            ? { ...baseStyle, ...this._toggleOnStyle(elementDef) }
            : baseStyle);
        applyStyle(element, style);
        element.classList.toggle('toggle-on', on);
        // Frameless and image buttons take their fill from artwork, so the
        // class above is the whole indication there -- an outline, which no
        // inline write of ours ever clobbers.
        if (elementDef.frameless) applyFrameless(element);
        this._reapplyButtonImage(element, elementDef, null, style.bg_color);

        // The words, only where the author asked for them. A toggle that names
        // neither label keeps whatever it is already showing: rewriting the
        // label every pass would fight a `ui.<id>.label` override and rebuild
        // an icon layout for nothing.
        const named = binding.on_label != null || binding.off_label != null;
        if (named && this._buttonShowsWords(elementDef)) {
            this._setLabelText(element, ...this._toggleWords(elementDef, binding, on, offline));
            this._drawLookIcon(element, elementDef, null);
        }
    }

    /**
     * A toggle's words and where an edit of them goes: its on or off word;
     * an empty one, or none while its device is away, falls back to the
     * button's own label, so an edit of what is showing belongs to whichever
     * one actually drew.
     */
    _toggleWords(elementDef, binding, on, offline) {
        const word = offline ? null : (on ? binding.on_label : binding.off_label);
        if (word == null || word === '') return [elementDef.label || '', ['label']];
        const labelBase = binding._labelBase || ['bindings', 'do', 'press', 0];
        return [word, [...labelBase, on ? 'on_label' : 'off_label']];
    }

    /**
     * The look a toggle takes while it is on: the accent, filled.
     *
     * The accent rather than a colour of our own, so it follows the theme and
     * the panel's own accent setting, and so an element that names its own
     * `accent_color` lights in that. Deliberately no border colour: the off
     * pass restores the element's own style, and `clearStateColours` drops
     * only what a state look may write (background, text colour, opacity)
     * -- adding another here without teaching that helper about it is how a
     * button ends up wearing half of a state it is no longer in.
     */
    _toggleOnStyle(elementDef) {
        const accent = elementDef.style?.accent_color
            // Read from the inline custom property the theme sets rather than
            // through getComputedStyle: this runs in the binding pass, and a
            // computed read there costs a style flush on every state batch.
            // The fallback is the same colour the :root rule in
            // panel-elements.css carries, so a panel that never loaded a theme
            // lights in the accent it is already drawn with.
            || document.documentElement.style.getPropertyValue('--panel-accent').trim()
            || '#2196F3';
        const style = { bg_color: accent };
        const text = this._readableTextOn(accent);
        if (text) style.text_color = text;
        return style;
    }

    /**
     * Near-black or white, whichever reads on this background -- picked by
     * WCAG contrast rather than a lightness guess, because the built-in
     * accents run from #1976D2 to #00E676 and one answer cannot serve both.
     *
     * Returns null when the colour cannot be read, and the button then keeps
     * its own text colour: hex and rgb()/rgba() are what every theme variable
     * and every colour picker in the IDE write, and resolving a named colour
     * or hsl() would mean asking the browser through a canvas, which this path
     * cannot afford.
     */
    _readableTextOn(color) {
        const rgb = this._parseRgbChannels(color);
        if (!rgb) return null;
        const lum = this._relativeLuminance(rgb);
        const DARK = 0.00518;              // relative luminance of #111111
        const onWhite = 1.05 / (lum + 0.05);
        const onDark = (lum + 0.05) / (DARK + 0.05);
        return onDark >= onWhite ? '#111111' : '#ffffff';
    }

    /** [r, g, b] 0-255 from a hex or rgb()/rgba() colour, or null. */
    _parseRgbChannels(color) {
        if (typeof color !== 'string') return null;
        const v = color.trim().toLowerCase();
        const hex = v.match(/^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/);
        if (hex) {
            const h = hex[1];
            const wide = h.length > 4;
            const pair = (i) => wide ? h.slice(i * 2, i * 2 + 2) : h[i] + h[i];
            return [0, 1, 2].map(i => parseInt(pair(i), 16));
        }
        const rgb = v.match(/^rgba?\(([^)]+)\)$/);
        if (rgb) {
            const parts = rgb[1].split(/[,/\s]+/).filter(Boolean);
            if (parts.length < 3) return null;
            const chan = (s) => {
                const n = s.endsWith('%') ? (parseFloat(s) / 100) * 255 : parseFloat(s);
                return isNaN(n) ? null : Math.max(0, Math.min(255, Math.round(n)));
            };
            const out = parts.slice(0, 3).map(chan);
            return out.every(c => c !== null) ? out : null;
        }
        return null;
    }

    /** WCAG relative luminance of [r, g, b]. */
    _relativeLuminance([r, g, b]) {
        const lin = [r, g, b].map(c => {
            const s = c / 255;
            return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
    }

    /**
     * A bound value as a label should print it.
     *
     * A device float arrives at float64 width, so a float32 reading of 0.06
     * comes across as 0.06000000238418579 and a raw print is unreadable on a
     * panel. `display_decimals` rounds it. Only real numbers are touched:
     * most labels are bound to text — device names, input modes, firmware
     * versions — and reformatting a version string of "2.10" would be wrong.
     */
    _labelValueText(value, elementDef) {
        const dec = this._displayDecimals(elementDef);
        if (dec == null || typeof value !== 'number' || !Number.isFinite(value)) return String(value);
        return value.toFixed(dec);
    }

    evaluateText(b) {
        const { element, elementDef, binding } = b;
        const value = this.state[binding.key];
        const useRich = elementDef?.style?.white_space;

        // `path` names the authored field this text came from, rooted at the
        // element definition, or null where what is drawn is not authored
        // anywhere -- a bare value, or the "--" an unreachable device earns.
        const setText = (text, path) => {
            // An edit in flight owns these words; see _setLabelText.
            if (this._textEdit && this._textEdit.el === element) return;
            this._recordTextSource(element, path);
            if (b._lastText === text) return;
            b._lastText = text;
            if (useRich) {
                element.innerHTML = this._formatRichText(text);
            } else {
                element.textContent = text;
            }
        };
        const FORMAT = ['bindings', 'show', 'value', 'format'];

        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline) {
            // The label keeps its sentence and loses its number: a format of
            // "Amp draw: {value} A" reads "Amp draw: -- A". What was
            // photographed for this fault was "Amp draw: 0.00 A" on an
            // amplifier drawing 0.076 A through a port that was broken.
            setText(binding.format
                ? String(binding.format).split('{value}').join('--')
                : '--', binding.format ? FORMAT : null);
            return;
        }

        if (binding.condition) {
            // Normalized compare (matches feedback/visible_when), so a numeric 1
            // or boolean true matches a condition.equals of '1'/'true' instead of
            // silently failing the strict-=== check and sticking on text_false.
            const isMatch = value !== undefined && value !== null &&
                String(value).toLowerCase() === String(binding.condition.equals).toLowerCase();
            setText(isMatch ? (binding.text_true || '') : (binding.text_false || ''),
                ['bindings', 'show', 'value', isMatch ? 'text_true' : 'text_false']);
            return;
        }

        if (value === undefined || value === null) {
            // Same sentence-without-the-number the offline branch draws: a
            // label formatted "Amp draw: {value} A" reads "Amp draw: -- A"
            // rather than vanishing, so the page keeps its shape and the reader
            // can see which reading is missing.
            setText(binding.format
                ? String(binding.format).split('{value}').join('--')
                : '--', binding.format ? FORMAT : null);
            return;
        }
        const shown = this._labelValueText(value, elementDef);
        if (binding.format) {
            // split/join replaces every {value} and treats the value literally,
            // so device values containing $-sequences (track titles, paths)
            // aren't reinterpreted the way String.replace would.
            setText(String(binding.format).split('{value}').join(shown), FORMAT);
        } else {
            // A bare value with no format around it is not authored text. The
            // editor opens it seeded with the literal `{value}` instead, so
            // typing a sentence around it CREATES the format.
            setText(shown, null);
        }
    }

    evaluateColor(b) {
        const { element, binding } = b;
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        // An unreachable device lights nothing. A status LED holding its last
        // colour is the smallest and most persuasive of these lies -- it is the
        // control an integrator puts on a page precisely to be glanced at.
        const value = offline ? undefined : this.state[binding.key];
        const colorMap = binding.map || {};
        const defaultColor = binding.default || '#9E9E9E';
        const color = offline ? defaultColor : (colorMap[value] || defaultColor);

        element.style.backgroundColor = color;
        element.style.color = color;
        // Treat all off-like values as inactive, not just the literal string
        // 'off' — 0 / false / '' / '0' / 'false' from a device should not light
        // the LED's active/glow treatment.
        const isOff = value === null || value === undefined || value === false || value === 0 ||
            (typeof value === 'string' && ['', 'off', 'false', '0', 'no'].includes(value.trim().toLowerCase()));
        element.classList.toggle('active', !isOff);

        // Add glow effect for active states
        if (color !== defaultColor) {
            element.style.boxShadow = `0 0 0.7143rem ${color}`;
        } else {
            element.style.boxShadow = '0 0 0.4286rem rgba(0,0,0,0.3)';
        }
    }

    evaluateSliderValue(b) {
        // Don't yank the thumb out from under an operator who is actively
        // dragging it when a device echo or another panel's change arrives.
        // A native range input keeps keyboard focus after pointer release or
        // a committed arrow-key change. Focus alone must not suppress later
        // readings or disconnection feedback; only an active drag does.
        if (b.element._dragging) return;
        const rawValue = this.state[b.binding.key];
        const offline = this._bindingOffline(b);
        if (b._lastSliderRaw === rawValue && b._lastSliderOffline === offline) return;
        b._lastSliderRaw = rawValue;
        b._lastSliderOffline = offline;
        this._markBindingAvailability(b, offline);
        b._slider.show(rawValue, offline);
    }

    evaluateSelectValue(b) {
        const { element, binding } = b;
        const value = this.state[binding.key];
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline || value === undefined || value === null) {
            // No selection at all rather than a stale one or the first option:
            // a dropdown reading "HDMI 2" for a switcher nobody can reach is
            // the same confident wrong answer a fader gives, and so is one
            // reading "HDMI 1" for a switcher that has not said yet.
            element.selectedIndex = -1;
            return;
        }
        element.value = String(value);
    }

    // Select appearance (show.look.style_map): the control takes the colors
    // configured for the option matching the bound key's current value, and
    // returns to the themed look when nothing matches. Both properties are
    // written on every pass so a previous match never lingers.
    evaluateSelectLook(b) {
        const { select, binding } = b;
        const stateValue = this.state[binding.key];
        const styleMap = binding.style_map || {};
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        const matched = offline || stateValue === undefined || stateValue === null
            ? undefined
            : styleMap[String(stateValue)];
        select.style.backgroundColor = (matched && matched.bg_color) || '';
        select.style.color = (matched && matched.text_color) || '';
    }

    evaluateTextInputValue(b) {
        const { element, binding } = b;
        // Don't overwrite if user is actively editing (prevents cursor loss)
        if (document.activeElement === element) return;
        const value = this.state[binding.key];
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline || value === undefined || value === null) {
            // Key deleted, or the device is unreachable — clear rather than
            // keeping a value that is no longer coming from anywhere.
            element.value = '';
            return;
        }
        element.value = String(value);
    }

    // --- Lock Screen ---

    /** Is a lock configured for this panel?
     *
     *  The server sends `lock_enabled` and keeps the PIN — a panel client is
     *  unauthenticated, so the definition it receives is public to the LAN.
     *  `lock_code` is only ever present when the Programmer pushed an UNSAVED
     *  draft into its Preview iframe, which is an authenticated surface; see
     *  `_checkLockCode`.
     */
    _lockConfigured() {
        const settings = this.uiSettings || {};
        return settings.lock_enabled === true || !!settings.lock_code;
    }

    /** Is this the right PIN? true, false, or null for "could not ask".
     *
     *  Two paths, and the split is exactly the split in who holds the PIN.
     *  Every real panel gets `lock_enabled` and no code, so the attempt goes
     *  to the server and only the verdict comes back. The Builder's Preview is
     *  handed the project it is editing, unsaved PIN and all, and compares
     *  locally — otherwise changing the PIN and pressing Preview would test
     *  the last SAVED one and tell the programmer their new PIN is wrong.
     *
     *  `null` is its own answer because "Incorrect PIN" for an unreachable
     *  server sends somebody to try the PIN they already know, over and over.
     *  The timeout is what turns a hung request into that answer instead of a
     *  lock screen whose Unlock button never comes back.
     */
    async _checkLockCode(attempt) {
        const drafted = this.uiSettings?.lock_code;
        if (drafted) return attempt === drafted;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 5000);
        try {
            const res = await fetch(`${basePath()}/api/panel/unlock`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: attempt }),
                signal: controller.signal,
            });
            if (!res.ok) return null;
            const data = await res.json();
            return data?.success === true;
        } catch (err) {
            console.warn('[panel] unlock check failed:', err);
            return null;
        } finally {
            clearTimeout(timer);
        }
    }

    /**
     * Reconcile the lock overlay against a freshly-received ui.definition.
     *
     * The server resends state.snapshot + ui.definition on every (re)connect,
     * so a transient socket drop must NOT re-lock a panel the operator already
     * unlocked. We therefore show the lock screen at most once per session
     * here; idle return-to-lock still re-shows it explicitly via resetIdleTimer.
     * Also reconciles a project edit that cleared the PIN while a panel sat
     * locked: a now-unconfigured lock overlay is removed so it can't get stuck.
     */
    _reconcileLockOnDefinition() {
        if (this.editMode) return;
        const overlay = document.getElementById('lock-overlay');
        if (!this._lockConfigured()) {
            // Lock disabled (or removed mid-session) — clear any stuck overlay.
            if (overlay) overlay.remove();
            this.locked = false;
            return;
        }
        if (!this._lockInitialized) {
            this._lockInitialized = true;
            this.showLockScreen();
        }
    }

    showLockScreen() {
        if (this.editMode) return;
        if (!this._lockConfigured()) return;

        // Prevent stacking multiple lock overlays
        if (document.getElementById('lock-overlay')) return;

        this.locked = true;

        // Create lock overlay
        const overlay = document.createElement('div');
        overlay.id = 'lock-overlay';
        overlay.className = 'lock-overlay';

        overlay.innerHTML = `
            <div class="lock-container">
                <div class="lock-icon">\u{1F512}</div>
                <div class="lock-title">Panel Locked</div>
                <input type="password" id="lock-input" class="lock-input" placeholder="Enter PIN" maxlength="6" inputmode="numeric" pattern="[0-9]*" />
                <button id="lock-submit" class="lock-submit">Unlock</button>
                <div id="lock-error" class="lock-error"></div>
            </div>
        `;

        document.body.appendChild(overlay);

        const input = document.getElementById('lock-input');
        const submit = document.getElementById('lock-submit');
        const error = document.getElementById('lock-error');

        let checking = false;
        const tryUnlock = async () => {
            if (checking) return;   // Enter and the button both fire; one attempt.
            checking = true;
            submit.disabled = true;
            try {
                const ok = await this._checkLockCode(input.value);
                if (!document.body.contains(overlay)) return;
                if (ok === true) {
                    overlay.remove();
                    this.locked = false;
                    this.resetIdleTimer();
                } else {
                    error.textContent = ok === null
                        ? "Can't reach the server — try again"
                        : 'Incorrect PIN';
                    if (ok === false) input.value = '';
                    input.focus();
                    setTimeout(() => { error.textContent = ''; }, 2000);
                }
            } finally {
                checking = false;
                submit.disabled = false;
            }
        };

        submit.addEventListener('click', tryUnlock);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') tryUnlock();
        });

        // Clear PIN when page is hidden (security on shared displays)
        const clearOnHide = () => {
            if (document.hidden) {
                input.value = '';
                error.textContent = '';
            }
        };
        document.addEventListener('visibilitychange', clearOnHide);
        // Clean up listener when overlay is removed
        const observer = new MutationObserver(() => {
            if (!document.body.contains(overlay)) {
                document.removeEventListener('visibilitychange', clearOnHide);
                observer.disconnect();
            }
        });
        observer.observe(document.body, { childList: true });

        input.focus();
    }

    // --- Idle Timeout ---

    resetIdleTimer() {
        if (this.idleTimer) clearTimeout(this.idleTimer);
        // Never arm the idle timer while disconnected — it would navigate a
        // dead panel and stack a lock screen over the offline overlay.
        if (this._offline) return;
        const timeout = this.uiSettings?.idle_timeout_seconds;
        if (!timeout || timeout <= 0 || this.locked) return;

        this.idleTimer = setTimeout(() => {
            let idlePage = this.uiSettings?.idle_page || 'main';
            // Validate against the current pages so a deleted/renamed idle_page
            // resolves deterministically to the first page instead of relying on
            // renderCurrentPage's silent fallback.
            const pages = this.uiDef?.pages || [];
            if (pages.length && !pages.some(p => p.id === idlePage)) {
                idlePage = pages[0].id;
            }
            if (this.currentPage !== idlePage || this.overlayStack.length > 0) {
                this.dismissAllOverlays();
                this.currentPage = idlePage;
                this.pageHistory = []; // Idle reset starts a fresh session — no $back into prior user's navigation
                this.renderCurrentPage();
            }
            // Re-show lock screen if lock code is set
            if (this._lockConfigured()) {
                this.showLockScreen();
            }
        }, timeout * 1000);
    }

    setupIdleListeners() {
        if (this._idleListenersSetup) return;
        this._idleListenersSetup = true;

        const events = ['mousedown', 'mousemove', 'keydown', 'touchstart', 'scroll'];
        events.forEach(evt => {
            document.addEventListener(evt, () => this.resetIdleTimer(), { passive: true });
        });
    }

    // --- Helpers ---

    /** Put a theme on the page, and redraw it if that changed how elements look.
     *
     *  Returns a promise that settles once a theme (or the fallback) has been
     *  applied, so the first draw can wait for it rather than drawing the whole
     *  page twice. `repaint: false` is for exactly that caller: it is about to
     *  draw the page itself, so there is nothing yet to repaint.
     */
    applyTheme(settings, { repaint = true } = {}) {
        const themeId = settings.theme_id || (settings.theme === 'light' ? 'light-modern' : 'dark-default');
        const overrides = settings.theme_overrides || {};

        if (this._themeApplyInProgress) return Promise.resolve();

        const prevDefaults = JSON.stringify(this.themeElementDefaults || {});

        // The same question at the end of all three branches: element defaults
        // are merged into what each renderer draws, so a theme that moved them
        // only takes effect on a redraw. Written once -- it was three copies,
        // and a rule that decides whether the panel repaints should not be able
        // to disagree with itself.
        const repaintIfLookChanged = () => {
            const newDefaults = JSON.stringify(this.themeElementDefaults || {});
            if (!repaint || prevDefaults === newDefaults || !this.snapshotReceived) return;
            this._themeApplyInProgress = true;
            this.renderCurrentPage();
            this._themeApplyInProgress = false;
        };

        // Theme Studio path: parent supplied a working-copy theme. Apply it
        // synchronously without hitting the network so picker drags reflect
        // within a frame instead of after a round-trip.
        if (this.inlineTheme && this.inlineTheme.id === themeId) {
            this._applyThemeData(this.inlineTheme, overrides, settings);
            this.currentTheme = this.inlineTheme;
            repaintIfLookChanged();
            return Promise.resolve();
        }

        if (this.currentTheme && this.currentTheme.id === themeId) {
            this._applyThemeData(this.currentTheme, overrides, settings);
            repaintIfLookChanged();
            return Promise.resolve();
        }


        return fetch(`${basePath()}/api/themes/${encodeURIComponent(themeId)}`)
            .then(res => {
                if (!res.ok) return null;
                return res.json().catch(() => null);
            })
            .catch(() => null)
            .then(theme => {
                if (theme) {
                    this._applyThemeData(theme, overrides, settings);
                    this.currentTheme = theme;
                } else {
                    this._applyFallbackTheme(settings);
                }
                repaintIfLookChanged();
            })
            .catch(() => this._applyFallbackTheme(settings));
    }

    /** Draw the first page once the theme is in hand -- but draw it regardless.
     *
     *  Everything an element looks like comes from a theme that is one fetch
     *  away, and `applyTheme` returns before it lands. Drawing first and again
     *  when it arrives is what made a custom control cost two frame loads
     *  instead of one, and rebuilding every frame on a page is the most
     *  expensive thing the panel does.
     *
     *  A theme that never answers must not leave a blank panel, so the wait is
     *  capped: past it the page draws with no theme -- which is what the
     *  discarded first pass used to draw -- and repaints when the theme lands.
     */
    _drawWhenThemeArrives() {
        const drawFirst = () => {
            if (this._firstThemeSettled) return;
            this._firstThemeSettled = true;
            this.renderCurrentPage();
        };
        const giveUpWaiting = setTimeout(drawFirst, FIRST_THEME_WAIT_MS);
        this.applyTheme(this.uiDef?.settings || {}, { repaint: false }).then(() => {
            clearTimeout(giveUpWaiting);
            // Already drew without it: this is now an ordinary theme change to
            // a page that is up, which is the repaint applyTheme normally does.
            if (this._firstThemeSettled) this.renderCurrentPage();
            else drawFirst();
        });
    }

    _applyThemeData(theme, overrides, settings) {
        const root = document.documentElement;
        const vars = { ...theme.variables, ...overrides };

        // Map theme variables to CSS custom properties. The last four
        // (accent_hover, button_border, surface, surface_border) aren't
        // consumed by any rule in panel-elements.css today, but are exposed
        // so theme authors and user CSS can reference them via var(--panel-*).
        // Hover derives from accent/button via CSS filter (no *_hover token).
        // Active button bg derives from --panel-accent in CSS (no separate token).
        const varMap = {
            panel_bg: '--panel-bg',
            panel_text: '--panel-text',
            accent: '--panel-accent',
            button_bg: '--panel-button-bg',
            button_text: '--panel-button-text',
            button_border: '--panel-button-border',
            danger: '--panel-danger',
            success: '--panel-success',
            warning: '--panel-warning',
            surface: '--panel-surface',
            surface_border: '--panel-surface-border',
            border_radius: '--panel-border-radius',
        };

        for (const [key, cssVar] of Object.entries(varMap)) {
            if (vars[key] != null) {
                // Numeric theme variables are measurements, and every stored
                // measurement is rem now -- the same unit the stylesheets and
                // the migration speak.
                const val = typeof vars[key] === 'number' ? vars[key] + 'rem' : vars[key];
                root.style.setProperty(cssVar, val);
            }
        }

        if (vars.font_family) {
            document.body.style.fontFamily = vars.font_family;
        }

        // Per-setting overrides take priority over the theme's variables.
        if (settings.accent_color) {
            root.style.setProperty('--panel-accent', settings.accent_color);
            vars.accent = settings.accent_color;
        }
        if (settings.font_family) {
            document.body.style.fontFamily = settings.font_family;
            vars.font_family = settings.font_family;
        }

        // Store element defaults for use in rendering
        this.themeElementDefaults = theme.element_defaults || {};
        // ...and the variables every element ends up drawing from: the theme's
        // own, overlaid with the project's theme_overrides and then the two
        // per-setting overrides above. Elements get these for free because they
        // read the --panel-* custom properties set here; page_defaults do not,
        // because their `var(name)` references are resolved in JS. Resolving
        // those against theme.variables painted the page in the theme's colour
        // while every element on it honoured the override.
        this.themeVariables = vars;
    }

    _applyFallbackTheme(settings) {
        const root = document.documentElement;
        if (settings.accent_color) {
            root.style.setProperty('--panel-accent', settings.accent_color);
        }
        if (settings.font_family) {
            document.body.style.fontFamily = settings.font_family;
        }
        // Basic light/dark fallback
        if (settings.theme === 'light') {
            root.style.setProperty('--panel-bg', '#f5f5f5');
            root.style.setProperty('--panel-text', '#212121');
            root.style.setProperty('--panel-button-bg', '#e0e0e0');
            root.style.setProperty('--panel-button-text', '#424242');
        }
        this.themeElementDefaults = {};
    }

    /**
     * Resolve a theme `"var(name)"` reference to its underlying variable value.
     * Returns the original value unchanged if it isn't a var() string.
     */
    _resolveThemeValue(value, variables) {
        if (typeof value !== 'string') return value;
        const match = value.match(/^var\(([^)]+)\)$/);
        if (!match) return value;
        const v = variables?.[match[1].trim()];
        return v != null ? v : null;
    }

    /**
     * Convert theme.page_defaults (with background_color/_image/_gradient keys
     * and `var(name)` references) into a page.background-shaped object so
     * _applyPageBackground can consume it.
     */
    _themePageDefaultsToBackground(defaults, variables) {
        if (!defaults) return null;
        const bg = {};
        const color = this._resolveThemeValue(defaults.background_color, variables);
        if (color) bg.color = color;
        const image = this._resolveThemeValue(defaults.background_image, variables);
        if (image) {
            bg.image = image;
            if (defaults.background_image_size) bg.image_size = defaults.background_image_size;
            if (defaults.background_image_position) bg.image_position = defaults.background_image_position;
            if (defaults.background_image_opacity != null) bg.image_opacity = defaults.background_image_opacity;
        }
        const gradient = defaults.background_gradient;
        if (gradient && typeof gradient === 'object') {
            const from = this._resolveThemeValue(gradient.from, variables);
            const to = this._resolveThemeValue(gradient.to, variables);
            if (from && to) {
                bg.gradient = { from, to, angle: gradient.angle };
            }
        }
        return Object.keys(bg).length ? bg : null;
    }

    _applyPageBackground(gridEl, bg) {
        // Inherit theme.page_defaults when the page itself doesn't set a background.
        // Keeps bg visuals consistent with the active theme for pages that don't opt out.
        if (!bg || (!bg.color && !bg.image && !bg.gradient)) {
            bg = this._themePageDefaultsToBackground(
                this.currentTheme?.page_defaults,
                this.themeVariables,
            );
        }
        if (!bg) return;
        gridEl.style.position = 'relative';

        // Solid color
        if (bg.color) {
            gridEl.style.backgroundColor = bg.color;
        }
        // Background image with opacity
        if (bg.image) {
            const imgLayer = document.createElement('div');
            imgLayer.className = 'panel-page-bg-image';
            drawFadedPicture(imgLayer, {
                url: assetUrl(bg.image),
                size: bg.image_size || 'cover',
                position: bg.image_position || 'center',
                opacity: bg.image_opacity ?? 1,
            });
            gridEl.prepend(imgLayer);
        }
        // Gradient overlay (renders on TOP of image)
        const gradient = gradientCss(bg.gradient);
        if (gradient) {
            const gradLayer = document.createElement('div');
            gradLayer.className = 'panel-page-bg-gradient';
            gradLayer.style.position = 'absolute';
            gradLayer.style.inset = '0';
            gradLayer.style.zIndex = '1';
            gradLayer.style.pointerEvents = 'none';
            gradLayer.style.background = gradient;
            gridEl.prepend(gradLayer);
        }
    }

    getThemedStyle(elementType, elementStyle) {
        const defaults = this.themeElementDefaults[elementType] || {};
        return { ...defaults, ...elementStyle };
    }

    /**
     * Remove text nodes from an element, leaving element children intact.
     * Used to suppress labels on image/icon-only buttons without wiping the image layer.
     */
    _removeTextNodes(el) {
        Array.from(el.childNodes).forEach((n) => {
            if (n.nodeType === Node.TEXT_NODE) n.remove();
            else if (n.classList?.contains('panel-label-span')) n.remove();
        });
    }

    /**
     * Record WHICH authored field produced the text now on screen.
     *
     * The Builder's in-place editor needs to answer "what do I write when
     * somebody edits this?", and it cannot be re-derived after the fact:
     * `evaluateLabelLook` deliberately DECLINES to write when a state names no
     * label of its own, so the words on screen may have come from a previous
     * state, from a `show.value` binding, or from the element's own `text`. The
     * only moment the answer is known is the moment the text is written, so
     * every writer states it here and the record is what the editor reads back.
     *
     * The path is a JSON array rather than a dotted string because a segment
     * can be a state VALUE -- `show.look.states["2.5"].label` is a legal slot,
     * and splitting that on dots loses it.
     *
     * `null` means the text is real but nothing authored it: a runtime
     * `ui.<id>.label` override, a computed value, a positional fallback. The
     * editor refuses those rather than writing somewhere that would not show.
     *
     * Edit mode only. A wall panel carries no editor and pays nothing for this.
     */
    _recordTextSource(el, path) {
        if (!this.editMode || !el || el.nodeType !== Node.ELEMENT_NODE) return;
        const host = el.closest ? (el.closest('[data-element-id]') || el) : el;
        if (path == null) host.removeAttribute('data-avc-text-src');
        else host.setAttribute('data-avc-text-src', JSON.stringify(path));
    }

    /**
     * Set or replace an element's label text without touching element children
     * (icons, image layer). Removes existing text nodes and appends a new one.
     * A button's accessible name follows its words, retaining its configured
     * name when the visible label is empty.
     *
     * `path` names the authored field this text came from, for the in-place
     * editor. Every caller passes one, or null where the words are not
     * authored anywhere (see `_recordTextSource`).
     */
    _setLabelText(el, text, path) {
        // An edit in flight owns this element's words. Live state keeps arriving
        // while somebody types -- the canvas seeds the real room's state and
        // re-sends it on every repost -- and a write here would wipe the caret,
        // or worse, swap the slot being edited out from under it.
        if (this._textEdit && this._textEdit.el === el) return;
        this._removeTextNodes(el);
        if (text != null && text !== '') {
            el.appendChild(document.createTextNode(String(text)));
        }
        if (el.tagName === 'BUTTON') {
            el.setAttribute('aria-label', text != null && text !== ''
                ? String(text) : (el._baseAccessibleName || ''));
        }
        if (path !== undefined) this._recordTextSource(el, path);
    }

    // --- In-place text editing (edit mode only) ---
    //
    // The Builder's canvas IS this document, in an iframe, with a transparent
    // overlay of hit boxes on top of it. A double-click out there arrives here
    // as `openavc:editor-edit-text` naming an element, and the text the author
    // is looking at becomes editable where it sits -- the real node, the real
    // font, the real box, so nothing shifts when the edit commits.
    //
    // What is edited is always the authored SOURCE, never the rendered form. A
    // label drawing "Amp draw: 0.08 A" opens as the literal
    // "Amp draw: {value} A"; one drawing bold text opens as "**bold**". That is
    // what makes plain-text editing correct everywhere and a rich-text editor
    // unnecessary.

    /**
     * A caption: which field it came from, and which node holds it.
     *
     * Two facts, and only the renderer has either. The field goes on the
     * element root (`_recordTextSource`); the node is marked here because a
     * caption is NOT findable by class -- `.group-label`, `.gauge-label` and
     * `.list-label` have one, but a fader's, a dropdown's, a keypad's and a
     * status LED's are a bare <label> or <div> with none.
     *
     * Getting this wrong is not a near miss. Without the mark, the editor fell
     * back to the element itself and opening a fader replaced the whole
     * control -- track, thumb, scale marks, value readout -- with the caption
     * text, then rebuilt it from saved HTML, which returns the nodes without
     * the listeners that made it draggable.
     */
    _recordCaption(root, captionNode, path) {
        this._recordTextSource(root, path);
        if (captionNode) root._captionNode = captionNode;
        if (this.editMode && captionNode) captionNode.dataset.avcTextHost = '1';
    }

    /**
     * The controls whose text is edited on the canvas, and nothing else.
     *
     * Not every control that draws words belongs here. The gesture is for text
     * somebody actually retypes: a button's words, a label, and the CAPTION on
     * the controls that carry one -- a dropdown's label, a gauge's label, a
     * fader's label. Those captions are a node of their own holding one field,
     * which is what makes them safe.
     *
     * Deliberately absent:
     *
     * - `matrix`, whose row names come from a generator plus overrides and a
     *   naming rule copied in four places. Its setup dialog owns them.
     * - `camera_preset`, which draws `preset_number` and `label` joined by a
     *   newline in ONE text node, so an edit would hand back "3\nWide Shot"
     *   and have to parse it apart again.
     * - `page_nav`, whose words fall back to the target page id, so committing
     *   what is on screen would freeze a copy of a name that should follow the
     *   page.
     * - `clock`, `image`, `plugin` and `custom`, which draw computed values,
     *   somebody else's markup, or no words at all.
     *
     * Checked at both doors: nothing outside this set is offered
     * (`_reportTextDefaults`) and nothing outside it opens (`_beginTextEdit`),
     * so a control cannot become editable as a side effect of gaining a
     * binding -- which is exactly how a camera preset used to slip in, through
     * `evaluateFeedback` recording a source its renderer never composed alone.
     */
    static get IN_PLACE_TEXT_TYPES() {
        return new Set([
            // Words that ARE the control.
            'button', 'label',
            // A caption beside the control, in a node of its own.
            'group', 'status_led', 'slider', 'fader', 'level_meter',
            'gauge', 'select', 'text_input', 'list', 'keypad',
            // A nav button is a button with words on it. Its text falls back to
            // the target page id, so it opens EMPTY with that id behind it as a
            // placeholder: committing the fallback would freeze a copy of a
            // name that should keep following the page (_textPlaceholderFor).
            'page_nav',
        ]);
    }

    /** The element type for every id currently drawn, for the two gates above. */
    _drawnElementTypes() {
        const types = {};
        const pages = (this.uiDef && this.uiDef.pages) || [];
        const page = pages.find(p => p && p.id === this.currentPage);
        for (const el of (page && page.elements) || []) {
            if (el && el.id) types[el.id] = el.type;
        }
        for (const el of (this.uiDef && this.uiDef.master_elements) || []) {
            if (el && el.id) types[el.id] = el.type;
        }
        return types;
    }

    /**
     * The node whose text an author edits, which is not always the element.
     *
     * A bare button holds its words in text nodes directly (`_setLabelText`);
     * one with an icon holds them in `.panel-label-span` (`renderElementContent`);
     * a group's sit in `.group-label`. Whoever holds them is who gets the caret.
     */
    _textHostFor(el) {
        // The caption the renderer marked, wherever it nested it; then a
        // button's icon-adjacent span; then the element itself, which is right
        // only for a control whose words ARE its whole content.
        return el.querySelector('[data-avc-text-host]')
            || el.querySelector(':scope > .panel-label-span')
            || el;
    }

    /**
     * Read the authored string at a recorded source path.
     *
     * Returns undefined when the path leads nowhere, which is the "author has
     * not written this yet" case and opens an empty editor rather than refusing.
     */
    _authoredTextAt(elementDef, path) {
        let node = elementDef;
        for (const seg of path) {
            if (node == null || typeof node !== 'object') return undefined;
            node = node[seg];
        }
        return node == null ? undefined : String(node);
    }

    /**
     * What to show behind an empty editor: the fallback the renderer would draw
     * if this field stayed empty.
     *
     * A `page_nav` with no label draws its target page id, a `camera_preset`
     * draws "Preset". Those are visible but NOT authored, and committing one as
     * authored text freezes a copy -- rename the page afterwards and the button
     * keeps the old word. So they open empty with the fallback as a placeholder:
     * Enter-unchanged is a no-op, typing creates the field.
     */
    _textPlaceholderFor(elementDef) {
        if (elementDef.type === 'page_nav') {
            return elementDef.target_page || 'No Target';
        }
        if (elementDef.type === 'camera_preset') return 'Preset';
        return '';
    }

    /**
     * The rendered node and the authored definition for an id.
     *
     * A page's elements are a flat array with parent references, so the def
     * is a scan and the node is a query.
     */
    _editableTarget(elementId) {
        // Document-scoped rather than #panel-root: an overlay page draws into
        // its own surface, and only one page is in the document at a time.
        const el = document.querySelector(
            `[data-element-id="${CSS.escape(elementId)}"]`);
        if (!el) return null;
        const pages = (this.uiDef && this.uiDef.pages) || [];
        const page = pages.find(p => p && p.id === this.currentPage);
        const flat = (page && Array.isArray(page.elements)) ? page.elements : [];
        const def = flat.find(e => e && e.id === elementId)
            || ((this.uiDef && this.uiDef.master_elements) || [])
                .find(e => e && e.id === elementId);
        return def ? { el, elementDef: def } : null;
    }

    /**
     * The caret ring and the placeholder, injected once and only in edit mode.
     *
     * Deliberately NOT in panel-elements.css: that stylesheet ships to real
     * glass, where none of this exists. The snap overlay makes the same choice.
     */
    _installTextEditStyles() {
        if (this._textEditStylesInstalled) return;
        this._textEditStylesInstalled = true;
        const style = document.createElement('style');
        style.textContent = `
            [data-avc-text-editing] {
                outline: 2px solid #42a5f5;
                outline-offset: 1px;
                cursor: text;
                min-width: 1ch;
                white-space: pre-wrap;
                /* What is being edited is the stored string, so it is shown as
                   stored. A group caption is drawn uppercase by the panel
                   stylesheet; typing "Audio" into a box reading "AUDIO" and
                   watching it stay capitalised reads as the edit being
                   ignored. The transform comes back on commit.

                   !important because the rule it has to beat is a two-class
                   selector (.panel-group .group-label) and this is one
                   attribute. Scoped to an element mid-edit, in edit mode
                   only, so it reaches nothing a panel ever draws. */
                text-transform: none !important;
            }
            [data-avc-text-editing]:empty::before,
            [data-avc-text-editing][data-avc-text-placeholder]:empty::before {
                content: attr(data-avc-text-placeholder);
                opacity: 0.4;
            }
        `;
        document.head.appendChild(style);
    }

    _beginTextEdit(elementId) {
        this._installTextEditStyles();
        const entry = this._editableTarget(elementId);
        if (!entry) return;
        const { el, elementDef } = entry;
        // A type outside the set is not edited here even if something asked.
        if (!PanelApp.IN_PLACE_TEXT_TYPES.has(elementDef.type)) return;
        if (this._textEdit) this._finishTextEdit(true);

        const raw = el.getAttribute('data-avc-text-src');
        if (raw === null) {
            // Nothing authored is drawing here. Either the renderer wrote text
            // it computed (a value readout, a runtime override) or this type
            // draws no editable text at all.
            const override = this.state[`ui.${elementId}.label`];
            this._postToParent({
                type: 'openavc:editor-text-refused',
                elementId,
                reason: override !== undefined && override !== null
                    ? `This text is being set by a script or macro through `
                      + `ui.${elementId}.label, not by the element's Label. Change the `
                      + `Label in Properties, or clear that key to show it again.`
                    : 'These words come from the room, not from the element\'s own '
                      + 'settings, so they cannot be changed here.',
            });
            return;
        }

        let path;
        try {
            path = JSON.parse(raw);
        } catch {
            return;
        }
        if (!Array.isArray(path)) return;

        const host = this._textHostFor(el);
        const source = this._authoredTextAt(elementDef, path);
        const multiline = elementDef.type === 'label';

        this._textEdit = {
            elementId,
            path,
            host,
            el,
            // Everything the host was showing, so a cancel puts it back exactly
            // rather than re-rendering the page around it.
            restore: { html: host.innerHTML, editable: host.contentEditable },
            before: source === undefined ? '' : source,
            multiline,
        };

        // Plain text, and plaintext-only where the browser has it: what is being
        // edited is a source string, so a pasted <b> has no meaning here.
        host.contentEditable = 'plaintext-only';
        if (host.contentEditable !== 'plaintext-only') host.contentEditable = 'true';
        host.textContent = this._textEdit.before;
        host.dataset.avcTextEditing = '1';
        if (!this._textEdit.before) {
            host.dataset.avcTextPlaceholder = this._textPlaceholderFor(elementDef);
        }

        host.focus();
        const range = document.createRange();
        range.selectNodeContents(host);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);

        host.addEventListener('keydown', this._onTextEditKey);
        host.addEventListener('blur', this._onTextEditBlur);

        this._postToParent({
            type: 'openavc:editor-text-open',
            elementId,
            path,
            source: this._textEdit.before,
            multiline,
        });
    }

    /**
     * Put a string in at the caret, replacing any selection.
     *
     * `execCommand` first because it is the only insertion the browser folds
     * into contenteditable's own undo stack, so ctrl+Z inside an edit still
     * steps back through what was typed. The Selection path is the fallback for
     * when that finally goes away.
     */
    _insertTextAtCaret(text) {
        if (document.execCommand && document.execCommand('insertText', false, text)) {
            return;
        }
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0) return;
        const range = sel.getRangeAt(0);
        range.deleteContents();
        const node = document.createTextNode(text);
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
    }

    /**
     * What the author actually typed, read blind to the CSS.
     *
     * NOT `innerText`, which is CSS-aware: a group's caption is styled
     * `text-transform: uppercase`, so `innerText` hands back "AUDIO" for a
     * caption authored as "Audio" -- and committing that would have retyped
     * every group caption in the project in capitals, once, permanently, the
     * first time anybody edited one.
     *
     * `textContent` alone would lose the line breaks a multi-line label is
     * allowed, so a `<br>` and a block the browser wrapped a line in each
     * count as a newline.
     */
    _editedText(host) {
        let out = '';
        for (const node of host.childNodes) {
            if (node.nodeType === Node.TEXT_NODE) {
                out += node.textContent;
            } else if (node.nodeName === 'BR') {
                out += '\n';
            } else {
                if (out !== '' && !out.endsWith('\n')) out += '\n';
                out += node.textContent;
            }
        }
        return out;
    }

    _finishTextEdit(commit) {
        const edit = this._textEdit;
        if (!edit) return;
        this._textEdit = null;

        const { host } = edit;
        host.removeEventListener('keydown', this._onTextEditKey);
        host.removeEventListener('blur', this._onTextEditBlur);
        const typed = this._editedText(host).replace(/\n$/, '');
        host.contentEditable = edit.restore.editable || 'inherit';
        delete host.dataset.avcTextEditing;
        delete host.dataset.avcTextPlaceholder;
        // Put the rendered form back either way. On a commit the Builder writes
        // the project and the repost redraws this element properly; until that
        // lands, what was on screen before is a truer picture than the source
        // string the author was just typing into.
        host.innerHTML = edit.restore.html;

        if (!commit || typed === edit.before) return;
        this._postToParent({
            type: 'openavc:editor-text-commit',
            elementId: edit.elementId,
            path: edit.path,
            value: typed,
        });
    }

    /** `size` is in rem, so an icon scales with the rest of the panel. Leave
     *  it out and the stylesheet sizes the icon instead: an inline size would
     *  beat any rule there. */
    renderIcon(iconName, size, color) {
        if (!iconName) return null;

        // Custom icon from asset system
        if (iconName.startsWith('assets://')) {
            const img = document.createElement('img');
            img.src = assetUrl(iconName);
            if (size != null) {
                img.style.width = `${size}rem`;
                img.style.height = `${size}rem`;
            }
            img.style.flexShrink = '0';
            if (color) img.style.filter = `brightness(0) saturate(100%)`;
            return img;
        }

        // Built-in Lucide icon from sprite sheet
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        const iconUrl = `${basePath()}/panel/icons.svg#${iconName}`;
        use.setAttribute('href', iconUrl);
        use.setAttributeNS('http://www.w3.org/1999/xlink', 'href', iconUrl);
        svg.appendChild(use);
        // Sized in CSS rather than the width/height attributes: SVG attributes
        // are user units and can't carry rem, and CSS wins over them anyway.
        if (size != null) {
            svg.style.width = `${size}rem`;
            svg.style.height = `${size}rem`;
        }
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', color || 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.style.flexShrink = '0';
        return svg;
    }

    renderElementContent(el, element) {
        const icon = element.style?.icon || element.icon;
        if (!icon) return; // No icon, text is already set

        const iconPos = element.style?.icon_position || element.icon_position || 'left';
        const iconSize = element.style?.icon_size || element.icon_size || 24 / REM_BASE_PX;
        const iconColor = element.style?.icon_color || element.icon_color || null;

        // Preserve the image layer (an element child) when rebuilding content.
        const imageLayer = el.querySelector(':scope > .panel-button-image-layer');

        // Capture label text from text nodes only (not from layer or other children)
        let labelText = '';
        Array.from(el.childNodes).forEach((n) => {
            if (n.nodeType === Node.TEXT_NODE) labelText += n.textContent;
            else if (n.nodeType === Node.ELEMENT_NODE && n !== imageLayer && n.tagName === 'SPAN') {
                labelText += n.textContent;
            }
        });

        // Clear existing content, then restore the image layer as first child
        el.textContent = '';
        if (imageLayer) el.prepend(imageLayer);

        const iconEl = this.renderIcon(icon, iconSize, iconColor);
        if (!iconEl) return;

        if (iconPos === 'center') {
            // Icon only, no text
            el.appendChild(iconEl);
            return;
        }

        const textSpan = document.createElement('span');
        textSpan.className = 'panel-label-span';
        textSpan.textContent = labelText;

        if (iconPos === 'top' || iconPos === 'bottom') {
            el.style.flexDirection = 'column';
            el.style.gap = '0.2857rem';
            if (iconPos === 'top') {
                el.appendChild(iconEl);
                el.appendChild(textSpan);
            } else {
                el.appendChild(textSpan);
                el.appendChild(iconEl);
            }
        } else {
            el.style.gap = '0.4286rem';
            if (iconPos === 'left') {
                el.appendChild(iconEl);
                el.appendChild(textSpan);
            } else {
                el.appendChild(textSpan);
                el.appendChild(iconEl);
            }
        }
    }
}

// Start the app
const app = new PanelApp();
// Expose for iframe debugging and programmer integration (read-only intent)
window.__openavcPanel = app;
document.addEventListener('DOMContentLoaded', () => app.start());
