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
        this.ws = null;
        this.state = {};
        this.uiDef = null;
        this.uiSettings = {};
        this.currentPage = params.get('page') || 'main';
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
        this._navigatingBack = false; // Skip history push when navigateToPage is recursing for $back
        this._runningMacros = {};    // macro_id -> { description, step_index, total_steps }
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000; // matches the backoff cap used in onclose
        this.reconnectAttempts = 0;
        this._offline = false;           // true while the WS is disconnected
        this._lockInitialized = false;   // lock screen shown once per session, not on every reconnect
        this._meetingStartTimes = {};    // element_id -> meeting start Date (survives re-render)
        this.themeElementDefaults = {};
        this.currentTheme = null;
        this._themeApplyInProgress = false;
        // Audio playback (driven by plugin.audio_player.* state)
        this._audioUnlocked = false;
        this._lastAudioRequestId = null;
        this._activeAudio = new Set();
        // Plugin panel_elements lookup: pluginId -> {[type]: extension}.
        // Populated once at startup via /api/plugins/extensions so per-element
        // iframe sandbox / allow attributes can apply the plugin's declared
        // permissions instead of always defaulting to allow-scripts only.
        this._pluginExtensions = {};
    }

    async _loadPluginExtensions() {
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        try {
            const res = await fetch(`${basePath}/api/plugins/extensions`);
            if (!res.ok) return;
            const data = await res.json();
            const elements = data.panel_elements || [];
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
        fetch(`${basePath}/api/project`)
            .then(r => r.ok ? r.json() : null)
            .then(proj => {
                if (!proj || !proj.ui) {
                    console.warn('[panel-edit] no project available from /api/project');
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
                this.applyOrientation();
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
                    this.applyOrientation();
                    this.renderCurrentPage();
                    this._postToParent({ type: 'openavc:editor-ready' });
                    break;
                }
                case 'openavc:editor-page': {
                    if (msg.pageId && msg.pageId !== this.currentPage) {
                        this.currentPage = msg.pageId;
                        this.renderCurrentPage();
                    }
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
                this.applyOrientation();
                if (this.snapshotReceived) {
                    this.renderCurrentPage();
                }
                this._reconcileLockOnDefinition();
                this.resetIdleTimer();
                break;

            case 'ui.navigate':
                if (msg.page_id) {
                    this.navigateToPage(msg.page_id);
                }
                break;

            case 'macro.started':
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

            case 'macro.completed':
            case 'macro.error':
            case 'macro.cancelled':
                delete this._runningMacros[msg.macro_id];
                this._updateMacroBusyState(msg.macro_id);
                this._updateMacroProgressBindings(msg.macro_id);
                break;

            case 'error':
                console.warn(`[WS Error] ${msg.source_type}: ${msg.message}`);
                break;
        }
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
            overlayEl.querySelectorAll('.panel-plugin').forEach(el => {
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

        if (pageType === 'sidebar') {
            const side = overlay.side || 'right';
            const width = overlay.width || 320;
            content.className = `overlay-content overlay-sidebar overlay-sidebar-${side}`;
            content.style.width = width + 'px';
        } else {
            const width = overlay.width || 400;
            const height = overlay.height || 300;
            const position = overlay.position || 'center';
            content.className = `overlay-content overlay-dialog overlay-pos-${position}`;
            content.style.width = width + 'px';
            content.style.height = height + 'px';
        }

        // Grid inside overlay content
        const grid = document.createElement('div');
        grid.className = 'panel-page';
        const cols = page.grid?.columns || 4;
        const rows = page.grid?.rows || 4;
        grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
        grid.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
        if (page.grid_gap != null) {
            grid.style.gap = `${page.grid_gap}px`;
        }
        grid.style.width = '100%';
        grid.style.height = '100%';

        for (const element of (page.elements || [])) {
            const el = this.renderElement(element);
            if (el) {
                const area = element.grid_area || {};
                el.style.gridColumn = `${area.col || 1} / span ${area.col_span || 1}`;
                el.style.gridRow = `${area.row || 1} / span ${area.row_span || 1}`;
                el.dataset.elementType = element.type;
                this.registerVisibleWhen(el, element);
                grid.appendChild(el);
            }
        }

        this._applyPageBackground(grid, page.background);
        content.appendChild(grid);
        container.appendChild(content);

        // Append to root (on top of everything)
        document.body.appendChild(container);

        // Keep a newly-opened overlay non-interactive while offline; commands
        // would be silently dropped but local optimistic UI would still flip.
        // setConnectionStatus re-enables overlays on reconnect.
        if (this._offline) container.style.pointerEvents = 'none';

        // Trigger animation
        requestAnimationFrame(() => container.classList.add('active'));

        // Evaluate bindings for new elements
        this.evaluateAllBindings();
    }

    // --- Rendering ---

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
            oldGrid.style.padding = 'var(--panel-grid-gap)';
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

        this.bindings = [];
        this.elementMap = {};

        // Apply theme
        this.applyTheme(this.uiDef.settings || {});

        // Make root relative for absolute positioning during transitions
        this.root.style.position = 'relative';
        this.root.style.overflow = 'hidden';

        // Create grid with safe defaults
        const grid = document.createElement('div');
        grid.className = 'panel-page';
        const cols = page.grid?.columns || 12;
        const rows = page.grid?.rows || 8;
        grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
        grid.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
        if (page.grid_gap != null) {
            grid.style.gap = `${page.grid_gap}px`;
        }

        // Apply per-page background
        this._applyPageBackground(grid, page.background);

        // Apply page enter animation
        if (pageTransition !== 'none') {
            grid.classList.add(`page-enter-${pageTransition}`);
        }

        // Edit-mode grid overlay (dashed cells). Rendered inside the grid so elements
        // stack above it and the iframe's real backgrounds are preserved.
        if (this.editMode && this._editShowGrid !== false) {
            const gridGap = page.grid_gap != null ? page.grid_gap : 8;
            const gridOverlay = document.createElement('div');
            gridOverlay.className = 'panel-page-grid-overlay';
            gridOverlay.style.cssText = [
                'grid-column: 1 / -1',
                'grid-row: 1 / -1',
                `display: grid`,
                `grid-template-columns: repeat(${cols}, 1fr)`,
                `grid-template-rows: repeat(${rows}, 1fr)`,
                `gap: ${gridGap}px`,
                'pointer-events: none',
                'z-index: 0',
            ].join(';');
            for (let i = 0; i < cols * rows; i++) {
                const cell = document.createElement('div');
                cell.style.cssText = 'border:1px dashed rgba(255,255,255,0.18);border-radius:4px;';
                gridOverlay.appendChild(cell);
            }
            grid.appendChild(gridOverlay);
        }

        // Render master elements (persistent across pages, below page elements)
        const masterElements = this.uiDef.master_elements || [];
        for (const mEl of masterElements) {
            const mPages = mEl.pages;
            const showOnPage = mPages === '*' || (Array.isArray(mPages) && mPages.includes(page.id));
            if (!showOnPage) continue;
            const el = this.renderElement(mEl);
            if (el) {
                const area = mEl.grid_area || {};
                el.style.gridColumn = `${area.col || 1} / span ${area.col_span || 1}`;
                el.style.gridRow = `${area.row || 1} / span ${area.row_span || 1}`;
                el.style.zIndex = '0';  // Below page elements
                el.dataset.elementType = mEl.type;
                this.registerVisibleWhen(el, mEl);
                grid.appendChild(el);
            }
        }

        // Render elements
        const elements = page.elements || [];
        for (let i = 0; i < elements.length; i++) {
            const element = elements[i];
            const el = this.renderElement(element);
            if (el) {
                const area = element.grid_area || {};
                el.style.gridColumn = `${area.col || 1} / span ${area.col_span || 1}`;
                el.style.gridRow = `${area.row || 1} / span ${area.row_span || 1}`;
                el.dataset.elementType = element.type;

                // Element entry animation
                if (entryAnimation !== 'none') {
                    el.style.opacity = '0';
                    const staggerStyle = this.uiSettings.element_stagger_style || 'fade-up';
                    const animClass = entryAnimation === 'stagger' ? `element-entry-${staggerStyle}` : `element-entry-${entryAnimation}`;
                    const delay = staggerMs * i;
                    setTimeout(() => {
                        el.style.opacity = '';
                        el.classList.add(animClass);
                    }, delay);
                }

                this.registerVisibleWhen(el, element);
                grid.appendChild(el);
            }
        }

        this.root.appendChild(grid);
        this.evaluateAllBindings();

        // Theme Studio direct manipulation — click any element in the preview
        // to jump to its section in the editor. Hover shows an outline + type label.
        if (this.editMode) {
            this._setupThemeStudioInteraction(grid);
        }
    }

    _setupThemeStudioInteraction(grid) {
        const TYPE_LABELS = {
            button: 'Button', label: 'Label', slider: 'Slider', fader: 'Fader',
            select: 'Select', text_input: 'Text Input', status_led: 'Status LED',
            gauge: 'Gauge', level_meter: 'Level Meter', list: 'List', matrix: 'Matrix',
            group: 'Group', image: 'Image', clock: 'Clock', spacer: 'Spacer',
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
            case 'spacer':        return this.renderSpacer(element);
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
                el.style.textShadow = '0 1px 3px rgba(0,0,0,0.8)';
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
            (element.thumb_size ?? themedSliderStyle.thumb_size ?? 44) + 'px',
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
            if (sliderStep > 0) v = Math.round(v / sliderStep) * sliderStep;
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
                placeholder.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--panel-text);opacity:0.5;font-size:12px;';
                el.appendChild(placeholder);
            };
            el.appendChild(img);
        }

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        return el;
    }

    renderSpacer(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-spacer';
        el.dataset.elementId = element.id;
        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        // Edit-mode hint: dashed outline + label when the spacer has no visible styling,
        // so designers can see where an otherwise invisible element sits.
        const style = element.style || {};
        const hasVisual = style.bg_color || style.background_image || style.background_gradient || style.border_width;
        if (this.editMode && !hasVisual) {
            el.style.border = '1px dashed var(--panel-text, rgba(255,255,255,0.3))';
            el.style.opacity = '0.3';
            el.style.borderRadius = el.style.borderRadius || '4px';
            el.style.display = 'flex';
            el.style.alignItems = 'center';
            el.style.justifyContent = 'center';
            el.style.color = 'var(--panel-text)';
            el.style.fontSize = '11px';
            el.textContent = 'Spacer';
        }
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
        const itemHeight = element.item_height || 44;
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
                row.style.minHeight = itemHeight + 'px';
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
        if (value !== undefined && value !== null) {
            selectedValues.clear();
            selectedValues.add(String(value));
            // Update visuals
            scrollArea.querySelectorAll('.list-item').forEach(item => {
                const isActive = selectedValues.has(item.dataset.value);
                item.style.backgroundColor = isActive ? itemActiveBg : itemBg;
                item.classList.toggle('active', isActive);
            });
        }
    }

    // --- Matrix ---

    renderMatrix(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-matrix';
        el.dataset.elementId = element.id;

        const config = element.matrix_config || {};
        const inputCount = config.input_count || 4;
        const outputCount = config.output_count || 4;
        const inputLabels = config.input_labels || Array.from({ length: inputCount }, (_, i) => `In ${i + 1}`);
        const outputLabels = config.output_labels || Array.from({ length: outputCount }, (_, i) => `Out ${i + 1}`);
        const routePattern = config.route_key_pattern || '';
        const audioRoutePattern = config.audio_route_key_pattern || '';
        const inputKeyPattern = config.input_key_pattern || '';
        const outputKeyPattern = config.output_key_pattern || '';
        const matrixStyle = element.matrix_style || 'crosspoint';
        const showLock = config.show_lock !== false;
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
        const cellSize = style.cell_size || 44;

        this.applyStyle(el, style);

        if (element.label) {
            const label = document.createElement('div');
            label.className = 'matrix-label';
            label.textContent = element.label;
            el.appendChild(label);
        }

        const scrollWrap = document.createElement('div');
        scrollWrap.className = 'matrix-scroll';

        // Matrix state tracking
        const lockedOutputs = new Set();
        const mutedOutputs = new Set();

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
                const row = document.createElement('div');
                row.className = 'matrix-list-row';

                const outLabel = document.createElement('span');
                outLabel.className = 'matrix-list-label';
                outLabel.textContent = outputLabels[o] || `Out ${o + 1}`;
                outLabel.dataset.outputIdx = String(o);
                row.appendChild(outLabel);
                // Hidden badge shown when audio route diverges from video route
                if (audioRoutePattern) {
                    const mismatch = document.createElement('span');
                    mismatch.className = 'matrix-route-mismatch';
                    mismatch.dataset.mismatchIdx = String(o);
                    mismatch.textContent = 'A≠V';
                    mismatch.title = 'Audio route does not match video route';
                    mismatch.hidden = true;
                    row.appendChild(mismatch);
                }

                const select = document.createElement('select');
                select.className = 'matrix-list-select';
                for (let i = 0; i < inputCount; i++) {
                    const opt = document.createElement('option');
                    opt.value = String(i + 1);
                    opt.textContent = inputLabels[i] || `In ${i + 1}`;
                    select.appendChild(opt);
                }
                select.dataset.outputIdx = String(o);

                select.addEventListener('change', () => {
                    if (lockedOutputs.has(o + 1)) return;
                    const inputIdx = parseInt(select.value);
                    const outputIdx = o + 1;
                    this.send({
                        type: 'ui.route',
                        element_id: element.id,
                        input: inputIdx,
                        output: outputIdx,
                    });
                    // Audio follow video
                    if (config.audio_follow_video && element.bindings?.do?.audio_route) {
                        this.send({
                            type: 'ui.route',
                            element_id: element.id,
                            input: inputIdx,
                            output: outputIdx,
                            audio: true,
                        });
                    }
                });

                if (showLock) {
                    const lockBtn = document.createElement('button');
                    lockBtn.className = 'matrix-lock-btn';
                    lockBtn.textContent = '\uD83D\uDD13';
                    lockBtn.title = 'Lock output';
                    lockBtn.addEventListener('click', () => {
                        if (lockedOutputs.has(o + 1)) {
                            lockedOutputs.delete(o + 1);
                            lockBtn.textContent = '\uD83D\uDD13';
                            lockBtn.classList.remove('locked');
                            select.disabled = false;
                        } else {
                            lockedOutputs.add(o + 1);
                            lockBtn.textContent = '\uD83D\uDD12';
                            lockBtn.classList.add('locked');
                            select.disabled = true;
                        }
                    });
                    row.appendChild(lockBtn);
                }

                if (showMute) {
                    const muteBtn = document.createElement('button');
                    muteBtn.className = 'matrix-mute-btn';
                    muteBtn.textContent = 'M';
                    muteBtn.title = 'Mute output';
                    muteBtn.addEventListener('click', () => {
                        const outputIdx = o + 1;
                        const isMuted = mutedOutputs.has(outputIdx);
                        if (isMuted) {
                            mutedOutputs.delete(outputIdx);
                            muteBtn.classList.remove('muted');
                        } else {
                            mutedOutputs.add(outputIdx);
                            muteBtn.classList.add('muted');
                        }
                        this.send({
                            type: 'ui.route',
                            element_id: element.id,
                            output: outputIdx,
                            mute: !isMuted,
                        });
                        // Audio follow video: also send audio-mute when AFV is on
                        // and the element has an audio_mute_route binding.
                        if (config.audio_follow_video && element.bindings?.do?.audio_mute_route) {
                            this.send({
                                type: 'ui.route',
                                element_id: element.id,
                                output: outputIdx,
                                mute: !isMuted,
                                audio: true,
                            });
                        }
                        if (select) select.disabled = mutedOutputs.has(outputIdx);
                    });
                    row.appendChild(muteBtn);
                }

                row.appendChild(select);
                list.appendChild(row);
            }

            scrollWrap.appendChild(list);
        } else {
            // --- Crosspoint view ---
            const extraColDefs = [];
            if (showLock) extraColDefs.push('28px');
            if (showMute) extraColDefs.push('28px');
            const table = document.createElement('div');
            table.className = 'matrix-grid';
            table.style.gridTemplateColumns = `auto repeat(${inputCount}, ${cellSize}px) ${extraColDefs.join(' ')}`.trim();
            table.style.gridTemplateRows = `auto repeat(${outputCount}, ${cellSize}px)`;

            // Top-left corner cell
            const corner = document.createElement('div');
            corner.className = 'matrix-corner';
            table.appendChild(corner);

            // Input headers (top row)
            for (let i = 0; i < inputCount; i++) {
                const header = document.createElement('div');
                header.className = 'matrix-header matrix-input-header';
                const span = document.createElement('span');
                span.textContent = inputLabels[i] || `In ${i + 1}`;
                span.dataset.inputIdx = String(i);
                if (inputCount > 4) header.classList.add('rotated');
                header.appendChild(span);
                table.appendChild(header);
            }
            // Lock/Mute column headers
            if (showLock) {
                const lockHdr = document.createElement('div');
                lockHdr.className = 'matrix-header';
                lockHdr.textContent = '\uD83D\uDD12';
                lockHdr.style.fontSize = '10px';
                table.appendChild(lockHdr);
            }
            if (showMute) {
                const muteHdr = document.createElement('div');
                muteHdr.className = 'matrix-header';
                muteHdr.textContent = 'M';
                muteHdr.style.fontSize = '10px';
                table.appendChild(muteHdr);
            }

            // Drag-to-route state
            let dragLine = null;
            let dragStartInput = null;

            // Output rows with crosspoints
            for (let o = 0; o < outputCount; o++) {
                // Output label — wrapped in a labelText span so the mismatch
                // badge (when shown) doesn't get wiped by the dynamic-label
                // updater. The data-output-idx attribute stays on the header
                // so existing query selectors still find it.
                const outHeader = document.createElement('div');
                outHeader.className = 'matrix-header matrix-output-header';
                outHeader.dataset.outputIdx = String(o);
                const outLabelText = document.createElement('span');
                outLabelText.dataset.labelText = '';
                outLabelText.textContent = outputLabels[o] || `Out ${o + 1}`;
                outHeader.appendChild(outLabelText);
                // Hidden badge shown when audio route diverges from video route
                if (audioRoutePattern) {
                    const mismatch = document.createElement('span');
                    mismatch.className = 'matrix-route-mismatch';
                    mismatch.dataset.mismatchIdx = String(o);
                    mismatch.textContent = 'A≠V';
                    mismatch.title = 'Audio route does not match video route';
                    mismatch.hidden = true;
                    outHeader.appendChild(mismatch);
                }
                table.appendChild(outHeader);

                // Crosspoint cells
                for (let i = 0; i < inputCount; i++) {
                    const cell = document.createElement('div');
                    cell.className = 'matrix-cell';
                    cell.setAttribute('aria-label', `Route input ${i + 1} to output ${o + 1}`);

                    const dot = document.createElement('div');
                    dot.className = 'matrix-crosspoint';
                    dot.style.backgroundColor = inactiveColor;
                    dot.dataset.input = String(i + 1);
                    dot.dataset.output = String(o + 1);

                    cell.addEventListener('click', () => {
                        if (lockedOutputs.has(o + 1)) return;
                        this.send({
                            type: 'ui.route',
                            element_id: element.id,
                            input: i + 1,
                            output: o + 1,
                        });
                        if (config.audio_follow_video && element.bindings?.do?.audio_route) {
                            this.send({
                                type: 'ui.route',
                                element_id: element.id,
                                input: i + 1,
                                output: o + 1,
                                audio: true,
                            });
                        }
                    });

                    // Drag-to-route: start drag from input header, drop on output row
                    cell.addEventListener('pointerdown', (e) => {
                        if (lockedOutputs.has(o + 1)) return;
                        dragStartInput = i + 1;
                        // Create visual feedback line
                        const rect = cell.getBoundingClientRect();
                        dragLine = document.createElement('div');
                        dragLine.className = 'matrix-drag-line';
                        dragLine.style.cssText = `
                            position: fixed; pointer-events: none; z-index: 999;
                            height: 2px; width: 0;
                            background: ${activeColor};
                            border-radius: 1px;
                            transform-origin: left center;
                            left: ${rect.left + rect.width / 2}px;
                            top: ${rect.top + rect.height / 2}px;
                        `;
                        document.body.appendChild(dragLine);
                        const onMove = (me) => {
                            if (!dragLine) return;
                            const dx = me.clientX - (rect.left + rect.width / 2);
                            const dy = me.clientY - (rect.top + rect.height / 2);
                            const len = Math.sqrt(dx * dx + dy * dy);
                            const angle = Math.atan2(dy, dx) * 180 / Math.PI;
                            dragLine.style.width = len + 'px';
                            dragLine.style.transform = `rotate(${angle}deg)`;
                            dragLine.style.transformOrigin = '0 0';
                        };
                        const onUp = (ue) => {
                            document.removeEventListener('pointermove', onMove);
                            document.removeEventListener('pointerup', onUp);
                            if (dragLine) { dragLine.remove(); dragLine = null; }
                            // Find which cell we dropped on
                            const target = document.elementFromPoint(ue.clientX, ue.clientY);
                            const cp = target?.closest?.('.matrix-crosspoint') || target?.closest?.('.matrix-cell')?.querySelector('.matrix-crosspoint');
                            if (cp && cp.dataset.output && dragStartInput) {
                                const dropOutput = parseInt(cp.dataset.output);
                                if (!lockedOutputs.has(dropOutput)) {
                                    this.send({ type: 'ui.route', element_id: element.id, input: dragStartInput, output: dropOutput });
                                    // Audio follow video: mirror the click handler so the drag
                                    // gesture and tap gesture behave the same with AFV on.
                                    if (config.audio_follow_video && element.bindings?.do?.audio_route) {
                                        this.send({
                                            type: 'ui.route',
                                            element_id: element.id,
                                            input: dragStartInput,
                                            output: dropOutput,
                                            audio: true,
                                        });
                                    }
                                }
                            }
                            dragStartInput = null;
                        };
                        document.addEventListener('pointermove', onMove);
                        document.addEventListener('pointerup', onUp);
                        el._matrixDragCleanup = () => {
                            document.removeEventListener('pointermove', onMove);
                            document.removeEventListener('pointerup', onUp);
                            if (dragLine) { dragLine.remove(); dragLine = null; }
                            dragStartInput = null;
                        };
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
                    lockBtn.textContent = '\uD83D\uDD13';
                    lockBtn.title = 'Lock output';
                    lockBtn.addEventListener('click', () => {
                        if (lockedOutputs.has(o + 1)) {
                            lockedOutputs.delete(o + 1);
                            lockBtn.textContent = '\uD83D\uDD13';
                            lockBtn.classList.remove('locked');
                        } else {
                            lockedOutputs.add(o + 1);
                            lockBtn.textContent = '\uD83D\uDD12';
                            lockBtn.classList.add('locked');
                        }
                    });
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
                        const outputIdx = o + 1;
                        const isMuted = mutedOutputs.has(outputIdx);
                        if (isMuted) { mutedOutputs.delete(outputIdx); muteBtn.classList.remove('muted'); }
                        else { mutedOutputs.add(outputIdx); muteBtn.classList.add('muted'); }
                        this.send({ type: 'ui.route', element_id: element.id, output: outputIdx, mute: !isMuted });
                        // Audio follow video: also send audio-mute when AFV is on
                        // and the element has an audio_mute_route binding.
                        if (config.audio_follow_video && element.bindings?.do?.audio_mute_route) {
                            this.send({ type: 'ui.route', element_id: element.id, output: outputIdx, mute: !isMuted, audio: true });
                        }
                    });
                    muteCell.appendChild(muteBtn);
                    table.appendChild(muteCell);
                }
            }

            scrollWrap.appendChild(table);
        }

        el.appendChild(scrollWrap);
        this.elementMap[element.id] = { el, elementDef: element };

        // State binding for routes. Carry every glob pattern the matrix reads
        // (route, audio route, dynamic in/out labels) as `_patterns` so the
        // incremental state.update filter re-evaluates the matrix when any of
        // them change. The old `key: routePattern` stored a literal glob that
        // the concrete-key filter could never match, so routes only refreshed
        // on a full re-render.
        if (routePattern) {
            this.bindings.push({
                type: 'matrix_routes',
                element: el,
                elementDef: element,
                binding: {
                    _patterns: [routePattern, audioRoutePattern, inputKeyPattern, outputKeyPattern]
                        .filter(Boolean),
                },
                _matrix: {
                    routePattern, audioRoutePattern,
                    inputKeyPattern, outputKeyPattern,
                    inputCount, outputCount, activeColor, inactiveColor,
                    matrixStyle,
                },
            });
        }

        return el;
    }

    evaluateMatrixRoutes(b) {
        const { routePattern, audioRoutePattern, inputKeyPattern, outputKeyPattern, inputCount, outputCount, activeColor, inactiveColor, matrixStyle } = b._matrix;
        const el = b.element;

        // Read current video routes from state
        const routes = {};  // output (1-based) -> input (1-based)
        for (let o = 1; o <= outputCount; o++) {
            const key = routePattern.replace('*', String(o));
            const val = this.state[key];
            if (val !== undefined && val !== null) {
                routes[o] = parseInt(String(val));
            }
        }

        // Read audio routes if a pattern is configured, so we can flag any
        // output whose audio route diverges from its video route.
        const audioRoutes = {};
        if (audioRoutePattern) {
            for (let o = 1; o <= outputCount; o++) {
                const key = audioRoutePattern.replace('*', String(o));
                const val = this.state[key];
                if (val !== undefined && val !== null) {
                    audioRoutes[o] = parseInt(String(val));
                }
            }
            // Toggle the per-output mismatch badge.
            const badges = el.querySelectorAll('.matrix-route-mismatch');
            badges.forEach(badge => {
                const idx = parseInt(badge.dataset.mismatchIdx) + 1;
                const video = routes[idx];
                const audio = audioRoutes[idx];
                const mismatch =
                    video !== undefined && audio !== undefined && video !== audio;
                badge.hidden = !mismatch;
            });
        }

        // Update dynamic labels from state — write to the [data-label-text]
        // child when present (crosspoint output header has siblings), else to
        // the header element directly (input headers, list-view labels).
        if (inputKeyPattern) {
            const headers = el.querySelectorAll('[data-input-idx]');
            headers.forEach(h => {
                const idx = parseInt(h.dataset.inputIdx);
                const key = inputKeyPattern.replace('*', String(idx + 1));
                const val = this.state[key];
                if (val !== undefined && val !== null) {
                    const target = h.querySelector('[data-label-text]') || h;
                    target.textContent = String(val);
                }
            });
        }
        if (outputKeyPattern) {
            const headers = el.querySelectorAll('[data-output-idx]');
            headers.forEach(h => {
                const idx = parseInt(h.dataset.outputIdx);
                const key = outputKeyPattern.replace('*', String(idx + 1));
                const val = this.state[key];
                if (val !== undefined && val !== null) {
                    const target = h.querySelector('[data-label-text]') || h;
                    target.textContent = String(val);
                }
            });
        }

        if (matrixStyle === 'list') {
            // Update select values
            const selects = el.querySelectorAll('.matrix-list-select');
            selects.forEach(sel => {
                const oIdx = parseInt(sel.dataset.outputIdx) + 1;
                if (routes[oIdx]) sel.value = String(routes[oIdx]);
            });
        } else {
            // Update crosspoint dots
            const dots = el.querySelectorAll('.matrix-crosspoint');
            dots.forEach(dot => {
                const inp = parseInt(dot.dataset.input);
                const out = parseInt(dot.dataset.output);
                const isActive = routes[out] === inp;
                dot.style.backgroundColor = isActive ? activeColor : inactiveColor;
                dot.classList.toggle('active', isActive);
                dot.setAttribute('aria-label', isActive ? `Active: input ${inp} to output ${out}` : `Inactive: input ${inp} to output ${out}`);
            });
        }
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
                _svg: { fgPath, valueText, startAngle, endAngle, radius, cx, cy, min, max, unit, gaugeColor, zones, showValue, arcPath: arcPath, polarToCart },
            });
        }

        return el;
    }

    evaluateGaugeValue(b) {
        const raw = this.state[b.binding.key];
        // Memoize: skip if unchanged (also short-circuits the undefined steady state)
        if (b._lastGaugeRaw === raw) return;
        b._lastGaugeRaw = raw;
        const { fgPath, valueText, startAngle, endAngle, radius, min, max, unit, gaugeColor, zones, showValue, arcPath: arcPathFn } = b._svg;
        if (raw === undefined || raw === null) {
            // Bound key was deleted (device removed/offline) — revert to the
            // no-data placeholder instead of freezing on the last reading.
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
            valueText.textContent = `${Math.round(value * 10) / 10}${unit}`;
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
        if (b._lastMeterRaw === raw) return;
        b._lastMeterRaw = raw;
        const { segments, min, max, bar, showPeak, peakHoldMs } = b._meter;
        const segs = bar.querySelectorAll('.meter-segment');
        if (raw === undefined || raw === null) {
            // Bound key deleted — clear the meter rather than freezing the level.
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
        const initFrac = this._responseCurveInverse((currentValue - min) / (max - min), response, responseDbRange);
        if (isHorizontal) handle.style.left = `${initFrac * 100}%`;
        else handle.style.bottom = `${initFrac * 100}%`;
        if (valueDisplay) valueDisplay.textContent = fmtFaderValue(currentValue);

        // Touch/mouse drag interaction
        let dragging = false;
        let debounceTimer = null;
        let currentDragVal = currentValue;

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
            return Math.round(val / step) * step;
        };

        const updateFader = (val) => {
            const frac = this._responseCurveInverse((val - min) / (max - min), response, responseDbRange);
            if (isHorizontal) handle.style.left = `${frac * 100}%`;
            else handle.style.bottom = `${frac * 100}%`;
            handle.setAttribute('aria-valuenow', String(Math.round(val * 10) / 10));
            if (valueDisplay) valueDisplay.textContent = fmtFaderValue(val);
        };

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
                current = Math.min(max, current + step);
                updateFader(current);
                sendChange(current);
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') {
                e.preventDefault();
                current = Math.max(min, current - step);
                updateFader(current);
                sendChange(current);
            }
        });

        this.elementMap[element.id] = { el, elementDef: element };

        // Value binding for state updates
        if (valueBinding) {
            this.bindings.push({
                type: 'fader_value',
                element: el,
                elementDef: element,
                binding: valueBinding,
                _fader: { handle, valueDisplay, min, max, unit, horizontal: isHorizontal, outputMin, outputMax, scaleToFull, response, responseDbRange, fmt: fmtFaderValue },
            });
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

    evaluateFaderValue(b) {
        const raw = this.state[b.binding.key];
        if (b._lastFaderRaw === raw) return;
        b._lastFaderRaw = raw;
        const { handle, valueDisplay, min, max, horizontal, outputMin, outputMax, scaleToFull, response, responseDbRange, fmt } = b._fader;
        // Don't fight the operator while they're dragging the handle.
        if (handle._dragging) return;
        const span = max - min;
        if (raw === undefined || raw === null) {
            // Bound key deleted — return the handle to the floor rather than
            // leaving it parked at the last device value.
            if (horizontal) handle.style.left = '0%';
            else handle.style.bottom = '0%';
            if (valueDisplay) valueDisplay.textContent = fmt(min);
            return;
        }
        const value = Math.max(min, Math.min(max, this._reverseScale(Number(raw), min, max, outputMin, outputMax, scaleToFull)));
        const frac = span > 0 ? this._responseCurveInverse((value - min) / span, response, responseDbRange) : 0;
        if (horizontal) handle.style.left = `${frac * 100}%`;
        else handle.style.bottom = `${frac * 100}%`;
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
                label.style.left = '8px';
            } else if (labelPos.endsWith('center')) {
                label.style.left = '50%';
                label.style.transform = 'translateX(-50%)';
            } else if (labelPos.endsWith('right')) {
                label.style.right = '8px';
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

    // --- Plugin Element (iframe-based) ---

    renderPluginElement(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-plugin';
        el.dataset.elementId = element.id;

        const pluginId = element.plugin_id;
        const pluginType = element.plugin_type;

        // Validate pluginId/pluginType format (alphanumeric, underscores, hyphens only)
        const validIdPattern = /^[a-zA-Z0-9_-]+$/;
        if (!pluginId || !pluginType || !validIdPattern.test(pluginId) || !validIdPattern.test(pluginType)) {
            el.textContent = 'Plugin element (unconfigured)';
            el.style.color = 'var(--panel-text)';
            el.style.opacity = '0.5';
            el.style.fontSize = '12px';
            el.style.display = 'flex';
            el.style.alignItems = 'center';
            el.style.justifyContent = 'center';
            return el;
        }

        // Edit mode: render a placeholder instead of booting the real plugin iframe.
        // Keeps the designer fast and avoids running plugin code while authoring.
        if (this.editMode) {
            el.style.display = 'flex';
            el.style.flexDirection = 'column';
            el.style.alignItems = 'center';
            el.style.justifyContent = 'center';
            el.style.gap = '4px';
            el.style.border = '1px dashed var(--panel-text, rgba(255,255,255,0.3))';
            el.style.borderRadius = '4px';
            el.style.color = 'var(--panel-text)';
            el.style.opacity = '0.4';
            el.style.fontSize = '11px';
            el.style.padding = '4px';
            el.style.textAlign = 'center';
            const label = document.createElement('div');
            label.textContent = 'Plugin';
            label.style.fontWeight = '600';
            const sub = document.createElement('div');
            sub.textContent = `${pluginId} / ${pluginType}`;
            sub.style.opacity = '0.8';
            sub.style.fontSize = '10px';
            el.appendChild(label);
            el.appendChild(sub);
            this.applyStyle(el, this.getThemedStyle('plugin', element.style));
            return el;
        }

        // Resolve renderer URL using relative path (works through tunnels)
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        const rendererUrl = `${basePath}/api/plugins/${encodeURIComponent(pluginId)}/panel/${encodeURIComponent(pluginType)}.html`;

        const iframe = document.createElement('iframe');
        iframe.src = rendererUrl;

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
        iframe.sandbox = sandboxTokens.join(' ');
        const allowFeatures = extDef?.allow_features || [];
        if (allowFeatures.length) {
            iframe.setAttribute('allow', allowFeatures.join('; '));
        }

        iframe.style.cssText = 'width:100%; height:100%; border:none; border-radius:inherit;';
        iframe.setAttribute('loading', 'lazy');
        el.style.overflow = 'hidden';
        el.appendChild(iframe);

        // Loading indicator
        const loadingIndicator = document.createElement('div');
        loadingIndicator.textContent = 'Loading plugin...';
        loadingIndicator.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--panel-text);opacity:0.5;font-size:12px;position:absolute;inset:0;z-index:1;';
        el.style.position = 'relative';
        el.appendChild(loadingIndicator);

        // Store reference for state updates
        this.elementMap[element.id] = el;
        el._pluginIframe = iframe;
        el._pluginId = pluginId;
        // The plugin's declared capabilities gate what the iframe bridge will
        // forward (see the openavc:action handler). Mirrors server PluginAPI.
        el._pluginCaps = extDef?.capabilities || [];
        el._pluginConfig = element.plugin_config || {};

        // postMessage API: send initial config + theme + state snapshot
        // when iframe loads. The state snapshot is filtered to the plugin's
        // own namespace (plugin.<id>.*) so iframes don't see unrelated keys.
        // Also re-run on demand ('openavc:request-init' from the iframe) so a
        // long-lived plugin iframe can recover a fresh ext token after its
        // TTL expires — wall panels outlive the token lifetime by design.
        const sendInit = async () => {
            loadingIndicator.remove();
            const themeVars = {};
            const root = document.documentElement;
            for (const prop of ['--panel-bg', '--panel-text', '--panel-accent',
                '--panel-button-bg', '--panel-button-text', '--panel-button-border',
                '--panel-surface', '--panel-surface-border',
                '--panel-danger', '--panel-success', '--panel-warning',
                '--panel-grid-gap', '--panel-border-radius']) {
                themeVars[prop] = getComputedStyle(root).getPropertyValue(prop).trim();
            }
            const stateSnapshot = {};
            const namespacePrefix = `plugin.${pluginId}.`;
            for (const [key, value] of Object.entries(this.state || {})) {
                if (key.startsWith(namespacePrefix)) {
                    stateSnapshot[key] = value;
                }
            }
            // Plugins that call their own /ext/* routes declare ext_auth. Fetch
            // a plugin-scoped token (our fetch is already authenticated) and pass
            // it in — the sandboxed iframe can't carry our credentials itself, so
            // it presents this token instead.
            let extToken;
            if (extDef && extDef.ext_auth) {
                extToken = await this._fetchPluginExtToken(pluginId);
            }
            if (!iframe.contentWindow) return;  // element removed during await
            iframe.contentWindow.postMessage({
                type: 'openavc:init',
                config: element.plugin_config || {},
                theme: themeVars,
                state: stateSnapshot,
                elementId: element.id,
                ext_token: extToken,
            }, '*');  // sandboxed iframe has opaque origin; source check provides security
        };
        iframe.addEventListener('load', sendInit);

        // Listen for messages from plugin iframe
        const handler = (event) => {
            if (event.source !== iframe.contentWindow) return;
            const msg = event.data;
            if (!msg || !msg.type) return;

            switch (msg.type) {
                case 'openavc:action': {
                    // This bridge carries the panel's WS authority, so gate it
                    // against the plugin's declared capabilities, mirroring the
                    // server-side PluginAPI: device commands require
                    // device_command; state writes are limited to the plugin's
                    // own plugin.<id>.* namespace (state_write) or var.*
                    // (variable_write). Anything else is a confused-deputy write
                    // and is dropped.
                    const caps = el._pluginCaps || [];
                    if (msg.action === 'device.command' && msg.device && msg.command) {
                        if (!caps.includes('device_command')) {
                            console.warn(`[panel] plugin '${pluginId}' attempted device.command without the device_command capability`);
                            break;
                        }
                        this.ws?.send(JSON.stringify({
                            type: 'command',
                            device_id: msg.device,
                            command: msg.command,
                            params: msg.params || {},
                        }));
                    } else if (msg.action === 'state.set' && msg.key) {
                        const key = String(msg.key);
                        const ownNamespace = key.startsWith(`plugin.${pluginId}.`);
                        const isVariable = key.startsWith('var.');
                        const allowed = (ownNamespace && caps.includes('state_write')) ||
                            (isVariable && caps.includes('variable_write'));
                        if (!allowed) {
                            console.warn(`[panel] plugin '${pluginId}' attempted state.set on '${key}' outside its declared scope`);
                            break;
                        }
                        this.ws?.send(JSON.stringify({
                            type: 'state.set',
                            key: msg.key,
                            value: msg.value,
                        }));
                    }
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
                    if (msg.page) this.navigateToPage(msg.page);
                    break;
            }
        };
        window.addEventListener('message', handler);
        el._pluginMessageHandler = handler;
        this._pluginMessageHandlers.add(handler);

        this.applyStyle(el, this.getThemedStyle('plugin', element.style));
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
        for (const [id, el] of Object.entries(this.elementMap)) {
            if (!el?._pluginIframe?.contentWindow) continue;
            // Scope live updates to each iframe's own namespace, exactly like the
            // init snapshot (plugin.<id>.*). Broadcasting every key let any
            // plugin passively observe all device/var/ui/system/other-plugin
            // state it was never granted, making the scoped init snapshot moot.
            const pid = el._pluginId;
            if (!pid || !String(key).startsWith(`plugin.${pid}.`)) continue;
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

    evaluateAllBindings(changedKeys = null) {
        for (const b of this.bindings) {
            try {
                // Skip bindings not affected by changed keys
                if (changedKeys) {
                    const bKey = b.binding?.key;
                    const bKeys = b.binding?._keys;        // visible_when: array of keys
                    const bPatterns = b.binding?._patterns; // matrix: array of glob patterns
                    const bPattern = b.binding?.key_pattern || b._routePattern;
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
                switch (b.type) {
                    case 'visible_when':
                        this.evaluateVisibleWhen(b);
                        break;
                    case 'feedback':
                        this.evaluateFeedback(b);
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
            } catch (e) {
                console.error('Binding error:', e);
            }
        }

        // Apply ui.* state overrides (set by macros/scripts)
        // These take priority over feedback bindings for direct control.
        this.evaluateUiOverrides();
    }

    evaluateUiOverrides() {
        for (const [elementId, entry] of Object.entries(this.elementMap)) {
            // Plugin entries store the element directly; others store {el, elementDef}.
            const el = entry.el || entry;
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

    evaluateFeedback(b) {
        const { element, elementDef, binding } = b;
        const stateValue = this.state[binding.key];
        const baseStyle = elementDef.style || {};
        const displayMode = elementDef.display_mode || 'text';
        const suppressLabel = displayMode === 'image' || displayMode === 'icon_only';

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
        if (binding.format) {
            // split/join replaces every {value} and treats the value literally,
            // so device values containing $-sequences (track titles, paths)
            // aren't reinterpreted the way String.replace would.
            setText(String(binding.format).split('{value}').join(String(value)));
        } else {
            setText(String(value));
        }
    }

    evaluateColor(b) {
        const { element, binding } = b;
        const value = this.state[binding.key];
        const colorMap = binding.map || {};
        const defaultColor = binding.default || '#9E9E9E';
        const color = colorMap[value] || defaultColor;

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
            element.style.boxShadow = `0 0 10px ${color}`;
        } else {
            element.style.boxShadow = '0 0 6px rgba(0,0,0,0.3)';
        }
    }

    evaluateSliderValue(b) {
        const { element, elementDef, binding, fill, valueDisplay, isVertical, outputMin, outputMax, scaleToFull, steps, valueToPos, fmtValue } = b;
        // Don't yank the thumb out from under an operator who is actively
        // dragging it (or has it focused) when a device echo / another panel's
        // change arrives mid-gesture.
        if (element._dragging || document.activeElement === element) return;
        const rawValue = this.state[binding.key];
        if (b._lastSliderRaw === rawValue) return;
        b._lastSliderRaw = rawValue;
        // The input runs in the position domain (0..steps); display min/max come
        // from the element definition, not the input's own min/max.
        const min = parseFloat(elementDef.min ?? 0);
        const max = parseFloat(elementDef.max ?? 100);
        const setFill = (pct) => {
            if (!fill) return;
            if (isVertical) fill.style.height = pct + '%';
            else fill.style.width = pct + '%';
        };
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
        const matched = stateValue === undefined || stateValue === null
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
        if (value === undefined || value === null) {
            // Bound key deleted — clear rather than keeping the stale value.
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

    // --- Orientation ---

    applyOrientation() {
        const orientation = this.uiSettings?.orientation || 'landscape';
        document.documentElement.setAttribute('data-orientation', orientation);
    }

    // --- Helpers ---

    applyTheme(settings) {
        const themeId = settings.theme_id || (settings.theme === 'light' ? 'light-modern' : 'dark-default');
        const overrides = settings.theme_overrides || {};

        if (this._themeApplyInProgress) return;

        const prevDefaults = JSON.stringify(this.themeElementDefaults || {});

        // Theme Studio path: parent supplied a working-copy theme. Apply it
        // synchronously without hitting the network so picker drags reflect
        // within a frame instead of after a round-trip.
        if (this.inlineTheme && this.inlineTheme.id === themeId) {
            this._applyThemeData(this.inlineTheme, overrides, settings);
            this.currentTheme = this.inlineTheme;
            const newDefaults = JSON.stringify(this.themeElementDefaults || {});
            if (prevDefaults !== newDefaults && this.snapshotReceived) {
                this._themeApplyInProgress = true;
                this.renderCurrentPage();
                this._themeApplyInProgress = false;
            }
            return;
        }

        if (this.currentTheme && this.currentTheme.id === themeId) {
            this._applyThemeData(this.currentTheme, overrides, settings);
            const newDefaults = JSON.stringify(this.themeElementDefaults || {});
            if (prevDefaults !== newDefaults && this.snapshotReceived) {
                this._themeApplyInProgress = true;
                this.renderCurrentPage();
                this._themeApplyInProgress = false;
            }
            return;
        }

        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';

        fetch(`${basePath}/api/themes/${encodeURIComponent(themeId)}`)
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
                // Re-render if element defaults changed
                const newDefaults = JSON.stringify(this.themeElementDefaults || {});
                if (prevDefaults !== newDefaults && this.snapshotReceived) {
                    this._themeApplyInProgress = true;
                    this.renderCurrentPage();
                    this._themeApplyInProgress = false;
                }
            })
            .catch(() => this._applyFallbackTheme(settings));
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
            grid_gap: '--panel-grid-gap',
            border_radius: '--panel-border-radius',
        };

        for (const [key, cssVar] of Object.entries(varMap)) {
            if (vars[key] != null) {
                const val = typeof vars[key] === 'number' ? vars[key] + 'px' : vars[key];
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
        if (style.font_size) el.style.fontSize = style.font_size + 'px';
        if (style.font_weight) el.style.fontWeight = style.font_weight;
        if (style.border_radius != null) el.style.borderRadius = style.border_radius + 'px';

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
            el.style.borderWidth = style.border_width + 'px';
            el.style.borderStyle = style.border_style || 'solid';
            if (style.border_color) {
                el.style.borderColor = style.border_color;
            }
        }

        // Box shadow with presets
        if (style.box_shadow && style.box_shadow !== 'none') {
            const shadowPresets = {
                sm: '0 2px 4px rgba(0,0,0,0.2)',
                md: '0 4px 8px rgba(0,0,0,0.3)',
                lg: '0 8px 16px rgba(0,0,0,0.4)',
                glow: `0 0 12px ${style.text_color || 'rgba(33,150,243,0.5)'}`,
                inset: 'inset 0 2px 4px rgba(0,0,0,0.3)',
            };
            el.style.boxShadow = shadowPresets[style.box_shadow] || style.box_shadow;
        }

        // Margin
        if (style.margin != null) {
            const mv = style.margin_vertical != null ? style.margin_vertical : style.margin;
            const mh = style.margin_horizontal != null ? style.margin_horizontal : style.margin;
            el.style.margin = `${mv}px ${mh}px`;
        } else {
            if (style.margin_vertical != null) {
                el.style.marginTop = style.margin_vertical + 'px';
                el.style.marginBottom = style.margin_vertical + 'px';
            }
            if (style.margin_horizontal != null) {
                el.style.marginLeft = style.margin_horizontal + 'px';
                el.style.marginRight = style.margin_horizontal + 'px';
            }
        }

        // Padding
        if (style.padding != null) {
            const pv = style.padding_vertical != null ? style.padding_vertical : style.padding;
            const ph = style.padding_horizontal != null ? style.padding_horizontal : style.padding;
            el.style.padding = `${pv}px ${ph}px`;
        } else {
            if (style.padding_vertical != null) {
                el.style.paddingTop = style.padding_vertical + 'px';
                el.style.paddingBottom = style.padding_vertical + 'px';
            }
            if (style.padding_horizontal != null) {
                el.style.paddingLeft = style.padding_horizontal + 'px';
                el.style.paddingRight = style.padding_horizontal + 'px';
            }
        }

        // Typography
        if (style.text_transform) el.style.textTransform = style.text_transform;
        if (style.letter_spacing) el.style.letterSpacing = style.letter_spacing + 'px';
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

    renderIcon(iconName, size, color) {
        if (!iconName) return null;

        // Custom icon from asset system
        if (iconName.startsWith('assets://')) {
            const img = document.createElement('img');
            img.src = this.resolveAssetUrl(iconName);
            img.style.width = `${size}px`;
            img.style.height = `${size}px`;
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
        svg.setAttribute('width', String(size));
        svg.setAttribute('height', String(size));
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
        const iconSize = element.style?.icon_size || element.icon_size || 24;
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
            el.style.gap = '4px';
            if (iconPos === 'top') {
                el.appendChild(iconEl);
                el.appendChild(textSpan);
            } else {
                el.appendChild(textSpan);
                el.appendChild(iconEl);
            }
        } else {
            el.style.gap = '6px';
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
