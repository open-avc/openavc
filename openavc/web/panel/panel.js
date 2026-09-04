/**
 * OpenAVC Panel UI — Phase 1
 *
 * Connects to the backend via WebSocket, renders the touch panel UI
 * from JSON definitions, and sends user interactions back to the server.
 */

// --- Programmer auth bridge -------------------------------------------------
// When the panel is embedded as an iframe inside the Programmer IDE (UI Builder
// canvas, Theme Studio preview) and a programmer password is configured, the
// SPA caches the credentials in sessionStorage. We're same-origin with the SPA
// so we can read them, and we must — otherwise our /api fetches and the
// WebSocket handshake return 401, which makes the browser pop its native HTTP
// Basic dialog inside the iframe. See openavc/web/programmer/src/api/auth.ts
// for the parent half.
(function installProgrammerAuthBridge() {
    const STORAGE_KEY = 'openavc.programmer.auth';

    function getStoredAuth() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed.user === 'string' && typeof parsed.pass === 'string') {
                return parsed;
            }
        } catch (_) { /* fall through */ }
        return null;
    }

    function getAuthHeader() {
        const a = getStoredAuth();
        if (!a) return null;
        return 'Basic ' + btoa(`${a.user}:${a.pass}`);
    }

    // Mirrors getAuthSubprotocols() in the Programmer SPA: URI-encode for
    // unicode safety, base64-encode, then URL-safe / strip padding so the value
    // is a valid WebSocket subprotocol token (RFC 6455 restricts these to HTTP
    // token chars). The server decodes it in check_ws_auth().
    function getAuthSubprotocol() {
        const a = getStoredAuth();
        if (!a) return null;
        const b64 = btoa(unescape(encodeURIComponent(a.pass)))
            .replace(/\+/g, '-')
            .replace(/\//g, '_')
            .replace(/=+$/, '');
        return `auth.b64.${b64}`;
    }

    // Exposed so the PanelApp WebSocket constructor can pull the subprotocol.
    window.__openavcGetAuthSubprotocol = getAuthSubprotocol;

    function isApiUrl(url) {
        // /api, /api/..., or /tunnel/<id>/api/...
        return /(^|\/)api(\/|$|\?)/.test(url);
    }

    // Patch fetch unconditionally; the header is only attached when credentials
    // are actually present. If no Programmer SPA is involved (panel opened
    // standalone after an interactive Basic login), the browser's own cache
    // handles auth and this patch is a no-op.
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

// How far a finger may travel on a matrix crosspoint before the gesture stops
// being a tap and becomes a drag-to-route. Deliberately generous: a wall panel
// is touched with a thumb, and a few pixels of travel on the way down is a tap
// by every reasonable reading.
const MATRIX_DRAG_THRESHOLD_PX = 8;

// A crosspoint cell sizes itself to the room the element was given, between
// these two. The floor is the 9mm finger rule the rest of the panel is held
// to; above it a cell grows so a matrix given half a page draws a grid you can
// hit from across the room instead of a postage stamp in the corner. The
// ceiling is where growth stops paying: the dot inside is 16px (20px lit), and
// past ~72px the grid is mostly gap and reads worse, not better.
//
// Below the floor a cell does not shrink -- .matrix-scroll scrolls instead --
// which is what makes the floor a MINIMUM BOX rather than a preference, and is
// why openavc/ui/control_minimums.py can state what a given grid needs.
const MATRIX_CELL_MIN_PX = 44;
const MATRIX_CELL_MAX_PX = 72;

// How much room the destination-name column keeps before it starts ellipsising.
// It has to be DECLARED rather than left to the names, for two reasons that pull
// the same way. A grid's spare room is shared out equally between the tracks that
// can take it, so once the cells could grow, the one label column was getting a
// ninth of it and "Main LCD" was drawing as "M" -- the names lost to the dots
// they label. And a column sized to its content is a column whose width is
// whatever somebody typed, which openavc/ui/control_minimums.py could not state a
// floor for. 80px is the same number .matrix-list-label already declares, so the
// two styles reserve the same room for the same names.
const MATRIX_LABEL_MIN_PX = 80;

// A tile in the `tiles` style: one card per destination, naming what is routed
// to it in large type. These are its floor -- it grows into whatever room the
// element has (the tracks are minmax(floor, 1fr)) and never shrinks past them,
// for the same reason a crosspoint does not.
const MATRIX_TILE_MIN_W_PX = 120;
const MATRIX_TILE_MIN_H_PX = 64;

/**
 * How many columns and rows a wall of `count` tiles is drawn in.
 *
 * From the square root, so the wall matches the shape of the screen it is on:
 * wider than it is tall in landscape, taller than it is wide in portrait. Eight
 * tiles are four across and two down on a landscape panel and two across and
 * four down on a portrait one; sixteen are four and four either way.
 *
 * The rule reads as "wide" but has never been about width. It is about a
 * destination's name being legible across a room, and matching the box is what
 * achieves that: on an 800px-wide portrait panel, four across is 200px a tile
 * where two across is 400px.
 *
 * The shape is a function of the COUNT and the ORIENTATION, and not of the box,
 * which is the part worth knowing. A wall that reflowed to whatever width it
 * was given would have no single smallest box, so openavc/ui/control_minimums.py
 * could not state a floor for it -- and the one-column corner of that curve,
 * which is the only rectangle you could publish, tells an author their eight
 * tiles need 576px of height when they draw perfectly in 179. Orientation is
 * knowable without measuring the box, which is what keeps the floor from
 * depending on the box it is judging. Mirrored there as tile_grid_shape and in
 * uiBuilderHelpers.ts; all three agree or the floor is for a shape nothing
 * renders.
 */
function matrixTileGridShape(count, orientation) {
    if (count <= 0) return [0, 0];
    const root = Math.max(1, Math.floor(Math.sqrt(count)));
    return orientation === 'portrait'
        ? [root, Math.ceil(count / root)]
        : [Math.ceil(count / root), root];
}

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
    'orphan_reason', 'orphaned', 'paused', 'reconnect_attempt', 'reconnect_failed',
]);

