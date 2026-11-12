/**
 * Custom controls and plugin panels: a web page somebody else wrote, run in a
 * sandboxed frame inside one element's box, and the bridge it talks to the
 * room through.
 *
 * Both element types are this one machinery; what differs is where the page
 * comes from, what it may see, and (for a plugin) an auth token for its own
 * routes, which the two renderers in panel.js say in a `source`. One frame for
 * both, because a second copy of the bridge is how one of them quietly loses a
 * guard.
 *
 * The grant is the rule worth knowing before changing anything here. What an
 * element was granted (devices, variables, the macro and navigation switches)
 * is set by whoever placed it, and every way a frame reaches the room asks
 * it: what state it is shown, at the start and live (`mayRead`), which keys it
 * may write (`mayWrite`), which devices it may command (`mayCommand`), and the
 * two switches. THE PANEL IS THE ENFORCEMENT POINT, AND IT CANNOT BE ANYWHERE
 * ELSE: a WebSocket frame carries no element identity, so the server cannot
 * tell which element asked for a command. What makes it a real boundary is
 * the sandbox: the frame has an opaque origin, cannot reach this document's
 * memory or its socket, and can only ask the panel to act for it. Do not
 * assume the server double-checks. It does not.
 *
 * The node a frame draws carries what the panel and its tests read off it:
 * `_pluginIframe`, `_pluginMessageHandler`, `_pluginConfig`, `_grant`,
 * `_ownNamespace` and `_stateFilter`. The grant is read off the node each time
 * a message arrives, never a copy, so a re-render that changes it is obeyed.
 *
 * The page review reads this module's text for what a custom control or a
 * plugin panel reads off its definition (tests/test_ui_page_review_mirrors.py
 * credits all of it to both renderers), so a definition is read here only
 * through a parameter named `element`, and only what both types have. What one
 * type reads alone stays in its renderer in panel.js.
 */

import { basePath } from './base_path.js';
import { applyStyle } from './paint.js';

// The theme a frame is told about when it opens.
const THEME_VARIABLES = [
    '--panel-bg', '--panel-text', '--panel-accent',
    '--panel-button-bg', '--panel-button-text', '--panel-button-border',
    '--panel-surface', '--panel-surface-border',
    '--panel-danger', '--panel-success', '--panel-warning',
    '--panel-border-radius',
];

const NO_GRANT = Object.freeze({ devices: [], variables: [], macros: false, navigate: false });

// --- The grant ---------------------------------------------------------------

/** The grant on an element, normalised so no check has to ask what shape it
 *  is. An element with no grant reaches nothing, which is what every element
 *  in every project written before grants existed has. */
export function grantOf(element) {
    const g = (element && element.grant) || {};
    return {
        devices: Array.isArray(g.devices) ? g.devices.map(String) : [],
        variables: Array.isArray(g.variables) ? g.variables.map(String) : [],
        macros: g.macros === true,
        navigate: g.navigate === true,
    };
}

/** A key in the namespace the frame owns outright (a plugin's `plugin.<id>.`). */
function ownsKey(ownNamespace, key) {
    return !!ownNamespace && key.startsWith(ownNamespace);
}

/** A variable is matched exactly, so `room_volume` is not `room_volume_max`. */
function grantsVariable(grant, key) {
    return grant.variables.some((id) => key === `var.${id}`);
}

/** A device's keys match on the prefix INCLUDING the trailing dot: a grant on
 *  `dsp1` must not also hand over `dsp10`. That is also what makes child
 *  entities work: their keys are `device.<parent>.<child>.<id>.<prop>`, so the
 *  parent's grant covers them without listing keys that exist only at runtime. */
function grantsDeviceKey(grant, key) {
    return grant.devices.some((id) => key.startsWith(`device.${id}.`));
}

/** What state a frame may see: asked by the opening snapshot and every live
 *  push, so the two cannot answer differently. */
export function mayRead(grant, ownNamespace, key) {
    const k = String(key);
    return ownsKey(ownNamespace, k) || grantsDeviceKey(grant, k) || grantsVariable(grant, k);
}

/** Which keys a frame may write: its own namespace and the variables it holds.
 *  Both are asked whatever the first answers, as the bridge always has. */
function mayWrite(grant, ownNamespace, key) {
    const own = ownsKey(ownNamespace, key);
    const granted = grantsVariable(grant, key);
    return own || granted;
}

/** Which devices a frame may command. */
function mayCommand(grant, device) {
    return grant.devices.includes(String(device));
}

/** The grant as the opening message tells it, so a control can hide a button
 *  for a device it cannot command instead of sending into a refusal. */
