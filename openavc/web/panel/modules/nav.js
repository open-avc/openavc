/**
 * A Page Nav: what it is called, the face it draws with and without a page
 * to go to, whether its page is on this panel's screen, and what its look
 * says.
 *
 * Its name is its label, else the page it goes to (`navName`): the words on
 * its face, the name a screen reader reads, and the words its look falls
 * back to all say the same. The panel keeps the writes of its words, where
 * an in-place edit's hold and record apply, the icon beside them, which the
 * button's looks share, and the binding renderPageNav registers, where the
 * page review's scans read it. Only a Page Nav hands this module its
 * definition: the page review's renderer scan
 * (tests/test_ui_page_review_mirrors.py) credits the whole module to every
 * control that does.
 */

import { pickLook, withTwoStateWords } from './looks.js';

/** What a nav is called: its label, else the page it goes to. */
export function navName(elementDef) {
    return elementDef.label || elementDef.target_page;
}

/**
 * The nav's face: its name and a press that asks to go to its page
 * (`host.go`), or, when it names no page, "No Target", greyed and disabled.
 * `host.wordsFrom(node)` records where an edit of the words goes, once they
 * are written.
 */
export function drawNavFace(node, element, host) {
    if (!element.target_page) {
        node.textContent = element.label || 'No Target';
        host.wordsFrom(node);
        node.disabled = true;
        node.style.opacity = '0.5';
        return;
    }
    node.textContent = navName(element);
    node.setAttribute('aria-label', `Navigate to ${navName(element)}`);
    host.wordsFrom(node);
    node.addEventListener('click', () => host.go(element.target_page));
}

/**
 * Whether a nav set to light up has its page on this panel's screen: the
 * page itself, or an overlay or sidebar open over it.
 *
 * From this panel's own page and overlay stack, never from state. State
 * reaches every panel in the building, and one tablet changing page must
 * not light the tabs on another.
 */
export function showsItsPage(elementDef, currentPage, overlayStack) {
    if (!elementDef.highlight_current) return false;
    const target = elementDef.target_page;
    // `$back` and `$dismiss` are moves, not places.
    if (!target || target.startsWith('$')) return false;
    return currentPage === target || overlayStack.includes(target);
}

/**
 * The entry a nav's look shows for `reading` (modules/looks.js), with a two
 * state look's own words: none at all when the look reads no key, or while
 * its device cannot be reached. Lit is drawn either way, since which page
 * is showing is still true.
 */
export function navLook(binding, reading, offline) {
    if (!binding.key || offline) return { appearance: {}, wordsPath: ['label'] };
    return withTwoStateWords(binding, pickLook(binding, reading));
}

/** The words a look draws on a nav and where an edit of them goes: the entry's, else the nav's own name. */
export function navWords(elementDef, look) {
    if (look.appearance.label !== undefined) return [String(look.appearance.label), look.wordsPath];
    return [navName(elementDef) || '', ['label']];
}

/** Lit or not, as a class for the stylesheet and as the page a screen reader is told is current. */
export function markCurrent(node, lit) {
    node.classList.toggle('nav-current', lit);
    if (lit) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
}
