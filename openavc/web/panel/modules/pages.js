/**
 * What a page is and what draws on it: the rules every path that shows a
 * page asks, so the runtime's navigation, the Builder's Preview and the
 * redraw checks read a page one way.
 *
 * The rules themselves are the project schema's page rules
 * (`openavc/ui/page_rules.py`, generated into `project_values.gen.js` as
 * `PAGE_RULES`), and `tests/test_page_rules_mirror.py` runs these functions
 * against that module's.
 *
 * A page's type decides how it is shown. A regular page ('page', none, or a
 * type the page rules do not know) replaces the one on screen and goes into
 * the history `$back` walks; an overlay or a sidebar opens over it, in a box
 * of its own. The
 * rule worth knowing before changing it: a path that shows a page asks
 * `opensOver` here. Navigation and the Builder's Preview do, and so does the
 * panel's page draw (`pageOpening`), which is how the idle return and a
 * `?page=` link open a dialog they name. Preview once set the page it was
 * handed and drew it, and a dialog came out as a flat page with no backdrop
 * and an empty overlay stack, so its Cancel did nothing: the type had been
 * read in one of the two paths and not the other.
 *
 * A master element draws on a page when its `pages` names the page, '*' or
 * a list of ids (left out, '*', the project schema's default), it is not
 * hidden, and its type is one the panel draws (a row in `RENDERERS`,
 * `dispatch.js`). Every reader asks `masterDrawsOn`: the page draw, the
 * empty-page notice and the ui/ file redraw, so none of them counts a master
 * nothing draws. The setup screen, the page reviews and the Builder's canvas
 * ask the page rules' `master_draws_on` and its Builder twin.
 *
 * Functions of the definition, except `rememberPage`, which adds to the
 * history it is handed, and `drawOverlayFrame` and `closeOverlayFrame`, which
 * build the nodes an overlay's page is drawn into and animate them away, and
 * read nothing of the panel.
 */

import { RENDERERS } from './dispatch.js';
import { pct } from './layout.js';
import { PAGE_RULES } from './project_values.gen.js';

/** How many regular pages `$back` can walk back through. */
const HISTORY_LIMIT = 50;

/**
 * How a page is shown: its type when the page rules know it, else what an
 * unknown type, or none, reads as (a regular page).
 */
function pageTypeOf(page) {
    const written = page.page_type;
    return PAGE_RULES.page_types.includes(written) ? written : PAGE_RULES.unknown_type_reads_as;
}

/** Does this page open over the one on screen: an overlay or a sidebar? */
export function opensOver(page) {
    return PAGE_RULES.opens_over.includes(pageTypeOf(page));
}

/** A regular page: shown in place of the one on screen. */
function isRegularPage(page) {
    return !opensOver(page);
}

/** The page drawn when the current one names nothing: the first regular page, else the first. */
export function fallbackPage(pages) {
    return pages.find(isRegularPage) || pages[0];
}

/** The page drawn for `pageId`: that page, else `fallbackPage` (null: there are no pages). */
export function standIn(pages, pageId) {
    return pages.find(p => p.id === pageId) || fallbackPage(pages) || null;
}

/**
 * The regular page a dialog is drawn over when Preview opens it: the one on
 * screen if it is regular, else the first regular page (none: undefined).
 * It is also where the dialog's `$back` lands, as it would on the glass.
 */
export function pageBehind(pages, currentId) {
    return pages.find(p => p.id === currentId && isRegularPage(p)) || pages.find(isRegularPage);
}

/**
 * What drawing `pageId` afresh puts on screen: `{ page, over }`, the regular
 * page drawn and the dialog or sidebar opened over it (null: none). The page
 * the id names, or the one standing in for it, is drawn itself, unless it
 * opens over: then it opens over the first regular page, which is where its
 * `$back` lands. With no regular page to open it over, it is drawn as the
 * page. `page` is null when there are no pages.
 */
export function pageOpening(pages, pageId) {
    const page = standIn(pages, pageId);
    const behind = page && opensOver(page) ? pageBehind(pages) : null;
    return behind ? { page: behind, over: page } : { page, over: null };
}

/**
 * Is the screen already what `pageOpening` draws for `pageId`: that page
 * showing (`currentId`), with its dialog alone open over it, or nothing open
 * when it has none? `overlays` are the ids open, oldest first.
 */
export function showsOpening(pages, pageId, currentId, overlays) {
    const { page, over } = pageOpening(pages, pageId);
    if (!over) return currentId === (page ? page.id : pageId) && overlays.length === 0;
    return currentId === page.id && overlays.length === 1 && overlays[0] === over.id;
}

/**
 * Put the page being left on the history `$back` walks: not when it is the
 * page being opened, not when there is none, and only the newest
 * HISTORY_LIMIT of them.
 */
export function rememberPage(history, leaving, opening) {
    if (!leaving || leaving === opening) return;
    history.push(leaving);
    if (history.length > HISTORY_LIMIT) history.shift();
}

/** Does a master element's `pages` name this page ('*' names every page)? */
export function namesPage(master, page) {
    const on = master.pages;
    return on === PAGE_RULES.every_page || (Array.isArray(on) && !!page && on.includes(page.id));
}