class PanelApp {
    constructor() {
        const params = new URLSearchParams(window.location.search);
        // Edit mode — iframe embedded in the UI Builder design canvas. No WS, no
        // binding sends, no idle/lock, no transitions. Definition arrives via
        // postMessage from the parent programmer window.
        this.editMode = params.get('edit') === '1';
        // Embedded — iframe hosted inside another window (the builder). The
        // parent is authoritative for the project definition; WS is used only
        // for live device state. Standalone tabs stay on the WS path for both.
        this.embedded = (window.parent && window.parent !== window);
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
        this.currentPage = params.get('page') || 'main';
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
        this.holdTimers = {};        // element_id -> interval for hold-repeat mode
        // A panel that goes to the background mid-press never sees the
        // release — end every hold-repeat rather than let it fire blind.
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                for (const t of Object.values(this.holdTimers)) clearInterval(t);
                this.holdTimers = {};
            }
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
        this._runningMacros = {};    // macro_id -> { description, step_index, total_steps }
        this._startedMacros = {};    // macro_id -> { at, reported, running } for macros THIS panel started
        this._macroRuns = {};        // macro_id -> how many runs of it are in flight (see _endMacroRun)
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000; // matches the backoff cap used in onclose
        this.reconnectAttempts = 0;
        this._offline = false;           // true while the WS is disconnected
        this._lastTouchedElementId = null; // control the next failure is about
        this._errorMessageTimer = null;    // auto-dismiss for the failure band
        this._lockInitialized = false;   // lock screen shown once per session, not on every reconnect
        this._meetingStartTimes = {};    // element_id -> meeting start Date (survives re-render)
        this.themeElementDefaults = {};
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
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        try {
            const res = await fetch(`${basePath}/api/plugins/extensions`);
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
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        try {
            const res = await fetch(
                `${basePath}/api/plugins/${encodeURIComponent(pluginId)}/ext-token`
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
        // Load plugin extensions before the first render so per-element
        // iframe sandbox / allow attributes can be applied correctly.
        // Best-effort: if the fetch fails or hangs, we proceed with defaults.
        await this._loadPluginExtensions();
        this.setupIdleListeners();
        this._setupAudioUnlock();
        this.connect();
    }

    _fetchProjectAndRender() {
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        // The RESOLVED ui, not /api/project. A matrix's sources and destinations
        // are expanded on the server (matrix plan D6) and this renderer has no
        // expander, so the authoring copy would draw an empty matrix here.
        fetch(`${basePath}/api/ui/resolved`)
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
        if (window.parent && window.parent !== window) {
            try { window.parent.postMessage(msg, '*'); } catch (_) { /* ignore */ }
        }
    }

    _setupEditModeListener() {
        window.addEventListener('message', (event) => {
            // Only accept messages from the parent programmer window
            if (event.source !== window.parent) return;
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
                    }
                    this._postToParent({ type: 'openavc:editor-ready' });
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
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Derive WS path relative to page location so tunneled access works.
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        const url = `${protocol}//${location.host}${basePath}/ws?client=panel&namespaces=device,var,ui,system,plugin`;

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

        this.ws.onclose = () => {
            this.setConnectionStatus(false);
            // Clear all active hold-repeat timers — pointer-events: none
            // blocks release events, so timers would run indefinitely
            for (const t of Object.values(this.holdTimers)) clearInterval(t);
            this.holdTimers = {};
            this.reconnectAttempts++;
            const retryEl = document.getElementById('reconnect-info');
            if (retryEl) {
                retryEl.textContent = `Reconnecting (attempt ${this.reconnectAttempts})...`;
            }
            // Always retry with exponential backoff (capped at maxReconnectDelay)
            setTimeout(() => this.connect(), this.reconnectDelay);
            this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, this.maxReconnectDelay);
        };

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

        if (connected) {
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
                // Seed audio dedupe id from current state so we don't replay
                // the most recent sound when (re)connecting.
                this._seedAudioDedupeFromSnapshot();
                break;

            case 'state.update':
                Object.assign(this.state, msg.changes || {});
                this._scheduleBindingEvaluation(Object.keys(msg.changes || {}));
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
                // the last-saved project.
                if (this.embedded) break;
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

            case 'macro.progress':
                if (this._runningMacros[msg.macro_id]) {
                    this._runningMacros[msg.macro_id].description = msg.description || '';
                    this._runningMacros[msg.macro_id].step_index = msg.step_index || 0;
                    this._runningMacros[msg.macro_id].total_steps = msg.total_steps || 0;
                }
                this._updateMacroProgressBindings(msg.macro_id);
                break;

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
     * in this loop is the one that would drift. `force` says the refusal is of
     * the operator's OWN command, which lifts exactly one of those guards --
     * see evaluateSliderValue. Nothing is lost by waiting: the release sends
     * again, so a refusal of that send is what puts the control back.
     */
    _revertRefusedInteraction(elementId) {
        const id = elementId || this._lastTouchedElementId;
        if (!id) return;
        for (const b of this.bindings) {
            if (b.elementDef?.id !== id) continue;
            for (const k of Object.keys(b)) {
                if (k.startsWith('_last')) delete b[k];
            }
            try { this._evaluateBinding(b, true); }
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
            // Clean up hold timers for overlay elements
            overlayEl.querySelectorAll('[data-element-id]').forEach(el => {
                const eid = el.dataset.elementId;
                if (eid && this.holdTimers[eid]) {
                    clearInterval(this.holdTimers[eid]);
                    delete this.holdTimers[eid];
                }
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
        // follows it (matrixTileGridShape), and it is the LAYOUT's orientation
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

        const pages = this.uiDef.pages || [];
        let page = pages.find(p => p.id === this.currentPage);
        if (!page) {
            if (pages.length > 0) {
                this.currentPage = pages[0].id;
                page = pages[0];
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

        // Clean up timers from previous render
        for (const t of Object.values(this.holdTimers)) clearInterval(t);
        this.holdTimers = {};
        for (const t of this.debounceTimers) clearTimeout(t);
        this.debounceTimers = [];
        // Clean up orphaned fader drag listeners
        for (const el of this.root.querySelectorAll('.panel-fader .fader-track-wrap')) {
            if (el._faderDragCleanup) el._faderDragCleanup();
        }
        // Clean up orphaned matrix drag listeners
        for (const el of this.root.querySelectorAll('.panel-matrix')) {
            if (el._matrixDragCleanup) el._matrixDragCleanup();
        }
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

        // Clean up orphaned matrix drag lines
        document.querySelectorAll('.matrix-drag-line').forEach(el => el.remove());
        // And a source chooser left open by the tile that opened it. Both live
        // on document.body rather than inside the element, so a re-render does
        // not take them with it.
        this._closeMatrixChooser();

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

        this.root.appendChild(surface);
        this._applyCoordinateSpaces(surface);
        this.evaluateAllBindings();

        // Theme Studio direct manipulation — click any element in the preview
        // to jump to its section in the editor. Hover shows an outline + type label.
        if (this.editMode) {
            this._setupThemeStudioInteraction(surface);
        }
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

    renderButton(element) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'panel-element panel-button';
        el.textContent = element.label || '';
        el.dataset.elementId = element.id;
        el.setAttribute('aria-label', element.label || element.id);

        // Apply static styles (theme defaults merged)
        const themedStyle = this.getThemedStyle('button', element.style);
        this.applyStyle(el, themedStyle);

        // Frameless: hide chrome so the image acts as the button
        if (element.frameless) this.applyFrameless(el);

        const displayMode = element.display_mode || 'text';
        const showImage = (displayMode === 'image' || displayMode === 'image_text') && element.button_image;

        // Clear label text for image-only/icon-only modes BEFORE content/layer rendering
        if (displayMode === 'image' || displayMode === 'icon_only') {
            el.textContent = '';
        }
        if (displayMode === 'icon_only' && !element.icon_position) {
            element.icon_position = 'center';
        }

        // Render icon+text content first (may call el.textContent = '' internally to rebuild).
        // Image layer must be prepended AFTER this so content rendering can't wipe it.
        this.renderElementContent(el, element);

        // Image effect last so its DOM layer isn't removed by other content rendering paths
        if (showImage) {
            this.applyImageEffect(el, element.button_image, {
                fit: element.image_fit,
                blend: element.image_blend_mode,
                opacity: element.image_opacity,
                tintColor: themedStyle.bg_color,
            });
            if (displayMode === 'image_text') {
                el.style.textShadow = '0 1px 0.2143rem rgba(0,0,0,0.8)';
            }
        }

        // Register in element map for ui.* overrides
        this.elementMap[element.id] = { el, elementDef: element };

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

        const endHold = () => {
            if (this.holdTimers[element.id]) {
                clearInterval(this.holdTimers[element.id]);
                delete this.holdTimers[element.id];
            }
        };

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
                endHold();
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
                endHold();
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
            endHold();
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

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
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
        this.applyStyle(el, this.getThemedStyle('status_led', element.style));

        const dot = document.createElement('div');
        dot.className = 'led-dot';
        el.appendChild(dot);

        if (element.label) {
            const label = document.createElement('label');
            label.className = 'led-label';
            label.textContent = element.label;
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
        const el = document.createElement('div');
        const isVertical = element.orientation === 'vertical';
        el.className = 'panel-element panel-slider' + (isVertical ? ' panel-slider-vertical' : '');
        el.dataset.elementId = element.id;

        const themedSliderStyle = this.getThemedStyle('slider', element.style);
        // Apply theme element_defaults to the wrapper so slider bg / border /
        // radius / shadow pick up the theme. Without this, sliders silently
        // ignored their theme styling.
        this.applyStyle(el, themedSliderStyle);

        // Thumb size: per-element value wins, otherwise theme element_default, otherwise 44.
        el.style.setProperty(
            '--thumb-size',
            (element.thumb_size ?? themedSliderStyle.thumb_size ?? 44 / REM_BASE_PX) + 'rem',
        );

        if (element.label) {
            const label = document.createElement('label');
            label.textContent = element.label;
            el.appendChild(label);
        }

        // Track wrapper (contains track background, fill, and the range input)
        const wrapper = document.createElement('div');
        wrapper.className = 'slider-track-wrapper';

        const track = document.createElement('div');
        track.className = 'slider-track';

        const fill = document.createElement('div');
        fill.className = 'slider-fill';
        track.appendChild(fill);
        wrapper.appendChild(track);

        const input = document.createElement('input');
        input.type = 'range';
        const sliderMin = element.min ?? 0;
        const sliderMax = element.max ?? 100;
        const sliderStep = element.step ?? 1;
        const sSpan = sliderMax - sliderMin;
        const sResponse = element.response || 'linear';
        const sResponseDbRange = element.response_db_range != null ? Number(element.response_db_range) : 60;
        const sUnit = element.unit || '';
        const sDisplayDecimals = element.display_decimals != null ? Number(element.display_decimals) : null;
        const sSendOnRelease = element.send_on_release === true;
        const sThrottle = element.send_throttle_ms != null ? Number(element.send_throttle_ms) : 100;
        input.setAttribute('aria-label', element.label || element.id);
        const sOutputMin = element.output_min;
        const sOutputMax = element.output_max;
        const sHasOutputRange = sOutputMin != null && sOutputMax != null;
        const sScaleToFull = element.scale_to_full !== false;

        // The native range input runs in a normalized POSITION domain (0..STEPS)
        // rather than the display-value domain, because a native thumb is always
        // linear in its own value — representing travel directly is the only way
        // to taper the feel. For a linear response STEPS equals the value-step
        // count, so position maps 1:1 to a value step and behaviour is unchanged.
        const rawSteps = sliderStep > 0 ? Math.round(sSpan / sliderStep) : 0;
        const STEPS = sResponse === 'logarithmic' ? Math.max(rawSteps, 200) : Math.max(rawSteps, 1);
        input.min = 0;
        input.max = STEPS;
        input.step = 1;

        // position (0..STEPS) -> display value (curved, snapped to step, clamped)
        const posToValue = (pos) => {
            const travel = STEPS > 0 ? pos / STEPS : 0;
            let v = sliderMin + this._responseCurve(travel, sResponse, sResponseDbRange) * sSpan;
            if (sliderStep > 0) v = this._snapToStep(v, sliderStep);
            v = Math.max(sliderMin, Math.min(sliderMax, v));
            if (sHasOutputRange && !sScaleToFull) v = Math.max(sOutputMin, Math.min(sOutputMax, v));
            return v;
        };
        // display value -> position (0..STEPS)
        const valueToPos = (v) => {
            const vf = sSpan !== 0 ? (v - sliderMin) / sSpan : 0;
            const travel = this._responseCurveInverse(vf, sResponse, sResponseDbRange);
            return Math.max(0, Math.min(STEPS, Math.round(travel * STEPS)));
        };
        const fmtValue = (v) => {
            const n = Number(v);
            const dec = sDisplayDecimals != null ? sDisplayDecimals : (sliderStep < 1 ? 1 : 0);
            const s = dec > 0 ? n.toFixed(dec) : String(Math.round(n));
            return sUnit ? `${s} ${sUnit}` : s;
        };

        // Set initial position from state if binding exists, else from min
        const sliderBinding = element.bindings?.show?.value;
        const initialRaw = sliderBinding?.key ? this.state[sliderBinding.key] : undefined;
        if (initialRaw !== undefined && initialRaw !== null) {
            const dv = this._reverseScale(Number(initialRaw), sliderMin, sliderMax, sOutputMin, sOutputMax, sScaleToFull);
            input.value = valueToPos(dv);
        } else {
            input.value = valueToPos(sliderMin);
        }

        // Update fill from current travel position
        const updateFill = () => {
            const pct = STEPS > 0 ? (parseFloat(input.value) / STEPS) * 100 : 0;
            if (isVertical) {
                fill.style.height = pct + '%';
            } else {
                fill.style.width = pct + '%';
            }
        };
        updateFill();

        // Value display element
        let valueDisplay = null;
        const showValue = element.style?.show_value === true;
        {
            const v0 = posToValue(parseFloat(input.value));
            input.setAttribute('aria-valuetext', fmtValue(v0));
            if (showValue) {
                valueDisplay = document.createElement('div');
                valueDisplay.className = 'slider-value';
                valueDisplay.textContent = fmtValue(v0);
            }
        }

        // Send handler: debounced while dragging live, immediate on release.
        let changeTimeout = null;
        const sendValue = (v, immediate) => {
            if (changeTimeout) { clearTimeout(changeTimeout); changeTimeout = null; }
            if (immediate) {
                this.send({ type: 'ui.change', element_id: element.id, value: v });
                return;
            }
            changeTimeout = setTimeout(() => {
                this.send({ type: 'ui.change', element_id: element.id, value: v });
            }, sThrottle);
            this.debounceTimers.push(changeTimeout);
        };
        input.addEventListener('input', () => {
            // Dead-space mode: clamp travel so the thumb can't enter the region
            // past the device's output limit (mirrors the value clamp above).
            if (sHasOutputRange && !sScaleToFull) {
                const loPos = valueToPos(sOutputMin);
                const hiPos = valueToPos(sOutputMax);
                const p = parseFloat(input.value);
                input.value = Math.max(Math.min(loPos, hiPos), Math.min(Math.max(loPos, hiPos), p));
            }
            updateFill();
            const v = posToValue(parseFloat(input.value));
            input.setAttribute('aria-valuetext', fmtValue(v));
            if (valueDisplay) valueDisplay.textContent = fmtValue(v);
            // Live mode streams while dragging; send-on-release waits for 'change'.
            if (!sSendOnRelease) sendValue(v, false);
        });
        // 'change' fires when the value is committed (mouse release, keyboard).
        // Always send the final value here so send-on-release delivers exactly
        // one command, and live mode is guaranteed to land on the end value.
        input.addEventListener('change', () => {
            const v = posToValue(parseFloat(input.value));
            if (valueDisplay) valueDisplay.textContent = fmtValue(v);
            sendValue(v, true);
        });

        // Track active dragging so inbound state echoes don't fight the operator
        // (see evaluateSliderValue). Range inputs aren't reliably focused during
        // touch drags, so a pointer/touch flag is needed alongside activeElement.
        input.addEventListener('pointerdown', () => { input._dragging = true; });
        const sliderEndDrag = () => { input._dragging = false; };
        input.addEventListener('pointerup', sliderEndDrag);
        input.addEventListener('pointercancel', sliderEndDrag);
        input.addEventListener('blur', sliderEndDrag);
        input.addEventListener('touchend', sliderEndDrag);
        input.addEventListener('touchcancel', sliderEndDrag);

        wrapper.appendChild(input);
        el.appendChild(wrapper);
        if (valueDisplay) el.appendChild(valueDisplay);

        // Value binding (read; two-way when show.value.write_back)
        const valueBinding = element.bindings?.show?.value;
        if (valueBinding) {
            this.bindings.push({
                type: 'slider_value',
                element: input,
                elementDef: element,
                binding: valueBinding,
                fill,
                valueDisplay,
                isVertical,
                outputMin: sOutputMin,
                outputMax: sOutputMax,
                scaleToFull: sScaleToFull,
                steps: STEPS,
                unit: sUnit,
                valueToPos,
                fmtValue,
            });
        }

        return el;
    }

    renderPageNav(element) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'panel-element panel-page-nav';
        el.dataset.elementId = element.id;

        if (element.target_page) {
            el.textContent = element.label || element.target_page;
            el.setAttribute('aria-label', `Navigate to ${element.label || element.target_page}`);
            el.addEventListener('click', () => {
                this.navigateToPage(element.target_page);
                this.send({ type: 'ui.page', page_id: element.target_page });
            });
        } else {
            el.textContent = element.label || 'No Target';
            el.disabled = true;
            el.style.opacity = '0.5';
        }

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.renderElementContent(el, element);
        return el;
    }

    renderSelect(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-select';
        el.dataset.elementId = element.id;

        if (element.label) {
            const label = document.createElement('label');
            label.textContent = element.label;
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
        this.applyStyle(el, this.getThemedStyle(element.type, element.style));

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
        this.applyStyle(el, this.getThemedStyle(element.type, element.style));

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

        if (element.src) {
            const img = document.createElement('img');
            img.src = this.resolveAssetUrl(element.src);
            img.alt = element.label || 'Panel image';
            img.loading = 'lazy';
            if (element.object_fit) img.style.objectFit = element.object_fit;
            img.onerror = () => {
                img.style.display = 'none';
                const placeholder = document.createElement('div');
                placeholder.textContent = 'Image not found';
                placeholder.title = element.src;
                placeholder.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--panel-text);opacity:0.5;font-size:0.8571rem;';
                el.appendChild(placeholder);
            };
            el.appendChild(img);
        }

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        return el;
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

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
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

        this.applyStyle(el, style);

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'list-label';
            label.textContent = element.label;
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

        this.elementMap[element.id] = { el, elementDef: element };

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
            // Collect items from state matching pattern (glob with *)
            const regex = new RegExp('^' + keyPattern.replace(/\./g, '\\.').replace(/\*/g, '(.+)') + '$');
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
     * A routed-source value in comparable form, or null when nothing is routed.
     *
     * Everything about which crosspoint lights, and whether audio agrees with
     * video, used to go through parseInt(). That is wrong in both directions:
     * parseInt('IN1') is NaN and NaN never equals itself, so a switcher that
     * labels its ports lit EVERY audio-mismatch badge while audio and video
     * were byte-for-byte identical, and lit no crosspoint at all.
     *
     * 0 counts as unrouted rather than as port zero. Every routing driver in
     * the corpus numbers its ports from 1, and AV gear conventionally reports
     * 0 for an idle port -- so "audio is on nothing" would otherwise read as
     * "audio is on something else", which is the badge's whole meaning.
     */
    _routeValue(v) {
        if (v === null || v === undefined) return null;
        if (typeof v === 'number') return Number.isFinite(v) && v !== 0 ? v : null;
        const s = String(v).trim();
        if (s === '' || s === '0') return null;
        if (/^[+-]?\d+(?:\.\d+)?$/.test(s)) return Number(s);
        return s.toLowerCase();
    }

    /** The single digit run in a string, or null when it does not have exactly one. */
    _routeDigits(s) {
        if (typeof s !== 'string') return null;
        const runs = s.match(/\d+/g);
        return runs && runs.length === 1 ? Number(runs[0]) : null;
    }

    /**
     * Do two routed-source values name the same source?
     *
     * Equality first, so identical values can never read as a mismatch. Then a
     * single embedded number, so a device reporting 'IN2' or 'HDMI 3' can still
     * light its crosspoint -- but only when there is exactly one digit run, so
     * nothing is guessed from a source NAME ('Laptop') or a value that happens
     * to carry two numbers ('1080p60').
     */
    _routeMatches(a, b) {
        const x = this._routeValue(a), y = this._routeValue(b);
        if (x === null || y === null) return false;
        if (x === y) return true;
        const dx = typeof x === 'number' ? x : this._routeDigits(x);
        const dy = typeof y === 'number' ? y : this._routeDigits(y);
        return dx !== null && dx === dy;
    }

    /**
     * What a source looks like when the DEVICE names it.
     *
     * Usually the same thing it is routed by, which is why a source carries one
     * value. Two vocabularies is a real shape though: at_atdm_0604a is routed by
     * sending "0" and reports back "Mic". One value cannot be both -- and worse,
     * "0" is exactly what _routeValue reads as "nothing is routed", so that
     * source could never light. `value` is what gets SENT, `report_value` is
     * what gets MATCHED, and omitting it makes them the same.
     */
    _sourceReports(src) {
        if (!src) return undefined;
        return src.report_value === undefined || src.report_value === null
            ? src.value : src.report_value;
    }

    /** Take down the tile wall's source chooser, wherever it was opened from. */
    _closeMatrixChooser() {
        document.querySelectorAll('.matrix-chooser').forEach(node => node.remove());
    }

    /** Whether a lock variable's value means "locked". */
    _lockEngaged(v) {
        if (v === true) return true;
        if (v === false || v === null || v === undefined) return false;
        const s = String(v).trim().toLowerCase();
        return s === 'true' || s === '1' || s === 'on' || s === 'locked' || s === 'yes';
    }

    /** The source a device's report names, or null when it names none of them. */
    _routedSource(sources, routed) {
        if (this._routeValue(routed) === null) return null;
        return sources.find(s => this._routeMatches(this._sourceReports(s), routed)) || null;
    }

    /**
     * What one destination is showing right now, as three cases rather than two.
     *
     * A destination routed to something the matrix does not list used to look
     * exactly like a destination routed to nothing: no crosspoint lit, no row
     * saying anything. They are different facts about the room -- the first
     * usually means a port was left out of the list or patched at the rack since
     * -- and the one thing a panel must never do is report the wrong one
     * confidently. Subsets are the normal way to author a matrix now (project
     * format 0.10.0), so "the device is on a port you left out" stopped being an
     * edge case the day that landed.
     */
    _routeState(sources, routed) {
        const value = this._routeValue(routed);
        if (value === null) return { state: 'none' };
        const src = this._routedSource(sources, routed);
        return src ? { state: 'listed', source: src }
                   : { state: 'unlisted', raw: String(routed) };
    }

    renderMatrix(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-matrix';
        el.dataset.elementId = element.id;

        const config = element.matrix_config || {};
        // TWO LISTS, already expanded. A matrix used to be two counts and four
        // glob patterns, which could only describe a rectangular frame with
        // contiguous ports numbered from one, on one device, reporting plain
        // integers. Each entry now carries its own keys and an opaque value, so
        // a six-row subset of an 8x8, a decoder's six routing planes and a
        // source that is an rtsp:// URL are all just entries.
        //
        // The expansion happens on the SERVER (matrix plan D6) -- openavc/ui/
        // matrix_model.py -- so this renderer and the Builder canvas, which is
        // an iframe of this renderer, cannot drift about what a config means.
        // An element that arrives unexpanded draws an empty box, which somebody
        // can see, rather than the phantom 4x4 the old default invented.
        const sources = Array.isArray(config.sources) ? config.sources : [];
        const destinations = Array.isArray(config.destinations) ? config.destinations : [];
        // A caption for a row nobody has named, in the same words the resolver
        // uses for a row with no live key at all -- so a matrix reads the same
        // either way. It lives here rather than in the stored entry because a
        // STORED name outranks the device's own (see _entryLabel), and an
        // invented one would then beat the endpoint's real name the moment
        // somebody typed it into the rack. Mutated in place: replacing the
        // entries would break the identity comparisons the routing does.
        sources.forEach((entry, i) => {
            if (entry && !entry.label) entry._caption = `In ${i + 1}`;
        });
        destinations.forEach((entry, i) => {
            if (entry && !entry.label) entry._caption = `Out ${i + 1}`;
        });
        const inputCount = sources.length;
        const outputCount = destinations.length;
        const matrixStyle = element.matrix_style || 'crosspoint';
        // Opt-in, where it used to be on unless turned off. It cost every matrix
        // ever authored a whole column for a button that sent nothing, reached
        // no other panel, and was forgotten the moment the page redrew (F10).
        const showLock = config.show_lock === true;
        // Mute buttons only render when there is a mute_route binding wired up —
        // otherwise clicking them sends a route command the engine has no action
        // for. The Programmer surfaces a warning next to "Show Mute" when this
        // gate is keeping the buttons hidden.
        const showMute = config.show_mute !== false && !!element.bindings?.do?.mute_route;
        // Merge theme element_defaults so crosspoint colors come from the
        // theme, not just per-element overrides.
        const style = this.getThemedStyle('matrix', element.style);
        const activeColor = style.crosspoint_active_color || '#4CAF50';
        const inactiveColor = style.crosspoint_inactive_color || '#333333';
        // An authored cell_size still pins the cell at exactly that size, which
        // is what it has always meant. With nothing authored the cell fits
        // itself to the box instead of sitting at a hardcoded 44px forever, so
        // a matrix given half a page draws a grid worth touching.
        const authoredCellPx = style.cell_size ? style.cell_size * REM_BASE_PX : null;
        const cellMinPx = authoredCellPx ?? MATRIX_CELL_MIN_PX;
        const cellMaxPx = authoredCellPx ?? MATRIX_CELL_MAX_PX;

        this.applyStyle(el, style);

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'matrix-label';
            label.textContent = element.label;
            el.appendChild(label);
        }

        const scrollWrap = document.createElement('div');
        scrollWrap.className = 'matrix-scroll';

        // The crosspoint grid's source names, read out under it. Built here so
        // it can be appended after the grid; null in list style, where every
        // row already carries its source name in a dropdown.
        let legend = null;

        // Matrix state tracking
        const mutedOutputs = new Set();

        // A lock only this panel remembers is not a lock. It is forgotten when
        // the page redraws, the panel by the other door never hears about it,
        // and the rack does not know either -- which is all of F10. So a
        // destination's lock lives in a VARIABLE and every panel reading that
        // key agrees about it.
        //
        // It has to be a variable specifically: `var.` and `plugin.` are the
        // only prefixes an unauthenticated panel may write (state_store's
        // PANEL_WRITABLE_PREFIXES), and a panel is the thing doing the locking.
        // A destination with no lock_key keeps the old panel-local behaviour
        // rather than losing the button, and the page review says so.
        const localLocks = new Set();
        const isLocked = (o) => {
            const key = destinations[o] && destinations[o].lock_key;
            return key ? this._lockEngaged(this.state[key]) : localLocks.has(o);
        };
        const toggleLock = (o) => {
            const key = destinations[o] && destinations[o].lock_key;
            if (!key) {
                if (localLocks.has(o)) localLocks.delete(o); else localLocks.add(o);
                this._applyMatrixLocks(el, destinations);
                return;
            }
            // No optimistic flip: the state change is what every panel sees, so
            // the one that pressed it should show the same thing the others do.
            // The frame names a key, not an element, so this is the only place
            // that knows which control the refusal of it would be about -- same
            // rule as the custom-control bridge.
            this._lastTouchedElementId = element.id;
            this.send({ type: 'state.set', key, value: !this._lockEngaged(this.state[key]) });
        };

        // The one place that turns "the user chose this input for that output"
        // into messages. The tap, the drag and the list dropdown all come here,
        // so they cannot drift apart on whether audio follows -- which they had,
        // three near-copies of the same audio_follow_video block.
        // input/output are the SOURCE's and DESTINATION's own values now, not row
        // and column numbers -- whatever the device reports and accepts. The
        // server finds the destination by that value to see whether it overrides
        // the element's route action.
        const sendRoute = (input, output) => {
            this.send({ type: 'ui.route', element_id: element.id, input, output });
            if (config.audio_follow_video && element.bindings?.do?.audio_route) {
                this.send({
                    type: 'ui.route', element_id: element.id, input, output, audio: true,
                });
            }
        };

        // The same bargain for mute, which the three styles were about to spell
        // three times each -- which is exactly how the route came to be sent
        // twice (F1) before Phase 1 collapsed it into sendRoute.
        const sendMute = (o, dest) => {
            const wasMuted = mutedOutputs.has(o);
            if (wasMuted) mutedOutputs.delete(o); else mutedOutputs.add(o);
            this.send({
                type: 'ui.route', element_id: element.id,
                output: dest.value, mute: !wasMuted,
            });
            if (config.audio_follow_video && element.bindings?.do?.audio_mute_route) {
                this.send({
                    type: 'ui.route', element_id: element.id,
                    output: dest.value, mute: !wasMuted, audio: true,
                });
            }
        };

        // The source chooser a tile opens.
        //
        // Over the whole panel rather than inside the element, because the
        // element is a wall of destinations and may be small: a chooser confined
        // to it would be a list of sources in a 120px card. Fixed positioning
        // also means it works the same in the Builder canvas, which is an iframe
        // of this renderer.
        const openSourceChooser = (o) => {
            this._closeMatrixChooser();
            const dest = destinations[o];
            const routed = this._routeState(
                sources, dest.route_key ? this.state[dest.route_key] : undefined);

            const overlay = document.createElement('div');
            overlay.className = 'matrix-chooser';
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this._closeMatrixChooser();
            });

            const sheet = document.createElement('div');
            sheet.className = 'matrix-chooser-sheet';

            const title = document.createElement('div');
            title.className = 'matrix-chooser-title';
            title.textContent = this._entryLabel(dest);
            sheet.appendChild(title);

            const grid = document.createElement('div');
            grid.className = 'matrix-chooser-grid';
            for (let i = 0; i < inputCount; i++) {
                const src = sources[i];
                const btn = document.createElement('button');
                btn.className = 'matrix-chooser-source';
                btn.dataset.sourceIdx = String(i);
                btn.textContent = this._entryLabel(src);
                if (routed.state === 'listed' && routed.source === src) {
                    btn.classList.add('active');
                    btn.style.borderColor = activeColor;
                    btn.style.color = activeColor;
                }
                btn.addEventListener('click', () => {
                    sendRoute(src.value, dest.value);
                    this._closeMatrixChooser();
                });
                grid.appendChild(btn);
            }
            sheet.appendChild(grid);

            const cancel = document.createElement('button');
            cancel.className = 'matrix-chooser-cancel';
            cancel.textContent = 'Cancel';
            cancel.addEventListener('click', () => this._closeMatrixChooser());
            sheet.appendChild(cancel);

            overlay.appendChild(sheet);
            document.body.appendChild(overlay);
        };

        // Presets bar (if presets defined in matrix config)
        const presets = element.matrix_config?.presets || [];
        if (presets.length > 0) {
            const presetBar = document.createElement('div');
            presetBar.className = 'matrix-presets';
            for (const preset of presets) {
                const btn = document.createElement('button');
                btn.className = 'matrix-preset-btn';
                btn.textContent = preset.name || 'Preset';
                btn.addEventListener('click', () => {
                    // Presets trigger a macro
                    if (preset.macro) {
                        // A macro frame names no element, so the failure band
                        // would place itself against whatever was touched
                        // before this -- and cover the preset bar somebody
                        // still has a finger on. Same rule as the lock above.
                        this._lastTouchedElementId = element.id;
                        this.send({ type: 'macro.execute', macro_id: preset.macro });
                    }
                });
                presetBar.appendChild(btn);
            }
            el.appendChild(presetBar);
        }

        if (matrixStyle === 'list') {
            // --- List view ---
            const list = document.createElement('div');
            list.className = 'matrix-list';

            for (let o = 0; o < outputCount; o++) {
                const dest = destinations[o];
                const row = document.createElement('div');
                row.className = 'matrix-list-row';

                const outLabel = document.createElement('span');
                outLabel.className = 'matrix-list-label';
                outLabel.textContent = dest.label || `Out ${o + 1}`;
                outLabel.dataset.outputIdx = String(o);
                row.appendChild(outLabel);
                // What the AUDIO is on, by name, whenever it differs from the
                // video. This was an 'A≠V' badge whose meaning lived in a title
                // attribute -- a tooltip, on a control whose entire audience is
                // standing at a touch screen with no pointer to hover. Stating
                // what is routed is useful; announcing that two things disagree
                // without saying what either of them is, somewhere nobody can
                // read it, is not.
                if (dest.audio_route_key) {
                    const audio = document.createElement('span');
                    audio.className = 'matrix-audio-source';
                    audio.dataset.audioIdx = String(o);
                    audio.hidden = true;
                    row.appendChild(audio);
                }

                const select = document.createElement('select');
                select.className = 'matrix-list-select';
                // A select shows its first option when nothing has selected one,
                // so a destination routed to NOTHING read as routed to whatever
                // was listed first -- four rows claiming "Apple TV" in one
                // screenshot, none of them routed. This is what it shows
                // instead, and it is disabled so it can never be chosen and sent.
                const idle = document.createElement('option');
                idle.className = 'matrix-list-idle';
                idle.value = '';
                idle.textContent = '—';
                idle.disabled = true;
                select.appendChild(idle);
                for (let i = 0; i < inputCount; i++) {
                    const opt = document.createElement('option');
                    // The option's value is the source's own value as text,
                    // because a DOM option value is always a string. The typed
                    // value is read back off the source list on change, so a
                    // device expecting the number 3 is not sent "3".
                    opt.value = String(sources[i].value);
                    opt.textContent = this._entryLabel(sources[i]);
                    opt.dataset.sourceIdx = String(i);
                    select.appendChild(opt);
                }
                // The other half of the same problem: a device reporting a
                // source this matrix does not list has no option to select, so
                // the row fell back to the first one and named a source that was
                // not routed. This one carries whatever the device actually
                // said, and is only shown when that happens.
                const unlisted = document.createElement('option');
                unlisted.className = 'matrix-list-unlisted';
                unlisted.value = '';
                unlisted.disabled = true;
                unlisted.hidden = true;
                select.appendChild(unlisted);
                // A browser picks the first ENABLED option on its own, so the
                // placeholder has to be selected explicitly or the row goes
                // straight back to claiming the first source.
                select.selectedIndex = 0;
                select.dataset.outputIdx = String(o);

                select.addEventListener('change', () => {
                    if (isLocked(o)) return;
                    // By the option's own index into the source list, never by
                    // the select's: two of the options are not sources.
                    const opt = select.selectedOptions[0];
                    const src = opt && opt.dataset.sourceIdx !== undefined
                        ? sources[parseInt(opt.dataset.sourceIdx)] : null;
                    if (src) sendRoute(src.value, dest.value);
                });

                if (showLock) {
                    const lockBtn = document.createElement('button');
                    lockBtn.className = 'matrix-lock-btn';
                    lockBtn.dataset.lockIdx = String(o);
                    lockBtn.textContent = '\uD83D\uDD13';
                    lockBtn.title = 'Lock output';
                    lockBtn.addEventListener('click', () => toggleLock(o));
                    row.appendChild(lockBtn);
                }

                if (showMute) {
                    const muteBtn = document.createElement('button');
                    muteBtn.className = 'matrix-mute-btn';
                    muteBtn.textContent = 'M';
                    muteBtn.title = 'Mute output';
                    muteBtn.addEventListener('click', () => {
                        sendMute(o, dest);
                        muteBtn.classList.toggle('muted', mutedOutputs.has(o));
                        // Recorded on the node so the lock pass can tell a
                        // dropdown disabled by a mute from one it disabled
                        // itself, and not re-enable somebody else's.
                        select.dataset.muted = mutedOutputs.has(o) ? '1' : '';
                        select.disabled = isLocked(o) || mutedOutputs.has(o);
                    });
                    row.appendChild(muteBtn);
                }

                row.appendChild(select);
                list.appendChild(row);
            }

            scrollWrap.appendChild(list);
        } else if (matrixStyle === 'tiles') {
            // --- Tile wall ---
            // Destination-first: one card per destination, saying in large type
            // what is on it. A crosspoint grid is a transliteration of a 1990s
            // front panel -- its unit of thought is the crosspoint, and nobody
            // standing in a space wants a grid of dots. They want to know what
            // is on the main display, and to change it.
            //
            // So the sources are not on the wall at all. A tap opens them as a
            // chooser over the panel, which is also what lets this style's floor
            // ignore the source count entirely: sixteen sources cost a tile wall
            // nothing, where they cost a crosspoint grid sixteen columns.
            const wall = document.createElement('div');
            wall.className = 'matrix-tiles';
            const [tileCols, tileRows] = matrixTileGridShape(
                outputCount, this._drawnOrientation);
            if (tileCols > 0) {
                // minmax(floor, 1fr): a tile never draws below the floor the
                // review states, and takes the whole box when there is more.
                wall.style.gridTemplateColumns =
                    `repeat(${tileCols}, minmax(${MATRIX_TILE_MIN_W_PX}px, 1fr))`;
                wall.style.gridTemplateRows =
                    `repeat(${tileRows}, minmax(${MATRIX_TILE_MIN_H_PX}px, 1fr))`;
            }

            for (let o = 0; o < outputCount; o++) {
                const dest = destinations[o];
                // A div rather than a <button>: the lock and mute are real
                // buttons and a button cannot contain one.
                const tile = document.createElement('div');
                tile.className = 'matrix-tile';
                tile.dataset.destIdx = String(o);
                tile.setAttribute('role', 'button');
                tile.setAttribute('tabindex', '0');

                const head = document.createElement('div');
                head.className = 'matrix-tile-head';
                const name = document.createElement('span');
                name.className = 'matrix-tile-dest';
                name.dataset.outputIdx = String(o);
                const nameText = document.createElement('span');
                nameText.dataset.labelText = '';
                nameText.textContent = dest.label || `Out ${o + 1}`;
                name.appendChild(nameText);
                head.appendChild(name);

                if (showLock) {
                    const lockBtn = document.createElement('button');
                    lockBtn.className = 'matrix-lock-btn';
                    lockBtn.dataset.lockIdx = String(o);
                    lockBtn.textContent = '🔓';
                    lockBtn.title = 'Lock output';
                    lockBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        toggleLock(o);
                    });
                    head.appendChild(lockBtn);
                }
                if (showMute) {
                    const muteBtn = document.createElement('button');
                    muteBtn.className = 'matrix-mute-btn';
                    muteBtn.textContent = 'M';
                    muteBtn.title = 'Mute output';
                    muteBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        sendMute(o, dest);
                        muteBtn.classList.toggle('muted', mutedOutputs.has(o));
                    });
                    head.appendChild(muteBtn);
                }
                tile.appendChild(head);

                const routed = document.createElement('div');
                routed.className = 'matrix-tile-source';
                routed.dataset.tileIdx = String(o);
                routed.textContent = '—';
                tile.appendChild(routed);

                if (dest.audio_route_key) {
                    const audio = document.createElement('div');
                    audio.className = 'matrix-audio-source';
                    audio.dataset.audioIdx = String(o);
                    audio.hidden = true;
                    tile.appendChild(audio);
                }

                const open = () => { if (!isLocked(o)) openSourceChooser(o); };
                tile.addEventListener('click', open);
                tile.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
                });
                wall.appendChild(tile);
            }
            scrollWrap.appendChild(wall);
        } else {
            // --- Crosspoint view ---
            // The lock and mute columns are touch targets, so they get the same
            // floor as a crosspoint rather than the 2rem they used to declare.
            // That 2rem never was the drawn width: .matrix-cell carried a 44px
            // min-width, so the button was laid out 44px wide inside a 28px
            // track and overhung its neighbour. One number now, in one place.
            const extraColDefs = [];
            if (showLock) extraColDefs.push(`${MATRIX_CELL_MIN_PX}px`);
            if (showMute) extraColDefs.push(`${MATRIX_CELL_MIN_PX}px`);
            const table = document.createElement('div');
            table.className = 'matrix-grid';
            // minmax() is what does the fitting: the track cannot go below the
            // floor (so a starved grid scrolls rather than drawing untouchable
            // cells) and grows toward the ceiling with whatever room is spare.
            // The stylesheet's `justify-content: start` keeps the auto label
            // column from swallowing that spare room first.
            const cellTrack = `minmax(${cellMinPx}px, ${cellMaxPx}px)`;
            // The label column keeps MATRIX_LABEL_MIN_PX and grows to the longest
            // name when there is room. It was `auto`, whose base size is
            // min-content -- and .matrix-header is `overflow: hidden`, which makes
            // that ZERO, so the column started at its own padding and then took an
            // equal share of the spare room alongside eight cell tracks.
            const labelTrack = `minmax(${MATRIX_LABEL_MIN_PX}px, max-content)`;
            // repeat(0, ...) is not valid CSS, and an unresolved or empty matrix
            // is now reachable -- the counts come from lists rather than from a
            // default of four.
            const cellCols = inputCount > 0 ? `repeat(${inputCount}, ${cellTrack})` : '';
            table.style.gridTemplateColumns =
                `${labelTrack} ${cellCols} ${extraColDefs.join(' ')}`.replace(/\s+/g, ' ').trim();
            table.style.gridTemplateRows = outputCount > 0
                ? `auto repeat(${outputCount}, ${cellTrack})` : 'auto';

            // Top-left corner cell
            const corner = document.createElement('div');
            corner.className = 'matrix-corner';
            table.appendChild(corner);

            // Input headers (top row) — the column NUMBER, never the source
            // name. Names went in here rotated 45 degrees and clipped, which
            // left seven of eight realistic source names unreadable ("Laptop
            // HDMI" drew 36px of the 63px it needs), and collided outright at
            // four inputs or fewer, where they did not rotate at all. A number
            // always fits, and the names go to the legend under the grid where
            // there is room to read them.
            for (let i = 0; i < inputCount; i++) {
                const header = document.createElement('div');
                header.className = 'matrix-header matrix-input-header';
                header.textContent = String(i + 1);
                table.appendChild(header);
            }
            // Lock/Mute column headers
            if (showLock) {
                const lockHdr = document.createElement('div');
                lockHdr.className = 'matrix-header';
                lockHdr.textContent = '\uD83D\uDD12';
                lockHdr.style.fontSize = '0.7143rem';
                table.appendChild(lockHdr);
            }
            if (showMute) {
                const muteHdr = document.createElement('div');
                muteHdr.className = 'matrix-header';
                muteHdr.textContent = 'M';
                muteHdr.style.fontSize = '0.7143rem';
                table.appendChild(muteHdr);
            }

            // Drag-to-route state
            let dragLine = null;
            let dragStartInput = null;

            // Output rows with crosspoints
            for (let o = 0; o < outputCount; o++) {
                const dest = destinations[o];
                // Output label — wrapped in a labelText span so the mismatch
                // badge (when shown) doesn't get wiped by the dynamic-label
                // updater. The data-output-idx attribute stays on the header
                // so existing query selectors still find it.
                const outHeader = document.createElement('div');
                outHeader.className = 'matrix-header matrix-output-header';
                outHeader.dataset.outputIdx = String(o);
                const outLabelText = document.createElement('span');
                outLabelText.dataset.labelText = '';
                outLabelText.textContent = this._entryLabel(dest);
                outHeader.appendChild(outLabelText);
                // The audio source's NAME, whenever it differs from the video's
                // (see the list style for why this replaced the A≠V badge).
                if (dest.audio_route_key) {
                    const audio = document.createElement('span');
                    audio.className = 'matrix-audio-source';
                    audio.dataset.audioIdx = String(o);
                    audio.hidden = true;
                    outHeader.appendChild(audio);
                }
                // What the device says is routed here when it is not one of the
                // sources on this matrix. Without it that row is indistinguishable
                // from a destination routed to nothing: no dot lit, nothing said.
                const unlisted = document.createElement('span');
                unlisted.className = 'matrix-unlisted';
                unlisted.dataset.unlistedIdx = String(o);
                unlisted.hidden = true;
                outHeader.appendChild(unlisted);
                table.appendChild(outHeader);

                // Crosspoint cells
                for (let i = 0; i < inputCount; i++) {
                    const src = sources[i];
                    const cell = document.createElement('div');
                    cell.className = 'matrix-cell';
                    cell.setAttribute('aria-label',
                        `Route ${src.label || `input ${i + 1}`} to ${dest.label || `output ${o + 1}`}`);

                    const dot = document.createElement('div');
                    dot.className = 'matrix-crosspoint';
                    dot.style.backgroundColor = inactiveColor;
                    // data-input / data-output carry the VALUES, which is what
                    // the route comparison needs; the -idx pair carries the row
                    // and column, which is what a typed value is read back by.
                    // A DOM dataset is strings only, and a device that wants the
                    // number 3 must not be sent "3".
                    dot.dataset.input = String(src.value);
                    dot.dataset.output = String(dest.value);
                    dot.dataset.sourceIdx = String(i);
                    dot.dataset.destIdx = String(o);
                    // What the device calls this source, which is what a report
                    // is matched against. Usually identical to data-input; the
                    // two part company on a device routed by one vocabulary and
                    // reporting in another (see _sourceReports).
                    dot.dataset.reports = String(this._sourceReports(src));

                    // ONE gesture, ONE route. The cell used to carry a click
                    // handler AND a drag handler whose pointerup routed as
                    // well, so a tap sent the command twice (four times with
                    // audio-follow-video on) -- invisible on a healthy device,
                    // and only visible on the wire. Worse, it routed on the
                    // drag that SCROLLS an oversized grid, which is the very
                    // gesture a matrix bigger than its box forces on you.
                    //
                    // So the whole interaction is one pointer sequence:
                    //   tap      -> route this cell
                    //   drag     -> route the cell released over
                    //   scrolled -> nothing; the finger was moving the grid
                    cell.addEventListener('pointerdown', (e) => {
                        if (isLocked(o)) return;
                        const startX = e.clientX, startY = e.clientY;
                        const startLeft = scrollWrap.scrollLeft;
                        const startTop = scrollWrap.scrollTop;
                        const rect = cell.getBoundingClientRect();
                        const originX = rect.left + rect.width / 2;
                        const originY = rect.top + rect.height / 2;
                        let dragging = false;
                        dragStartInput = i;

                        // The grid moved under the finger, so the gesture was
                        // a scroll no matter where it ended up.
                        const scrolled = () =>
                            scrollWrap.scrollLeft !== startLeft ||
                            scrollWrap.scrollTop !== startTop;

                        const dropLine = () => {
                            if (dragLine) { dragLine.remove(); dragLine = null; }
                        };

                        const onMove = (me) => {
                            if (scrolled()) { dropLine(); dragging = false; return; }
                            const dx = me.clientX - startX, dy = me.clientY - startY;
                            if (!dragging && Math.hypot(dx, dy) < MATRIX_DRAG_THRESHOLD_PX) return;
                            dragging = true;
                            if (!dragLine) {
                                dragLine = document.createElement('div');
                                dragLine.className = 'matrix-drag-line';
                                dragLine.style.cssText = `
                                    position: fixed; pointer-events: none; z-index: 999;
                                    height: 2px; width: 0;
                                    background: ${activeColor};
                                    border-radius: 1px;
                                    transform-origin: 0 0;
                                    left: ${originX}px;
                                    top: ${originY}px;
                                `;
                                document.body.appendChild(dragLine);
                            }
                            const lx = me.clientX - originX, ly = me.clientY - originY;
                            dragLine.style.width = Math.hypot(lx, ly) + 'px';
                            dragLine.style.transform =
                                `rotate(${Math.atan2(ly, lx) * 180 / Math.PI}deg)`;
                        };

                        const finish = (ue, cancelled) => {
                            document.removeEventListener('pointermove', onMove);
                            document.removeEventListener('pointerup', onUp);
                            document.removeEventListener('pointercancel', onCancel);
                            el._matrixDragCleanup = null;
                            dropLine();
                            const wasDragging = dragging;
                            // Row and column indices, not port numbers -- index
                            // 0 is a real source, so every guard below asks
                            // whether it is a number rather than whether it is
                            // truthy.
                            const srcIdx = dragStartInput;
                            dragging = false;
                            dragStartInput = null;
                            // A cancelled pointer is the browser saying it took
                            // this gesture over to scroll with; a moved
                            // scrollbar says the same thing after the fact.
                            if (cancelled || scrolled() || srcIdx === null) return;
                            let destIdx = o;
                            if (wasDragging) {
                                const target = document.elementFromPoint(ue.clientX, ue.clientY);
                                const cp = target?.closest?.('.matrix-crosspoint')
                                    || target?.closest?.('.matrix-cell')?.querySelector('.matrix-crosspoint');
                                if (!cp || cp.dataset.destIdx === undefined) return;
                                destIdx = parseInt(cp.dataset.destIdx);
                            }
                            if (!Number.isInteger(destIdx) || isLocked(destIdx)) return;
                            const from = sources[srcIdx], to = destinations[destIdx];
                            if (!from || !to) return;
                            sendRoute(from.value, to.value);
                        };
                        const onUp = (ue) => finish(ue, false);
                        const onCancel = () => finish(null, true);

                        document.addEventListener('pointermove', onMove);
                        document.addEventListener('pointerup', onUp);
                        document.addEventListener('pointercancel', onCancel);
                        el._matrixDragCleanup = () => finish(null, true);
                    });

                    cell.appendChild(dot);
                    table.appendChild(cell);
                }

                // Lock button for this output
                if (showLock) {
                    const lockCell = document.createElement('div');
                    lockCell.className = 'matrix-cell matrix-toggle';
                    const lockBtn = document.createElement('button');
                    lockBtn.className = 'matrix-lock-btn';
                    lockBtn.dataset.lockIdx = String(o);
                    lockBtn.textContent = '\uD83D\uDD13';
                    lockBtn.title = 'Lock output';
                    lockBtn.addEventListener('click', () => toggleLock(o));
                    lockCell.appendChild(lockBtn);
                    table.appendChild(lockCell);
                }

                // Mute button for this output
                if (showMute) {
                    const muteCell = document.createElement('div');
                    muteCell.className = 'matrix-cell matrix-toggle';
                    const muteBtn = document.createElement('button');
                    muteBtn.className = 'matrix-mute-btn';
                    muteBtn.textContent = 'M';
                    muteBtn.title = 'Mute output';
                    muteBtn.addEventListener('click', () => {
                        sendMute(o, dest);
                        muteBtn.classList.toggle('muted', mutedOutputs.has(o));
                    });
                    muteCell.appendChild(muteBtn);
                    table.appendChild(muteCell);
                }
            }

            scrollWrap.appendChild(table);

            // The source legend: which name each numbered column is. This is
            // the half of F8 that makes numbered columns readable rather than
            // merely unclipped, and it is where a live input name now lands --
            // data-input-idx moved here from the header, so the state updater
            // writes the name where there is room for it.
            legend = document.createElement('div');
            legend.className = 'matrix-legend';
            for (let i = 0; i < inputCount; i++) {
                const item = document.createElement('span');
                item.className = 'matrix-legend-item';
                item.dataset.inputIdx = String(i);
                const num = document.createElement('span');
                num.className = 'matrix-legend-num';
                num.textContent = String(i + 1);
                const name = document.createElement('span');
                name.dataset.labelText = '';
                name.textContent = this._entryLabel(sources[i]);
                item.appendChild(num);
                item.appendChild(name);
                legend.appendChild(item);
            }
        }

        el.appendChild(scrollWrap);
        if (legend) el.appendChild(legend);
        this.elementMap[element.id] = { el, elementDef: element };
        // Where a destination with no lock_key remembers its lock. Hung on the
        // node so the state pass can read it, because that pass is what draws
        // every lock -- keyed or not -- from one place.
        el._matrixLocalLocks = localLocks;
        this._applyMatrixLocks(el, destinations);

        // State binding for routes. Every key this matrix reads, listed by name
        // rather than as a glob: a route key per destination, an audio key per
        // destination, and a live-name key per entry on either axis. The
        // incremental state.update filter matches on a prefix, and a concrete
        // key is its own prefix, so nothing about that filter had to change --
        // it just stopped being handed a literal `*` it could never match.
        const watched = [];
        for (const src of sources) if (src.label_key) watched.push(src.label_key);
        for (const dest of destinations) {
            if (dest.route_key) watched.push(dest.route_key);
            if (dest.audio_route_key) watched.push(dest.audio_route_key);
            if (dest.label_key) watched.push(dest.label_key);
            if (dest.lock_key) watched.push(dest.lock_key);
        }
        if (watched.length) {
            this.bindings.push({
                type: 'matrix_routes',
                element: el,
                elementDef: element,
                binding: { _patterns: watched },
                _matrix: {
                    sources, destinations,
                    activeColor, inactiveColor, matrixStyle,
                },
            });
        }

        return el;
    }

    evaluateMatrixRoutes(b) {
        const { sources, destinations, activeColor, inactiveColor, matrixStyle } = b._matrix;
        const el = b.element;

        // Each destination reads its OWN key. That is the change that lets one
        // matrix span several devices, or several routing planes of one device
        // -- the plane is part of the key, so nothing here has to know planes
        // exist. The RAW value is kept: the device decides what a routed source
        // looks like, and _routeMatches decides whether two of them name the
        // same thing.
        //
        // A destination whose own device cannot be reached reads as unreported
        // rather than as whatever it last said. This is per destination, not
        // per matrix: one matrix can span several switchers, and going quiet
        // about the live ones because a third is down would be its own lie.
        const destOffline = destinations.map(
            d => !!(d.route_key && this._keyDeviceOffline(d.route_key)));
        const routes = destinations.map(
            (d, i) => (d.route_key && !destOffline[i] ? this.state[d.route_key] : undefined));
        this._markMatrixAvailability(el, destOffline);

        // The name of what the AUDIO is on, whenever it differs from the video.
        //
        // This was a badge reading 'A\u2260V' with its explanation in a title
        // attribute -- a tooltip, on a control whose whole audience is standing
        // at a touch screen. Announcing that two things disagree is not the same
        // as saying what they are, and only the second is any use in the room.
        el.querySelectorAll('.matrix-audio-source').forEach(node => {
            const idx = parseInt(node.dataset.audioIdx);
            const dest = destinations[idx];
            const video = this._routeValue(routes[idx]);
            const raw = dest?.audio_route_key && !this._keyDeviceOffline(dest.audio_route_key)
                ? this.state[dest.audio_route_key] : undefined;
            const audio = this._routeValue(raw);
            // Only when BOTH are routed and they disagree. An unreported or
            // idle audio port is not a disagreement, it is an absence.
            const differs = video !== null && audio !== null
                && !this._routeMatches(video, audio);
            node.hidden = !differs;
            if (!differs) return;
            const src = this._routedSource(sources, raw);
            const name = src ? this._entryLabel(src) : String(raw);
            node.textContent = `Audio: ${name}`;
            node.title = `Audio is on ${name}`;
        });

        // Update dynamic labels from state \u2014 write to the [data-label-text]
        // child when present (crosspoint output header has siblings), else to
        // the header element directly (input headers, list-view labels).
        // Through _entryLabel, not straight off label_key: the authored name wins
        // where there is one, and this pass runs after the render, so reading the
        // live key here would put the device's name back over it.
        const applyLabel = (node, entry) => {
            if (!entry) return;
            const text = this._entryLabel(entry);
            if (text === '') return;
            (node.querySelector('[data-label-text]') || node).textContent = text;
        };
        el.querySelectorAll('[data-input-idx]').forEach(h => {
            applyLabel(h, sources[parseInt(h.dataset.inputIdx)]);
        });
        // Scoped to the LABEL nodes. The list view's <select> carries the same
        // data-output-idx (its change handler reads it), so an unscoped query
        // wrote the destination's name into the dropdown -- and setting
        // textContent on a <select> destroys every <option> in it. Live output
        // names emptied the control outright.
        el.querySelectorAll(
            '.matrix-output-header[data-output-idx], .matrix-list-label[data-output-idx], '
            + '.matrix-tile-dest[data-output-idx]'
        ).forEach(h => {
            applyLabel(h, destinations[parseInt(h.dataset.outputIdx)]);
        });

        this._applyMatrixLocks(el, destinations);

        if (matrixStyle === 'list') {
            const selects = el.querySelectorAll('.matrix-list-select');
            selects.forEach(sel => {
                // Live SOURCE names reach the dropdown here. The label updater
                // above only touches nodes tagged data-input-idx, which the
                // list view has none of, so its options kept their authored
                // captions no matter what the device reported.
                for (const opt of sel.options) {
                    if (opt.dataset.sourceIdx === undefined) continue;
                    const entry = sources[parseInt(opt.dataset.sourceIdx)];
                    const text = entry ? this._entryLabel(entry) : '';
                    if (text !== '') opt.textContent = text;
                }
                const raw = routes[parseInt(sel.dataset.outputIdx)];
                const routed = this._routeState(sources, raw);
                const unlisted = sel.querySelector('.matrix-list-unlisted');
                if (routed.state === 'listed') {
                    // The option whose source the device's report names, so a
                    // device reporting 'IN2' still moves the dropdown to Input 2.
                    const i = sources.indexOf(routed.source);
                    const hit = sel.querySelector(`option[data-source-idx="${i}"]`);
                    if (hit) sel.selectedIndex = hit.index;
                    if (unlisted) unlisted.hidden = true;
                } else if (routed.state === 'unlisted' && unlisted) {
                    // A source this matrix does not list. Showing the first
                    // option instead -- which is what a <select> does on its own
                    // -- names a source that is not routed, confidently.
                    unlisted.textContent = `${routed.raw} (not listed)`;
                    unlisted.hidden = false;
                    sel.selectedIndex = unlisted.index;
                } else {
                    // Nothing routed. Older than this plan and just as wrong:
                    // four rows read "Main LCD -> Apple TV" in one screenshot
                    // with nothing routed to any of them.
                    sel.selectedIndex = 0;
                    if (unlisted) unlisted.hidden = true;
                }
            });
        } else if (matrixStyle === 'tiles') {
            el.querySelectorAll('.matrix-tile-source').forEach(node => {
                const idx = parseInt(node.dataset.tileIdx);
                const routed = this._routeState(sources, routes[idx]);
                node.classList.toggle('unlisted', routed.state === 'unlisted');
                node.classList.toggle('idle', routed.state === 'none');
                if (routed.state === 'listed') {
                    node.textContent = this._entryLabel(routed.source);
                    node.style.color = activeColor;
                    node.title = '';
                } else if (routed.state === 'unlisted') {
                    node.textContent = routed.raw;
                    node.style.color = '';
                    node.title = `${routed.raw} is not one of this matrix's sources`;
                } else {
                    node.textContent = '\u2014';
                    node.style.color = '';
                    node.title = '';
                }
            });
        } else {
            // Update crosspoint dots
            const dots = el.querySelectorAll('.matrix-crosspoint');
            dots.forEach(dot => {
                // data-reports, not data-input: what the device CALLS this
                // source, which is the same thing on all but a couple of drivers
                // and is the only thing that can be compared on those.
                const isActive = this._routeMatches(
                    dot.dataset.reports, routes[parseInt(dot.dataset.destIdx)]);
                dot.style.backgroundColor = isActive ? activeColor : inactiveColor;
                dot.classList.toggle('active', isActive);
                const what = `${dot.dataset.input} to ${dot.dataset.output}`;
                dot.setAttribute('aria-label',
                    isActive ? `Active: ${what}` : `Inactive: ${what}`);
            });
            // A row whose device reports a source this matrix does not list
            // lights no dot, which is what a row routed to NOTHING looks like
            // too. Saying which one it is takes a word.
            el.querySelectorAll('.matrix-unlisted').forEach(node => {
                const routed = this._routeState(
                    sources, routes[parseInt(node.dataset.unlistedIdx)]);
                node.hidden = routed.state !== 'unlisted';
                if (routed.state === 'unlisted') {
                    node.textContent = routed.raw;
                    node.title = `${routed.raw} is not one of this matrix's sources`;
                }
            });
        }
    }

    /**
     * What this row is called: the name somebody typed, else the device's own.
     *
     * Same precedence as `child_display_name` on the server, which is THE answer
     * to what a port is called -- the integrator's label first, because a rack
     * label of `DEC-04` is not what belongs on a panel. Reading the live name
     * first (which this did until 2026-08-17) meant a name typed in the matrix
     * picker was stored and then never drawn on any device that reports its own
     * port names, with nothing to say so.
     *
     * It stays consistent between a tile and its chooser either way, which is the
     * thing that must not drift: both come through here.
     *
     * The picker holds up its end -- it writes `label` only when the author
     * changed it -- and so does the resolver, which no longer stamps "Out 2" onto
     * an entry that names a `label_key`. Without those two, this precedence would
     * freeze whatever the device happened to be called at setup.
     */
    _entryLabel(entry) {
        if (!entry) return '';
        const authored = entry.label;
        if (authored !== undefined && authored !== null && String(authored) !== '') {
            return String(authored);
        }
        const live = entry.label_key ? this.state[entry.label_key] : undefined;
        if (live !== undefined && live !== null && String(live) !== '') return String(live);
        // Nobody has named this port -- not here and not on the device. "Out 3"
        // beats the id it would otherwise read as, which on an AVoIP endpoint is
        // a MAC address.
        return String(entry._caption || entry.value);
    }

    /**
     * Draw every lock this matrix has, keyed or not, from one place.
     *
     * A lock backed by a variable is whatever that variable says -- which is how
     * it survives a redraw and reaches the panel by the other door. One with no
     * key falls back to this panel's own memory, which is what the lock has
     * always been and is why it was decorative (F10); the page review says so at
     * authoring time rather than leaving it to be discovered in a room.
     */
    _applyMatrixLocks(el, destinations) {
        const local = el._matrixLocalLocks || new Set();
        el.querySelectorAll('[data-lock-idx]').forEach(btn => {
            const o = parseInt(btn.dataset.lockIdx);
            const key = destinations[o] && destinations[o].lock_key;
            const engaged = key ? this._lockEngaged(this.state[key]) : local.has(o);
            btn.textContent = engaged ? '\uD83D\uDD12' : '\uD83D\uDD13';
            btn.classList.toggle('locked', engaged);
            btn.title = engaged ? 'Unlock output' : 'Lock output';
            const row = btn.closest('.matrix-list-row');
            const sel = row && row.querySelector('.matrix-list-select');
            // Muted disables the dropdown too, so unlocking must not undo it.
            if (sel) sel.disabled = engaged || sel.dataset.muted === '1';
            const tile = btn.closest('.matrix-tile');
            if (tile) tile.classList.toggle('locked', engaged);
        });
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
            el.appendChild(label);
        }

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.elementMap[element.id] = { el, elementDef: element };

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

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.elementMap[element.id] = { el, elementDef: element };

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

        const orientation = element.orientation || 'vertical';
        const isHorizontal = orientation === 'horizontal';
        let min = element.min != null ? parseFloat(element.min) : 0;
        let max = element.max != null ? parseFloat(element.max) : 100;
        if (min >= max) { const tmp = min; min = max; max = tmp; }
        const step = element.step ?? 1;
        const unit = element.unit || '%';
        const outputMin = element.output_min;
        const outputMax = element.output_max;
        const hasOutputRange = outputMin != null && outputMax != null;
        const scaleToFull = element.scale_to_full !== false;
        const response = element.response || 'linear';
        const responseDbRange = element.response_db_range != null ? Number(element.response_db_range) : 60;
        const faderDisplayDecimals = element.display_decimals != null ? Number(element.display_decimals) : 1;
        const faderSendOnRelease = element.send_on_release === true;
        const faderThrottle = element.send_throttle_ms != null ? Number(element.send_throttle_ms) : 50;
        const fmtFaderValue = (v) => {
            const n = Number(v);
            const s = faderDisplayDecimals > 0 ? n.toFixed(faderDisplayDecimals) : String(Math.round(n));
            return unit ? `${s} ${unit}` : s;
        };
        // Merge theme element_defaults for consistency with other renderers,
        // even though show_value/show_scale aren't currently theme-editable
        // — keeps the pattern uniform if those flags become themable later.
        const style = this.getThemedStyle('fader', element.style);
        const showValue = style.show_value !== false;
        const showScale = style.show_scale !== false;

        el.classList.add(isHorizontal ? 'horizontal' : 'vertical');

        // Element-level wrapper styling (bg_color, border, padding, shadow, etc.)
        this.applyStyle(el, this.getThemedStyle('fader', element.style));

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'fader-label';
            label.textContent = element.label;
            el.appendChild(label);
        }

