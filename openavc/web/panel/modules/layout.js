/**
 * Where an element goes: the arrangement a page is drawn in at this
 * viewport, a master element's box, the box every element is placed in, the
 * coordinate space a container gives its children, and the walk that draws a
 * page's elements into them.
 *
 * Every stored measurement of position is four percentages of whatever the
 * element sits inside, so a panel drawn once is the same panel on any screen
 * of the same shape. A page's boxes live on its `layouts`: the one whose
 * orientation matches the glass, else the primary, with its `inherits` chain
 * folded down base first, so a secondary layout only says what moved. A
 * master element carries a box per orientation instead, because it has to be
 * valid on every page it appears on.
 *
 * The rule worth knowing before changing anything here: this arithmetic has
 * other homes. `ui/page_geometry.py` (the AI's write path and the reviewer
 * behind it) and the Builder's `pageGeometry.ts` fold the same chain;
 * `ui/page_review.py` and `reviewMasters.ts` pick a master's box by the same
 * fallbacks and default a missing coordinate the same way. A change to how a
 * box is chosen or defaulted goes to all of them, or the canvas, the review
 * and the panel disagree about the same file. `tests/test_panel_layout_mirror.py`
 * runs these functions under Node against the Python copies: the fold, the
 * hidden ids, a master's box and the coordinate defaults.
 *
 * Two things here are not placement but ride on it: the elements' entry
 * stagger, which needs the order the walk draws them in, and the vmin pin
 * (`applyVminOverride`) the Builder sends with the box it previews a page in.
 *
 * `drawElementTree` is lent `how`: `element(def)`, the panel's renderer, and
 * `visibleWhen(node, def)`, which registers the node's visibility rule, both
 * asked as each element is drawn; and the page's entry motion,
 * `entryAnimation` ('none', 'stagger' or a class suffix), `staggerMs` and
 * `staggerStyle`.
 */

import { applyCssClass } from './stylesheet.js';

// Every stored measurement is rem, and the root type scale makes 1rem equal
// 14px at the 1280x800 reference. Runtime defaults are written as
// <old px> / REM_BASE_PX so they still land on the pixel they always did.
export const REM_BASE_PX = 14;

// The snap increment a page falls back to: the old 12x8 grid's spacing.
const SNAP_FALLBACK = { x: 100 / 12, y: 100 / 8 };

// A border width written as these draws no frame.
const NO_FRAME = new Set(['0', '0px']);

/** A number, or the fallback when the value isn't one. */
export function pct(value, fallback) {
    const n = typeof value === 'number' ? value : parseFloat(value);
    return Number.isFinite(n) ? n : fallback;
}

/** Which way the glass is turned right now. */
export function viewportOrientation() {
    const { width, height } = viewportSize();
    return width >= height ? 'landscape' : 'portrait';
}

/** The glass in px: the window's size, else the document's when the window reports none. */
function viewportSize() {
    const doc = document.documentElement;
    return {
        width: window.innerWidth || doc.clientWidth || 0,
        height: window.innerHeight || doc.clientHeight || 0,
    };
}

/**
 * The arrangement a page renders in on glass turned `orientation`: its
 * placements and hidden ids with the `inherits` chain folded down, and which
 * layout was chosen.
 */
export function selectLayout(page, orientation) {
    const layouts = Array.isArray(page?.layouts) ? page.layouts : [];
    const chosen = chosenLayout(layouts, orientation);
    if (!chosen) return { placements: {}, hidden: new Set(), id: null, orientation: null };
    const { placements, hidden } = foldDown(inheritsChain(layouts, chosen));
    return { placements, hidden, id: chosen.id, orientation: chosen.orientation };
}

/** One layout's placements and hidden ids from a chain, later layouts winning. */
function foldDown(chain) {
    const placements = {};
    const hidden = new Set();
    for (const layout of chain) {
        Object.assign(placements, layout.placements || {});
        for (const id of (layout.hidden || [])) hidden.add(id);
    }
    return { placements, hidden };
}

/** The layout whose orientation matches, else the primary, else the first. */
function chosenLayout(layouts, orientation) {
    if (!layouts.length) return null;
    const primary = layouts.find(l => l?.primary) || layouts[0];
    return layouts.find(l => l?.orientation === orientation) || primary;
}