/**
 * Does this master element draw on this page: its `pages` names the page
 * (left out, '*'), it is not hidden, and the panel has a renderer for its
 * type? A type with no renderer is left out, so it is not drawn and no
 * warning is logged for it.
 */
export function masterDrawsOn(master, page) {
    return !!master && !master.hidden && RENDERERS.has(master.type)
        && (master.pages === undefined || namesPage(master, page));
}

/** A page that is the author's own markup filling the screen. */
export function isCustomPage(page) {
    return !!page && page.render_mode === 'custom' && !!page.custom_file;
}

/**
 * Will this page draw nothing at all?
 *
 * Three things draw: the page's own elements, a master element that names
 * the page, and a custom page's frame. None of the three is a black screen,
 * and a black screen reads as a broken system rather than an unbuilt one --
 * which is what the seed project's single empty page looked like to anyone
 * opening /panel to see what their client sees. The setup screen asks this
 * of the whole project, this asks it of one page, so they are not
 * interchangeable, but they agree on what draws: both ask the one rule
 * whether a master draws on a page (`masterDrawsOn`).
 */
export function pageDrawsNothing(page, masters) {
    if (!page || isCustomPage(page)) return false;
    const elements = Array.isArray(page.elements) ? page.elements : [];
    if (elements.length) return false;
    return !(masters || []).some(mEl => masterDrawsOn(mEl, page));
}

/**
 * Is anything on this page drawn from a file in `ui/`?
 *
 * Asked of the page showing and of each dialog open over it before
 * re-rendering on a ui.files push, so a panel showing nothing with author
 * markup does not redraw -- a redraw is cheap but not free, and it is
 * visible: every frame on screen loads again and a control drops any
 * transient state it was holding. A master element can be a custom control
 * too, and one draws on every page it names, so a panel sitting on a plain
 * page still redraws when the logo strip's markup changes.
 */
export function pageRunsAuthorMarkup(ui, currentId) {
    const page = (ui?.pages || []).find(p => p.id === currentId);
    return isCustomPage(page) || holdsCustomControl(page) || customMasterOn(ui?.master_elements, page);
}

/** Is one of the page's own elements a custom control? */
function holdsCustomControl(page) {
    return (page?.elements || []).some(el => el?.type === 'custom');
}

/** Is one of these master elements a custom control that draws on the page? */
function customMasterOn(masters, page) {
    return (masters || []).some(mEl => mEl?.type === 'custom' && masterDrawsOn(mEl, page));
}

/**
 * The nodes an overlay or sidebar page is drawn into: the container (on the
 * document's body, over everything), its backdrop, and the content box,
 * which is a percentage of the viewport so an overlay scales with the glass.
 * `dismiss` is what a tap on the backdrop does, unless the page turned that
 * off. Returns `{ container, content }`.
 */
export function drawOverlayFrame(page, dismiss) {
    const overlay = page.overlay || {};
    const container = document.createElement('div');
    container.className = `panel-overlay panel-overlay-${overlay.animation || 'fade'}`;
    container.dataset.pageId = page.id;

    const backdropEl = document.createElement('div');
    backdropEl.className = `overlay-backdrop overlay-backdrop-${overlay.backdrop || 'dim'}`;
    if (overlay.dismiss_on_backdrop !== false) {
        backdropEl.addEventListener('click', (e) => {
            e.stopPropagation();
            dismiss();
        });
    }
    container.appendChild(backdropEl);

    const content = document.createElement('div');
    sizeOverlayBox(content, page, overlay);
    return { container, content };
}

/**
 * An overlay's container animates away and is removed when its transition
 * ends, or after half a second when none runs.
 */
export function closeOverlayFrame(container) {
    container.classList.add('dismissing');
    container.addEventListener('transitionend', () => container.remove(), { once: true });
    setTimeout(() => { if (container.parentNode) container.remove(); }, 500);
}

/**
 * The box an overlay or sidebar page draws in, `{ width, height }` as
 * percentages of the screen: each side it sets, else its type's default. A
 * sidebar is the screen's height, whatever its own height says.
 */
export function overlayBox(page) {
    const overlay = page.overlay || {};
    if (pageTypeOf(page) === 'sidebar') {
        return { width: pct(overlay.width, PAGE_RULES.overlay_box.sidebar.width), height: PAGE_RULES.sidebar_height };
    }
    const box = PAGE_RULES.overlay_box.overlay;
    return { width: pct(overlay.width, box.width), height: pct(overlay.height, box.height) };
}

/** A sidebar is a strip on one side of the screen, its height the CSS's; anything else is a dialog. */
function sizeOverlayBox(content, page, overlay) {
    const box = overlayBox(page);
    if (pageTypeOf(page) === 'sidebar') {
        const side = overlay.side || 'right';
        content.className = `overlay-content overlay-sidebar overlay-sidebar-${side}`;
        content.style.width = box.width + '%';
        return;
    }
    const position = overlay.position || 'center';
    content.className = `overlay-content overlay-dialog overlay-pos-${position}`;
    content.style.width = box.width + '%';
    content.style.height = box.height + '%';
}