        const body = document.createElement('div');
        body.className = 'fader-body';

        // Scale marks
        if (showScale) {
            const scale = document.createElement('div');
            scale.className = 'fader-scale';
            const marks = this._faderScaleMarks(min, max);
            for (const m of marks) {
                const mark = document.createElement('div');
                mark.className = 'fader-mark';
                const frac = this._responseCurveInverse((m - min) / (max - min), response, responseDbRange);
                if (isHorizontal) mark.style.left = `${frac * 100}%`;
                else mark.style.bottom = `${frac * 100}%`;
                mark.textContent = m.toString();
                scale.appendChild(mark);
            }
            body.appendChild(scale);
        }

        // Track + handle
        const trackWrap = document.createElement('div');
        trackWrap.className = 'fader-track-wrap';

        const track = document.createElement('div');
        track.className = 'fader-track';
        trackWrap.appendChild(track);

        // Dead space overlay when output range is clamped and not scaled to full.
        // Position it through the response curve so it lines up with where the
        // handle actually stops on a logarithmic fader (identity for linear).
        if (hasOutputRange && !scaleToFull) {
            const maxFrac = this._responseCurveInverse((outputMax - min) / (max - min), response, responseDbRange);
            const minFrac = this._responseCurveInverse((outputMin - min) / (max - min), response, responseDbRange);
            if (maxFrac < 1) {
                const dead = document.createElement('div');
                dead.className = 'fader-dead-space';
                if (isHorizontal) { dead.style.left = `${maxFrac * 100}%`; dead.style.right = '0'; dead.style.top = '0'; dead.style.bottom = '0'; }
                else { dead.style.bottom = `${maxFrac * 100}%`; dead.style.top = '0'; dead.style.left = '0'; dead.style.right = '0'; }
                trackWrap.appendChild(dead);
            }
            if (minFrac > 0) {
                const dead = document.createElement('div');
                dead.className = 'fader-dead-space';
                if (isHorizontal) { dead.style.left = '0'; dead.style.right = `${(1 - minFrac) * 100}%`; dead.style.top = '0'; dead.style.bottom = '0'; }
                else { dead.style.bottom = '0'; dead.style.top = `${(1 - minFrac) * 100}%`; dead.style.left = '0'; dead.style.right = '0'; }
                trackWrap.appendChild(dead);
            }
        }

