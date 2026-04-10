/**
 * OpenAVC Panel UI — Phase 1
 *
 * Connects to the backend via WebSocket, renders the touch panel UI
 * from JSON definitions, and sends user interactions back to the server.
 */

class PanelApp {
    constructor() {
        this.ws = null;
        this.state = {};
        this.uiDef = null;
        this.uiSettings = {};
        this.currentPage = new URLSearchParams(window.location.search).get('page') || 'main';
        this.locked = false;
        this.snapshotReceived = false;
        this.idleTimer = null;
        this.root = document.getElementById('panel-root');
        this.statusEl = document.getElementById('connection-status');
        this.bindings = [];          // Active bindings to evaluate on state change
        this.elementMap = {};        // element_id -> {el, elementDef} for ui.* overrides
        this.holdTimers = {};        // element_id -> interval for hold-repeat mode
        this.debounceTimers = [];    // Track all debounce timeouts for cleanup
        this._pluginMessageHandlers = new Set(); // Track all plugin iframe message handlers
        this._clockElements = [];    // All clock update functions for batched interval
        this._clockInterval = null;  // Single global clock interval
        this._pendingBindingKeys = null; // Batched binding keys for rAF
        this._bindingRafId = null;       // requestAnimationFrame ID
        this.overlayStack = [];      // Stack of overlay page IDs (newest on top)
        this._runningMacros = {};    // macro_id -> { description, step_index, total_steps }
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 10000;
        this.reconnectAttempts = 0;
        this.themeElementDefaults = {};
        this.currentTheme = null;
        this._themeApplyInProgress = false;
    }

    start() {
        this.setupIdleListeners();
        this.connect();
    }

    // --- WebSocket ---

    connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Derive WS path relative to page location so tunneled access works.
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        const url = `${protocol}//${location.host}${basePath}/ws?client=panel&namespaces=device,var,ui,system,plugin`;

