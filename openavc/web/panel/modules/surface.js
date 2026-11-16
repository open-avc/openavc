/**
 * THE record of what a drawn page owns, and the one way all of it ends.
 *
 * Every draw of a page makes one surface. A control drawn on it registers
 * what it starts while it is on screen, on the surface it was drawn on:
 * - its bindings and its element-map entry (the panel's `bindings` and
 *   `elementMap` are the page surface's);
 * - a timer, with `keep`: a pending send, a hold-repeat, an entry animation;
 * - anything with its own way to stop, with `onEnd`: a fader's drag (which
 *   sends its last value), a matrix gesture (which sends nothing), a clock, a
 *   frame's listener and its status waiter, the Theme Studio's name tag.
 *
 * `end()` ends all of it at once: every kept timer cleared unsent, every
 * `onEnd` run once. Something registered on a surface that has already ended
 * is ended on the spot, so a timer a control starts after its page went never
 * runs. `onEnd` hands back what unregisters it, for something that ends on
 * its own first (a hold let go, a dialog closed).
 *
 * The nodes a surface put on screen (`show`) go when the next page is drawn
 * (`takeAway`): at once with no page transition, else each animated out and
 * removed when its own transition ends, so pages changed faster than the
 * transition each go on their own time and only the page asked for stays.
 *
 * Reads no element definition: what a control registers is handed in.
 */

/** Run one end, logging rather than stopping the others when it fails. */
function runEnd(fn) {
    try { fn(); } catch (e) { console.error('Surface end failed:', e); }
}

export class Surface {
    constructor() {
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

/** Page nodes on their way out, each removed when its own transition ends. */
const LEAVING = new WeakSet();

/**
 * Take what `surface` put in `root` off the screen. With no transition the
 * root is emptied at once, a page still leaving included. With one, each of
 * the surface's pages animates out and is removed when its own transition
 * ends, a page already leaving keeps its own time, and anything else in the
 * root (the notice a project with no pages shows) goes at once.
 */
export function takeAway(root, surface, { transition, duration }) {
    if (transition === 'none') {
        root.textContent = '';
        return;
    }
    for (const node of surface.nodes) {
        if (node.parentNode !== root || !node.classList.contains('panel-page') || LEAVING.has(node)) continue;
        LEAVING.add(node);
        node.classList.add(`page-exit-${transition}`);
        node.style.position = 'absolute';
        node.style.inset = '0';
        setTimeout(() => node.remove(), duration);
    }
    for (const node of [...root.children]) {
        if (!LEAVING.has(node)) node.remove();
    }
}
