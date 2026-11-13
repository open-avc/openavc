/**
 * OpenAVC Panel UI — Phase 1
 *
 * Connects to the backend via WebSocket, renders the touch panel UI
 * from JSON definitions, and sends user interactions back to the server.
 */

import { NOT_APPROVED_CLOSE, PanelAccess } from './modules/access.js';
import { assetUrl, basePath } from './modules/base_path.js';
import { lookPictures, pickLook, withTwoStateWords } from './modules/looks.js';
import {
    applyStyle, clearStateColours, dropFadedBackground, updateImageTint,
} from './modules/paint.js';
import {
    clearMatrixOverlays, createMatrix, endMatrixGestures,
} from './modules/matrix.js';
import { FaderControl, SliderControl, endFaderDrags } from './modules/level.js';
import {
    FrameStatusWatch, drawFrame, fetchExtToken, framePlaceholder, loadPanelElementExtensions,
    pushToFrames, releaseFrames, releaseFramesIn,
} from './modules/frames.js';
import { ListControl, rowsFromState, showListSelection } from './modules/list.js';
import { GaugeControl } from './modules/gauge.js';
import { LevelMeterControl } from './modules/meter.js';
import { ClockControl, ClockTicker } from './modules/clock.js';
import { KeypadControl } from './modules/keypad.js';
import { TextEditor } from './modules/text_edit.js';
import { PanelLock } from './modules/lock.js';
import { SoundPlayer } from './modules/sound.js';
import { PanelTheme, ThemeStudioPicker } from './modules/theme.js';
import { UiOverrides } from './modules/overrides.js';
import { Availability, changeReaches, devicesFlipped } from './modules/availability.js';
import { FailureBand } from './modules/failure_band.js';
import { MacroRuns } from './modules/macro_runs.js';
import { ProjectStylesheet } from './modules/stylesheet.js';
import { attachPress, pressOf } from './modules/press.js';
import {
    drawFace, drawPicture, lookWords, redrawPicture, repaint, showsWords,
} from './modules/button.js';
import { toggleLit, toggleNamesWords, toggleWords } from './modules/toggle.js';
import { litStyle } from './modules/lit.js';
import { richText, valueWords } from './modules/label.js';
import { showPicture } from './modules/image.js';
import {
    drawNavFace, markCurrent, navLook, navWords, showsItsPage,
} from './modules/nav.js';
import { drawIcon, iconNode, removeWords, showWords } from './modules/words.js';
import { isShown, visibilityOf } from './modules/visibility.js';
import {
    REM_BASE_PX, applyCoordinateSpaces, applyLivePlacements, applyVminOverride, drawElementTree, drawSnapRuler,
    masterOrientation, masterPlacement, placeElement, selectLayout, viewportOrientation,
} from './modules/layout.js';
import {
    drawOverlayFrame, fallbackPage, isCustomPage, namesPage, opensOver, pageBehind, pageDrawsNothing,
    pageRunsAuthorMarkup, rememberPage,
} from './modules/pages.js';
import {
    carries, definitionIn, demoStateOf, isBuilderFrame, listenToBuilder, postToBuilder, startingTextSizes,
} from './modules/builder_link.js';
import {
    drawSelect, drawTextInput, showSelectLook, showSelectValue, showTextValue,
} from './modules/fields.js';
import { installSessionBridge } from './modules/session.js';
import { EVALUATORS, RENDERERS } from './modules/dispatch.js';

// The Programmer's sign-in, carried on every /api request and the socket
// when the IDE embeds this panel; installed before anything fetches.
installSessionBridge(window);

/**
 * What the panel does with each frame the server sends, by its `type`: the
 * whole state, a change, keys going away, the project and its ui/ files, a
 * page change, a macro run's progress, and a refusal or a failed ack.
 */
const FRAMES = new Map([
    ['ping', (app) => app.send({ type: 'pong' })],
    ['state.snapshot', (app, msg) => app._takeSnapshot(msg)],
    ['state.update', (app, msg) => app._takeChanges(msg)],
    ['state.delete', (app, msg) => app._dropKeys(msg)],
    ['ui.definition', (app, msg) => app._takeDefinition(msg)],
    ['ui.files', (app, msg) => app._takeFilesVersion(msg)],
    ['ui.navigate', (app, msg) => { if (msg.page_id) app.navigateToPage(msg.page_id); }],
    ['macro.started', (app, msg) => app.macroRuns.started(msg)],
    ['macro.progress', (app, msg) => app.macroRuns.progress(msg)],
    ['macro.step_error', (app, msg) => app.macroRuns.stepFailed(msg)],
    ['macro.completed', (app, msg) => app.macroRuns.ended(msg)],
    ['macro.error', (app, msg) => app.macroRuns.ended(msg)],
    ['macro.cancelled', (app, msg) => app.macroRuns.ended(msg)],
    ['error', (app, msg) => app._takeRefusal(msg)],
    ['command.ack', (app, msg) => app._takeAck(msg)],
    ['state.set.ack', (app, msg) => app._takeAck(msg)],
]);

/**
 * A Container's caption at its `label_position`: against the top or the
 * bottom edge, and at the left, the middle or the right.
 */
function placeGroupCaption(label, labelPos) {
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
    return label;
}

/**
 * Forget what a binding last drew. A binding's renderer remembers its last
 * value on the binding (`_last*`) and skips a write that would change
 * nothing; a redraw that has to put the control back whatever is on screen
 * forgets it first.
 */