        const handle = document.createElement('div');
        handle.className = 'fader-handle';
        handle.setAttribute('role', 'slider');
        handle.setAttribute('aria-label', element.label || `Fader ${element.id}`);
        handle.setAttribute('aria-valuemin', String(min));
        handle.setAttribute('aria-valuemax', String(max));
        handle.tabIndex = 0;
        trackWrap.appendChild(handle);

        body.appendChild(trackWrap);
        el.appendChild(body);

        // Value display
        let valueDisplay = null;
        if (showValue) {
            valueDisplay = document.createElement('div');
            valueDisplay.className = 'fader-value';
            valueDisplay.textContent = fmtFaderValue(0);
            el.appendChild(valueDisplay);
        }

        // Position handle — initial value from state or 0
        const valueBinding = element.bindings?.show?.value;
        let currentValue = 0;
        if (valueBinding?.key) {
            const sv = this.state[valueBinding.key];
            if (sv !== undefined && sv !== null) {
                // Reverse-scale device value to display value
                currentValue = this._reverseScale(Number(sv), min, max, outputMin, outputMax, scaleToFull);
            }
        }
        currentValue = Math.max(min, Math.min(max, currentValue));

        // Touch/mouse drag interaction
        let dragging = false;
        let debounceTimer = null;
        let currentDragVal = currentValue;
        // Filled in below when this fader has a value binding; the drag-end
        // handler needs it to put an unreachable device's readout back.
        let faderBinding = null;

