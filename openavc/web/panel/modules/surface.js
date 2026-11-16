/**
 * THE record of what a drawn page or overlay owns, and the one way all of it
 * ends.
 *
 * Every draw of a page makes one surface, and so does every overlay or
 * sidebar opened over it; the overlay stack holds the overlays' surfaces,
 * each knowing the page it draws (`pageId`), and `Surfaces` holds the page's
 * and the stack. A control drawn on a surface registers what it starts while
 * it is on screen, on that surface:
 * - its bindings and its element-map entry (the panel's `bindings` and
 *   `elementMap` are the surface being drawn's);
 * - a timer, with `keep`: a pending send, a hold-repeat, an entry animation;
 * - anything with its own way to stop, with `onEnd`: a fader's drag (which
 *   sends its last value), a matrix gesture (which sends nothing), a clock, a
 *   frame's listener and its status waiter, the Theme Studio's name tag.
 *
 * `end()` ends all of it at once: every kept timer cleared unsent, every
 * `onEnd` run once. Something registered on a surface that has already ended
 * is ended on the spot, so a timer a control starts after its page or its
 * overlay went never runs. `onEnd` hands back what unregisters it, for
 * something that ends on its own first (a hold let go).
 *
 * The nodes a surface put on screen (`show`) go with it. A page's go when
 * the next page is drawn (`takeAway`): at once with no page transition, else
 * each animated out and removed when its own transition ends, so pages
 * changed faster than the transition each go on their own time and only the
 * page asked for stays. An overlay's are its box, which the panel takes down
 * when it closes.
 *
 * Reads no element definition: what a control registers is handed in.
 */

/** Run one end, logging rather than stopping the others when it fails. */
function runEnd(fn) {
    try { fn(); } catch (e) { console.error('Surface end failed:', e); }
}

export class Surface {
    /** `pageId`: the page this surface draws, for an overlay's. */
    constructor(pageId = null) {
        this.pageId = pageId;
        this.bindings = [];
        this.elementMap = {};
        this.nodes = [];
        this.ended = false;
        this._timers = new Set();
        this._ends = new Set();
    }

    /** A node this surface put on screen, taken away with it; returns the node. */
    show(node) {
        this.nodes.push(node);
        return node;
    }

    /**
     * A timer (setTimeout's or setInterval's) cleared when the surface ends,
     * or at once if it has; returns it. clearTimeout clears either kind: the
     * two share one list of timers.
     */
    keep(timer) {
        if (this.ended) clearTimeout(timer);
        else this._timers.add(timer);
        return timer;
    }

    /** `fn` runs once when the surface ends, or now if it has; returns what unregisters it. */
    onEnd(fn) {
        if (this.ended) {
            runEnd(fn);
            return () => {};
        }
        this._ends.add(fn);
        return () => { this._ends.delete(fn); };
    }

    /** End everything registered, once: timers cleared, each end run, bindings and element map let go. */
    end() {
        if (this.ended) return;
        this.ended = true;
        for (const timer of this._timers) clearTimeout(timer);
        this._timers.clear();
        const ends = [...this._ends];
        this._ends.clear();
        for (const fn of ends) runEnd(fn);
        this.bindings = [];
        this.elementMap = {};
    }
}

/**
 * The surfaces on screen: the page's, and the overlay stack, each overlay
 * open over it a surface of its own, oldest first. While an overlay draws its
 * controls register on it; on the page otherwise (`drawing`).
 */
export class Surfaces {
    constructor() {
        this.page = new Surface();
        this.overlays = [];
        this._drawingOverlay = null;
    }

    /** The surface a control being drawn registers on. */
    get drawing() { return this._drawingOverlay || this.page; }

    /** Run `draw` with `overlay` as the surface being drawn, and return what it returns. */
    drawOn(overlay, draw) {
        const was = this._drawingOverlay;
        this._drawingOverlay = overlay;
        try { return draw(); } finally { this._drawingOverlay = was; }
    }

    /** Every binding on screen: the page's, then each overlay's, oldest first. */
    everyBinding() {
        if (!this.overlays.length) return this.page.bindings;
        return [this.page, ...this.overlays].flatMap((surface) => surface.bindings);
    }

    /** Every control on screen, by element id; an overlay's over the page's. */
    everyControl() {
        if (!this.overlays.length) return this.page.elementMap;
        return Object.assign({}, this.page.elementMap, ...this.overlays.map((surface) => surface.elementMap));
    }
}

/** Page nodes on their way out, each removed when its own transition ends. */
const LEAVING = new WeakSet();

/** Is `node` a page drawn in `root`? */
function isPageIn(root, node) {
    return node.parentNode === root && node.classList.contains('panel-page');
}

/** Animate a page out, and remove it when its own transition ends. */
function leave(node, { transition, duration }) {
    LEAVING.add(node);
    node.classList.add(`page-exit-${transition}`);
    node.style.position = 'absolute';
    node.style.inset = '0';
    setTimeout(() => node.remove(), duration);
}

/**
 * Take what `surface` put in `root` off the screen. With no transition the
 * root is emptied at once, a page still leaving included. With one, each of
 * the surface's pages animates out and is removed when its own transition
 * ends, a page already leaving keeps its own time, and anything else in the
 * root (the notice a project with no pages shows) goes at once.
 */
export function takeAway(root, surface, motion) {
    if (motion.transition === 'none') {
        root.textContent = '';
        return;
    }
    const going = surface.nodes.filter((node) => isPageIn(root, node)).filter((node) => !LEAVING.has(node));
    for (const node of going) leave(node, motion);
    for (const node of [...root.children].filter((n) => !LEAVING.has(n))) node.remove();
}
