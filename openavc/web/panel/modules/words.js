/**
 * A control's words and the icon beside them: which nodes are its words, how
 * they are written, and where an icon goes.
 *
 * The rule worth knowing before changing anything here: a control's words
 * are the text sitting in its node, or, once an icon stands beside them, the
 * one span this module draws them in (`LABEL_SPAN`). The span is found by
 * `labelSpan` (a `ui.<id>.label` override and the in-place editor ask it) and
 * taken away with the text by `removeWords`; `drawIcon` gathers the words it
 * moves into a new span from the text and any span child, which today is only
 * that one. A second idea of where the words are is how a state's old words
 * once stayed on screen beside its new ones. What an override puts back and
 * what the editor reads out of the node are theirs (modules/overrides.js,
 * modules/text_edit.js).
 *
 * A button's accessible name follows its words (`nameAfterWords`), falling
 * back to the name it was drawn with when it shows none.
 */

import { assetUrl, basePath } from './base_path.js';
import { REM_BASE_PX } from './layout.js';

/** The class of the span a control's words go in beside an icon. */
const LABEL_SPAN = 'panel-label-span';

const SVG = 'http://www.w3.org/2000/svg';

/** The span a control with an icon keeps its words in, or null. */
export function labelSpan(node) {
    return node.querySelector(`:scope > .${LABEL_SPAN}`);
}

/** Take a control's words off, leaving its other children (an icon, a picture layer) where they are. */
export function removeWords(el) {
    Array.from(el.childNodes).forEach((n) => {
        if (n.nodeType === Node.TEXT_NODE) n.remove();
        else if (n.classList?.contains(LABEL_SPAN)) n.remove();
    });
}

/** Write `text` as a node's words in place of the ones it has; a button is named after them. */
export function showWords(el, text) {
    removeWords(el);
    if (text != null && text !== '') {
        el.appendChild(document.createTextNode(String(text)));
    }
    if (el.tagName === 'BUTTON') nameAfterWords(el, text);
}

/** A button's accessible name: its words, or the name it was drawn with when it shows none. */
export function nameAfterWords(button, text) {
    button.setAttribute('aria-label', text != null && text !== ''
        ? String(text) : (button._baseAccessibleName || ''));
}

/**
 * An icon node: an uploaded file (`assets://`) as a picture, else one from
 * the panel's sprite. `size` is in rem, so an icon scales with the rest of
 * the panel; leave it out and the stylesheet sizes the icon instead, since an
 * inline size would beat any rule there.
 */
export function iconNode(iconName, size, color) {
    if (!iconName) return null;
    if (iconName.startsWith('assets://')) return uploadedIcon(iconName, size, color);

    const svg = document.createElementNS(SVG, 'svg');
    const use = document.createElementNS(SVG, 'use');
    const iconUrl = `${basePath()}/panel/icons.svg#${iconName}`;
    use.setAttribute('href', iconUrl);
    use.setAttributeNS('http://www.w3.org/1999/xlink', 'href', iconUrl);
    svg.appendChild(use);
    // Sized in CSS rather than the width/height attributes: SVG attributes
    // are user units and can't carry rem, and CSS wins over them anyway.
    sizeInRem(svg, size);
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', color || 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.style.flexShrink = '0';
    return svg;
}

/** An uploaded icon, drawn dark when the control asks for a colour (a picture cannot take a stroke). */
function uploadedIcon(iconName, size, color) {
    const img = document.createElement('img');
    img.src = assetUrl(iconName);
    sizeInRem(img, size);
    img.style.flexShrink = '0';
    if (color) img.style.filter = `brightness(0) saturate(100%)`;
    return img;
}

function sizeInRem(node, size) {
    if (size == null) return;
    node.style.width = `${size}rem`;
    node.style.height = `${size}rem`;
}

/**
 * Draw the icon a control names beside its words, from the words already on
 * it: on the side it names (`left` unless it says otherwise), above or below
 * them, or alone in the middle (`center`). The style's icon settings win over
 * the control's own. A control with no icon is left as it is.
 */
export function drawIcon(el, element) {
    const icon = element.style?.icon || element.icon;
    if (!icon) return;
    const { position, size, color } = iconSettings(element);

    // The picture layer is a child of its own and goes back first; the words
    // are what text the node holds, in it or in a span.
    const imageLayer = el.querySelector(':scope > .panel-button-image-layer');
    const words = wordsOn(el, imageLayer);
    el.textContent = '';
    if (imageLayer) el.prepend(imageLayer);

    const iconEl = iconNode(icon, size, color);
    if (!iconEl) return;
    if (position === 'center') {
        el.appendChild(iconEl);
        return;
    }
    const textSpan = document.createElement('span');
    textSpan.className = LABEL_SPAN;
    textSpan.textContent = words;
    placeBeside(el, iconEl, textSpan, position);
}

/** Where an icon goes, how big, and its colour. */
function iconSettings(element) {
    return {
        position: styleFirst(element.style?.icon_position, element.icon_position, 'left'),
        size: styleFirst(element.style?.icon_size, element.icon_size, 24 / REM_BASE_PX),
        color: styleFirst(element.style?.icon_color, element.icon_color, null),
    };
}

/** An icon setting: the style's, else the control's own, else `fallback`; an empty one counts as unset. */
function styleFirst(fromStyle, own, fallback) {
    return fromStyle || own || fallback;
}

/** The words on a node: its text, in it or in a span, leaving the picture layer out. */
function wordsOn(el, imageLayer) {
    let text = '';
    Array.from(el.childNodes).forEach((n) => {
        if (n.nodeType === Node.TEXT_NODE || isWordsSpan(n, imageLayer)) text += n.textContent;
    });
    return text;
}

/** A span among a node's children, other than its picture layer: words drawn beside an icon. */
function isWordsSpan(n, imageLayer) {
    return n.nodeType === Node.ELEMENT_NODE && n !== imageLayer && n.tagName === 'SPAN';
}

/** The icon and the words in a column (`top`, `bottom`) or a row, the icon first on `top` and `left`. */
function placeBeside(el, iconEl, textSpan, position) {
    const column = position === 'top' || position === 'bottom';
    if (column) el.style.flexDirection = 'column';
    el.style.gap = column ? '0.2857rem' : '0.4286rem';
    const iconFirst = position === (column ? 'top' : 'left');
    el.appendChild(iconFirst ? iconEl : textSpan);
    el.appendChild(iconFirst ? textSpan : iconEl);
}