function grantMessage(grant) {
    return {
        devices: [...grant.devices],
        variables: [...grant.variables],
        macros: grant.macros,
        navigate: grant.navigate,
    };
}

// --- The bridge -------------------------------------------------------------

/** What an `openavc:action` message asks for: the gate it must pass, what is
 *  said when it does not, and the frame sent when it does. Null for an action
 *  the bridge does not carry, or one missing what it names. */
function bridgeRequest(msg) {
    if (msg.action === 'device.command') return msg.device && msg.command ? commandRequest(msg) : null;
    if (msg.action === 'state.set') return msg.key ? writeRequest(msg) : null;
    if (msg.action === 'macro.run') return msg.macro ? macroRequest(msg) : null;
    return null;
}

function commandRequest(msg) {
    return {
        allowed: (grant) => mayCommand(grant, msg.device),
        refusal: () => `attempted a command on device '${msg.device}', which it was not granted`,
        frame: () => ({ type: 'command', device_id: msg.device, command: msg.command, params: msg.params || {} }),
    };
}

function writeRequest(msg) {
    const key = String(msg.key);
    return {
        allowed: (grant, ownNamespace) => mayWrite(grant, ownNamespace, key),
        refusal: () => `attempted to write '${key}', which it was not granted`,
        frame: () => ({ type: 'state.set', key: msg.key, value: msg.value }),
    };
}

function macroRequest(msg) {
    return {
        allowed: (grant) => grant.macros,
        refusal: () => `attempted to run macro '${msg.macro}' without the macro switch`,
        frame: () => ({ type: 'macro.execute', macro_id: String(msg.macro) }),
    };
}

// --- Drawing ----------------------------------------------------------------

/** The box a frame element draws when it cannot render for real: unconfigured
 *  (no `detail`), or the design canvas where a plugin's code does not run. */
export function framePlaceholder(host, element, { styleType, label, detail }) {
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
        // Unconfigured: one line, no border -- it is not a stand-in for
        // something that would otherwise draw.
        el.style.opacity = '0.5';
        el.appendChild(textLine(label, { fontSize: '0.8571em' }));
        return el;
    }
    el.style.border = '1px dashed var(--panel-text, rgba(255,255,255,0.3))';
    el.style.borderRadius = '4px';
    el.style.opacity = '0.4';
    el.appendChild(textLine(label, { fontWeight: '600', fontSize: '0.7857em' }));
    el.appendChild(textLine(detail, { opacity: '0.8', fontSize: '0.7143em' }));
    applyStyle(el, host.themedStyle(styleType, element.style));
    return el;
}

function textLine(text, style) {
    const line = document.createElement('div');
    line.textContent = text;
    Object.assign(line.style, style);
    return line;
}

/**
 * A frame element: the page from `source` running in a sandboxed frame, or a
 * placeholder in the design canvas for a source that does not run there. The
 * `host` is the panel, read when the frame asks (tests replace its methods).
 */
export function drawFrame(host, element, source) {
    // Edit mode: a plugin element stays a placeholder -- it is somebody
    // else's shipped code and the author is not editing it, so there is
    // nothing to see and no reason to run it while they drag. A custom
    // control is the opposite: it is the thing being written.
    if (host.editMode && !source.liveInEditMode) {
        return framePlaceholder(host, element, {
            styleType: source.styleType, label: source.placeholderLabel, detail: source.placeholderDetail,
        });
    }
    return new PanelFrame(host, element, source).node;
}

/** One drawn frame, its opening message and its bridge. */
class PanelFrame {
    constructor(host, element, source) {
        this.host = host;
        this.element = element;
        this.source = source;
        this.who = source.pluginId ? `plugin '${source.pluginId}'` : `custom control '${element.id}'`;
        this.node = this._drawBox();
        // Is the page actually there? Registered before the frame is in the
        // document, so the answer cannot arrive before something is listening.
        if (source.fileLabel) this._checkFile();
        this.loading = this._drawLoading();
        this._register();
        this.iframe.addEventListener('load', () => this.open());
        this._listen();
        applyStyle(this.node, host.themedStyle(source.styleType, element.style));
    }