        const getValueFromEvent = (e) => {
            const rect = trackWrap.getBoundingClientRect();
            let frac;
            if (isHorizontal) {
                const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
            } else {
                const clientY = e.touches ? e.touches[0].clientY : e.clientY;
                frac = 1 - Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
            }
            let val = min + this._responseCurve(frac, response, responseDbRange) * (max - min);
            // Clamp to output range when not scaling (dead space mode)
            if (hasOutputRange && !scaleToFull) {
                val = Math.max(outputMin, Math.min(outputMax, val));
            }
            return this._snapToStep(val, step);
        };

        const updateFader = (val) => {
            const frac = this._responseCurveInverse((val - min) / (max - min), response, responseDbRange);
            if (isHorizontal) handle.style.left = `${frac * 100}%`;
            else handle.style.bottom = `${frac * 100}%`;
            handle.setAttribute('aria-valuenow', String(Math.round(val * 10) / 10));
            if (valueDisplay) valueDisplay.textContent = fmtFaderValue(val);
        };
        // Draw the starting value through the same function the drag uses. It
        // used to be an inline copy of these four lines placed above, which is
        // where the aria value went missing: nothing announced a reading until
        // somebody dragged the handle, and the arrow keys read their starting
        // point back out of that attribute -- so the first press of Down on a
        // freshly-drawn fader jumped it to the floor.
        updateFader(currentValue);

        // Debounced during a live drag; `immediate` sends the final value at once
        // (used on release so send-on-release fires exactly one command).
        const sendChange = (val, immediate) => {
            if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
            if (immediate) {
                this.send({ type: 'ui.change', element_id: element.id, value: val });
                return;
            }
            debounceTimer = setTimeout(() => {
                this.send({ type: 'ui.change', element_id: element.id, value: val });
            }, faderThrottle);
            this.debounceTimers.push(debounceTimer);
        };