function forgetDrawn(b) {
    for (const k of Object.keys(b)) {
        if (k.startsWith('_last')) delete b[k];
    }
}

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
        this.embedded = isBuilderFrame(params, this.editMode);
        // An embedder pins the type scale by handing us the preset's vmin in px.
        applyVminOverride(params.get('vmin'));
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
        this.snapshotReceived = false;
        this.root = document.getElementById('panel-root');
        this.statusEl = document.getElementById('connection-status');
        this.bindings = [];          // Active bindings to evaluate on state change
        this.elementMap = {};        // element_id -> {el, elementDef} for ui.* overrides
        // Whether what a control shows can be believed and whether a press on
        // it can land, and Preview's copy of what each press reaches
        // (modules/availability.js).
        this.availability = new Availability(this._availabilityHost());
        // Editing words in place on the design canvas (modules/text_edit.js):
        // the edit in flight, and the gate both of its doors ask.
        this._textEditor = new TextEditor(this._textEditHost());
        // ui.<id>.* overrides a script or macro sets (modules/overrides.js).
        this._overrides = new UiOverrides(this._overridesHost());
        this.holdTimers = {};        // element_id -> interval for hold-repeat mode
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) this._endAllHolds();
        });
        this.debounceTimers = [];    // Track all debounce timeouts for cleanup
        this._pluginMessageHandlers = new Set(); // Track all plugin iframe message handlers
        this._clocks = new ClockTicker();  // Every clock on screen, one interval; meeting starts outlive a redraw
        this._pendingBindingKeys = null; // Batched binding keys for rAF
        this._bindingRafId = null;       // requestAnimationFrame ID
        this.overlayStack = [];      // Stack of overlay page IDs (newest on top)
        this.pageHistory = [];       // Stack of previously-visited regular pages (newest on top) for $back
        this._previewPageId = null;  // Page the builder's preview last asked for (see _showPageAsRuntimeWould)
        this._navigatingBack = false; // Skip history push when navigateToPage is recursing for $back
        // The macro runs in flight and which of them this panel started
        // (modules/macro_runs.js).
        this.macroRuns = new MacroRuns(this._macroRunsHost());
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000; // matches the backoff cap used in onclose
        this.reconnectAttempts = 0;
        this._offline = false;           // true while the WS is disconnected
        this._lastTouchedElementId = null; // control the next failure is about
        // Why the last press did not work (modules/failure_band.js).
        this._failureBand = new FailureBand(this._failureBandHost());
        // The PIN lock and the idle timer (modules/lock.js).
        this.lock = new PanelLock(this._lockHost());
        this.access = new PanelAccess({
            hidePageStatus: () => this._hidePageStatus(),
            showPageStatus: () => this._showPageStatus(),
            endHolds: () => this._endAllHolds(),
        });
        // The theme every control draws from (modules/theme.js).
        this.theme = new PanelTheme(this._themeHost());
        // The project's own stylesheet (modules/stylesheet.js).
        this._stylesheet = new ProjectStylesheet();
        // Sound the audio player plugin asks for (modules/sound.js).
        this._sound = new SoundPlayer(this._soundHost());
        // Plugin panel_elements lookup: pluginId -> {[type]: extension}.
        // Populated once at startup via /api/plugins/extensions so per-element
        // iframe sandbox / allow attributes can apply the plugin's declared
        // permissions instead of always defaulting to allow-scripts only.
        this._pluginExtensions = {};
        // What each frame's own request answered, for its file check.
        this._frameStatus = new FrameStatusWatch();
    }

    /** What the availability rule reads from the panel, each at call time. */
    _availabilityHost() {
        return {
            state: () => this.state,
            editMode: () => this.editMode,
            elementMap: () => this.elementMap,
        };
    }

    /** What the macro runs read from the panel and ask of it, each at call time. */
    _macroRunsHost() {
        return {
            elementMap: () => this.elementMap,
            bindings: () => this.bindings,
            recordSource: (node, path) => this._textEditor.recordSource(node, path),
            showFailure: (text) => this._failureBand.show(text),
            revert: () => this._revertRefusedInteraction(null),
        };
    }

    /** What the failure band reads from the panel, each at call time. */
    _failureBandHost() {
        return {
            settings: () => this.uiSettings,
            touchedNode: () => {
                const entry = this._lastTouchedElementId
                    ? this.elementMap[this._lastTouchedElementId]
                    : null;
                return entry && entry.el;
            },
        };
    }

    /** What the in-place text editor reads from the panel, each at call time. */
    _textEditHost() {
        return {
            editMode: () => this.editMode,
            state: () => this.state,
            ui: () => this.uiDef,
            page: () => this.currentPage,
            post: (msg) => this._postToParent(msg),
        };
    }

    /** What the lock and the idle timer read from the panel, each at call time. */
    _lockHost() {
        return {
            editMode: () => this.editMode,
            uiSettings: () => this.uiSettings,
            pages: () => this.uiDef?.pages,
            offline: () => this._offline,
            activity: () => this.resetIdleTimer(),
            goIdle: (pageId) => this._returnToIdlePage(pageId),
        };
    }

    /** What the ui overrides read from the panel, each at call time. */
    _overridesHost() {
        return {
            state: () => this.state,
            holds: (el) => this._textEditor.holds(el),
            setLabelText: (node, text, path) => this._setLabelText(node, text, path),
            redraw: (elementId) => this._redrawElement(elementId),
        };
    }

    /** What the theme reads from the panel, each at call time. */
    _themeHost() {
        return {
            redraw: () => this.renderCurrentPage(),
            stateArrived: () => this.snapshotReceived,
            settings: () => this.uiDef?.settings,
        };
    }

    /** What the sound player reads from the panel, each at call time. */
    _soundHost() {
        return {
            state: () => this.state,
            editMode: () => this.editMode,
        };
    }

    // The plugin panel elements the server lists, read once at start, so a
    // plugin frame's sandbox and allow attributes take what its plugin declared.
    async _loadPluginExtensions() {
        await loadPanelElementExtensions(() => this._pluginExtensions);
    }

    // A plugin-scoped token for a frame that declared ext_auth (modules/frames.js).
    async _fetchPluginExtToken(pluginId) {
        return fetchExtToken(pluginId);
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
        this.lock.listenForActivity();
        this._sound.allowOnFirstTouch();
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

    /** To the Builder, when this panel is its canvas or Preview (modules/builder_link.js). */
    _postToParent(msg) {
        postToBuilder(this.embedded, msg);
    }

    /** What the Builder tells the canvas and Preview, from the parent on this origin only. */
    _setupEditModeListener() {
        listenToBuilder({
            project: (msg) => this._takeEditorProject(msg),
            page: (msg) => this._takeEditorPage(msg),
            // A double-click on the Builder's overlay. The iframe takes no
            // pointer events in edit mode, so the element arrives by id rather
            // than by the click landing in here.
            editText: (msg) => {
                if (this.editMode && msg.elementId) this._textEditor.begin(String(msg.elementId));
            },
            cancelText: () => this._textEditor.cancel(),
            // A drag in progress. The builder commits geometry to its store
            // once, on pointer-up, so between those it just tells us where the
            // boxes are and we move the nodes we already have -- four inline
            // styles instead of rebuilding a page sixty times a second.
            placements: (msg) => {
                if (this.editMode) applyLivePlacements(msg.placements);
            },
        });
    }

    /** A definition from the Builder (editor-init, editor-project), drawn as the canvas or Preview draws it. */
    _takeEditorProject(msg) {
        const ui = definitionIn(msg);
        if (!ui) return;
        this.uiDef = ui;
        this.uiSettings = ui.settings || {};
        this._takeEditorView(msg);
        // Edit mode has no WS, so the parent supplies state (or none).
        // Preview mode has a WS that manages state — don't clobber it.
        if (this.editMode) this.state = demoStateOf(msg);
        this.snapshotReceived = true;
        this._setupViewportListener();
        if (this.editMode) {
            this.renderCurrentPage();
        } else {
            // Preview: an overlay page has to go through the
            // navigation path or it draws as a flat page.
            this._showPageAsRuntimeWould(msg.pageId || this.currentPage);
            this.lock.reconcile();
            this.resetIdleTimer();
        }
        this._postToParent({ type: 'openavc:editor-ready' });
    }

    /** What a definition message says about the view: the page, the grid, the files, the type scale, the theme. */
    _takeEditorView(msg) {
        if (msg.pageId) this.currentPage = msg.pageId;
        if (typeof msg.showGrid === 'boolean') this._editShowGrid = msg.showGrid;
        // Bumped by the Builder every time a file lands in ui/. It rides on the
        // src of every custom control so a save is visible in the canvas
        // without reloading the IDE.
        if (carries(msg, 'uiFilesVersion')) this._uiFilesVersion = msg.uiFilesVersion;
        // The builder sizes an overlay preview to the overlay box, not the
        // screen, so it hands us the preset's vmin to keep preview type the
        // size it will be at runtime.
        if (carries(msg, 'vmin')) applyVminOverride(msg.vmin);
        // The Theme Studio's working copy of the theme, or word that there is none.
        this.theme.takeWorkingCopy(msg);
    }

    /**
     * The Builder moved to a page (editor-page). A page change can move the
     * preview between a full-screen page and an overlay box, which is exactly
     * when the vmin the builder wants us to use changes.
     */
    _takeEditorPage(msg) {
        const repinned = carries(msg, 'vmin');
        if (repinned) applyVminOverride(msg.vmin);
        if (!this.editMode) {
            this._reshowPreviewPage(msg.pageId, repinned);
            return;
        }
        const moved = !!msg.pageId && msg.pageId !== this.currentPage;
        if (moved) this.currentPage = msg.pageId;
        if (repinned || moved) this.renderCurrentPage();
    }

    /**
     * Preview always rebuilds through the runtime's own path, so a dialog
     * stays a dialog. Going through renderCurrentPage instead would tear it
     * down: that function opens by dismissing every overlay, so a bare vmin
     * nudge used to close an open dialog.
     */
    _reshowPreviewPage(pageId, repinned) {
        const want = pageId || this._previewPageId || this.currentPage;
        if (repinned || want !== this._previewPageId) this._showPageAsRuntimeWould(want);
    }

    // --- WebSocket ---

    connect() {
        // A Remote Access session that ended does not come back on a retry:
        // the reconnect scheduled before the probe answered stops here.
        if (this.access.ended) return;
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${location.host}${basePath()}/ws?client=${this.embedded ? "preview" : "panel"}&namespaces=device,var,ui,system,plugin`;

        // When embedded in the Programmer IDE, the Programmer's session rides
        // as the socket's subprotocol (modules/session.js offers it) so the
        // handshake authenticates without prompting. A panel opened on its own
        // holds no session and opens the socket with no subprotocol.
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
        this.macroRuns.claimFrom(msg);
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(msg));
        }
    }

    setConnectionStatus(connected) {
        this._showConnectionWord(connected);
        this._takeTouches(connected);
        this._offline = !connected;
        if (!connected) {
            // Don't let a previously-scheduled idle timer fire while offline —
            // it would navigate the dead panel and stack a lock screen over the
            // offline overlay. resetIdleTimer re-arms it on reconnect.
            this.lock.stopIdle();
            // Stop any in-flight notification audio; stale chimes on reconnect
            // are worse than missed ones.
            this._sound.stopAll();
        }
    }

    /** The connection's word in the corner: shown while it is lost, flashed
     *  for three seconds when it comes back. */
    _showConnectionWord(connected) {
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
    }

    /** The offline cover, and the page taking touches only while connected. */
    _takeTouches(connected) {
        const overlay = document.getElementById('offline-overlay');
        if (overlay) {
            overlay.classList.toggle('visible', !connected);
        }
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
    }

    // --- Message Handling ---

    /** A frame from the server, handled by its type (FRAMES); any other type is ignored. */
    handleMessage(msg) {
        const handle = FRAMES.get(msg.type);
        if (typeof handle === 'function') handle(this, msg);
    }

    /** The whole state: drawn at once, and the loading note taken away. */
    _takeSnapshot(msg) {
        this.state = msg.state || {};
        this.snapshotReceived = true;
        this._hideLoadingState();
        this.evaluateAllBindings();
        this._updateSimulationTag();
        // Seed audio dedupe id from current state so we don't replay
        // the most recent sound when (re)connecting.
        this._sound.seed();
    }

    /** State that changed: drawn on the next frame, told to the frames and the sound player. */
    _takeChanges(msg) {
        const changes = msg.changes || {};
        Object.assign(this.state, changes);
        this._scheduleBindingEvaluation(Object.keys(changes));
        if ('system.simulation_active' in changes) {
            this._updateSimulationTag();
        }
        for (const [k, v] of Object.entries(changes)) {
            this._notifyPluginIframes(k, v);
            this._sound.noteChange(k, v);
        }
    }

    /** Keys that went away: drawn again, and the frames told their value is gone. */
    _dropKeys(msg) {
        if (!Array.isArray(msg.keys) || msg.keys.length === 0) return;
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

    /**
     * The project the server holds. Embedded iframes take their definition
     * from the parent programmer window via postMessage, so a served one is
     * ignored there and in-flight builder edits aren't clobbered by the
     * last-saved project. All Preview keeps of it is which devices each
     * control's press reaches, which only the server can work out
     * (`ui/action_devices.py`).
     */
    _takeDefinition(msg) {
        if (this.embedded) {
            if (!this.editMode) {
                this.availability.takeServed(msg.ui);
                this.availability.markActions();
            }
            return;
        }
        this.uiDef = msg.ui;
        this.uiSettings = msg.ui?.settings || {};
        this._setupViewportListener();
        // The first draw waits for the theme (see drawWhenReady).
        if (this.snapshotReceived) this.theme.drawWhenReady();
        this.lock.reconcile();
        this.resetIdleTimer();
    }

    /**
     * A file in the project's ui/ tree changed. Nothing about the project
     * moved, so no ui.definition follows and this page would keep drawing the
     * control the browser already has -- for as long as nobody navigated away
     * and back. The version rides onto each frame's src, so a re-render
     * re-fetches.
     *
     * Embedded is the Builder's canvas, which runs its own counter over
     * postMessage and would fight this one. Same reason ui.definition is
     * ignored there.
     */
    _takeFilesVersion(msg) {
        if (this.embedded) return;
        this._uiFilesVersion = msg.version;
        if (this.snapshotReceived && pageRunsAuthorMarkup(this.uiDef, this.currentPage)) {
            this.renderCurrentPage();
        }
    }

    /**
     * A refusal: logged, said in the failure band, and the control it was
     * about put back.
     *
     * source_type is absent for connection-level errors (rate limit,
     * malformed JSON) -- printing it unconditionally put a literal
     * "undefined" in front of every one of those. The band only ever carries
     * a failure of something somebody did; a frame with no source_type is the
     * connection refusing a message before anything was read off it, and it
     * cannot name what failed because nothing was parsed. "Rate limit
     * exceeded" on a wall panel is a fact about our protocol in front of a
     * room that can do nothing with it, and it displaces the sentence that
     * would have been useful. The revert still runs, which is the half that
     * matters there: the control stops showing a value the device never took.
     */
    _takeRefusal(msg) {
        console.warn(msg.source_type
            ? `[WS Error] ${msg.source_type}: ${msg.message}`
            : `[WS Error] ${msg.message}`);
        if (msg.source_type) this._failureBand.show(msg.message);
        this._revertRefusedInteraction(msg.element_id);
    }

    /**
     * A command's or a state write's answer. A control somebody wrote
     * themselves sends its commands and its writes straight down this socket,
     * and so does the lock on a matrix row, so those failures come back on an
     * ack rather than as an error frame. Same sentence either way, and nobody
     * standing in the room can tell which kind of control they just pressed.
     */
    _takeAck(msg) {
        if (msg.success === false) {
            this._failureBand.show(msg.error);
            this._revertRefusedInteraction(null);
        }
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
            forgetDrawn(b);
            this._drawBinding(b);
        }
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
        if (this._followPageCommand(pageId)) return;

        const pages = this.uiDef?.pages || [];
        const targetPage = pages.find(p => p.id === pageId);
        if (!targetPage) return;

        // The message names a press on the page being left, and the control it
        // was avoiding is about to be gone.
        this._failureBand.dismiss();

        if (opensOver(targetPage)) {
            this.overlayStack.push(pageId);
            this.renderOverlay(targetPage);
            return;
        }
        // Regular page — remember the current one (so $back can return to it),
        // close all overlays, and switch. Not while going back: that is the
        // walk the history is for.
        if (!this._navigatingBack) rememberPage(this.pageHistory, this.currentPage, pageId);
        this.dismissAllOverlays();
        this.currentPage = pageId;
        this.renderCurrentPage();
    }

    /** `$dismiss` and `$back`, which name no page: true when `pageId` was one of them. */
    _followPageCommand(pageId) {
        // $dismiss — overlay only, no page-history fallback
        if (pageId === '$dismiss') {
            this.dismissOverlay();
            return true;
        }
        if (pageId !== '$back') return false;
        // $back — phone-style: if an overlay is open, dismiss it;
        // otherwise pop the page-history stack and go there.
        if (this.overlayStack.length > 0) this.dismissOverlay();
        else this._goBack();
        return true;
    }

    /** `$back` with no overlay open: the page before this one, if there is one. */
    _goBack() {
        const prev = this.pageHistory.pop();
        if (!prev) return; // No history → no-op
        this._navigatingBack = true;
        try { this.navigateToPage(prev); }
        finally { this._navigatingBack = false; }
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
        if (overlayEl) this._takeDownOverlay(overlayEl);
        // Clean up bindings from the dismissed overlay
        this.bindings = this.bindings.filter(b => {
            const elId = b.elementDef?.id;
            if (!elId) return true;
            return !overlayEl?.querySelector(`[data-element-id="${elId}"]`);
        });
        this._refreshPageNavs();
    }

    /** End what an overlay's controls were doing, then let it animate away. */
    _takeDownOverlay(overlayEl) {
        overlayEl.querySelectorAll('[data-element-id]').forEach(el => {
            const eid = el.dataset.elementId;
            if (eid) this._endHold(eid);
        });
        // Its clocks stop: removing the element alone would leave the
        // shared interval showing them every second forever.
        this._clocks.releaseIn(overlayEl);
        // Remove window 'message' listeners registered by plugin iframes in
        // this overlay — otherwise each open/dismiss cycle leaks one stale
        // listener (retaining the removed iframe's closure).
        releaseFramesIn(overlayEl, this._pluginMessageHandlers);
        overlayEl.classList.add('dismissing');
        overlayEl.addEventListener('transitionend', () => overlayEl.remove(), { once: true });
        // Fallback in case transitionend doesn't fire (e.g., no transition defined)
        setTimeout(() => { if (overlayEl.parentNode) overlayEl.remove(); }, 500);
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
        const { container, content } = drawOverlayFrame(page, () => this.dismissOverlay());

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
        const customFrame = isCustomPage(page) ? this._renderCustomPageFrame(page) : null;
        if (customFrame) surface.appendChild(customFrame);
        else this._renderPageElements(page, surface);

        this.theme.paintBackground(surface, page.background);
        content.appendChild(surface);
        container.appendChild(content);

        // Append to root (on top of everything)
        document.body.appendChild(container);
        applyCoordinateSpaces(surface);

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

    /**
     * Draw every element of a page onto a surface, in the arrangement this
     * viewport calls for (modules/layout.js). Shared by full pages and by
     * overlays and sidebars. Returns the layout drawn.
     */
    _renderPageElements(page, surface, opts = {}) {
        const layout = selectLayout(page, viewportOrientation());
        // Which way round the arrangement being drawn is. A tile wall's grid
        // follows it (matrixTileGridShape in modules/matrix.js), and it is the
        // LAYOUT's orientation rather than the viewport's on purpose: a page
        // with no portrait arrangement falls back to its landscape one, and the
        // review measured that arrangement as landscape. The two have to agree.
        this._drawnOrientation = layout.orientation || 'landscape';
        drawElementTree(page, layout, surface, {
            ...opts,
            element: def => this.renderElement(def),
            visibleWhen: (node, def) => this.registerVisibleWhen(node, def),
        });
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
        let last = viewportOrientation();
        const onChange = () => {
            const now = viewportOrientation();
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
     * the exception: at runtime it opens through navigateToPage (`opensOver`,
     * modules/pages.js, says which pages do), so assigning currentPage rendered
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
        // What the embedder last asked for, which is NOT `currentPage` once an
        // overlay is open -- that holds the page drawn behind it. Without this
        // the next editor-page for the same dialog reads as a move and reopens
        // it, so a vmin nudge or an unrelated edit makes it flicker.
        this._previewPageId = pageId;
        if (!target || !opensOver(target)) {
            this.currentPage = pageId;
            this.renderCurrentPage();
            return;
        }
        const behind = pageBehind(pages, this.currentPage);
        if (behind) {
            this.currentPage = behind.id;
            this.renderCurrentPage();
        }
        this.navigateToPage(pageId);
    }

    renderCurrentPage() {
        if (!this.uiDef) return;
        // Everything below is about to be replaced, including whatever node an
        // in-place edit was holding.
        this._textEditor.release();

        const pages = this.uiDef.pages || [];
        let page = pages.find(p => p.id === this.currentPage);
        if (!page) {
            if (!pages.length) {
                this._showNoPages();
                return;
            }
            page = fallbackPage(pages);
            this.currentPage = page.id;
        }

        // Clean up overlays
        this.dismissAllOverlays();
        this._stopOldPageActivity();

        const motion = this._pageMotion();
        this.root.style.setProperty('--page-transition-duration', motion.duration + 'ms');
        this._swapOutOldPage(motion);

        // Every frame on the page being replaced stops listening.
        releaseFrames(this._pluginMessageHandlers);

        clearMatrixOverlays();

        this.bindings = [];
        this.elementMap = {};
        // Frames from the page being replaced are about to be discarded, so
        // drop what they were waiting to hear. Every frame this render builds
        // registers again below.
        this._frameStatus.clear();

        // Apply theme
        this.theme.apply(this.uiDef.settings || {});

        // The project stylesheet goes on after the theme, so an author's rule
        // can override what a theme variable produced.
        this._stylesheet.apply(this.uiDef.custom_css);

        // Make root relative for absolute positioning during transitions
        this.root.style.position = 'relative';
        this.root.style.overflow = 'hidden';

        // The page surface. Elements are absolutely positioned inside it in
        // percentages, so the same design fills any screen of the same shape.
        const surface = document.createElement('div');
        surface.className = 'panel-page';
        this.theme.paintBackground(surface, page.background);
        if (motion.transition !== 'none') surface.classList.add(`page-enter-${motion.transition}`);

        this._drawPageContent(page, surface, motion);

        this.root.appendChild(surface);
        applyCoordinateSpaces(surface);
        this.evaluateAllBindings();

        // Theme Studio direct manipulation — click any element in the preview
        // to jump to its section in the editor. Hover shows an outline + type label.
        if (this.editMode) {
            new ThemeStudioPicker(surface, (msg) => this._postToParent(msg));
            this._reportTextDefaults();
        }
    }

    /** What the page being replaced was doing stops: holds, pending sends, drags, gestures, clocks. */
    _stopOldPageActivity() {
        this._endAllHolds();
        for (const t of this.debounceTimers) clearTimeout(t);
        this.debounceTimers = [];
        endFaderDrags(this.root);
        endMatrixGestures(this.root);
        this._clocks.stop();
    }

    /** A project with no pages at all says so. */
    _showNoPages() {
        this.root.textContent = '';
        const emptyMsg = document.createElement('div');
        emptyMsg.style.cssText = 'padding:2rem;text-align:center;color:var(--panel-text);opacity:0.5;';
        emptyMsg.textContent = 'No panels configured';
        this.root.appendChild(emptyMsg);
    }

    /**
     * How a page arrives: the page transition and the elements' entry
     * animation. Both are off on the design canvas, so the designer isn't
     * fighting re-entry animations on every live-preview rebuild.
     */
    _pageMotion() {
        const settings = this.uiSettings || {};
        const still = this.editMode;
        return {
            transition: still ? 'none' : (settings.page_transition || 'none'),
            duration: settings.page_transition_duration || 200,
            entryAnimation: still ? 'none' : (settings.element_entry || 'none'),
            staggerMs: settings.element_stagger_ms || 30,
            staggerStyle: settings.element_stagger_style,
        };
    }

    /** The page on screen leaves: animated out when there is a transition, else at once. */
    _swapOutOldPage({ transition, duration }) {
        const oldGrid = this.root.querySelector('.panel-page');
        if (!oldGrid || transition === 'none') {
            this.root.innerHTML = '';
            return;
        }
        oldGrid.classList.add(`page-exit-${transition}`);
        oldGrid.style.position = 'absolute';
        oldGrid.style.inset = '0';
        setTimeout(() => oldGrid.remove(), duration);
    }

    /** What draws on a page's surface, in the order that keeps each on top of the last. */
    _drawPageContent(page, surface, motion) {
        // A custom page is the author's own markup filling the screen, so there
        // is no ruler to draw on and nothing to snap to.
        const customFrame = isCustomPage(page) ? this._renderCustomPageFrame(page) : null;
        if (customFrame) {
            // BEFORE the master elements, not after: every child of .panel-page
            // gets the same z-index, so the later sibling wins, and a full-box
            // frame appended last would paint over every master element on the
            // page. A master nav bar is how somebody gets off a custom page.
            surface.appendChild(customFrame);
        } else if (this.editMode && this._editShowGrid !== false) {
            // Design-time snap overlay. The grid is a ruler now rather than a
            // container, so this only draws where things will snap to -- showing
            // or hiding it moves nothing.
            drawSnapRuler(page, surface);
        }

        this._drawMasters(page, surface);

        if (!customFrame) this._renderPageElements(page, surface, motion);
        this._noteEmptyPage(page, surface);
    }

    /**
     * A page nobody has put anything on says so, in the room's own terms.
     * Deliberately says nothing about the Programmer: this same document is
     * what a wall panel shows, and on a kiosk display the reader is an end
     * user who has no business being sent to the IDE. Not in edit mode -- the
     * Builder canvas embeds this page with `edit=1`, and an empty page there
     * is an author one drop away from their first control.
     */
    _noteEmptyPage(page, surface) {
        if (this.editMode || !pageDrawsNothing(page, this.uiDef?.master_elements)) return;
        const notice = document.createElement('div');
        notice.className = 'panel-page-empty';
        notice.textContent = 'This page has no controls yet.';
        surface.appendChild(notice);
    }

    /**
     * Master elements come before the page's own, so DOM order alone keeps
     * them behind. They used to carry an inline z-index for that, which also
     * dropped them behind the page's gradient layer. On a custom page that
     * puts them AFTER the frame, which is the other half of the same rule:
     * they draw over the author's markup, and are the way off it.
     */
    _drawMasters(page, surface) {
        for (const mEl of (this.uiDef.master_elements || [])) {
            if (!namesPage(mEl, page) || mEl.hidden) continue;
            this._drawnOrientation = masterOrientation(mEl, viewportOrientation());
            const el = this.renderElement(mEl);
            if (!el) continue;
            el.dataset.elementType = mEl.type;
            const node = placeElement(el, masterPlacement(mEl, viewportOrientation()), mEl);
            this.registerVisibleWhen(node, mEl);
            surface.appendChild(node);
        }
    }

    /**
     * Tell the Builder what size each element's text STARTS at, in rem
     * (`startingTextSizes` in modules/builder_link.js says how it is read),
     * and which words can be edited in place. The Style panel shows the size
     * as the Font Size placeholder; the Builder keeps no copy of the panel's
     * default, so this message is how it knows.
     */
    _reportTextDefaults() {
        if (window.parent === window) return;
        const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize);
        if (!rootPx) return;
        // Which elements have text an author can edit in place, and why the
        // others drawing words cannot be. Without the second, a double-click on
        // a button whose label a script is setting is a silent no-op. Runs
        // after evaluateAllBindings, so the records have settled.
        const { editableText, textRefusals } = this._textEditor.offers(this.root);
        const fontSizeRem = startingTextSizes(this.root, rootPx);
        this._postToParent({
            type: 'openavc:editor-text-defaults', fontSizeRem, editableText,
            textRefusals,
        });
    }

    /**
     * Register a visible_when binding for an element if it has one.
     * Call this after renderElement() for every element placed on a page.
     */
    registerVisibleWhen(el, element) {
        const binding = visibilityOf(element);
        if (!binding) return;
        this.bindings.push({
            type: 'visible_when',
            element: el,
            elementDef: element,
            binding,
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

    /** The control drawn by its type's renderer (modules/dispatch.js); an unknown type is logged and draws nothing. */
    _renderElementOfType(element) {
        const render = RENDERERS.get(element.type);
        if (typeof render === 'function') return render(this, element);
        console.warn('Unknown element type:', element.type);
        return null;
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
        this._textEditor.recordSource(el, ['label']);
        el._baseAccessibleName = element.label || element.id;
        el.setAttribute('aria-label', el._baseAccessibleName);

        // Its themed style, then its face (modules/button.js).
        const themedStyle = this.theme.styleFor('button', element.style);
        applyStyle(el, themedStyle);
        if (!showsWords(element)) {
            // The label still exists in data and is still the accessible name,
            // but nothing is on screen to put a caret in.
            this._textEditor.recordSource(el, null);
        }
        drawFace(el, element, themedStyle);

        // What a press sends, in its mode (modules/press.js).
        const press = pressOf(element);
        attachPress(el, element, press, this._pressHost());

        // Appearance (state-driven look) binding
        if (element.bindings?.show?.look) {
            this.bindings.push({
                type: 'feedback',
                element: el,
                elementDef: element,
                binding: element.bindings.show.look,
            });
        } else if (press.mode === 'toggle') {
            // A toggle button's own indication. Registered ONLY when the
            // element has no look binding of its own: that binding is the
            // author saying what this button looks like, and two bindings
            // writing one element's colour and label is how the two drift
            // into disagreeing.
            this.bindings.push({
                type: 'toggle_look',
                element: el,
                elementDef: element,
                binding: press.toggleLook,
            });
        }

        return el;
    }

    /** What a button's press borrows from the panel, each at call time: its
     *  send, its state, and the hold timers every page change ends. */
    _pressHost() {
        return {
            send: (msg) => this.send(msg),
            read: (key) => this.state[key],
            repeat: (elementId, ms, fn) => {
                this._endHold(elementId);
                this.holdTimers[elementId] = setInterval(fn, ms);
            },
            endHold: (elementId) => this._endHold(elementId),
        };
    }

    renderLabel(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-label';
        el.dataset.elementId = element.id;

        const text = element.text || '';
        const whiteSpace = element.style?.white_space;
        if (whiteSpace) {
            el.innerHTML = richText(text);
        } else {
            el.textContent = text;
        }
        // Rich or not, what the author owns is the source string -- markup and
        // all -- so the path is the same and the editor reveals `**bold**`.
        this._textEditor.recordSource(el, ['text']);

        applyStyle(el, this.theme.styleFor(element.type, element.style));
        drawIcon(el, element);

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
                this.macroRuns.showIdle(el, textBinding);
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
        applyStyle(el, this.theme.styleFor('status_led', element.style));

        const dot = document.createElement('div');
        dot.className = 'led-dot';
        el.appendChild(dot);

        if (element.label) el.appendChild(this._caption(el, element, 'label', 'led-label'));

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
        const themedSliderStyle = this.theme.styleFor('slider', element.style);
        applyStyle(el, themedSliderStyle);

        // Thumb size: per-element value wins, otherwise theme element_default, otherwise 44.
        el.style.setProperty(
            '--thumb-size',
            (element.thumb_size ?? themedSliderStyle.thumb_size ?? 44 / REM_BASE_PX) + 'rem',
        );

        if (element.label) el.appendChild(this._caption(el, element));
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
                return !!binding && this.availability.readingOffline(binding);
            },
        };
    }

    renderPageNav(element) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'panel-element panel-page-nav';
        el.dataset.elementId = element.id;

        // Its name and its press (modules/nav.js).
        drawNavFace(el, element, {
            wordsFrom: (node) => this._textEditor.recordSource(node, ['label']),
            // The server replies with ui.navigate to this panel only. Moving
            // here too opens overlays twice and consumes two Back steps. All
            // moves use the reply handler, including $back.
            go: (pageId) => this.send({ type: 'ui.page', page_id: pageId }),
        });

        applyStyle(el, this.theme.styleFor(element.type, element.style));
        drawIcon(el, element);

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
     * Redraw every Page Nav after an overlay closes. Opening one evaluates
     * everything, and a page change renders everything again, but closing one
     * re-renders nothing under it, so a tab to the overlay would stay lit.
     */
    _refreshPageNavs() {
        for (const b of this.bindings) {
            if (b.type === 'nav_look') this._drawBinding(b);
        }
        this.evaluateUiOverrides();
    }

    renderSelect(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-select';
        el.dataset.elementId = element.id;

        if (element.label) el.appendChild(this._caption(el, element));

        // Per-option colours from show.look.style_map (the Builder's Appearance card).
        const lookBinding = element.bindings?.show?.look;
        const styleMap = lookBinding && lookBinding.style_map ? lookBinding.style_map : null;
        const select = drawSelect(this._choiceHost(), element.id, element.options || [], styleMap);
        el.appendChild(select);
        applyStyle(el, this.theme.styleFor(element.type, element.style));

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

        if (element.label) el.appendChild(this._caption(el, element));

        const input = drawTextInput(this._choiceHost(), element.id, element.placeholder || '');
        el.appendChild(input);
        applyStyle(el, this.theme.styleFor(element.type, element.style));

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

        showPicture(el, element, element.src);
        applyStyle(el, this.theme.styleFor(element.type, element.style));

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
     * Fetch every picture a look can show (`lookPictures`, modules/looks.js),
     * once, so a state change swaps an Image's picture or a Container's
     * background at once instead of leaving it blank while it downloads.
     */
    _preloadStatePictures(look) {
        for (const ref of lookPictures(look)) {
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

        applyStyle(el, this.theme.styleFor(element.type, element.style));
        drawIcon(el, element);

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

        // Merge theme element_defaults so `item_bg` / `item_active_bg` from
        // the theme actually drive list row colors. Reading raw element.style
        // here was a long-standing bug — theme edits looked dead because
        // only per-element overrides won.
        const style = this.theme.styleFor('list', element.style);
        applyStyle(el, style);

        if (element.label) el.appendChild(this._caption(el, element, 'div', 'list-label'));

        const list = new ListControl(this._choiceHost(), element, el, style);

        // State-driven items binding
        const itemsBinding = element.bindings?.show?.items;
        if (itemsBinding) {
            this.bindings.push({
                type: 'list_items',
                element: el,
                elementDef: element,
                binding: itemsBinding,
                _list: list,
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
                _list: list,
            });
        }

        return el;
    }

    evaluateListItems(b) {
        const { renderItems, staticItems } = b._list;
        const keyPattern = b.binding.key_pattern || '';
        const found = keyPattern ? rowsFromState(this.state, keyPattern) : [];
        // The list's own items when the pattern finds nothing.
        const items = found.length > 0 ? found : staticItems;
        const hash = JSON.stringify(items);
        if (b._lastItemsHash === hash) return;
        b._lastItemsHash = hash;
        renderItems(items);
    }

    evaluateListSelected(b) {
        const value = this.state[b.binding.key];
        const offline = this.availability.markReading(b);
        showListSelection(b._list, value, offline);
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
        const style = this.theme.styleFor('matrix', element.style);
        const matrix = createMatrix(element, style, this._matrixHost(element));
        applyStyle(el, style);

        if (element.label) el.appendChild(this._caption(el, element, 'div', 'matrix-label'));
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
            offline: (key) => this.availability.keyOffline(key),
            orientation: this._drawnOrientation,
            remPx: REM_BASE_PX,
        };
    }

    evaluateMatrixRoutes(b) {
        const matrix = b._matrix;
        const destOffline = matrix.offlineDestinations();
        this.availability.markMatrixRows(matrix, destOffline);
        matrix.update(destOffline);
    }

    // --- Gauge ---

    renderGauge(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-gauge';
        el.dataset.elementId = element.id;

        // The theme's element_defaults give gauge_color and gauge_bg_color.
        const gauge = new GaugeControl(element, this.theme.styleFor('gauge', element.style), this._displayDecimals(element));
        gauge.mount(el);

        if (element.label) el.appendChild(this._caption(el, element, 'div', 'gauge-label'));

        applyStyle(el, this.theme.styleFor(element.type, element.style));

        // Value binding
        if (element.bindings?.show?.value) {
            this.bindings.push({
                type: 'gauge_value',
                element: el,
                elementDef: element,
                binding: element.bindings.show.value,
                _gauge: gauge,
            });
        }

        return el;
    }

    evaluateGaugeValue(b) {
        const raw = this.state[b.binding.key];
        const offline = this.availability.readingOffline(b);
        // Memoize: skip if unchanged (also short-circuits the undefined steady state)
        if (b._lastGaugeRaw === raw && b._lastGaugeOffline === offline) return;
        b._lastGaugeRaw = raw;
        b._lastGaugeOffline = offline;
        this.availability.markReading(b, offline);
        b._gauge.show(raw, offline);
    }

    // --- Level Meter ---

    renderLevelMeter(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-level-meter';
        el.dataset.elementId = element.id;

        // The theme's element_defaults give the green_to / yellow_to thresholds.
        const meter = new LevelMeterControl(element, this.theme.styleFor('level_meter', element.style));
        el.classList.add(meter.horizontal ? 'horizontal' : 'vertical');

        if (element.label) el.appendChild(this._caption(el, element, 'div', 'meter-label'));

        meter.mount(el);

        applyStyle(el, this.theme.styleFor(element.type, element.style));

        // Value binding
        if (element.bindings?.show?.value) {
            this.bindings.push({
                type: 'level_meter_value',
                element: el,
                elementDef: element,
                binding: element.bindings.show.value,
                _meter: meter,
            });
        }

        return el;
    }

    evaluateLevelMeterValue(b) {
        const raw = this.state[b.binding.key];
        const offline = this.availability.readingOffline(b);
        if (b._lastMeterRaw === raw && b._lastMeterOffline === offline) return;
        b._lastMeterRaw = raw;
        b._lastMeterOffline = offline;
        this.availability.markReading(b, offline);
        b._meter.show(raw, offline);
    }

    // --- Fader ---

    renderFader(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-fader';
        el.dataset.elementId = element.id;

        // The theme's element_defaults style the wrapper (bg_color, border,
        // padding, shadow) and say whether it shows its value and its scale.
        const style = this.theme.styleFor('fader', element.style);
        // Filled in below when the fader has a value binding: the end of a
        // drag asks whether that binding's device is reachable.
        let faderBinding = null;
        const fader = new FaderControl(element, style, this._levelHost(element.id, () => faderBinding));
        el.classList.add(fader.horizontal ? 'horizontal' : 'vertical');
        applyStyle(el, style);

        if (element.label) el.appendChild(this._caption(el, element, 'div', 'fader-label'));
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
        const offline = this.availability.readingOffline(b);
        // Connectivity is part of the memo: the value can be identical either
        // side of a device going away, and what it is worth is not.
        if (b._lastFaderRaw === raw && b._lastFaderOffline === offline) return;
        b._lastFaderRaw = raw;
        b._lastFaderOffline = offline;
        this.availability.markReading(b, offline);
        b._fader.show(raw, offline);
    }

    // --- Group ---

    renderGroup(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-group';
        el.dataset.elementId = element.id;

        const labelPos = element.label_position || 'top-left';

        if (element.label) {
            el.appendChild(placeGroupCaption(this._caption(el, element, 'div', 'group-label'), labelPos));
        }

        applyStyle(el, this.theme.styleFor(element.type, element.style));

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

        const clock = new ClockControl(element, (key) => this.state[key], this._clocks);
        clock.mount(el);

        applyStyle(el, this.theme.styleFor(element.type, element.style));

        this._clocks.add(el, clock);
        return el;
    }

    // --- Keypad ---

    renderKeypad(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-keypad';
        el.dataset.elementId = element.id;

        const keypad = new KeypadControl(element, {
            send: (frame) => this.send(frame),
            keepTimer: (timer) => this.debounceTimers.push(timer),
            icon: (name) => iconNode(name),
        });

        applyStyle(el, this.theme.styleFor(element.type, element.style));

        if (element.label) el.appendChild(this._caption(el, element, 'div', 'keypad-label'));

        keypad.mount(el);
        return el;
    }

    // --- Iframe elements: plugin panels and custom controls ---
    //
    // Two element types render an author's own web page inside one element's
    // box, drawn and bridged by one frame (modules/frames.js). Each renderer
    // says only where its page comes from, what it may see on its own, and
    // (for a plugin) whether it needs an auth token for its own routes.

    renderPluginElement(element) {
        const pluginId = element.plugin_id;
        const pluginType = element.plugin_type;

        // Validate pluginId/pluginType format (alphanumeric, underscores, hyphens only)
        const validIdPattern = /^[a-zA-Z0-9_-]+$/;
        if (!pluginId || !pluginType || !validIdPattern.test(pluginId) || !validIdPattern.test(pluginType)) {
            return framePlaceholder(this._frameHost(), element, { styleType: 'plugin', label: 'Plugin element (unconfigured)' });
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

        return drawFrame(this._frameHost(), element, {
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
            return framePlaceholder(this._frameHost(), element, { styleType: 'custom', label: 'Custom control (no file chosen)' });
        }
        const src = file.split('/').map(encodeURIComponent).join('/');
        // The designer re-renders this page whenever the author saves a file
        // into ui/, and the browser would otherwise hand back the copy it
        // already has. The version rides in from the Builder; at runtime there
        // is none and the URL is the plain one.
        const bust = this._uiFilesVersion ? `?v=${encodeURIComponent(this._uiFilesVersion)}` : '';

        return drawFrame(this._frameHost(), element, {
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
     *  nothing about the event itself crosses. That is the whole guard on a
     *  frame's idle reset (modules/frames.js) -- a message from a frame the user is not in must not
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

    /** A custom PAGE is a custom control sized to the page.
     *
     *  Same frame, same sandbox, same bridge, same grant -- the only difference
     *  is the box, and `placeElement` with no placement is already the full
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
        return placeElement(el, null, asElement);
    }

    /** What a list, a dropdown or a text field asks of the panel (modules/list.js,
     *  modules/fields.js), read when it asks: tests replace `send` on the app,
     *  and a page redraw replaces the timers' list. */
    _choiceHost() {
        const app = this;
        return {
            get state() { return app.state; },
            send: (msg) => app.send(msg),
            keepTimer: (timer) => app.debounceTimers.push(timer),
            remBasePx: REM_BASE_PX,
        };
    }

    /** What a frame asks of the panel (modules/frames.js), read when it asks:
     *  tests replace these on the app. */
    _frameHost() {
        const app = this;
        return {
            get state() { return app.state; },
            get editMode() { return app.editMode; },
            get handlers() { return app._pluginMessageHandlers; },
            get frameStatus() { return app._frameStatus; },
            send: (msg) => app.send(msg),
            touched: (elementId) => { app._lastTouchedElementId = elementId; },
            noteActivity: (iframe) => { if (app._frameHasFocus(iframe)) app.resetIdleTimer(); },
            fetchToken: (pluginId) => app._fetchPluginExtToken(pluginId),
            postToParent: (msg) => app._postToParent(msg),
            themedStyle: (type, style) => app.theme.styleFor(type, style),
            register: (id, entry) => { app.elementMap[id] = entry; },
        };
    }

    // Send state update to plugin iframes
    _notifyPluginIframes(key, value) {
        pushToFrames(this.elementMap, key, value);
    }

    // --- Bindings ---

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
     * Draw a button's words, or take them off where its display mode draws
     * none. Only the text goes: the picture layer and an icon are children of
     * their own. `words` is [text, where an edit of it goes], or null to leave
     * the words as they are.
     */
    _drawButtonWords(element, elementDef, words) {
        if (!showsWords(elementDef)) {
            removeWords(element);
            this._textEditor.recordSource(element, null);
        } else if (words) {
            this._setLabelText(element, ...words);
        }
    }

    /**
     * Draw again the icon a look names, else the control's own, beside the
     * words: drawIcon (modules/words.js) rebuilds the icon and text layout from the
     * words on the control, so they are written first, and draws nothing on a
     * control with no icon. With no look (`appearance` null) the control's own
     * definition is drawn as it is.
     */
    _drawLookIcon(element, elementDef, appearance) {
        drawIcon(element, appearance ? this._withLookIcon(elementDef, appearance) : elementDef);
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
        // Which devices just changed reachability: a binding reading one of
        // them is drawn again even though its own key did not move.
        const flipped = devicesFlipped(changedKeys);
        for (const b of this.bindings) {
            try {
                if (changedKeys && !changeReaches(b, changedKeys, flipped)) continue;
                this._evaluateBinding(b);
            } catch (e) {
                console.error('Binding error:', e);
            }
        }

        // Controls whose ACTIONS cannot land. Not a binding, so it is not in
        // the loop above.
        this.availability.markActions();

        // Apply ui.* state overrides (set by macros/scripts)
        // These take priority over feedback bindings for direct control.
        this.evaluateUiOverrides();
    }

    /** Draw one binding from current state; one that fails is logged, and the others still draw. */
    _drawBinding(b) {
        try { this._evaluateBinding(b); }
        catch (e) { console.error('Binding error:', e); }
    }

    /**
     * Draw one binding from current state, by its type's evaluator
     * (modules/dispatch.js); a type with no evaluator draws nothing.
     *
     * Shared with the refusal revert, which needs exactly this and none of the
     * incremental filtering around it. Each renderer protects active gestures.
     */
    _evaluateBinding(b) {
        const evaluate = EVALUATORS.get(b.type);
        if (typeof evaluate === 'function') evaluate(this, b);
    }

    /** Draw every control's ui.<id>.* overrides from current state (modules/overrides.js). */
    evaluateUiOverrides() {
        this._overrides.apply(this.elementMap);
    }

    /** Show or hide a control by its visible_when (modules/visibility.js). */
    evaluateVisibleWhen(b) {
        b.element.style.display = isShown(b.binding, this.state) ? '' : 'none';
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

        const offline = this.availability.markReading(b);
        if (offline) {
            // Back to the label's own colours and its own words, asserting no
            // state. Where a `show.value` binding owns the text instead, it is
            // left alone: that binding has its own unknown form and writing
            // here would clobber it a moment after it arrived.
            clearStateColours(element, baseStyle);
            applyStyle(element, this.theme.styleFor(elementDef.type, baseStyle));
            if (!elementDef.bindings?.show?.value && elementDef.text !== undefined) {
                this._setLabelText(element, String(elementDef.text), ['text']);
            }
            return;
        }

        const { appearance, wordsPath } = withTwoStateWords(binding, pickLook(binding, stateValue));

        clearStateColours(element, this.theme.styleFor(elementDef.type, baseStyle));
        applyStyle(element, this.theme.styleFor(elementDef.type, { ...baseStyle, ...appearance }));

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
        const rest = this.theme.styleFor(elementDef.type, elementDef.style || {});

        const offline = this.availability.markReading(b);

        const appearance = offline ? {} : pickLook(binding, this.state[binding.key]).appearance;

        clearStateColours(element, rest);
        applyStyle(element, { ...rest, ...appearance });
        showPicture(element, elementDef, appearance.button_image || elementDef.src);
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
        const rest = this.theme.styleFor(elementDef.type, elementDef.style || {});
        const offline = this.availability.markReading(b);
        const look = navLook(binding, this.state[binding.key], offline);

        const lit = showsItsPage(elementDef, this.currentPage, this.overlayStack);
        clearStateColours(element, rest);
        applyStyle(element, {
            ...rest, ...look.appearance, ...(lit ? litStyle(elementDef.style) : {}),
        });
        markCurrent(element, lit);

        // Words and icon only where a look can change them. A nav that only
        // lights keeps what it drew, so a `ui.<id>.label` a script set is not
        // written over on every page change.
        if (!binding.key) return;
        const [words, path] = navWords(elementDef, look);
        this._setLabelText(element, words, path);
        this._drawLookIcon(element, elementDef, look.appearance);
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
        const rest = this.theme.styleFor(elementDef.type, elementDef.style || {});

        const appearance = this.availability.readingOffline(b)
            ? {}
            : pickLook(binding, this.state[binding.key]).appearance;

        const style = { ...rest, ...appearance };
        if (appearance.button_image) style.background_image = appearance.button_image;
        clearStateColours(element, rest);
        applyStyle(element, style);
        // A faded picture is a layer of its own: a state with no picture
        // takes it away.
        if (!style.background_image) dropFadedBackground(element);
    }

    evaluateFeedback(b) {
        const { element, elementDef, binding } = b;
        const offline = this.availability.markReading(b);
        if (offline) {
            this._drawButtonClaimingNoState(element, elementDef);
            return;
        }

        const look = pickLook(binding, this.state[binding.key]);
        const rest = this.theme.styleFor(elementDef.type, elementDef.style || {});
        const style = { ...rest, ...look.appearance };
        repaint(element, elementDef, rest, style);
        redrawPicture(element, elementDef, look.appearance.button_image, style.bg_color);
        this._drawButtonWords(element, elementDef, lookWords(elementDef, binding, look, style));
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
        repaint(element, elementDef, own, this.theme.styleFor(elementDef.type, own));
        if (own.bg_color) updateImageTint(element, own.bg_color);
        this._drawButtonWords(element, elementDef, [elementDef.label || '', ['label']]);
        this._drawLookIcon(element, elementDef, null);
        if (elementDef.button_image) {
            drawPicture(element, elementDef, elementDef.button_image, own.bg_color);
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

        const offline = this.availability.markReading(b);

        const on = toggleLit(binding, this.state[binding.key], offline);

        // Going off drops the lit colours first, or a button whose own style
        // names none stays lit for good.
        const style = this.theme.styleFor(elementDef.type, on
            ? { ...baseStyle, ...litStyle(elementDef.style) }
            : baseStyle);
        repaint(element, elementDef, baseStyle, style);
        // Frameless and image buttons take their fill from artwork, so the
        // class is the whole indication there: an outline, which no inline
        // write of ours ever clobbers.
        element.classList.toggle('toggle-on', on);
        redrawPicture(element, elementDef, null, style.bg_color);

        // The words, only where the author asked for them. A toggle that names
        // neither label keeps whatever it is already showing: rewriting the
        // label every pass would fight a `ui.<id>.label` override and rebuild
        // an icon layout for nothing.
        if (toggleNamesWords(binding) && showsWords(elementDef)) {
            const [words, path] = toggleWords(elementDef, binding, on, offline);
            this._setLabelText(element, words, path);
            this._drawLookIcon(element, elementDef, null);
        }
    }

    /**
     * Draw a label's bound value (modules/label.js), unless an edit in flight
     * holds the words. What was written last is kept on the binding, so a
     * batch that leaves the words as they were rewrites nothing.
     */
    evaluateText(b) {
        const { element, elementDef, binding } = b;
        const value = this.state[binding.key];
        const offline = this.availability.markReading(b);
        const [text, path] = valueWords(binding, value, offline, this._displayDecimals(elementDef));
        if (this._textEditor.holds(element)) return;
        this._textEditor.recordSource(element, path);
        if (b._lastText === text) return;
        b._lastText = text;
        if (elementDef?.style?.white_space) {
            element.innerHTML = richText(text);
        } else {
            element.textContent = text;
        }
    }

    evaluateColor(b) {
        const { element, binding } = b;
        const offline = this.availability.markReading(b);
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
        const offline = this.availability.readingOffline(b);
        if (b._lastSliderRaw === rawValue && b._lastSliderOffline === offline) return;
        b._lastSliderRaw = rawValue;
        b._lastSliderOffline = offline;
        this.availability.markReading(b, offline);
        b._slider.show(rawValue, offline);
    }

    evaluateSelectValue(b) {
        const value = this.state[b.binding.key];
        const offline = this.availability.markReading(b);
        showSelectValue(b.element, value, offline);
    }

    // Select appearance (show.look.style_map): the control takes the colors
    // configured for the option matching the bound key's current value.
    evaluateSelectLook(b) {
        const stateValue = this.state[b.binding.key];
        const styleMap = b.binding.style_map || {};
        const offline = this.availability.markReading(b);
        showSelectLook(b.select, styleMap, stateValue, offline);
    }

    evaluateTextInputValue(b) {
        // Don't overwrite if user is actively editing (prevents cursor loss)
        if (document.activeElement === b.element) return;
        const value = this.state[b.binding.key];
        const offline = this.availability.markReading(b);
        showTextValue(b.element, value, offline);
    }

    // --- Lock and idle (modules/lock.js) ---

    /** Somebody used the panel: the idle timer starts again. Frames, the
     *  activity listeners and an unlock all come here, and tests replace it. */
    resetIdleTimer() {
        this.lock.resetIdle();
    }

    /** Back to the idle page, overlays closed and the history of the last
     *  person's navigation forgotten -- unless the panel is already there. */
    _returnToIdlePage(idlePage) {
        if (this.currentPage !== idlePage || this.overlayStack.length > 0) {
            this.dismissAllOverlays();
            this.currentPage = idlePage;
            this.pageHistory = [];
            this.renderCurrentPage();
        }
    }

    // --- Helpers ---

    /**
     * Write a control's words (modules/words.js), unless an edit in flight
     * holds them. Live state keeps arriving while somebody types -- the
     * canvas seeds the real room's state and re-sends it on every repost --
     * and a write here would wipe the caret, or worse, swap the slot being
     * edited out from under it.
     *
     * `path` names the authored field this text came from, for the in-place
     * editor. Every caller passes one, or null where the words are not
     * authored anywhere (see `recordSource` in modules/text_edit.js).
     */
    _setLabelText(el, text, path) {
        if (this._textEditor.holds(el)) return;
        showWords(el, text);
        if (path !== undefined) this._textEditor.recordSource(el, path);
    }

    /**
     * The caption a control carries, its `label` in a `tag` of `className`,
     * returned for the renderer to place. Recorded as the words an in-place
     * edit or a `ui.<id>.label` override changes: the field is the in-place
     * editor's (modules/text_edit.js), and the node is kept on the control as
     * `_captionNode` because a caption is not findable by class
     * (`overrideWordsNode` in modules/overrides.js).
     */
    _caption(el, element, tag = 'label', className = '') {
        const label = document.createElement(tag);
        if (className) label.className = className;
        label.textContent = element.label;
        this._textEditor.recordSource(el, ['label']);
        el._captionNode = label;
        this._textEditor.markCaption(label);
        return label;
    }
}

// Start the app
const app = new PanelApp();
// Expose for iframe debugging and programmer integration (read-only intent)
window.__openavcPanel = app;
document.addEventListener('DOMContentLoaded', () => app.start());