    _drawBox() {
        const el = document.createElement('div');
        el.className = `panel-element ${this.source.className}`;
        el.dataset.elementId = this.element.id;
        const iframe = document.createElement('iframe');
        iframe.src = this.source.src;
        // setAttribute rather than `iframe.sandbox = ...`: identical in a
        // browser, but the property assignment leaves the attribute unset under
        // jsdom, and the sandbox attribute is the entire isolation story here --
        // it has to be assertable.
        iframe.setAttribute('sandbox', this.source.sandboxTokens.join(' '));
        if (this.source.allowFeatures.length) {
            iframe.setAttribute('allow', this.source.allowFeatures.join('; '));
        }
        iframe.style.cssText = 'width:100%; height:100%; border:none; border-radius:inherit;';
        // NOT loading="lazy". A panel page is sized to fill the screen, so
        // every frame on it is in the viewport already and lazy defers nothing
        // -- it only delays the fetch behind a layout pass, on the path a
        // person is waiting at. It also costs the one thing that makes the
        // second request in _checkFile unnecessary: Chromium zeroes
        // responseStatus and transferSize on a lazily-loaded frame's timing
        // entry, so the parent could no longer tell a 404 from a control that
        // drew perfectly well.
        el.style.overflow = 'hidden';
        el.appendChild(iframe);
        this.iframe = iframe;
        return el;
    }