        this.ws = new WebSocket(url);

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
            // Always retry with exponential backoff (max 30s)
            setTimeout(() => this.connect(), this.reconnectDelay);
            this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, 30000);
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
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(msg));
        }
    }

    setConnectionStatus(connected) {
        this.statusEl.textContent = connected ? 'Connected' : 'Disconnected';
        this.statusEl.className = connected ? 'connected' : 'disconnected';

        // Offline overlay
        const overlay = document.getElementById('offline-overlay');
        if (overlay) {
            overlay.classList.toggle('visible', !connected);
        }
        // Disable panel interaction when offline
        if (this.root) {
            this.root.style.pointerEvents = connected ? '' : 'none';
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
                break;

            case 'state.update':
                Object.assign(this.state, msg.changes || {});
                this._scheduleBindingEvaluation(Object.keys(msg.changes || {}));
                // Notify plugin iframes of state changes
                for (const [k, v] of Object.entries(msg.changes || {})) {
                    this._notifyPluginIframes(k, v);
                }
                break;

            case 'ui.definition':
                this.uiDef = msg.ui;
                this.uiSettings = msg.ui?.settings || {};
                this.applyOrientation();
                if (this.snapshotReceived) {
                    this.renderCurrentPage();
                }
                this.showLockScreen();
                this.resetIdleTimer();
                break;

            case 'device.status':
                if (msg.device_id) {
                    this.state[`device.${msg.device_id}.connected`] = msg.connected;
                    this.evaluateAllBindings();
                }
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
        // Handle $back / $dismiss — pop overlay stack
        if (pageId === '$back' || pageId === '$dismiss') {
            this.dismissOverlay();
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
            // Regular page — close all overlays and switch
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
            // Clean up clock intervals for overlay elements
            overlayEl.querySelectorAll('.panel-clock').forEach(el => {
                if (el._clockInterval) clearInterval(el._clockInterval);
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
                this.registerVisibleWhen(el, element);
                grid.appendChild(el);
            }
        }

        this._applyPageBackground(grid, page.background);
        content.appendChild(grid);
        container.appendChild(content);

        // Append to root (on top of everything)
        document.body.appendChild(container);

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
                emptyMsg.style.cssText = 'padding:2rem;text-align:center;color:#999;';
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
        // Clean up global clock interval
        if (this._clockInterval) {
            clearInterval(this._clockInterval);
            this._clockInterval = null;
        }
        this._clockElements = [];

        // Page transition settings
        const settings = this.uiSettings || {};
        const pageTransition = settings.page_transition || 'none';
        const transitionDuration = settings.page_transition_duration || 200;
        const entryAnimation = settings.element_entry || 'none';
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
    }

    /**
     * Register a visible_when binding for an element if it has one.
     * Call this after renderElement() for every element placed on a page.
     */
    registerVisibleWhen(el, element) {
        const vw = element.bindings?.visible_when;
        if (!vw) return;

        // Single condition or compound (all:[...])
        const conditions = vw.all || [vw];
        // Collect all keys for the optimized change-detection
        const keys = conditions.map(c => c.key).filter(Boolean);

        this.bindings.push({
            type: 'visible_when',
            element: el,
            elementDef: element,
            binding: { conditions, _keys: keys },
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
        this.applyStyle(el, this.getThemedStyle('button', element.style));

        // Display mode: image buttons
        const displayMode = element.display_mode || 'text';
        if ((displayMode === 'image' || displayMode === 'image_text') && element.button_image) {
            const url = this.resolveAssetUrl(element.button_image);
            el.style.backgroundImage = `url(${url})`;
            el.style.backgroundSize = element.image_fit || 'cover';
            el.style.backgroundPosition = 'center';
            el.style.backgroundRepeat = 'no-repeat';
            if (displayMode === 'image') {
                el.textContent = '';
            } else {
                // image_text: add text shadow for readability
                el.style.textShadow = '0 1px 3px rgba(0,0,0,0.8)';
            }
        } else if (displayMode === 'icon_only') {
            el.textContent = '';
            if (!element.icon_position) element.icon_position = 'center';
        }

        // Render icon+text content
        this.renderElementContent(el, element);

        // Register in element map for ui.* overrides
        this.elementMap[element.id] = { el, elementDef: element };

        // Button mode: tap (default), toggle, hold_repeat, tap_hold
        // Press binding is an array of actions; mode properties come from the first action
        const pressActions = element.bindings?.press || [];
        const pressBinding = (Array.isArray(pressActions) ? pressActions[0] : pressActions) || {};
        const mode = pressBinding.mode || 'tap';
        const holdRepeatMs = pressBinding.hold_repeat_ms || 200;
        const holdThresholdMs = pressBinding.hold_threshold_ms || 500;

        // Toggle without toggle_key falls back to tap mode
        const effectiveMode = (mode === 'toggle' && !pressBinding.toggle_key) ? 'tap' : mode;

        let pressTime = 0;

        const onPress = (e) => {
            e.preventDefault();
            el.classList.add('pressing');
            pressTime = Date.now();

            if (effectiveMode === 'hold_repeat') {
                this.send({ type: 'ui.press', element_id: element.id });
                // Clear any existing timer before starting a new one
                if (this.holdTimers[element.id]) clearInterval(this.holdTimers[element.id]);
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
            e.preventDefault();
            el.classList.remove('pressing');

            if (effectiveMode === 'hold_repeat') {
                clearInterval(this.holdTimers[element.id]);
                delete this.holdTimers[element.id];
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
            el.classList.remove('pressing');
            if (this.holdTimers[element.id]) {
                clearInterval(this.holdTimers[element.id]);
                delete this.holdTimers[element.id];
            }
        });
        el.style.touchAction = 'none';
        el.addEventListener('touchstart', onPress);
        el.addEventListener('touchend', onRelease);
        el.addEventListener('touchcancel', () => {
            el.classList.remove('pressing');
            if (this.holdTimers[element.id]) {
                clearInterval(this.holdTimers[element.id]);
                delete this.holdTimers[element.id];
            }
        });

        // Feedback binding
        if (element.bindings && element.bindings.feedback) {
            this.bindings.push({
                type: 'feedback',
                element: el,
                elementDef: element,
                binding: element.bindings.feedback,
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

        // Text binding
        if (element.bindings && element.bindings.text) {
            const textBinding = element.bindings.text;
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

        const dot = document.createElement('div');
        dot.className = 'led-dot';
        el.appendChild(dot);

        // Color binding
        if (element.bindings && element.bindings.color) {
            this.bindings.push({
                type: 'color',
                element: dot,
                elementDef: element,
                binding: element.bindings.color,
            });
        }

        return el;
    }

    renderSlider(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-slider';
        el.dataset.elementId = element.id;

        if (element.label) {
            const label = document.createElement('label');
            label.textContent = element.label;
            el.appendChild(label);
        }

        const input = document.createElement('input');
        input.type = 'range';
        input.min = element.min ?? 0;
        input.max = element.max ?? 100;
        input.step = element.step ?? 1;
        input.setAttribute('aria-label', element.label || element.id);

        // Set initial value from state if binding exists, else use min
        const sliderBinding = element.bindings?.variable || element.bindings?.value;
        const initialValue = sliderBinding?.key ? this.state[sliderBinding.key] : undefined;
        input.value = (initialValue !== undefined && initialValue !== null) ? initialValue : (element.min ?? 0);

        // Debounced change handler
        let changeTimeout = null;
        input.addEventListener('input', () => {
            if (changeTimeout) clearTimeout(changeTimeout);
            changeTimeout = setTimeout(() => {
                this.send({
                    type: 'ui.change',
                    element_id: element.id,
                    value: parseFloat(input.value),
                });
            }, 100);
            this.debounceTimers.push(changeTimeout);
        });

        el.appendChild(input);

        // Variable binding (two-way) or value binding (read-only)
        const valueBinding = (element.bindings && element.bindings.variable) || (element.bindings && element.bindings.value);
        if (valueBinding) {
            this.bindings.push({
                type: 'slider_value',
                element: input,
                elementDef: element,
                binding: valueBinding,
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
        for (const opt of options) {
            const option = document.createElement('option');
            option.value = opt.value;
            option.textContent = opt.label;
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

        // Variable binding (two-way) or value binding (read-only)
        const valueBinding = (element.bindings && element.bindings.variable) || (element.bindings && element.bindings.value);
        if (valueBinding) {
            this.bindings.push({
                type: 'select_value',
                element: select,
                elementDef: element,
                binding: valueBinding,
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

        const valueBinding = (element.bindings && element.bindings.variable) || (element.bindings && element.bindings.value);
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
            img.src = element.src;
            img.alt = element.label || 'Panel image';
            img.loading = 'lazy';
            img.onerror = () => {
                img.style.display = 'none';
                const placeholder = document.createElement('div');
                placeholder.textContent = 'Image not found';
                placeholder.title = element.src;
                placeholder.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:#999;font-size:12px;';
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
        return el;
    }

    renderCameraPreset(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-button';
        el.dataset.elementId = element.id;

        let content = element.label || 'Preset';
        if (element.preset_number != null) {
            content = element.preset_number + '\n' + content;
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

        if (element.bindings && element.bindings.feedback) {
            this.bindings.push({
                type: 'feedback',
                element: el,
                elementDef: element,
                binding: element.bindings.feedback,
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
        const style = element.style || {};
        const itemBg = style.item_bg || '#2a2a4e';
        const itemActiveBg = style.item_active_bg || '#42a5f5';

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));

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
            const selBinding = element.bindings?.selected;
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
                            this.send({ type: 'ui.change', element_id: element.id, value: item.value });
                        } else if (listStyle === 'multi_select') {
                            if (selectedValues.has(String(item.value))) {
                                selectedValues.delete(String(item.value));
                            } else {
                                selectedValues.add(String(item.value));
                            }
                            this.send({ type: 'ui.change', element_id: element.id, value: item.value });
                        } else if (listStyle === 'action') {
                            this.send({ type: 'ui.press', element_id: element.id, value: item.value });
                        }
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
        const itemsBinding = element.bindings?.items;
        if (itemsBinding) {
            this.bindings.push({
                type: 'list_items',
                element: el,
                elementDef: element,
                binding: itemsBinding,
                _list: { renderItems, scrollArea, staticItems, itemBg, itemActiveBg, listStyle, selectedValues },
            });
        }

        // Selection binding
        const selBinding = element.bindings?.selected;
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
        const inputKeyPattern = config.input_key_pattern || '';
        const outputKeyPattern = config.output_key_pattern || '';
        const matrixStyle = element.matrix_style || 'crosspoint';
        const style = element.style || {};
        const activeColor = style.crosspoint_active_color || '#4CAF50';
        const inactiveColor = style.crosspoint_inactive_color || '#333333';
        const cellSize = style.cell_size || 44;

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));

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

        // Presets bar (if presets defined in bindings)
        const presets = element.bindings?.presets || [];
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
                        this.send({ type: 'macro.run', macro_id: preset.macro });
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
                    if (config.audio_follow_video && element.bindings?.audio_route) {
                        this.send({
                            type: 'ui.route',
                            element_id: element.id,
                            input: inputIdx,
                            output: outputIdx,
                            audio: true,
                        });
                    }
                });

                // Lock toggle
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

                // Mute toggle
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
                    // Disable routing when output is muted
                    if (select) select.disabled = mutedOutputs.has(outputIdx);
                });
                row.appendChild(muteBtn);

                row.appendChild(select);
                list.appendChild(row);
            }

            scrollWrap.appendChild(list);
        } else {
            // --- Crosspoint view ---
            // Extra column for lock/mute controls
            const extraCols = 2; // lock + mute columns
            const table = document.createElement('div');
            table.className = 'matrix-grid';
            table.style.gridTemplateColumns = `auto repeat(${inputCount}, ${cellSize}px) 28px 28px`;
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
            const lockHdr = document.createElement('div');
            lockHdr.className = 'matrix-header';
            lockHdr.textContent = '\uD83D\uDD12';
            lockHdr.style.fontSize = '10px';
            table.appendChild(lockHdr);
            const muteHdr = document.createElement('div');
            muteHdr.className = 'matrix-header';
            muteHdr.textContent = 'M';
            muteHdr.style.fontSize = '10px';
            table.appendChild(muteHdr);

            // Drag-to-route state
            let dragLine = null;
            let dragStartInput = null;

            // Output rows with crosspoints
            for (let o = 0; o < outputCount; o++) {
                // Output label
                const outHeader = document.createElement('div');
                outHeader.className = 'matrix-header matrix-output-header';
                outHeader.textContent = outputLabels[o] || `Out ${o + 1}`;
                outHeader.dataset.outputIdx = String(o);
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
                        if (config.audio_follow_video && element.bindings?.audio_route) {
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
                                }
                            }
                            dragStartInput = null;
                        };
                        document.addEventListener('pointermove', onMove);
                        document.addEventListener('pointerup', onUp);
                    });

                    cell.appendChild(dot);
                    table.appendChild(cell);
                }

                // Lock button for this output
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

                // Mute button for this output
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
                });
                muteCell.appendChild(muteBtn);
                table.appendChild(muteCell);
            }

            scrollWrap.appendChild(table);
        }

        el.appendChild(scrollWrap);
        this.elementMap[element.id] = { el, elementDef: element };

        // State binding for routes
        if (routePattern) {
            this.bindings.push({
                type: 'matrix_routes',
                element: el,
                elementDef: element,
                binding: { key: routePattern },
                _matrix: {
                    routePattern, inputKeyPattern, outputKeyPattern,
                    inputCount, outputCount, activeColor, inactiveColor,
                    matrixStyle,
                },
            });
        }

        return el;
    }

    evaluateMatrixRoutes(b) {
        const { routePattern, inputKeyPattern, outputKeyPattern, inputCount, outputCount, activeColor, inactiveColor, matrixStyle } = b._matrix;
        const el = b.element;

        // Read current routes from state
        const routes = {};  // output (1-based) -> input (1-based)
        for (let o = 1; o <= outputCount; o++) {
            const key = routePattern.replace('*', String(o));
            const val = this.state[key];
            if (val !== undefined && val !== null) {
                routes[o] = parseInt(String(val));
            }
        }

        // Update dynamic labels from state
        if (inputKeyPattern) {
            const headers = el.querySelectorAll('[data-input-idx]');
            headers.forEach(h => {
                const idx = parseInt(h.dataset.inputIdx);
                const key = inputKeyPattern.replace('*', String(idx + 1));
                const val = this.state[key];
                if (val !== undefined && val !== null) h.textContent = String(val);
            });
        }
        if (outputKeyPattern) {
            const headers = el.querySelectorAll('[data-output-idx]');
            headers.forEach(h => {
                const idx = parseInt(h.dataset.outputIdx);
                const key = outputKeyPattern.replace('*', String(idx + 1));
                const val = this.state[key];
                if (val !== undefined && val !== null) h.textContent = String(val);
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
        const style = element.style || {};
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
        if (element.bindings && element.bindings.value) {
            this.bindings.push({
                type: 'gauge_value',
                element: el,
                elementDef: element,
                binding: element.bindings.value,
                _svg: { fgPath, valueText, startAngle, endAngle, radius, cx, cy, min, max, unit, gaugeColor, zones, showValue, arcPath: arcPath, polarToCart },
            });
        }

        return el;
    }

    evaluateGaugeValue(b) {
        const raw = this.state[b.binding.key];
        if (raw === undefined || raw === null) return;
        // Memoize: skip if value unchanged
        if (b._lastGaugeRaw === raw) return;
        b._lastGaugeRaw = raw;
        const { fgPath, valueText, startAngle, endAngle, radius, min, max, unit, gaugeColor, zones, showValue, arcPath: arcPathFn } = b._svg;
        const value = Math.max(min, Math.min(max, Number(raw)));
        const frac = (value - min) / (max - min);
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
        const style = element.style || {};
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

        // Create segments (for vertical: bottom=min, top=max)
        for (let i = 0; i < segments; i++) {
            const seg = document.createElement('div');
            seg.className = 'meter-segment';
            const segValue = min + (i / (segments - 1)) * (max - min);
            if (segValue >= yellowTo) {
                seg.dataset.zone = 'red';
            } else if (segValue >= greenTo) {
                seg.dataset.zone = 'yellow';
            } else {
                seg.dataset.zone = 'green';
            }
            // Use solid dim color instead of opacity to prevent background bleed-through
            const dimColors = { green: '#0f2410', yellow: '#332701', red: '#310d0b' };
            seg.style.backgroundColor = dimColors[seg.dataset.zone] || dimColors.green;
            seg.style.opacity = '1';
            bar.appendChild(seg);
        }

        el.appendChild(bar);

        this.applyStyle(el, this.getThemedStyle(element.type, element.style));
        this.elementMap[element.id] = { el, elementDef: element };

        // Value binding
        if (element.bindings && element.bindings.value) {
            this.bindings.push({
                type: 'level_meter_value',
                element: el,
                elementDef: element,
                binding: element.bindings.value,
                _meter: { segments, min, max, bar, showPeak, peakValue: -Infinity, peakTime: 0, peakHoldMs: style.peak_hold_ms || 1500 },
            });
        }

        return el;
    }

    evaluateLevelMeterValue(b) {
        const raw = this.state[b.binding.key];
        if (raw === undefined || raw === null) return;
        const { segments, min, max, bar, showPeak, peakHoldMs } = b._meter;
        const value = Math.max(min, Math.min(max, Number(raw)));
        const frac = (value - min) / (max - min);
        const litCount = Math.round(frac * segments);

        // Peak hold
        const now = Date.now();
        if (value > b._meter.peakValue || now - b._meter.peakTime > peakHoldMs) {
            b._meter.peakValue = value;
            b._meter.peakTime = now;
        }
        const peakFrac = (b._meter.peakValue - min) / (max - min);
        const peakIdx = Math.round(peakFrac * (segments - 1));

        const segs = bar.querySelectorAll('.meter-segment');
        const activeColors = { green: '#4CAF50', yellow: '#FF9800', red: '#F44336' };
        const dimColors = { green: '#0f2410', yellow: '#332701', red: '#310d0b' };
        for (let i = 0; i < segs.length; i++) {
            const zone = segs[i].dataset.zone || 'green';
            if (i < litCount) {
                segs[i].style.backgroundColor = activeColors[zone] || activeColors.green;
                segs[i].style.opacity = '1';
            } else if (showPeak && i === peakIdx) {
                segs[i].style.backgroundColor = activeColors[zone] || activeColors.green;
                segs[i].style.opacity = '0.7';
            } else {
                segs[i].style.backgroundColor = dimColors[zone] || dimColors.green;
                segs[i].style.opacity = '1';
            }
        }
    }

    // --- Fader ---

    renderFader(element) {
        const el = document.createElement('div');
        el.className = 'panel-element panel-fader';
        el.dataset.elementId = element.id;

        const orientation = element.orientation || 'vertical';
        let min = parseFloat(element.min ?? -80) || -80;
        let max = parseFloat(element.max ?? 10) || 10;
        if (min >= max) { const tmp = min; min = max; max = tmp; }
        const step = element.step ?? 0.5;
        const unit = element.unit || 'dB';
        const style = element.style || {};
        const showValue = style.show_value !== false;
        const showScale = style.show_scale !== false;

        el.classList.add(orientation === 'horizontal' ? 'horizontal' : 'vertical');

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
                const frac = (m - min) / (max - min);
                mark.style.bottom = `${frac * 100}%`;
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
            valueDisplay.textContent = `0 ${unit}`;
            el.appendChild(valueDisplay);
        }

        // Position handle — initial value from state or 0
        const valueBinding = element.bindings?.value;
        let currentValue = 0;
        if (valueBinding?.key) {
            const sv = this.state[valueBinding.key];
            if (sv !== undefined && sv !== null) currentValue = Number(sv);
        }
        currentValue = Math.max(min, Math.min(max, currentValue));
        const initFrac = (currentValue - min) / (max - min);
        handle.style.bottom = `${initFrac * 100}%`;
        if (valueDisplay) valueDisplay.textContent = `${Math.round(currentValue * 10) / 10} ${unit}`;

        // Touch/mouse drag interaction
        let dragging = false;
        let debounceTimer = null;

        const getValueFromEvent = (e) => {
            const rect = trackWrap.getBoundingClientRect();
            const clientY = e.touches ? e.touches[0].clientY : e.clientY;
            const frac = 1 - Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
            const val = min + frac * (max - min);
            return Math.round(val / step) * step;
        };

        const updateFader = (val) => {
            const frac = (val - min) / (max - min);
            handle.style.bottom = `${frac * 100}%`;
            handle.setAttribute('aria-valuenow', String(Math.round(val * 10) / 10));
            if (valueDisplay) valueDisplay.textContent = `${Math.round(val * 10) / 10} ${unit}`;
        };

        const sendChange = (val) => {
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                this.send({ type: 'ui.change', element_id: element.id, value: val });
            }, 50);
            this.debounceTimers.push(debounceTimer);
        };

        const onStart = (e) => {
            e.preventDefault();
            dragging = true;
            const val = getValueFromEvent(e);
            updateFader(val);
            sendChange(val);
        };
        const onMove = (e) => {
            if (!dragging) return;
            e.preventDefault();
            const val = getValueFromEvent(e);
            updateFader(val);
            sendChange(val);
        };
        const onEnd = () => {
            dragging = false;
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
            const step = (max - min) * 0.02; // 2% per keystroke
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
                _fader: { handle, valueDisplay, min, max, unit },
            });
        }

        return el;
    }

    _faderScaleMarks(min, max) {
        // Generate sensible scale marks for a dB fader
        const range = max - min;
        if (range <= 20) return [min, Math.round((min + max) / 2), max];
        const marks = [];
        const candidates = [-80, -60, -40, -20, -10, -5, 0, 5, 10, 20];
        for (const c of candidates) {
            if (c >= min && c <= max) marks.push(c);
        }
        if (marks.length < 3) {
            return [min, Math.round((min + max) / 2), max];
        }
        return marks;
    }

    evaluateFaderValue(b) {
        const raw = this.state[b.binding.key];
        if (raw === undefined || raw === null) return;
        const { handle, valueDisplay, min, max, unit } = b._fader;
        const value = Math.max(min, Math.min(max, Number(raw)));
        const frac = (value - min) / (max - min);
        handle.style.bottom = `${frac * 100}%`;
        if (valueDisplay) valueDisplay.textContent = `${Math.round(value * 10) / 10} ${unit}`;
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
        const format = element.format || 'h:mm A';
        const timezone = element.timezone || undefined;
        const durationMin = element.duration_minutes || 60;

        // Meeting timer state
        let meetingStarted = false;
        let meetingStartTime = null;

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
                    if (element.target_time) {
                        const target = new Date(element.target_time);
                        const diff = Math.max(0, target - now);
                        text = this._formatDuration(diff);
                    } else {
                        // Check state binding for countdown target
                        const key = element.bindings?.value?.key || element.start_key;
                        const stateVal = key ? this.state[key] : null;
                        if (stateVal) {
                            const target = new Date(stateVal);
                            const diff = Math.max(0, target - now);
                            text = this._formatDuration(diff);
                        } else {
                            text = '--:--:--';
                        }
                    }
                    break;
                }
                case 'elapsed': {
                    const key = element.start_key;
                    const stateVal = key ? this.state[key] : null;
                    if (stateVal) {
                        const start = new Date(stateVal);
                        const diff = Math.max(0, now - start);
                        text = this._formatDuration(diff);
                    } else {
                        text = '00:00:00';
                    }
                    break;
                }
                case 'meeting': {
                    if (!meetingStarted) {
                        meetingStarted = true;
                        meetingStartTime = now;
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
        // Register with global clock interval instead of per-element interval
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
                }).formatToParts(date);
                const get = (type) => parts.find(p => p.type === type)?.value || '';
                const tzDate = {
                    year: parseInt(get('year')),
                    month: parseInt(get('month')),
                    day: parseInt(get('day')),
                    hour: parseInt(get('hour')),
                    minute: parseInt(get('minute')),
                    second: parseInt(get('second')),
                };
                d = tzDate;

                return this._applyFormat(d, format, true);
            } catch (e) {
                // Fall through to local time
            }
        }
        return this._applyFormat({
            year: d.getFullYear(), month: d.getMonth() + 1, day: d.getDate(),
            hour: d.getHours(), minute: d.getMinutes(), second: d.getSeconds()
        }, format, true);
    }

    _applyFormat(d, format) {
        const h24 = d.hour;
        const h12 = h24 % 12 || 12;
        const ampm = h24 < 12 ? 'AM' : 'PM';
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

        return format
            .replace(/HH/g, String(h24).padStart(2, '0'))
            .replace(/H/g, String(h24))
            .replace(/hh/g, String(h12).padStart(2, '0'))
            .replace(/\bh\b/g, String(h12))
            .replace(/mm/g, String(d.minute).padStart(2, '0'))
            .replace(/ss/g, String(d.second).padStart(2, '0'))
            .replace(/A/g, ampm)
            .replace(/a/g, ampm.toLowerCase())
            .replace(/YYYY/g, String(d.year))
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
            el.style.color = '#999';
            el.style.fontSize = '12px';
            el.style.display = 'flex';
            el.style.alignItems = 'center';
            el.style.justifyContent = 'center';
            return el;
        }

        // Resolve renderer URL using relative path (works through tunnels)
        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        const rendererUrl = `${basePath}/api/plugins/${encodeURIComponent(pluginId)}/panel/${encodeURIComponent(pluginType)}.html`;

        const iframe = document.createElement('iframe');
        iframe.src = rendererUrl;
        iframe.sandbox = 'allow-scripts';
        iframe.style.cssText = 'width:100%; height:100%; border:none; border-radius:inherit;';
        iframe.setAttribute('loading', 'lazy');
        el.style.overflow = 'hidden';
        el.appendChild(iframe);

        // Loading indicator
        const loadingIndicator = document.createElement('div');
        loadingIndicator.textContent = 'Loading plugin...';
        loadingIndicator.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:#999;font-size:12px;position:absolute;inset:0;z-index:1;';
        el.style.position = 'relative';
        el.appendChild(loadingIndicator);

        // Store reference for state updates
        this.elementMap[element.id] = el;
        el._pluginIframe = iframe;
        el._pluginId = pluginId;
        el._pluginConfig = element.plugin_config || {};

        // postMessage API: send initial config + theme when iframe loads
        iframe.addEventListener('load', () => {
            loadingIndicator.remove();
            const themeVars = {};
            const root = document.documentElement;
            for (const prop of ['--panel-bg', '--panel-text', '--panel-accent',
                '--panel-button-bg', '--panel-button-text', '--panel-button-active-bg',
                '--panel-button-active-text', '--panel-danger', '--panel-success',
                '--panel-warning', '--panel-grid-gap', '--panel-border-radius']) {
                themeVars[prop] = getComputedStyle(root).getPropertyValue(prop).trim();
            }
            iframe.contentWindow.postMessage({
                type: 'openavc:init',
                config: element.plugin_config || {},
                theme: themeVars,
                elementId: element.id,
            }, '*');  // sandboxed iframe has opaque origin; source check provides security
        });

        // Listen for messages from plugin iframe
        const handler = (event) => {
            if (event.source !== iframe.contentWindow) return;
            const msg = event.data;
            if (!msg || !msg.type) return;

            switch (msg.type) {
                case 'openavc:action':
                    // Plugin requests a device command or state change
                    if (msg.action === 'device.command' && msg.device && msg.command) {
                        this.ws?.send(JSON.stringify({
                            type: 'device.command',
                            device_id: msg.device,
                            command: msg.command,
                            params: msg.params || {},
                        }));
                    } else if (msg.action === 'state.set' && msg.key) {
                        this.ws?.send(JSON.stringify({
                            type: 'state.set',
                            key: msg.key,
                            value: msg.value,
                        }));
                    }
                    break;
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

    // Send state update to plugin iframes
    _notifyPluginIframes(key, value) {
        for (const [id, el] of Object.entries(this.elementMap)) {
            if (el?._pluginIframe?.contentWindow) {
                el._pluginIframe.contentWindow.postMessage({
                    type: 'openavc:state',
                    key,
                    value,
                }, '*');  // sandboxed iframe has opaque origin; source check provides security
            }
        }
    }

    // --- Bindings ---

    _updateMacroBusyState(macroId) {
        // Apply or remove busy state on buttons whose press binding triggers this macro
        for (const [elemId, entry] of Object.entries(this.elementMap)) {
            const pressActions = entry.elementDef?.bindings?.press;
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
                    const bKeys = b.binding?._keys;  // visible_when: array of keys
                    const bPattern = b.binding?.key_pattern || b._routePattern;
                    if (bKeys && !bKeys.some(k => changedKeys.includes(k))) continue;
                    if (bKey && !bKeys && !changedKeys.includes(bKey)) continue;
                    if (bPattern) {
                        const prefix = bPattern.replace(/\*.*$/, '');
                        if (!changedKeys.some(k => k.startsWith(prefix))) continue;
                    }
                    if (!bKey && !bPattern) { /* safety: evaluate anyway */ }
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
            const { el, elementDef } = entry;
            const prefix = `ui.${elementId}.`;

            // Check for label override
            const labelOverride = this.state[prefix + 'label'];
            if (labelOverride !== undefined && labelOverride !== null) {
                el.textContent = String(labelOverride);
            }

            // Check for style overrides
            const bgOverride = this.state[prefix + 'bg_color'];
            if (bgOverride !== undefined && bgOverride !== null) {
                el.style.backgroundColor = String(bgOverride);
            }

            const textColorOverride = this.state[prefix + 'text_color'];
            if (textColorOverride !== undefined && textColorOverride !== null) {
                el.style.color = String(textColorOverride);
            }

            const opacityOverride = this.state[prefix + 'opacity'];
            if (opacityOverride !== undefined && opacityOverride !== null) {
                el.style.opacity = String(opacityOverride);
            }

            const visibleOverride = this.state[prefix + 'visible'];
            if (visibleOverride !== undefined && visibleOverride !== null) {
                el.style.display = (visibleOverride === false || visibleOverride === 'false')
                    ? 'none' : '';
            }
        }
    }

    evaluateVisibleWhen(b) {
        const { element, binding } = b;
        const conditions = binding.conditions || [];
        // All conditions must be true for the element to be visible
        const visible = conditions.every(cond => {
            const actual = this.state[cond.key];
            return this._evalConditionOp(cond.operator || 'eq', actual, cond.value);
        });
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

        // Multi-state feedback (new)
        if (binding.states) {
            const stateKey = stateValue != null ? String(stateValue) : (binding.default_state || '');
            const appearance = binding.states[stateKey] || binding.states[binding.default_state || ''] || {};
            const style = { ...baseStyle, ...appearance };
            this.applyStyle(element, style);

            // Update label
            if (appearance.label !== undefined) {
                element.textContent = String(appearance.label);
            } else if (elementDef.label) {
                element.textContent = elementDef.label;
            }

            // Update icon if specified
            if (appearance.icon !== undefined || appearance.icon_color !== undefined) {
                const iconDef = {
                    ...elementDef,
                    icon: appearance.icon || elementDef.icon,
                    icon_color: appearance.icon_color || elementDef.icon_color,
                };
                this.renderElementContent(element, iconDef);
            }

            // Swap button image if specified
            if (appearance.button_image) {
                const url = this.resolveAssetUrl(String(appearance.button_image));
                element.style.backgroundImage = `url(${url})`;
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

        // Image button active/inactive swap
        if (isActive && elementDef.button_image_active) {
            const url = this.resolveAssetUrl(elementDef.button_image_active);
            element.style.backgroundImage = `url(${url})`;
        } else if (!isActive && elementDef.button_image) {
            const url = this.resolveAssetUrl(elementDef.button_image);
            element.style.backgroundImage = `url(${url})`;
        }

        // Conditional labels
        if (isActive && binding.label_active) {
            element.textContent = binding.label_active;
        } else if (!isActive && binding.label_inactive) {
            element.textContent = binding.label_inactive;
        } else if (style.label !== undefined) {
            element.textContent = style.label;
        } else if (elementDef.label) {
            element.textContent = elementDef.label;
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
            const isMatch = value === binding.condition.equals;
            setText(isMatch ? (binding.text_true || '') : (binding.text_false || ''));
            return;
        }

        if (value === undefined || value === null) {
            setText('');
            return;
        }
        if (binding.format) {
            setText(binding.format.replace('{value}', value));
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
        element.classList.toggle('active', value !== null && value !== undefined && value !== 'off');

        // Add glow effect for active states
        if (color !== defaultColor) {
            element.style.boxShadow = `0 0 10px ${color}`;
        } else {
            element.style.boxShadow = '0 0 6px rgba(0,0,0,0.3)';
        }
    }

    evaluateSliderValue(b) {
        const { element, binding } = b;
        const value = this.state[binding.key];
        if (value !== undefined && value !== null) {
            element.value = value;
        }
    }

    evaluateSelectValue(b) {
        const { element, binding } = b;
        const value = this.state[binding.key];
        if (value !== undefined && value !== null) {
            element.value = String(value);
        }
    }

    evaluateTextInputValue(b) {
        const { element, binding } = b;
        // Don't overwrite if user is actively editing (prevents cursor loss)
        if (document.activeElement === element) return;
        const value = this.state[binding.key];
        if (value !== undefined && value !== null) {
            element.value = String(value);
        }
    }

    // --- Lock Screen ---

    showLockScreen() {
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
        const timeout = this.uiSettings?.idle_timeout_seconds;
        if (!timeout || timeout <= 0 || this.locked) return;

        this.idleTimer = setTimeout(() => {
            const idlePage = this.uiSettings?.idle_page || 'main';
            if (this.currentPage !== idlePage || this.overlayStack.length > 0) {
                this.dismissAllOverlays();
                this.currentPage = idlePage;
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

        const pathParts = location.pathname.split('/panel');
        const basePath = pathParts[0] || '';
        const prevDefaults = JSON.stringify(this.themeElementDefaults || {});

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

        // Map theme variables to CSS custom properties
        const varMap = {
            panel_bg: '--panel-bg',
            panel_text: '--panel-text',
            accent: '--panel-accent',
            button_bg: '--panel-button-bg',
            button_text: '--panel-button-text',
            button_active_bg: '--panel-button-active-bg',
            button_active_text: '--panel-button-active-text',
            danger: '--panel-danger',
            success: '--panel-success',
            warning: '--panel-warning',
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

        // Per-setting overrides take priority
        if (settings.accent_color) {
            root.style.setProperty('--panel-accent', settings.accent_color);
            root.style.setProperty('--panel-button-active-bg', settings.accent_color);
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
            root.style.setProperty('--panel-button-active-bg', settings.accent_color);
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
            root.style.setProperty('--panel-button-active-text', '#ffffff');
        }
        this.themeElementDefaults = {};
    }

    _applyPageBackground(gridEl, bg) {
        if (!bg) return;
        gridEl.style.position = 'relative';

        // Solid color
        if (bg.color) {
            gridEl.style.backgroundColor = bg.color;
        }
        // Background image with opacity
        if (bg.image) {
            const pathParts = location.pathname.split('/panel');
            const basePath = pathParts[0] || '';
            const imgUrl = bg.image.startsWith('assets://')
                ? `${basePath}/api/projects/default/assets/${bg.image.replace('assets://', '')}`
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

        // Border
        if (style.border_width) {
            el.style.borderWidth = style.border_width + 'px';
            el.style.borderStyle = style.border_style || 'solid';
            el.style.borderColor = style.border_color || '#666666';
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
    }

    _sanitizeCssValue(value) {
        // Strip CSS injection vectors: expressions, url(), @import, javascript:, behavior
        if (typeof value !== 'string') return String(value ?? '');
        return value.replace(/expression\s*\(/gi, '')
                     .replace(/javascript\s*:/gi, '')
                     .replace(/behavior\s*:/gi, '')
                     .replace(/@import/gi, '')
                     .replace(/\\/g, '');
    }

    _sanitizeCssUrl(url) {
        // Only allow http:, https:, data:image, and relative URLs
        if (typeof url !== 'string') return '';
        const trimmed = url.trim();
        if (trimmed.startsWith('javascript:') || trimmed.startsWith('data:text')) return '';
        return CSS.escape ? trimmed : trimmed.replace(/['"\\()]/g, '');
    }

    resolveAssetUrl(ref) {
        if (!ref) return '';
        if (ref.startsWith('assets://')) {
            // Resolve to project asset endpoint
            return `/api/projects/default/assets/${ref.slice('assets://'.length)}`;
        }
        return ref;
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
        const labelText = el.textContent;

        // Clear existing content
        el.textContent = '';

        const iconEl = this.renderIcon(icon, iconSize, iconColor);
        if (!iconEl) return;

        if (iconPos === 'center') {
            // Icon only, no text
            el.appendChild(iconEl);
            return;
        }

        const textSpan = document.createElement('span');
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
document.addEventListener('DOMContentLoaded', () => app.start());