        const onStart = (e) => {
            e.preventDefault();
            dragging = true;
            handle._dragging = true; // suppress inbound state echoes mid-drag
            const val = getValueFromEvent(e);
            currentDragVal = val;
            updateFader(val);
            if (!faderSendOnRelease) sendChange(val);
        };
        const onMove = (e) => {
            if (!dragging) return;
            e.preventDefault();
            const val = getValueFromEvent(e);
            currentDragVal = val;
            updateFader(val);
            if (!faderSendOnRelease) sendChange(val);
        };
        const onEnd = () => {
            const wasDragging = dragging;
            dragging = false;
            handle._dragging = false;
            // Send the final position once when the drag ends. In send-on-release
            // mode this is the only send; in live mode it just guarantees the
            // stream lands on the end value.
            if (wasDragging) sendChange(currentDragVal, true);
            // A drag on an unreachable device leaves the readout showing the
            // number the operator dragged to, which is exactly the confident
            // wrong value this control is not allowed to draw. Put it straight
            // back to unknown. (A drag on a LIVE device whose command is then
            // refused is the other half, and it cannot be answered here: the
            // refusal has not arrived yet. See _revertRefusedInteraction.)
            if (wasDragging && faderBinding && this._bindingOffline(faderBinding)) {
                this._renderFaderUnknown(faderBinding);
            }
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onEnd);
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onEnd);
        };
        // Store drag cleanup so renderCurrentPage can remove orphaned listeners
        trackWrap._faderDragCleanup = () => {
            onEnd();
        };

        trackWrap.addEventListener('mousedown', (e) => {
            onStart(e);
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onEnd);
        });
        trackWrap.style.touchAction = 'none';
        trackWrap.addEventListener('touchstart', (e) => {
            onStart(e);
            document.addEventListener('touchmove', onMove);
            document.addEventListener('touchend', onEnd);
        });

        // Double-tap to reset to 0
        let lastTap = 0;
        handle.addEventListener('mousedown', (e) => {
            const now = Date.now();
            if (now - lastTap < 300) {
                const resetVal = Math.max(min, Math.min(max, 0));
                updateFader(resetVal);
                sendChange(resetVal);
            }
            lastTap = now;
        });

        // Keyboard arrow key support for fader
        handle.addEventListener('keydown', (e) => {
            let current = parseFloat(handle.getAttribute('aria-valuenow') || String(min));
            if (e.key === 'ArrowUp' || e.key === 'ArrowRight') {
                e.preventDefault();
                current = Math.min(max, this._snapToStep(current + step, step));
                updateFader(current);
                sendChange(current);
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') {
                e.preventDefault();
                current = Math.max(min, this._snapToStep(current - step, step));
                updateFader(current);
                sendChange(current);
            }
        });

        this.elementMap[element.id] = { el, elementDef: element };

        // Value binding for state updates
        if (valueBinding) {
            faderBinding = {
                type: 'fader_value',
                element: el,
                elementDef: element,
                binding: valueBinding,
                _fader: { handle, valueDisplay, min, max, unit, horizontal: isHorizontal, outputMin, outputMax, scaleToFull, response, responseDbRange, fmt: fmtFaderValue },
            };
            this.bindings.push(faderBinding);
        }

        return el;
    }

    _reverseScale(deviceValue, displayMin, displayMax, outputMin, outputMax, scaleToFull) {
        if (outputMin == null || outputMax == null) return deviceValue;
        if (scaleToFull === false) return deviceValue;
        const outputRange = outputMax - outputMin;
        if (outputRange === 0) return displayMin;
        const frac = (deviceValue - outputMin) / outputRange;
        return displayMin + frac * (displayMax - displayMin);
    }

    // Snap a value to the nearest multiple of `step`, then strip the binary
    // float noise that Math.round(value/step)*step leaves behind — e.g. a 0.1
    // step yielding 0.30000000000000004 — by rounding to the step's own decimal
    // precision. Without this a fractional-step slider/fader puts that noise
    // straight on the wire (the engine only re-rounds when a driver declares a
    // decimals rule, which new controls no longer carry a default for). A
    // non-positive step means "don't snap" and returns the value unchanged.
    _snapToStep(value, step) {
        if (!(step > 0)) return value;
        const snapped = Math.round(value / step) * step;
        // Decimal places the step implies (0.1 -> 1, 0.25 -> 2, 5 -> 0). Panel
        // steps are clean decimals, so String() never goes exponential here.
        const s = String(step);
        const dot = s.indexOf('.');
        const decimals = dot === -1 ? 0 : s.length - dot - 1;
        return decimals > 0 ? Number(snapped.toFixed(decimals)) : snapped;
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

    // Response curve for faders/sliders. Maps physical travel (0..1, bottom to
    // top) to a normalized value fraction (0..1). Linear is the identity.
    // Logarithmic makes the travel linear in decibels so the control feels like
    // a real audio fader — equal travel is an equal loudness step — instead of
    // cramming all the audible change into the top of the throw. `dbRange` is
    // how many dB the throw spans (larger = finer control down low). This is
    // purely a feel transform: the value handed to the device is unchanged, so
    // it lives entirely on the panel and the server never sees the curve.
    _responseCurve(travelFrac, response, dbRange) {
        if (response !== 'logarithmic') return travelFrac;
        const D = dbRange > 0 ? dbRange : 60;
        const denom = Math.pow(10, D / 20) - 1;
        if (denom <= 0) return travelFrac;
        return (Math.pow(10, (D * travelFrac) / 20) - 1) / denom;
    }

    // Inverse of _responseCurve: normalized value fraction (0..1) -> travel (0..1).
    // Used to place the handle/thumb (and scale marks) for a known value.
    _responseCurveInverse(valueFrac, response, dbRange) {
        if (response !== 'logarithmic') return valueFrac;
        const D = dbRange > 0 ? dbRange : 60;
        const denom = Math.pow(10, D / 20) - 1;
        if (denom <= 0) return valueFrac;
        const vf = Math.max(0, Math.min(1, valueFrac));
        return (20 / D) * Math.log10(vf * denom + 1);
    }

    _faderScaleMarks(min, max) {
        const range = max - min;
        if (range === 0) return [min];
        // Pick a step that gives 3-7 marks
        const rawStep = range / 5;
        const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
        const nice = [1, 2, 2.5, 5, 10].find(n => n * mag >= rawStep) * mag;
        const marks = [];
        const start = Math.ceil(min / nice) * nice;
        for (let v = start; v <= max + nice * 0.01; v += nice) {
            const rounded = Math.round(v * 1e6) / 1e6;
            if (rounded >= min && rounded <= max) marks.push(rounded);
        }
        if (marks.length === 0 || marks[0] > min) marks.unshift(min);
        if (marks[marks.length - 1] < max) marks.push(max);
        return marks;
    }

    /**
     * The fader with nothing to show, because the device it reads is gone.
     *
     * The handle is hidden (CSS, off the element's `device-offline` class)
     * rather than parked at the floor: a handle at the bottom is a claim of
     * minimum, and on a -80..0 fader that reads as fully attenuated. What was
     * photographed for this fault was the opposite claim -- a handle at the
     * top over "0.0 dB" -- on an amplifier sitting at -6.0 and muted.
     */
    _renderFaderUnknown(b) {
        const { handle, valueDisplay, unit, horizontal } = b._fader;
        if (horizontal) handle.style.left = '0%';
        else handle.style.bottom = '0%';
        handle.removeAttribute('aria-valuenow');
        if (valueDisplay) valueDisplay.textContent = this._unknownValueText(unit);
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
        const { handle, valueDisplay, min, max, horizontal, outputMin, outputMax, scaleToFull, response, responseDbRange, fmt } = b._fader;
        // Don't fight the operator while they're dragging the handle.
        if (handle._dragging) return;
        const span = max - min;
        if (offline) {
            this._renderFaderUnknown(b);
            return;
        }
        if (raw === undefined || raw === null) {
            // Bound key deleted — return the handle to the floor rather than
            // leaving it parked at the last device value.
            if (horizontal) handle.style.left = '0%';
            else handle.style.bottom = '0%';
            handle.setAttribute('aria-valuenow', String(min));
            if (valueDisplay) valueDisplay.textContent = fmt(min);
            return;
        }
        const value = Math.max(min, Math.min(max, this._reverseScale(Number(raw), min, max, outputMin, outputMax, scaleToFull)));
        const frac = span > 0 ? this._responseCurveInverse((value - min) / span, response, responseDbRange) : 0;
        if (horizontal) handle.style.left = `${frac * 100}%`;
        else handle.style.bottom = `${frac * 100}%`;
        // The arrow keys read their starting point off this, so a state update
        // that moved the handle and left it behind would send the operator's
        // next keystroke off the OLD value.
        handle.setAttribute('aria-valuenow', String(Math.round(value * 10) / 10));
        if (valueDisplay) valueDisplay.textContent = fmt(value);
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

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.elementMap[element.id] = { el, elementDef: element };
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

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.elementMap[element.id] = { el, elementDef: element };

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

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'keypad-label';
            label.textContent = element.label;
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
            keys = ['1','2','3','4','5','6','7','8','9','C','0','⏎'];
        }

        let autoSendTimer = null;

        const updateDisplay = () => {
            if (displayEl) displayEl.textContent = buffer || '';
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
            btn.textContent = key;
            btn.setAttribute('aria-label', key === 'C' ? 'Clear' : key === '⏎' ? 'Enter' : key === '*' ? 'Star' : key === '#' ? 'Hash' : `Key ${key}`);

            if (key === 'C') btn.classList.add('keypad-clear');
            if (key === '⏎') btn.classList.add('keypad-enter');

            btn.addEventListener('click', (e) => {
                e.preventDefault();
                if (key === 'C') {
                    buffer = '';
                    if (autoSendTimer) { clearTimeout(autoSendTimer); autoSendTimer = null; }
                } else if (key === '⏎') {
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
        this.elementMap[element.id] = { el, elementDef: element };
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
            src: `${this._panelBasePath()}/api/plugins/${encodeURIComponent(pluginId)}/panel/${encodeURIComponent(pluginType)}.html`,
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
            src: `${this._panelBasePath()}/api/projects/default/ui/${src}${bust}`,
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
     *  guard, which is the reason §4.3 collapsed the plugin and control paths
     *  into one to begin with. */
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

    /** The path the panel is served under, so every URL we build is relative.
     *  An absolute one works on the LAN and dies through the cloud tunnel. */
    _panelBasePath() {
        return location.pathname.split('/panel')[0] || '';
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
        if (detail === undefined) {
            // Unconfigured: one line, no border — it is not a stand-in for
            // something that would otherwise draw.
            el.style.opacity = '0.5';
            el.style.fontSize = '0.8571rem';
            el.textContent = label;
            return el;
        }
        el.style.border = '1px dashed var(--panel-text, rgba(255,255,255,0.3))';
        el.style.borderRadius = '4px';
        el.style.opacity = '0.4';
        el.style.fontSize = '11px';
        const title = document.createElement('div');
        title.textContent = label;
        title.style.fontWeight = '600';
        const sub = document.createElement('div');
        sub.textContent = detail;
        sub.style.opacity = '0.8';
        sub.style.fontSize = '10px';
        el.appendChild(title);
        el.appendChild(sub);
        this.applyStyle(el, this.getThemedStyle(styleType, element.style));
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
                    this.navigateToPage(msg.page);
                    // Tell the server too, exactly as the page-nav button does
                    // (renderPageNav). It turns this into the `ui.page.<id>`
                    // event triggers fire on, so a room that dims its lights
                    // when somebody lands on a page behaves the same whether
                    // they got there by button or from inside a frame. Without
                    // it the trigger simply never runs, and nothing says why.
                    this.send({ type: 'ui.page', page_id: msg.page });
                    break;
            }
        };
        window.addEventListener('message', handler);
        el._pluginMessageHandler = handler;
        this._pluginMessageHandlers.add(handler);

        this.applyStyle(el, this.getThemedStyle(opts.styleType, element.style));
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
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        return basePath + url;
    }

    _resolveSoundUrl(soundId) {
        if (!soundId || typeof soundId !== 'string') return null;
        if (soundId.startsWith('assets://')) {
            return this.resolveAssetUrl(soundId);
        }
        if (soundId.startsWith('http://') || soundId.startsWith('https://') || soundId.startsWith('/')) {
            return this._resolveAbsoluteUrl(soundId);
        }
        // Last-resort fallback for sounds the plugin didn't resolve a URL for.
        // Assumes .mp3 by convention; works for plugins that follow it.
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        return `${basePath}/api/plugins/audio_player/files/sounds/${encodeURIComponent(soundId)}.mp3`;
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
            if (running) {
                const text = running.description || `Step ${running.step_index + 1} of ${running.total_steps}`;
                b.element.textContent = text;
            } else {
                b.element.textContent = b.binding.idle_text || '';
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

    /** Whether this binding's reading can be believed. */
    _bindingOffline(b) {
        const ids = this._bindingDeviceIds(b);
        if (!ids) return false;
        return ids.some(id => this._deviceOffline(id));
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
     * The same unavailable mark, one destination at a time.
     *
     * A matrix is the one control that can be half true, so it is marked per
     * row rather than per element. Each style tags its destination label with
     * `data-output-idx`, and two of the three hang that label inside a real
     * row container the mark can go on.
     *
     * The crosspoint does not. It is a flat CSS grid -- a corner, one header
     * per source, then for each destination its name followed by one cell per
     * source -- with no element standing for a row and no `data-` on a cell.
     * So its row is the header plus the siblings up to the next header, and it
     * is marked node by node. Marking only the name would leave a row of live-
     * looking crosspoints beside it, which is the thing this exists to stop.
     */
    _markMatrixAvailability(el, destOffline) {
        const off = (i) => !!destOffline[i];
        for (const [labelSel, rowSel] of [
            ['.matrix-tile-dest[data-output-idx]', '.matrix-tile'],
            ['.matrix-list-label[data-output-idx]', '.matrix-list-row'],
        ]) {
            el.querySelectorAll(labelSel).forEach(node => {
                const row = node.closest(rowSel);
                if (row) row.classList.toggle('device-offline', off(parseInt(node.dataset.outputIdx)));
            });
        }
        el.querySelectorAll('.matrix-output-header[data-output-idx]').forEach(header => {
            const offline = off(parseInt(header.dataset.outputIdx));
            let node = header;
            do {
                node.classList.toggle('device-offline', offline);
                node = node.nextElementSibling;
            } while (node && !node.classList.contains('matrix-output-header'));
        });
    }

    /**
     * Drop the inline colours a state look wrote, where the element's own
     * style has nothing to put back. applyStyle writes only the properties it
     * is given, so without this a revert to base leaves the last state's
     * colours standing.
     */
    _clearStateColours(element, baseStyle) {
        if (!element.style) return;
        if (!baseStyle.bg_color && !baseStyle.background_gradient) {
            element.style.backgroundColor = '';
        }
        if (!baseStyle.text_color) element.style.color = '';
    }

    /** What a readout says when there is no reading: "--", with the unit if it carries one. */
    _unknownValueText(unit) {
        return unit ? `-- ${unit}` : '--';
    }

    evaluateAllBindings(changedKeys = null) {
        // Which devices just changed reachability. A binding reading one of
        // them has to be re-evaluated even though its OWN key did not move:
        // the value is the same and what it is worth has changed.
        let flipped = null;
        if (changedKeys) {
            for (const k of changedKeys) {
                if (typeof k === 'string' && k.startsWith('device.') && k.endsWith('.connected')) {
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

        // Apply ui.* state overrides (set by macros/scripts)
        // These take priority over feedback bindings for direct control.
        this.evaluateUiOverrides();
    }

    /**
     * Draw one binding from current state.
     *
     * Shared with the refusal revert, which needs exactly this and none of the
     * incremental filtering around it. `force` says the operator's own command
     * was rejected, which is the one time a renderer may overwrite a value
     * they are still sitting on.
     */
    _evaluateBinding(b, force) {
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
            case 'text':
                this.evaluateText(b);
                break;
            case 'color':
                this.evaluateColor(b);
                break;
            case 'slider_value':
                this.evaluateSliderValue(b, force);
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
                entry._uiBase = {
                    backgroundColor: el.style.backgroundColor,
                    color: el.style.color,
                    opacity: el.style.opacity,
                    display: el.style.display,
                    label: elementDef?.label,
                };
                entry._uiApplied = new Set();
            }
            const base = entry._uiBase;
            const applied = entry._uiApplied;

            // Label override (preserve image layer and other element children)
            const labelOverride = this.state[prefix + 'label'];
            if (labelOverride !== undefined && labelOverride !== null) {
                this._setLabelText(el, String(labelOverride));
                applied.add('label');
            } else if (applied.has('label')) {
                this._setLabelText(el, base.label != null ? String(base.label) : '');
                applied.delete('label');
            }

            const bgOverride = this.state[prefix + 'bg_color'];
            if (bgOverride !== undefined && bgOverride !== null) {
                el.style.backgroundColor = String(bgOverride);
                applied.add('bg');
            } else if (applied.has('bg')) {
                el.style.backgroundColor = base.backgroundColor;
                applied.delete('bg');
            }

            const textColorOverride = this.state[prefix + 'text_color'];
            if (textColorOverride !== undefined && textColorOverride !== null) {
                el.style.color = String(textColorOverride);
                applied.add('text');
            } else if (applied.has('text')) {
                el.style.color = base.color;
                applied.delete('text');
            }

            const opacityOverride = this.state[prefix + 'opacity'];
            if (opacityOverride !== undefined && opacityOverride !== null) {
                el.style.opacity = String(opacityOverride);
                applied.add('opacity');
            } else if (applied.has('opacity')) {
                el.style.opacity = base.opacity;
                applied.delete('opacity');
            }

            const visibleOverride = this.state[prefix + 'visible'];
            if (visibleOverride !== undefined && visibleOverride !== null) {
                el.style.display = (visibleOverride === false || visibleOverride === 'false')
                    ? 'none' : '';
                applied.add('visible');
            } else if (applied.has('visible')) {
                // Hand display back to a visible_when binding if one governs this
                // element (it re-asserts on the next evaluation); otherwise
                // restore the rendered base.
                el.style.display = elementDef?.bindings?.show?.visible_when ? '' : base.display;
                applied.delete('visible');
            }
        }
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
            this._clearStateColours(element, baseStyle);
            this.applyStyle(element, this.getThemedStyle(elementDef.type, baseStyle));
            if (!elementDef.bindings?.show?.value && elementDef.text !== undefined) {
                this._setLabelText(element, String(elementDef.text));
            }
            return;
        }

        let appearance;
        if (binding.states) {
            const stateKey = stateValue != null ? String(stateValue) : (binding.default_state || '');
            appearance = binding.states[stateKey]
                || binding.states[binding.default_state || '']
                || {};
        } else {
            // Legacy binary look, same shape the button honors.
            const condition = binding.condition || {};
            const isActive = stateValue !== undefined &&
                String(stateValue).toLowerCase() === String(condition.equals).toLowerCase();
            appearance = (isActive ? binding.style_active : binding.style_inactive) || {};
            const legacyText = isActive ? binding.label_active : binding.label_inactive;
            if (legacyText !== undefined && legacyText !== null) {
                appearance = { ...appearance, label: legacyText };
            }
        }

        this.applyStyle(element, this.getThemedStyle(elementDef.type, { ...baseStyle, ...appearance }));

        if (appearance.label !== undefined) {
            this._setLabelText(element, String(appearance.label));
        }
    }

    evaluateFeedback(b) {
        const { element, elementDef, binding } = b;
        const stateValue = this.state[binding.key];
        const baseStyle = elementDef.style || {};
        const displayMode = elementDef.display_mode || 'text';
        const suppressLabel = displayMode === 'image' || displayMode === 'icon_only';

        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline) {
            // No state is asserted while the device is unreachable: the button
            // goes back to its own look and its own label. This is the mute
            // button that was photographed drawing its not-muted face over a
            // muted amplifier -- a null child key resolves to `default_state`,
            // so "unknown" was being rendered as whatever state was nominated
            // as the default.
            // applyStyle only ever writes a property it has a value for, so a
            // state colour on an element whose own style names none would
            // survive the revert -- a MUTED button staying red for an amplifier
            // nobody can reach is the whole fault, one property further down.
            this._clearStateColours(element, baseStyle);
            this.applyStyle(element, this.getThemedStyle(elementDef.type, baseStyle));
            if (elementDef.frameless) this.applyFrameless(element);
            if (baseStyle.bg_color) this.updateImageTint(element, baseStyle.bg_color);
            if (suppressLabel) {
                this._removeTextNodes(element);
            } else {
                // A state's WORD is a claim as much as its colour. A button
                // with no name of its own is left blank rather than still
                // reading MUTED for an amplifier nobody can reach.
                this._setLabelText(element, elementDef.label || '');
            }
            const baseIcon = elementDef.icon || elementDef.style?.icon;
            if (baseIcon) this.renderElementContent(element, elementDef);
            if (elementDef.button_image) {
                this.applyImageEffect(element, elementDef.button_image, {
                    fit: elementDef.image_fit,
                    blend: elementDef.image_blend_mode,
                    opacity: elementDef.image_opacity,
                    tintColor: baseStyle.bg_color,
                });
            }
            return;
        }

        // Multi-state feedback (new)
        if (binding.states) {
            const stateKey = stateValue != null ? String(stateValue) : (binding.default_state || '');
            const appearance = binding.states[stateKey] || binding.states[binding.default_state || ''] || {};
            const style = { ...baseStyle, ...appearance };
            this.applyStyle(element, style);
            // Re-apply frameless so state bg_color changes don't reintroduce chrome
            if (elementDef.frameless) this.applyFrameless(element);
            // Retint the image layer so tint tracks state bg_color
            if (style.bg_color) this.updateImageTint(element, style.bg_color);

            // Update label (suppressed when display mode hides text).
            // Remove only text nodes so we don't wipe the image layer (an element child).
            if (suppressLabel) {
                this._removeTextNodes(element);
            } else if (appearance.label !== undefined) {
                this._setLabelText(element, String(appearance.label));
            } else if (elementDef.label) {
                this._setLabelText(element, elementDef.label);
            }

            // Rebuild icon+text layout if element has any icon (from appearance or base element)
            const resolvedIcon = appearance.icon || elementDef.icon || elementDef.style?.icon;
            if (resolvedIcon) {
                const iconDef = {
                    ...elementDef,
                    icon: appearance.icon || elementDef.icon,
                    icon_color: appearance.icon_color || elementDef.icon_color,
                };
                this.renderElementContent(element, iconDef);
            }

            // Swap button image if state overrides it (10% case: genuinely different image per state)
            if (appearance.button_image && elementDef.button_image !== appearance.button_image) {
                this.applyImageEffect(element, appearance.button_image, {
                    fit: elementDef.image_fit,
                    blend: elementDef.image_blend_mode,
                    opacity: elementDef.image_opacity,
                    tintColor: style.bg_color,
                });
            }
            return;
        }

        // Legacy binary feedback (backwards compatible)
        const condition = binding.condition || {};
        const isActive = stateValue !== undefined &&
            String(stateValue).toLowerCase() === String(condition.equals).toLowerCase();

        const activeStyle = binding.style_active || {};
        const inactiveStyle = binding.style_inactive || {};

        const style = isActive
            ? { ...baseStyle, ...activeStyle }
            : { ...baseStyle, ...inactiveStyle };

        this.applyStyle(element, style);
        if (elementDef.frameless) this.applyFrameless(element);
        if (style.bg_color) this.updateImageTint(element, style.bg_color);

        // Per-state image override (legacy feedback)
        const stateImage = (isActive ? activeStyle.button_image : inactiveStyle.button_image);
        if (stateImage && elementDef.button_image !== stateImage) {
            this.applyImageEffect(element, stateImage, {
                fit: elementDef.image_fit,
                blend: elementDef.image_blend_mode,
                opacity: elementDef.image_opacity,
                tintColor: style.bg_color,
            });
        }

        // Conditional labels — must run BEFORE renderElementContent so
        // the icon+text layout rebuild captures the updated text.
        // Suppressed when display mode hides text.
        // Remove only text nodes to preserve the image layer (an element child).
        if (suppressLabel) {
            this._removeTextNodes(element);
        } else if (isActive && binding.label_active) {
            this._setLabelText(element, binding.label_active);
        } else if (!isActive && binding.label_inactive) {
            this._setLabelText(element, binding.label_inactive);
        } else if (style.label !== undefined) {
            this._setLabelText(element, style.label);
        } else if (elementDef.label) {
            this._setLabelText(element, elementDef.label);
        }

        // Rebuild icon+text layout if element has any icon (from feedback or base element)
        const appliedStyle = isActive ? activeStyle : inactiveStyle;
        const resolvedIcon = appliedStyle.icon || elementDef.icon || elementDef.style?.icon;
        if (resolvedIcon) {
            const iconDef = {
                ...elementDef,
                icon: appliedStyle.icon || elementDef.icon,
                icon_color: appliedStyle.icon_color || elementDef.icon_color,
            };
            this.renderElementContent(element, iconDef);
        }
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
        const displayMode = elementDef.display_mode || 'text';
        const suppressLabel = displayMode === 'image' || displayMode === 'icon_only';

        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);

        const stateValue = this.state[binding.key];
        const on = !offline && stateValue != null && binding.value !== undefined &&
            String(stateValue).toLowerCase() === String(binding.value).toLowerCase();

        // applyStyle only ever writes a property it has a value for, so going
        // off has to drop the lit colours first or a button whose own style
        // names none stays lit for good.
        this._clearStateColours(element, baseStyle);
        const style = this.getThemedStyle(elementDef.type, on
            ? { ...baseStyle, ...this._toggleOnStyle(elementDef) }
            : baseStyle);
        this.applyStyle(element, style);
        element.classList.toggle('toggle-on', on);
        // Frameless and image buttons take their fill from artwork, so the
        // class above is the whole indication there -- an outline, which no
        // inline write of ours ever clobbers.
        if (elementDef.frameless) this.applyFrameless(element);
        // Empty rather than skipped when there is nothing to put back: a tinted
        // image layer would otherwise stay lit after the button went off.
        this.updateImageTint(element, style.bg_color || '');

        // The words, only where the author asked for them. A toggle that names
        // neither label keeps whatever it is already showing: rewriting the
        // label every pass would fight a `ui.<id>.label` override and rebuild
        // an icon layout for nothing.
        const named = binding.on_label != null || binding.off_label != null;
        if (named && !suppressLabel) {
            const word = offline ? null : (on ? binding.on_label : binding.off_label);
            this._setLabelText(element, word != null && word !== '' ? word : (elementDef.label || ''));
            const icon = elementDef.icon || elementDef.style?.icon;
            if (icon) this.renderElementContent(element, elementDef);
        }
    }

    /**
     * The look a toggle takes while it is on: the accent, filled.
     *
     * The accent rather than a colour of our own, so it follows the theme and
     * the panel's own accent setting, and so an element that names its own
     * `accent_color` lights in that. Deliberately no border colour: the off
     * pass restores the element's own style, and `_clearStateColours` drops
     * exactly the two properties a state look may write (background and text)
     * -- adding a third here without teaching that helper about it is how a
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

        const setText = (text) => {
            if (b._lastText === text) return;
            b._lastText = text;
            if (useRich) {
                element.innerHTML = this._formatRichText(text);
            } else {
                element.textContent = text;
            }
        };

        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline) {
            // The label keeps its sentence and loses its number: a format of
            // "Amp draw: {value} A" reads "Amp draw: -- A". What was
            // photographed for this fault was "Amp draw: 0.00 A" on an
            // amplifier drawing 0.076 A through a port that was broken.
            setText(binding.format
                ? String(binding.format).split('{value}').join('--')
                : '--');
            return;
        }

        if (binding.condition) {
            // Normalized compare (matches feedback/visible_when), so a numeric 1
            // or boolean true matches a condition.equals of '1'/'true' instead of
            // silently failing the strict-=== check and sticking on text_false.
            const isMatch = value !== undefined && value !== null &&
                String(value).toLowerCase() === String(binding.condition.equals).toLowerCase();
            setText(isMatch ? (binding.text_true || '') : (binding.text_false || ''));
            return;
        }

        if (value === undefined || value === null) {
            setText('');
            return;
        }
        const shown = this._labelValueText(value, elementDef);
        if (binding.format) {
            // split/join replaces every {value} and treats the value literally,
            // so device values containing $-sequences (track titles, paths)
            // aren't reinterpreted the way String.replace would.
            setText(String(binding.format).split('{value}').join(shown));
        } else {
            setText(shown);
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

    evaluateSliderValue(b, force) {
        const { element, elementDef, binding, fill, valueDisplay, isVertical, outputMin, outputMax, scaleToFull, steps, unit, valueToPos, fmtValue } = b;
        // Don't yank the thumb out from under an operator who is actively
        // dragging it (or has it focused) when a device echo / another panel's
        // change arrives mid-gesture. `force` is the refusal of this panel's
        // own command, and there the focus half has to be ignored: a range
        // input keeps focus after the drag that set it, so honouring it would
        // mean the rejected value never goes back. The drag half still holds.
        if (element._dragging || (!force && document.activeElement === element)) return;
        const rawValue = this.state[binding.key];
        const offline = this._bindingOffline(b);
        if (b._lastSliderRaw === rawValue && b._lastSliderOffline === offline) return;
        b._lastSliderRaw = rawValue;
        b._lastSliderOffline = offline;
        this._markBindingAvailability(b, offline);
        // The input runs in the position domain (0..steps); display min/max come
        // from the element definition, not the input's own min/max.
        const min = parseFloat(elementDef.min ?? 0);
        const max = parseFloat(elementDef.max ?? 100);
        const setFill = (pct) => {
            if (!fill) return;
            if (isVertical) fill.style.height = pct + '%';
            else fill.style.width = pct + '%';
        };
        if (offline) {
            // Nothing to read: the fill is emptied, the thumb is hidden by CSS
            // (a thumb at the bottom is a claim of minimum) and the readout
            // says so rather than printing the range's floor as a value.
            element.value = valueToPos(min);
            element.removeAttribute('aria-valuetext');
            setFill(0);
            if (valueDisplay) valueDisplay.textContent = this._unknownValueText(unit);
            return;
        }
        if (rawValue === undefined || rawValue === null) {
            // Bound key deleted — return the slider to its minimum (bottom).
            element.value = valueToPos(min);
            element.setAttribute('aria-valuetext', fmtValue(min));
            setFill(0);
            if (valueDisplay) valueDisplay.textContent = fmtValue(min);
            return;
        }
        const displayValue = this._reverseScale(Number(rawValue), min, max, outputMin, outputMax, scaleToFull);
        const pos = valueToPos(displayValue);
        element.value = pos;
        element.setAttribute('aria-valuetext', fmtValue(displayValue));
        setFill(steps > 0 ? (pos / steps) * 100 : 0);
        if (valueDisplay) valueDisplay.textContent = fmtValue(displayValue);
    }

    evaluateSelectValue(b) {
        const { element, binding } = b;
        const value = this.state[binding.key];
        const offline = this._bindingOffline(b);
        this._markBindingAvailability(b, offline);
        if (offline) {
            // No selection at all rather than a stale one or the first option:
            // a dropdown reading "HDMI 2" for a switcher nobody can reach is
            // the same confident wrong answer a fader gives.
            element.selectedIndex = -1;
            return;
        }
        if (value === undefined || value === null) {
            // Bound key deleted — fall back to the first option rather than
            // pinning the last device selection.
            if (element.options.length) element.selectedIndex = 0;
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
        const lockCode = this.uiSettings?.lock_code;
        const overlay = document.getElementById('lock-overlay');
        if (!lockCode) {
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
        const lockCode = this.uiSettings?.lock_code;
        if (!lockCode) return;

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

        const tryUnlock = () => {
            if (input.value === lockCode) {
                overlay.remove();
                this.locked = false;
                this.resetIdleTimer();
            } else {
                error.textContent = 'Incorrect PIN';
                input.value = '';
                input.focus();
                setTimeout(() => { error.textContent = ''; }, 2000);
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
            if (this.uiSettings?.lock_code) {
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

        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';

        return fetch(`${basePath}/api/themes/${encodeURIComponent(themeId)}`)
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
        }
        if (settings.font_family) {
            document.body.style.fontFamily = settings.font_family;
        }

        // Store element defaults for use in rendering
        this.themeElementDefaults = theme.element_defaults || {};
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
                this.currentTheme?.variables,
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
            const imgUrl = bg.image.startsWith('assets://')
                ? this.resolveAssetUrl(bg.image)
                : bg.image;
            const opacity = bg.image_opacity ?? 1;
            const size = bg.image_size || 'cover';
            const position = bg.image_position || 'center';

            const imgLayer = document.createElement('div');
            imgLayer.className = 'panel-page-bg-image';
            imgLayer.style.position = 'absolute';
            imgLayer.style.inset = '0';
            imgLayer.style.zIndex = '0';
            imgLayer.style.pointerEvents = 'none';
            imgLayer.style.backgroundImage = `url("${this._sanitizeCssUrl(imgUrl)}")`;
            imgLayer.style.backgroundSize = this._sanitizeCssValue(size);
            imgLayer.style.backgroundPosition = this._sanitizeCssValue(position);
            imgLayer.style.backgroundRepeat = 'no-repeat';
            imgLayer.style.opacity = String(parseFloat(opacity) || 1);
            gridEl.prepend(imgLayer);
        }
        // Gradient overlay (renders on TOP of image)
        if (bg.gradient && bg.gradient.from && bg.gradient.to) {
            const g = bg.gradient;
            const angle = g.angle ?? 180;
            const gradLayer = document.createElement('div');
            gradLayer.className = 'panel-page-bg-gradient';
            gradLayer.style.position = 'absolute';
            gradLayer.style.inset = '0';
            gradLayer.style.zIndex = '1';
            gradLayer.style.pointerEvents = 'none';
            gradLayer.style.background = `linear-gradient(${parseFloat(angle) || 180}deg, ${this._sanitizeCssValue(g.from)}, ${this._sanitizeCssValue(g.to)})`;
            gridEl.prepend(gradLayer);
        }
    }

    getThemedStyle(elementType, elementStyle) {
        const defaults = this.themeElementDefaults[elementType] || {};
        return { ...defaults, ...elementStyle };
    }

    applyStyle(el, style) {
        if (!style) return;

        // Background: gradient takes priority over solid color
        if (style.background_gradient && style.background_gradient.from && style.background_gradient.to) {
            const g = style.background_gradient;
            const angle = g.angle != null ? g.angle : 180;
            el.style.background = `linear-gradient(${parseFloat(angle) || 180}deg, ${this._sanitizeCssValue(g.from)}, ${this._sanitizeCssValue(g.to)})`;
        } else if (style.bg_color) {
            el.style.backgroundColor = style.bg_color;
        }

        // Background image (assets:// resolved by panel, see resolveAssetUrl)
        if (style.background_image) {
            const url = this.resolveAssetUrl(style.background_image);
            const size = style.background_size || 'cover';
            const pos = style.background_position || 'center';
            const opacity = style.background_opacity != null ? style.background_opacity : 1;

            if (opacity < 1) {
                // Use a child div for opacity control (can't opacity just the bg image)
                el.style.position = 'relative';
                const bgLayer = document.createElement('div');
                bgLayer.style.position = 'absolute';
                bgLayer.style.inset = '0';
                bgLayer.style.zIndex = '0';
                bgLayer.style.pointerEvents = 'none';
                bgLayer.style.backgroundImage = `url("${this._sanitizeCssUrl(url)}")`;
                bgLayer.style.backgroundSize = this._sanitizeCssValue(size === 'stretch' ? '100% 100%' : size);
                bgLayer.style.backgroundPosition = this._sanitizeCssValue(pos);
                bgLayer.style.backgroundRepeat = 'no-repeat';
                bgLayer.style.opacity = String(parseFloat(opacity) || 1);
                el.prepend(bgLayer);
                // Ensure content is above the bg layer
                Array.from(el.children).forEach(child => {
                    if (child !== bgLayer && !child.style.zIndex) {
                        child.style.position = 'relative';
                        child.style.zIndex = '1';
                    }
                });
            } else {
                el.style.backgroundImage = `url("${this._sanitizeCssUrl(url)}")`;
                el.style.backgroundSize = this._sanitizeCssValue(size === 'stretch' ? '100% 100%' : size);
                el.style.backgroundPosition = this._sanitizeCssValue(pos);
                el.style.backgroundRepeat = 'no-repeat';
            }
        }

        if (style.text_color) el.style.color = style.text_color;
        if (style.font_size) el.style.fontSize = style.font_size + 'rem';
        if (style.font_weight) el.style.fontWeight = style.font_weight;
        if (style.border_radius != null) el.style.borderRadius = style.border_radius + 'rem';

        // Text alignment → maps to justify-content (fixes flexbox override bug)
        if (style.text_align) {
            const alignMap = { left: 'flex-start', center: 'center', right: 'flex-end' };
            el.style.justifyContent = alignMap[style.text_align] || 'center';
            el.style.textAlign = style.text_align;
        }

        // Vertical alignment → maps to align-items
        if (style.vertical_align) {
            const vMap = { top: 'flex-start', center: 'center', bottom: 'flex-end' };
            el.style.alignItems = vMap[style.vertical_align] || 'center';
        }

        // Border — only set properties that are explicitly in the style.
        // Elements that rely on CSS variables for border-color (e.g. buttons
        // using --panel-button-border) must not be clobbered by a fallback.
        if (style.border_width) {
            // Hairlines stay visible. A blanket rem would turn a 1px border
            // into 0.0714rem, which on a small phone resolves to a third of a
            // pixel and can render as nothing at all -- while a deliberately
            // thick border should still scale with the panel.
            el.style.borderWidth = `max(1px, ${style.border_width}rem)`;
            el.style.borderStyle = style.border_style || 'solid';
            if (style.border_color) {
                el.style.borderColor = style.border_color;
            }
        }

        // Box shadow with presets
        if (style.box_shadow && style.box_shadow !== 'none') {
            const shadowPresets = {
                sm: '0 0.1429rem 0.2857rem rgba(0,0,0,0.2)',
                md: '0 0.2857rem 0.5714rem rgba(0,0,0,0.3)',
                lg: '0 0.5714rem 1.1429rem rgba(0,0,0,0.4)',
                glow: `0 0 0.8571rem ${style.text_color || 'rgba(33,150,243,0.5)'}`,
                inset: 'inset 0 0.1429rem 0.2857rem rgba(0,0,0,0.3)',
            };
            el.style.boxShadow = shadowPresets[style.box_shadow] || style.box_shadow;
        }

        // Margin
        if (style.margin != null) {
            const mv = style.margin_vertical != null ? style.margin_vertical : style.margin;
            const mh = style.margin_horizontal != null ? style.margin_horizontal : style.margin;
            el.style.margin = `${mv}rem ${mh}rem`;
        } else {
            if (style.margin_vertical != null) {
                el.style.marginTop = style.margin_vertical + 'rem';
                el.style.marginBottom = style.margin_vertical + 'rem';
            }
            if (style.margin_horizontal != null) {
                el.style.marginLeft = style.margin_horizontal + 'rem';
                el.style.marginRight = style.margin_horizontal + 'rem';
            }
        }

        // Padding
        if (style.padding != null) {
            const pv = style.padding_vertical != null ? style.padding_vertical : style.padding;
            const ph = style.padding_horizontal != null ? style.padding_horizontal : style.padding;
            el.style.padding = `${pv}rem ${ph}rem`;
        } else {
            if (style.padding_vertical != null) {
                el.style.paddingTop = style.padding_vertical + 'rem';
                el.style.paddingBottom = style.padding_vertical + 'rem';
            }
            if (style.padding_horizontal != null) {
                el.style.paddingLeft = style.padding_horizontal + 'rem';
                el.style.paddingRight = style.padding_horizontal + 'rem';
            }
        }

        // Typography
        if (style.text_transform) el.style.textTransform = style.text_transform;
        if (style.letter_spacing) el.style.letterSpacing = style.letter_spacing + 'rem';
        if (style.line_height) el.style.lineHeight = String(style.line_height);

        // White space (multi-line labels)
        if (style.white_space) el.style.whiteSpace = style.white_space;

        // Custom transition duration
        if (style.transition_duration != null) {
            el.style.transitionDuration = style.transition_duration + 'ms';
        }

        // Overflow
        if (style.overflow) el.style.overflow = style.overflow;

        // Opacity (also handled by ui.* overrides, but allow static setting)
        if (style.opacity != null) el.style.opacity = String(style.opacity);

        // Per-element CSS custom properties for accent/surface colors.
        // These override theme-level --panel-accent / --panel-surface for
        // sub-elements (thumb, fill, handle, track) that reference --el-*.
        if (style.accent_color) el.style.setProperty('--el-accent', style.accent_color);
        if (style.track_color) {
            el.style.setProperty('--el-surface', style.track_color);
            el.style.setProperty('--el-surface-border', style.track_color);
        }
    }

    _sanitizeCssValue(value) {
        // These values are interpolated into a CSS declaration (gradient stops,
        // background-size/position). Strip everything that could break out of
        // the value and inject another declaration or a url() — semicolons,
        // braces, quotes, comments, url()/image-set(), expression(), and the
        // usual scheme tricks. Parentheses and commas are kept so legitimate
        // color functions like rgb(0,0,0) / hsl(...) still work.
        if (typeof value !== 'string') return String(value ?? '');
        return value.replace(/expression\s*\(/gi, '')
                     .replace(/javascript\s*:/gi, '')
                     .replace(/behavior\s*:/gi, '')
                     .replace(/@import/gi, '')
                     .replace(/url\s*\(/gi, '')
                     .replace(/image-set\s*\(/gi, '')
                     .replace(/\/\*/g, '')
                     .replace(/\*\//g, '')
                     .replace(/[;{}"']/g, '')
                     .replace(/\\/g, '')
                     .replace(/[\r\n]/g, '');
    }

    _sanitizeCssUrl(url) {
        // The result is interpolated into url("...") so it must not contain
        // characters that close the string/paren, and must use an allowed
        // scheme (http:, https:, data:image/, or relative/no-scheme).
        if (typeof url !== 'string') return '';
        const trimmed = url.trim();
        const lower = trimmed.toLowerCase();
        if (lower.startsWith('javascript:') || lower.startsWith('vbscript:')) return '';
        if (lower.startsWith('data:') && !lower.startsWith('data:image/')) return '';
        const scheme = trimmed.match(/^([a-z][a-z0-9+.-]*):/i);
        if (scheme) {
            const s = scheme[1].toLowerCase();
            if (s !== 'http' && s !== 'https' && s !== 'data') return '';
        }
        // Percent-encode the few characters that could escape the url("...")
        // context. Structural URL characters (:/?&=%#) are left intact, so real
        // asset URLs keep working; spaces and quotes become %20/%22 etc. We map
        // by char code rather than encodeURIComponent because the latter leaves
        // ( ) ' unescaped (they're "unreserved marks") — exactly the breakout
        // characters we need to neutralize.
        return trimmed.replace(/[\\"'()\s]/g,
            (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0'));
    }

    resolveAssetUrl(ref) {
        if (!ref) return '';
        if (ref.startsWith('assets://')) {
            // Derive base path so asset URLs route through cloud tunnel
            const pathParts = location.pathname.split('/panel');
            const basePath = pathParts[0] || '';
            // Encode the filename so legal-but-special names (spaces etc.,
            // allowed by the server's asset FILENAME_PATTERN) resolve. Asset
            // names are flat filenames, so encodeURIComponent is safe and
            // matches the programmer's getAssetUrl.
            const name = encodeURIComponent(ref.slice('assets://'.length));
            return `${basePath}/api/projects/default/assets/${name}`;
        }
        return ref;
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
     * Set or replace an element's label text without touching element children
     * (icons, image layer). Removes existing text nodes and appends a new one.
     */
    _setLabelText(el, text) {
        this._removeTextNodes(el);
        if (text != null && text !== '') {
            el.appendChild(document.createTextNode(String(text)));
        }
    }

    /**
     * Hide button chrome (bg_color, border, box_shadow) so an image acts as the button.
     * Uses only longhand CSS properties so subsequent backgroundImage assignments
     * (from applyImageEffect) aren't wiped out by a shorthand reset.
     */
    applyFrameless(el) {
        el.style.backgroundColor = 'transparent';
        el.style.backgroundImage = 'none';
        el.style.borderWidth = '0';
        el.style.borderStyle = 'none';
        el.style.borderColor = 'transparent';
        el.style.boxShadow = 'none';
    }

    /**
     * Apply a button image with optional blend mode and opacity effects.
     * Idempotent: safe to call repeatedly as state changes.
     *
     * Tint color (passed via options.tintColor) lives on the image layer, not
     * the button itself, so frameless buttons can still tint/mask without
     * depending on the visible button background. Falls back to the button's
     * current bg_color if no tintColor is given.
     */
    applyImageEffect(el, imageRef, options = {}) {
        const url = this.resolveAssetUrl(imageRef);
        if (!url) return;
        const fit = options.fit || 'cover';
        const blend = options.blend || 'none';
        const opacity = options.opacity != null ? Number(options.opacity) : 1;
        // Fall back to currentColor so mask/blend modes always render something even
        // if no bg_color is set on the element or in theme.
        // Use the explicit tintColor if given; fall back to currentColor (text color) rather than
        // reading el.style.backgroundColor, because frameless may have just set it to transparent.
        const tintColor = options.tintColor || 'currentColor';
        const sanitizedUrl = this._sanitizeCssUrl(url);
        const sizeCss = this._sanitizeCssValue(fit === 'fill' ? '100% 100%' : fit);

        // Remove any existing image layer
        const existingLayer = el.querySelector(':scope > .panel-button-image-layer');
        if (existingLayer) existingLayer.remove();

        // Clear any mask previously applied to the button itself (legacy path)
        el.style.webkitMaskImage = '';
        el.style.maskImage = '';

        const needsBlend = blend && blend !== 'none' && blend !== 'normal' && blend !== 'mask';
        const isMask = blend === 'mask';
        const needsLayer = needsBlend || isMask || opacity < 1;

        if (!needsLayer) {
            // Simple background image on the button, no effect layer
            el.style.backgroundImage = `url("${sanitizedUrl}")`;
            el.style.backgroundSize = sizeCss;
            el.style.backgroundPosition = 'center';
            el.style.backgroundRepeat = 'no-repeat';
            // Clear isolation if previously set from another render
            el.style.isolation = '';
            return;
        }

        // Image effect runs on a child layer. Use isolation + negative z-index so the
        // layer paints above the button's own background but below text/icons, without
        // needing to wrap every text node or content element.
        el.style.backgroundImage = 'none';
        el.style.position = 'relative';
        el.style.isolation = 'isolate';

        const layer = document.createElement('div');
        layer.className = 'panel-button-image-layer';
        layer.style.position = 'absolute';
        layer.style.inset = '0';
        layer.style.pointerEvents = 'none';
        layer.style.zIndex = '-1';
        if (opacity < 1) layer.style.opacity = String(opacity);

        if (isMask) {
            // Mask mode: tint color fills the image shape. Button chrome untouched.
            layer.style.backgroundColor = tintColor;
            layer.style.webkitMaskImage = `url("${sanitizedUrl}")`;
            layer.style.maskImage = `url("${sanitizedUrl}")`;
            layer.style.webkitMaskSize = sizeCss;
            layer.style.maskSize = sizeCss;
            layer.style.webkitMaskPosition = 'center';
            layer.style.maskPosition = 'center';
            layer.style.webkitMaskRepeat = 'no-repeat';
            layer.style.maskRepeat = 'no-repeat';
        } else if (needsBlend) {
            // Blend mode: layer holds both image and tint color, composited via background-blend-mode.
            // This makes the tint self-contained on the layer so frameless buttons still tint.
            layer.style.backgroundImage = `url("${sanitizedUrl}")`;
            layer.style.backgroundColor = tintColor;
            layer.style.backgroundSize = sizeCss;
            layer.style.backgroundPosition = 'center';
            layer.style.backgroundRepeat = 'no-repeat';
            layer.style.backgroundBlendMode = blend;
        } else {
            // Opacity-only: plain image layer, no blend
            layer.style.backgroundImage = `url("${sanitizedUrl}")`;
            layer.style.backgroundSize = sizeCss;
            layer.style.backgroundPosition = 'center';
            layer.style.backgroundRepeat = 'no-repeat';
        }
        el.prepend(layer);
    }

    /**
     * Update just the tint color on an existing image layer without recreating it.
     * Called during feedback state changes to retint in place.
     */
    updateImageTint(el, tintColor) {
        const layer = el.querySelector(':scope > .panel-button-image-layer');
        if (!layer) return;
        // Only layers with a mask or blend mode use tint color
        if (layer.style.maskImage || layer.style.webkitMaskImage || layer.style.backgroundBlendMode) {
            layer.style.backgroundColor = tintColor;
        }
    }

    /** `size` is in rem, so an icon scales with the rest of the panel. */
    renderIcon(iconName, size, color) {
        if (!iconName) return null;

        // Custom icon from asset system
        if (iconName.startsWith('assets://')) {
            const img = document.createElement('img');
            img.src = this.resolveAssetUrl(iconName);
            img.style.width = `${size}rem`;
            img.style.height = `${size}rem`;
            img.style.flexShrink = '0';
            if (color) img.style.filter = `brightness(0) saturate(100%)`;
            return img;
        }

        // Built-in Lucide icon from sprite sheet
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        // Derive sprite URL relative to panel location so tunneled access works
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        const iconUrl = `${basePath}/panel/icons.svg#${iconName}`;
        use.setAttribute('href', iconUrl);
        use.setAttributeNS('http://www.w3.org/1999/xlink', 'href', iconUrl);
        svg.appendChild(use);
        // Sized in CSS rather than the width/height attributes: SVG attributes
        // are user units and can't carry rem, and CSS wins over them anyway.
        svg.style.width = `${size}rem`;
        svg.style.height = `${size}rem`;
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