    _drawLoading() {
        const loading = document.createElement('div');
        loading.textContent = this.source.loadingText;
        loading.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--panel-text);opacity:0.5;font-size:0.8571rem;position:absolute;inset:0;z-index:1;';
        this.node.style.position = 'relative';
        this.node.appendChild(loading);
        return loading;
    }

    /** The element map entry, and what the panel and its tests read off the node. */
    _register() {
        const el = this.node;
        this.host.register(this.element.id, { el, elementDef: this.element });
        el._pluginIframe = this.iframe;
        el._pluginConfig = this.source.config;
        // The grant this element was placed with, and the namespace it owns
        // outright (a plugin's own `plugin.<id>.`, nothing for a custom
        // control). Every check reads these off the node rather than
        // re-deriving them, so the opening snapshot, the live pushes and the
        // bridge cannot end up answering differently.
        el._grant = grantOf(this.element);
        el._ownNamespace = this.source.ownNamespace;
        this.stateFilter = (key) => mayRead(el._grant, el._ownNamespace, key);
        el._stateFilter = this.stateFilter;
    }

    _listen() {
        const handler = (event) => this.receive(event);
        window.addEventListener('message', handler);
        this.node._pluginMessageHandler = handler;
        this.host.handlers.add(handler);
    }

    /** Say so in the element's box if the control's file never arrived. Reads
     *  the status off the frame's own request where the browser will tell (no
     *  extra traffic), and only asks for it where it will not. */
    _checkFile() {
        const failed = (status) => this.showFault(status
            ? `${this.source.fileLabel} could not be loaded (${status})`
            : `${this.source.fileLabel} could not be loaded`);
        // The second request, for when the frame's own status is not knowable.
        // `no-store` because its whole purpose is to find out what is there now.
        const ask = () => {
            fetch(this.source.src, { cache: 'no-store' }).then((res) => {
                if (!res.ok) failed(res.status);
            }).catch(() => failed(0));
        };
        const watch = this.host.frameStatus;
        if (!watch.reading) {
            ask();
            return;
        }
        // The timeline names resources absolutely; `iframe.src` reflects the
        // resolved URL, which is the same string.
        watch.wait(this.iframe.src, (status) => {
            // A status of 0 is not a verdict -- it is the browser declining to
            // give one. A request that never got an answer reads that way, and
            // so does one the browser will not report on. Asking is right for
            // both: the frame either really is broken, in which case the round
            // trip is the least of it, or it drew fine and an accusation would
            // have been wrong.
            if (status === 0) ask();
            else if (status >= 400) failed(status);
        });
    }

    /** Say, in the element's own box, that the page in it failed. A control
     *  that breaks is otherwise a blank rectangle with the answer in a console
     *  nobody has open, and on a wall panel there is no console at all. The
     *  strip sits over the frame, so a page that half-drew still shows what it
     *  managed. */
    showFault(message) {
        const el = this.node;
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
        this.host.postToParent({
            type: 'openavc:element-error',
            elementId: el.dataset.elementId,
            message,
        });
    }

    /** The opening message: config, theme, the state it may see, and what it
     *  was granted. Sent when the frame loads, and again when it asks (a
     *  long-lived plugin frame recovers a fresh ext token after its TTL;
     *  wall panels outlive the token lifetime by design). */
    async open() {
        this.loading.remove();
        const themeVars = {};
        const root = document.documentElement;
        for (const prop of THEME_VARIABLES) {
            themeVars[prop] = getComputedStyle(root).getPropertyValue(prop).trim();
        }
        const stateSnapshot = {};
        for (const [key, value] of Object.entries(this.host.state || {})) {
            if (this.stateFilter(key)) stateSnapshot[key] = value;
        }
        // Plugins that call their own /ext/* routes declare ext_auth. The
        // sandboxed frame cannot carry our credentials, so it presents a
        // plugin-scoped token instead, fetched with ours.
        let extToken;
        if (this.source.extAuth) {
            extToken = await this.host.fetchToken(this.source.pluginId);
        }
        if (!this.iframe.contentWindow) return;  // element removed during await
        this.iframe.contentWindow.postMessage({
            type: 'openavc:init',
            config: this.source.config,
            theme: themeVars,
            state: stateSnapshot,
            elementId: this.element.id,
            ext_token: extToken,
            // True in the Builder's design canvas: the control is drawing for
            // its author, with whatever sample state the Builder sent and no
            // way to reach the room.
            edit: !!this.host.editMode,
            grant: grantMessage(this.node._grant),
        }, '*');  // sandboxed iframe has opaque origin; source check provides security
    }

    /** A message from the page in the frame. */
    receive(event) {
        if (event.source !== this.iframe.contentWindow) return;
        const msg = event.data;
        if (!msg || !msg.type) return;
        // Read off the node, not a copy: a re-render replaces the grant, and a
        // stale copy would keep answering for a grant already taken away.
        const grant = this.node._grant || NO_GRANT;
        // Somebody using this frame is using the panel. Nothing inside a
        // cross-origin sandboxed frame reaches the document listeners that
        // reset the idle timer, so without this a person working a custom page
        // is invisible to it. Any message counts, but only while the frame HAS
        // FOCUS, so a frame nobody is touching cannot hold a panel unlocked
        // forever by posting in a loop.
        this.host.noteActivity(this.iframe);
        this.handle(msg, grant);
    }

    /** What one message from the frame asks for. */
    handle(msg, grant) {
        switch (msg.type) {
            case 'openavc:activity':
                // The reset above is the whole message: it exists for the
                // control that draws rather than acts (a room map, a dashboard).
                break;
            case 'openavc:action':
                this.act(msg, grant);
                break;
            case 'openavc:error': {
                // Nothing out here can see a script error inside the frame;
                // the one line the docs give an author (window.onerror -> this
                // message) is what makes a broken control say so.
                const text = String(msg.message || 'error').slice(0, 200);
                console.warn(`[panel] ${this.who} reported an error: ${text}`);
                this.showFault(text);
                break;
            }
            case 'openavc:request-init':
                // A plugin frame asks when an /ext/* request starts returning
                // 401 mid-session: its token expired.
                this.open();
                break;
            case 'openavc:navigate':
                this.navigate(msg, grant);
                break;
        }
    }

    /** A command, a write or a macro the frame asks the panel to send. This
     *  bridge carries the panel's WebSocket authority, so each is gated
     *  against the grant (the module docstring says why here and nowhere
     *  else). Through send(), which refuses to talk to the room while the
     *  designer is authoring. */
    act(msg, grant) {
        // A failure coming back is about the control that asked, the same as
        // a finger on a button; this is the only place that knows which one.
        this.host.touched(this.node.dataset.elementId);
        const request = bridgeRequest(msg);
        if (!request) return;
        if (!request.allowed(grant, this.node._ownNamespace)) {
            console.warn(`[panel] ${this.who} ${request.refusal()}`);
            return;
        }
        this.host.send(request.frame());
    }

    /** A page change the frame asks for, by the same server round trip as a
     *  page-nav button: ui.page.<id>, then the panel navigates on the reply. */
    navigate(msg, grant) {
        if (!grant.navigate) {
            console.warn(`[panel] ${this.who} attempted to change pages without the navigation switch`);
            return;
        }
        // In the designer the author is on the page they are editing, and a
        // control must not move them off it.
        if (this.host.editMode) return;
        if (!msg.page) return;
        this.host.send({ type: 'ui.page', page_id: msg.page });
    }
}

// --- The panel's side -------------------------------------------------------

/** State pushed to every frame on the page, each told only what its grant lets
 *  it see: the same filter its opening snapshot used. */
export function pushToFrames(elementMap, key, value) {
    for (const entry of Object.values(elementMap)) {
        const el = entry?.el;
        if (!el?._pluginIframe?.contentWindow) continue;
        if (!el._stateFilter || !el._stateFilter(String(key))) continue;
        el._pluginIframe.contentWindow.postMessage({
            type: 'openavc:state',
            key,
            value,
        }, '*');  // sandboxed iframe has opaque origin; source check provides security
    }
}