/**
 * The layouts feeding the chosen one, base first, so applying them in order
 * lets the chosen layout's own placements win. The seen-set is a cycle
 * guard: a hand-edited project can point two layouts at each other, and the
 * panel still has to draw something.
 */
function inheritsChain(layouts, chosen) {
    const chain = [];
    const seen = new Set();
    let cursor = chosen;
    while (cursor && !seen.has(cursor.id)) {
        seen.add(cursor.id);
        chain.unshift(cursor);
        cursor = cursor.inherits ? layouts.find(l => l?.id === cursor.inherits) : null;
    }
    return chain;
}

/**
 * Which of a master's orientation-keyed boxes it is drawn in on glass turned
 * `orientation`, and the box: its own for that orientation, then landscape,
 * then portrait, then whichever it has first. A tile wall inside a master is
 * drawn the shape the key says, because the review measures each key against
 * its own screen, so answering "landscape" for a box picked from `portrait`
 * would publish a floor for a wall nobody draws.
 */
function masterBox(master, orientation) {
    const placements = master?.placements || {};
    const key = masterKey(placements, orientation);
    return { key: key || 'landscape', box: key === undefined ? null : (placements[key] || null) };
}

/** The key masterBox draws from: this orientation's, landscape or portrait when set, else the first key whatever its value (none: undefined). */
function masterKey(placements, orientation) {
    return [orientation, 'landscape', 'portrait'].find(k => placements[k]) ?? Object.keys(placements)[0];
}

/** Which orientation's box a master is drawn in (see masterBox). */
export function masterOrientation(master, orientation) {
    return masterBox(master, orientation).key;
}

/** A master element's box on glass turned `orientation` (none: null). */
export function masterPlacement(master, orientation) {
    return masterBox(master, orientation).box;
}

/**
 * The ratio an element holds under a stretch, or null to stretch freely.
 *
 * Deliberately no per-type default here. A renderer that locked, say, every
 * status LED to 1:1 would re-shape elements in a project that was migrated,
 * not authored, against the promise that a migrated panel looks exactly like
 * it used to. The default belongs where the element is created, so a *new*
 * LED is locked and an existing one is left alone.
 */
