/**
 * What a page is and what draws on it: the rules every path that shows a
 * page asks, so the runtime's navigation, the Builder's Preview and the
 * redraw checks read a page one way.
 *
 * A page's type decides how it is shown. A regular page (no type, or
 * 'page') replaces the one on screen and goes into the history `$back`
 * walks; an overlay or a sidebar opens over it, in a box of its own. The
 * rule worth knowing before changing it: every path that shows a page asks
 * `opensOver` here. Preview once set the page it was handed and drew it, and
 * a dialog came out as a flat page with no backdrop and an empty overlay
 * stack, so its Cancel did nothing: the type had been read in one of the two
 * paths and not the other.
 *
 * A master element draws on every page its `pages` names, '*' or a list of
 * ids (`namesPage`), unless it is hidden. The appliance's setup screen
 * (`api/routes/setup.py` `_panel_has_content`) asks the whole project the
 * same question before it hands the display to /panel, so the two must agree
 * on what draws.
 *
 * Pure functions of the definition, except `drawOverlayFrame`, which builds
 * the nodes an overlay's page is drawn into and reads nothing of the panel.
 */

import { pct } from './layout.js';

/** How many regular pages `$back` can walk back through. */
const HISTORY_LIMIT = 50;

// Overlay and sidebar boxes, as percentages of the viewport. These are the
// old hardcoded pixel defaults (400x300 dialog, 320-wide sidebar) measured
// against the 1280x800 reference, so an overlay that never set a size keeps
// the proportions it had.
const OVERLAY_DEFAULTS = {
    dialogWidth: 31.25,
    dialogHeight: 37.5,
    sidebarWidth: 25,
};

/** How a page is shown. A page with no type is a regular page. */
function pageTypeOf(page) {
    return page.page_type || 'page';
}

/** Does this page open over the one on screen: an overlay or a sidebar? */
export function opensOver(page) {
    const type = pageTypeOf(page);
    return type === 'overlay' || type === 'sidebar';
}

/** A regular page: shown in place of the one on screen. */
function isRegularPage(page) {
    return pageTypeOf(page) === 'page';
}

/** The page drawn when the current one names nothing: the first regular page, else the first. */
export function fallbackPage(pages) {
    return pages.find(isRegularPage) || pages[0];
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
    return on === '*' || (Array.isArray(on) && !!page && on.includes(page.id));
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
 * interchangeable, but they must agree on what draws. A master element with
 * no `pages` draws nowhere, so it does not count here either.
 */
export function pageDrawsNothing(page, masters) {
    if (!page || isCustomPage(page)) return false;
    const elements = Array.isArray(page.elements) ? page.elements : [];
    if (elements.length) return false;
    return !(masters || []).some(mEl => !!mEl && !mEl.hidden && namesPage(mEl, page));
}

/**
 * Is anything on the page showing now drawn from a file in `ui/`?
 *
 * Asked before re-rendering on a ui.files push, so a panel showing a page
 * with no author markup on it does not redraw -- a redraw is cheap but not
 * free, and it is visible: it restarts page-enter animations and drops any
 * transient state a control was holding. A master element can be a custom
 * control too, and one draws on every page it names, so a panel sitting on a
 * plain page still redraws when the logo strip's markup changes.
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
    return (masters || []).some(mEl => mEl?.type === 'custom' && namesPage(mEl, page));
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

/** A sidebar is a strip on one side of the screen; anything else is a dialog. */
function sizeOverlayBox(content, page, overlay) {
    if (pageTypeOf(page) === 'sidebar') {
        const side = overlay.side || 'right';
        content.className = `overlay-content overlay-sidebar overlay-sidebar-${side}`;
        content.style.width = pct(overlay.width, OVERLAY_DEFAULTS.sidebarWidth) + '%';
        return;
    }
    const position = overlay.position || 'center';
    content.className = `overlay-content overlay-dialog overlay-pos-${position}`;
    content.style.width = pct(overlay.width, OVERLAY_DEFAULTS.dialogWidth) + '%';
    content.style.height = pct(overlay.height, OVERLAY_DEFAULTS.dialogHeight) + '%';
}