/** Every frame stops listening (the page is being redrawn). */
export function releaseFrames(handlers) {
    for (const handler of handlers) {
        window.removeEventListener('message', handler);
    }
    handlers.clear();
}

/** The frames inside `container` stop listening (an overlay going away); each
 *  open and dismiss would otherwise leak a listener holding a removed frame. */
export function releaseFramesIn(container, handlers) {
    container.querySelectorAll('.panel-plugin, .panel-custom').forEach((el) => {
        if (el._pluginMessageHandler) {
            window.removeEventListener('message', el._pluginMessageHandler);
            handlers.delete(el._pluginMessageHandler);
        }
    });
}

/**
 * What the browser says about each frame's own request, for the file check.
 *
 * A frame pointed at a missing file has to say so. The server answers a
 * missing page with a small HTML document (api/static_files.py), but that only
 * reaches whoever looks inside the frame; the strip out here names the FILE
 * and tells the Builder which element failed. Finding out used to cost a
 * second request per frame, on the one path in the panel that never caches
 * (the ui/ route is no-cache so an author saving a file sees it).
 *
 * The parent's resource timeline reports the HTTP status of a frame's request
 * even though the frame is sandboxed into an opaque origin. The frame's
 * `load` event does not: it fires for a 404 exactly as for a 200. An observer
 * rather than a lookup in performance.getEntriesByName(): the timeline stops
 * recording at 250 entries, which a wall panel reaches in days, and past that
 * the lookup finds nothing while an observer is still delivered. The observer
 * fires just after `load`, so it has to be the trigger.
 */
function timelineReportsStatus() {
    return typeof PerformanceObserver !== 'undefined'
        && typeof PerformanceResourceTiming !== 'undefined'
        && 'responseStatus' in PerformanceResourceTiming.prototype;
}

export class FrameStatusWatch {
    constructor() {
        this.waiters = new Map();
        this.reading = false;
        // Old WebViews have no responseStatus, and there the second request
        // is still the only way to know. Detect it rather than assume it.
        if (!timelineReportsStatus()) return;
        try {
            new PerformanceObserver((list) => this._delivered(list.getEntries()))
                .observe({ type: 'resource', buffered: true });
            this.reading = true;
        } catch (_) {
            // Nothing to read: the file check asks instead.
        }
    }

    _delivered(entries) {
        for (const entry of entries) {
            if (entry.initiatorType !== 'iframe') continue;
            const waiting = this.waiters.get(entry.name);
            if (!waiting) continue;
            this.waiters.delete(entry.name);
            for (const fn of waiting) {
                try { fn(entry.responseStatus); } catch (_) { /* one frame */ }
            }
        }
    }

    /** `fn(status)` once, when the timeline reports a frame's request for `url`. */
    wait(url, fn) {
        let waiting = this.waiters.get(url);
        if (!waiting) {
            waiting = new Set();
            this.waiters.set(url, waiting);
        }
        waiting.add(fn);
    }

    /** Frames from a page being replaced are about to be discarded: a callback
     *  held here keeps the element it draws into alive. */
    clear() {
        this.waiters.clear();
    }
}

/** The plugin panel elements the server lists (`/api/plugins/extensions`),
 *  added into the table `table()` returns, by plugin and then type. A failed
 *  load leaves the table as it was. */
export async function loadPanelElementExtensions(table) {
    try {
        const res = await fetch(`${basePath()}/api/plugins/extensions`);
        if (!res.ok) return;
        const data = await res.json();
        addExtensions(table, (data.extensions || {}).panel_elements || []);
    } catch (err) {
        console.warn('[panel] failed to load plugin extensions:', err);
    }
}

function addExtensions(table, elements) {
    for (const ext of elements) {
        if (!ext.plugin_id || !ext.type) continue;
        const byType = table()[ext.plugin_id] || {};
        byType[ext.type] = ext;
        table()[ext.plugin_id] = byType;
    }
}

/** A plugin-scoped token for a frame that declared ext_auth, fetched with the
 *  panel's own credentials, so the frame can reach its plugin's
 *  /api/plugins/<id>/ext/* routes. Undefined when the instance is open (an
 *  empty token) or on any error. */
export async function fetchExtToken(pluginId) {
    try {
        const res = await fetch(`${basePath()}/api/plugins/${encodeURIComponent(pluginId)}/ext-token`);
        if (!res.ok) return undefined;
        const data = await res.json();
        return data && data.token ? data.token : undefined;
    } catch (err) {
        console.warn('[panel] failed to fetch plugin ext token:', err);
        return undefined;
    }
}