function aspectLockFor(element) {
    if (element?.aspect_lock == null) return null;
    const n = parseFloat(element.aspect_lock);
    return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * A stored box as four numbers: a coordinate that is missing or not a number
 * is the whole box (x and y 0, w and h 100). A zero is kept as zero.
 */
export function boxOf(box) {
    const p = box || {};
    return { x: pct(p.x, 0), y: pct(p.y, 0), w: pct(p.w, 100), h: pct(p.h, 100) };
}

/** Put a node in a box, four percentages of what it sits inside. */
function setBox(node, box) {
    const { x, y, w, h } = boxOf(box);
    node.style.left = `${x}%`;
    node.style.top = `${y}%`;
    node.style.width = `${w}%`;
    node.style.height = `${h}%`;
}

/**
 * THE placement path. Page elements, container children, overlay contents,
 * custom pages and master elements all get their box from here.
 *
 * Returns the node to append, which is the element itself unless it is
 * aspect-locked -- CSS can only hold a ratio when one axis is free, and
 * "shrink to fit, stay centred" needs a box to shrink inside, so a locked
 * element gets a placement box and sits within it.
 */
export function placeElement(el, placement, element) {
    // The author's own classes, for the project stylesheet to target. Every
    // element type sets its own className, so this is the one place both page
    // elements and master elements pass through. It lands on the element
    // itself rather than the aspect-lock wrapper below, because the wrapper is
    // the placement box and the element is what was styled.
    applyCssClass(el, element);

    const ratio = aspectLockFor(element);
    if (!ratio) {
        el.style.position = 'absolute';
        setBox(el, placement);
        return el;
    }
    const box = document.createElement('div');
    box.className = 'panel-placement';
    if (element?.id) box.dataset.placementFor = element.id;
    box.style.position = 'absolute';
    setBox(box, placement);
    el.style.aspectRatio = String(ratio);
    box.appendChild(el);
    return box;
}

/**
 * Move already-rendered elements to new boxes, without a re-render.
 *
 * What the Builder sends while a control is under the pointer on the design
 * canvas. The node to move is the placement box when the element is
 * aspect-locked (that box is what holds the position) and the element itself
 * otherwise.
 */
export function applyLivePlacements(placements) {
    if (!placements || typeof placements !== 'object') return;
    for (const [id, p] of Object.entries(placements)) {
        const node = p && placedNode(id);
        if (node) setBox(node, p);
    }
}

/** The node that holds an element's position: its placement box, else the element. */
function placedNode(id) {
    const name = CSS.escape(id);
    return document.querySelector(`[data-placement-for="${name}"]`)
        || document.querySelector(`[data-element-id="${name}"]`);
}

/**
 * Pin the type scale to a fixed vmin instead of the live viewport. Pass
 * nothing to hand it back to the viewport.
 *
 * Type scales with the panel via vmin, which resolves against the viewport.
 * That is right on real glass and inside overlays, but wrong when the panel
 * is embedded in a box smaller than the screen it stands for -- the Builder
 * previewing a 400px overlay would render text at a third of runtime size.
 */
export function applyVminOverride(value) {
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
 * Make each container's box and its contents' coordinate space the same box.
 *
 * A child's percentages are OF its container, and CSS resolves an absolutely
 * positioned box against its ancestor's PADDING box -- the border box minus
 * the border. So a container with the theme's 1px frame quietly moves
 * everything inside it in by a pixel and shrinks it by two, while the
 * Builder measures against the rectangle it drew. The two disagree, and a
 * control shuffles a little every time it moves in or out of a container.
 *
 * An outline draws the same rectangle and takes up no space at all, so the
 * frame stays exactly where it was and the coordinate space becomes the whole
 * box. Only containers need this; a leaf element's border is nobody's frame
 * of reference.
 *
 * A themed or authored border arrives inline, but a stylesheet is just as
 * capable of putting one there (panel-elements.css does, and so can a
 * project stylesheet through css_class), and a border this never saw would
 * shift every child inside it. So the inline value is tried first and the
 * browser is asked second. Asking needs the node in the document, which is
 * why this runs as a pass after the page is attached: computed style on a
 * detached node is empty.
 */
export function applyCoordinateSpaces(root) {
    if (!root || typeof root.querySelectorAll !== 'function') return;
    for (const el of root.querySelectorAll('[data-coordinate-space]')) {
        const frame = inlineFrame(el) || computedFrame(el);
        if (frame) drawAsOutline(el, frame);
    }
}

/** Draw the frame as an outline, which takes up no space, in place of the border. */
function drawAsOutline(el, frame) {
    el.style.outline = `${frame.width} ${frame.style || 'solid'} ${frame.color || 'currentColor'}`;
    el.style.outlineOffset = `calc(0px - ${frame.width})`;
    el.style.borderWidth = '0';
}

/**
 * The border written on the node itself, verbatim: it is a CSS expression --
 * max(1px, Xrem) -- and re-emitting it keeps the frame scaling with the panel
 * the way every other measurement does. Resolving it to px here would pin it
 * until the next re-render.
 */
function inlineFrame(el) {
    const width = el.style.borderWidth;
    if (!width || NO_FRAME.has(width)) return null;
    return { width, style: el.style.borderStyle, color: el.style.borderColor };
}

/**
 * The border the cascade gave the node, when none is written on it. Sides
 * are uniform in every border the platform itself writes, so the widest is
 * THE frame. An asymmetric one draws as a uniform frame rather than shifting
 * its contents: the coordinate space is the invariant, the exact frame is
 * cosmetic.
 */
function computedFrame(el) {
    if (typeof getComputedStyle !== 'function' || !el.isConnected) return null;
    const computed = getComputedStyle(el);
    if (!computed) return null;
    const px = Math.max(...[
        computed.borderTopWidth, computed.borderRightWidth,
        computed.borderBottomWidth, computed.borderLeftWidth,
    ].map(v => parseFloat(v) || 0));
    if (!px) return null;
    return { width: `${px}px`, style: computed.borderTopStyle, color: computed.borderTopColor };
}

/**
 * Draw where things will snap to, behind the page on the design canvas. Two
 * repeating gradients rather than a div per cell: the increment is a float
 * (8.3333% is the old 12-column spacing), and a background-size in percent
 * lands on it exactly at any page size. The grid draws whenever the Builder
 * asks for it, snapping on or off: the grid toggle controls what you see,
 * the snap toggle what pulls.
 */
export function drawSnapRuler(page, surface) {
    const snap = page.snap || {};
    const stepX = pct(snap.x, SNAP_FALLBACK.x);
    const stepY = pct(snap.y, SNAP_FALLBACK.y);
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

/**
 * Draw every element of a page onto a surface, in `layout` (selectLayout's
 * answer), the way `how` says (see the top of this file). Full pages and
 * overlays share it. A parent id that names nothing on the page is treated as
 * page-level, so an orphaned child still draws.
 */
export function drawElementTree(page, layout, surface, how) {
    const elements = Array.isArray(page.elements) ? page.elements : [];
    const ctx = {
        layout,
        byParent: groupByParent(elements),
        rendered: new Set(),
        order: 0,
        entryAnimation: how.entryAnimation || 'none',
        staggerMs: how.staggerMs || 30,
        staggerStyle: how.staggerStyle,
        how,
    };
    for (const element of (ctx.byParent.get(null) || [])) {
        drawSubtree(element, ctx, surface);
    }
}

/** The page's elements by the parent they draw inside (null: the page). */
function groupByParent(elements) {
    const ids = new Set(elements.map(e => e?.id).filter(Boolean));
    const byParent = new Map();
    for (const element of elements.filter(e => e?.id)) {
        const key = ids.has(element.parent) ? element.parent : null;
        if (!byParent.has(key)) byParent.set(key, []);
        byParent.get(key).push(element);
    }
    return byParent;
}

/**
 * Draw one element, and anything that names it as a parent, into `host`.
 *
 * A container is a real parent: its children are percentages *of it*, so
 * moving or resizing the container carries them along and one visible_when
 * on the container hides the whole group.
 */
function drawSubtree(element, ctx, host) {
    if (!claim(element, ctx)) return;
    const el = ctx.how.element(element);
    if (!el) return;
    el.dataset.elementType = element.type;
    drawChildren(el, element, ctx);

    const node = placeElement(el, ctx.layout.placements[element.id], element);
    if (ctx.entryAnimation !== 'none') animateEntry(node, ctx);
    ctx.how.visibleWhen(node, element);
    host.appendChild(node);
}

/**
 * Is this element one to draw now? Each id draws once, so an element listed
 * twice, or a parent cycle a hand-edited project could introduce, draws
 * once; and not at all when this layout hides it.
 */
function claim(element, ctx) {
    if (!element?.id) return false;
    if (ctx.rendered.has(element.id)) return false;
    ctx.rendered.add(element.id);
    return !ctx.layout.hidden.has(element.id);
}

/** A container's children, drawn inside it: their percentages are of it. */
function drawChildren(el, element, ctx) {
    const children = ctx.byParent.get(element.id) || [];
    if (!children.length) return;
    // Containers do not clip. A child nudged past the edge stays visible --
    // the Builder's out-of-bounds badge is what says so, consistent with free
    // positioning warning rather than preventing.
    el.style.overflow = 'visible';
    el.style.pointerEvents = 'auto';
    // Converted to an outline once the page is attached and the browser has
    // resolved every border that reaches this box, inline or not.
    el.dataset.coordinateSpace = '1';
    for (const child of children) drawSubtree(child, ctx, el);
}

/** Bring an element in after the ones drawn before it, one stagger step each. */
function animateEntry(node, ctx) {
    const index = ctx.order++;
    node.style.opacity = '0';
    const animClass = ctx.entryAnimation === 'stagger'
        ? `element-entry-${ctx.staggerStyle || 'fade-up'}`
        : `element-entry-${ctx.entryAnimation}`;
    setTimeout(() => {
        node.style.opacity = '';
        node.classList.add(animClass);
    }, ctx.staggerMs * index);
}
