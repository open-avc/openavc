/**
 * THE record of what a drawn page or overlay owns, and the one way all of it
 * ends.
 *
 * Every draw of a page makes one surface, and so does every overlay or
 * sidebar opened over it; each knows the page it draws (`pageId`), the
 * overlay stack holds the overlays' surfaces, and `Surfaces` holds the
 * page's and the stack. A control drawn on a surface registers what it starts while
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
    /** `pageId`: the page this surface draws (none before the first page draw, nor with no pages). */
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
 * open over it a surface of its own, oldest first; plus the sub-page
 * containers' surfaces, one per container element showing a sub-page. While
 * an overlay or a container draws, its controls register on it; on the page
 * otherwise (`drawing`, innermost scope wins).
 * 屏幕上的表面：页面本身、弹窗栈（最旧在前），以及每个正在显示子页面的容器
 * 元素各自的 Surface。弹窗或容器绘制期间，其控件注册在它上面；否则注册在
 * 页面上（drawing，最内层作用域优先）。
 */
export class Surfaces {
    constructor() {
        this.page = new Surface();
        this.overlays = [];
        // The open sub-page containers, by element id -- a LIST per id, since
        // the same sub-page can show in two containers and a sub-page shown
        // twice draws its own containers twice: each instance is a surface of
        // its own, and one ending takes only itself off the list.
        // 打开的子页面容器，按元素 id——每个 id 对应一个列表：同一子页面可
        // 显示在两个容器里，其内部的容器也随之画两份，每个实例都有自己的
        // Surface，一个结束只从列表里移除它自己。
        this.containers = new Map();
        this._drawingOverlay = null;
        this._drawingContainer = null;
    }

    /**
     * The surface a control being drawn registers on. A container drawing its
     * sub-page is the innermost scope, then an overlay drawing its page, then
     * the page itself.
     * 正在绘制的控件注册到的表面。容器绘制子页面是最内层作用域，其次是弹窗，
     * 最后是页面本身。
     */
    get drawing() { return this._drawingContainer || this._drawingOverlay || this.page; }

    /** Run `draw` with `overlay` as the surface being drawn, and return what it returns. */
    drawOn(overlay, draw) {
        const was = this._drawingOverlay;
        this._drawingOverlay = overlay;
        try { return draw(); } finally { this._drawingOverlay = was; }
    }

    /**
     * Run `draw` with `surface` as the container being drawn, and register it
     * under `containerId` while it runs, so showSubpage finds the entry even
     * from inside the first draw. Nested containers save and restore, exactly
     * as overlays do.
     * 以 `surface` 为正在绘制的容器运行 `draw`，并在运行期间以 `containerId`
     * 登记，使首次绘制期间 showSubpage 也能找到该条目。嵌套容器与弹窗一样
     * 保存并恢复。
     */
    drawInContainer(containerId, surface, draw) {
        const was = this._drawingContainer;
        this._drawingContainer = surface;
        // Registered before the draw and left afterwards: the entry stays in
        // the map for as long as the surface shows, ending only when its host
        // ends or the container switches sub-page (the panel owns both).
        // 在绘制前登记并在绘制后保留：只要该 Surface 还在显示，条目就留在
        // 映射中，直到宿主结束或容器切换子页面（两者都由面板负责）。
        const list = this.containers.get(containerId) || [];
        list.push(surface);
        this.containers.set(containerId, list);
        try { return draw(); } finally { this._drawingContainer = was; }
    }

    /** Take one container instance off its list, by identity: a switch
     *  replaces its own, a host end takes only what ended, and a twin on
     *  another host stays live.
     *  按同一性从列表移除一个容器实例：切换只替换它自己，宿主结束只带走
     *  结束的那个，另一宿主上的孪生实例照常存活。 */
    removeContainer(containerId, surface) {
        const list = this.containers.get(containerId);
        if (!list) return;
        const next = list.filter((s) => s !== surface);
        if (next.length === list.length) return;
        if (next.length) this.containers.set(containerId, next);
        else this.containers.delete(containerId);
    }

    /** Every binding on screen: the page's, then each overlay's, oldest first, then each container's. */
    everyBinding() {
        const containerBindings = [...this.containers.values()].flat()
            .flatMap((surface) => surface.bindings);
        if (!this.overlays.length) return [...this.page.bindings, ...containerBindings];
        return [this.page, ...this.overlays].flatMap((surface) => surface.bindings).concat(containerBindings);
    }

    /** Every control on screen, by element id; an overlay's over the page's, a container's over both. */
    everyControl() {
        const maps = [this.page.elementMap, ...this.overlays.map((surface) => surface.elementMap)];
        return Object.assign({}, ...maps, ...[...this.containers.values()].flat()
            .map((surface) => surface.elementMap));
    }

    /** Every control entry on screen, one per INSTANCE: the same sub-page in
     *  two containers yields two entries with one id, and it is how an update
     *  that must reach every mounted copy (a ui.<id>.url override) walks.
     *  屏幕上每个控件条目，每实例一条：同一子页面在两个容器里产生两条同 id
     *  条目；必须抵达每个已挂载副本的更新（如 ui.<id>.url 覆盖）由此遍历。 */
    everyControlEntries() {
        const list = [];
        for (const surface of [this.page, ...this.overlays, ...[...this.containers.values()].flat()]) {
            list.push(...Object.values(surface.elementMap));
        }
        return list;
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
