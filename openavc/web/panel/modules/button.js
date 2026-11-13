/**
 * A button's face: which of its parts its display mode draws, its picture
 * after every paint, whether a toggle is lit, and the words each kind of
 * look puts on it together with the field an in-place edit of them writes.
 *
 * The panel keeps everything that borrows its own state: the node, the
 * press, the binding records, the theme, the unavailable mark and every
 * write of words (an edit in flight holds them, `_setLabelText`). What is
 * here decides from the definition, the look binding and the entry the look
 * picked (modules/looks.js), or draws on the node through modules/paint.js.
 *
 * The page review's renderer scan (tests/test_ui_page_review_mirrors.py)
 * credits all of this text to the two controls that hand it a definition,
 * the button and the camera preset (which registers the button's look), so
 * it reads only what both of them read through that look already.
 */

import { applyFrameless, applyImageEffect, applyStyle, clearStateColours } from './paint.js';
import { sameWords } from './looks.js';
import { drawIcon } from './words.js';

/** The display modes that draw no words, and those that draw the button's own picture. */
const WORDLESS_MODES = new Set(['image', 'icon_only']);
const PICTURE_MODES = new Set(['image', 'image_text']);

/** Where a toggle's two words are authored when its press says nothing else. */
const PRESS_WORDS = ['bindings', 'do', 'press', 0];

/**
 * Whether a button's display mode draws its words (not `image` or
 * `icon_only`) and its own picture (`image` or `image_text`). A picture a
 * state names shows in any mode: the Builder offers it on every button.
 */
export function showsWords(elementDef) {
    return !WORDLESS_MODES.has(elementDef.display_mode || 'text');
}

export function showsOwnPicture(elementDef) {
    return PICTURE_MODES.has(elementDef.display_mode || 'text');
}

/**
 * Draw a button's own face once, over its themed style: no chrome when it is
 * frameless, no words where its display mode draws none, an icon-only
 * button's icon in the middle unless it says where (written onto the
 * definition, as the panel always has), the icon beside its words, and its
 * own picture where its mode draws one. The picture goes last, so nothing
 * that rebuilds the words can take its layer away.
 */
export function drawFace(node, element, themedStyle) {
    if (element.frameless) applyFrameless(node);
    const displayMode = element.display_mode || 'text';
    if (!showsWords(element)) node.textContent = '';
    if (displayMode === 'icon_only' && !element.icon_position) {
        element.icon_position = 'center';
    }
    drawIcon(node, element);
    if (showsOwnPicture(element) && element.button_image) {
        drawPicture(node, element, element.button_image, themedStyle.bg_color);
        if (displayMode === 'image_text') {
            node.style.textShadow = '0 1px 0.2143rem rgba(0,0,0,0.8)';
        }
    }
}

/**
 * Paint a button in `style` after a look or its device changed: the colours a
 * state wrote go first where `rest` has none to put back, then the style, and
 * a frameless button loses its chrome again, which the colours bring back.
 */
export function repaint(node, elementDef, rest, style) {
    clearStateColours(node, rest);
    applyStyle(node, style);
    if (elementDef.frameless) applyFrameless(node);
}

/** Draw `image` as a button's picture, with its fit, blend and opacity, tinted with `tint`. */
export function drawPicture(node, elementDef, image, tint) {
    applyImageEffect(node, image, {
        fit: elementDef.image_fit,
        blend: elementDef.image_blend_mode,
        opacity: elementDef.image_opacity,
        tintColor: tint,
    });
}

/**
 * Draw the picture a button shows in its current state, after a paint has
 * written its background image for the gradient (every paint does, and the
 * picture lives in the same property): the state's own, else the button's
 * where its display mode draws one. With neither, a previous state's
 * picture on an effect layer goes too. The tint falls back to the text
 * colour inside the paint, so a masked picture stays visible when the state
 * carries no colour.
 */
export function redrawPicture(node, elementDef, stateImage, tint) {
    const image = stateImage
        || (showsOwnPicture(elementDef) ? elementDef.button_image : null);
    if (!image) {
        node.querySelector(':scope > .panel-button-image-layer')?.remove();
        return;
    }
    drawPicture(node, elementDef, image, tint);
}

/**
 * The words a button's look draws, as [text, the field an edit of them
 * writes]: what the look's form names, else the button's own label. Null
 * leaves the words as they are.
 */
export function lookWords(elementDef, binding, look, style) {
    const named = binding.states ? stateWords(look) : twoStateWords(binding, look, style);
    return named || (elementDef.label ? [elementDef.label, ['label']] : null);
}

/** A state's own words, when it names any. */
function stateWords({ appearance, wordsPath }) {
    return appearance.label !== undefined ? [String(appearance.label), wordsPath] : null;
}

/**
 * The two-state form's words: the active or inactive label, else a label
 * in the style, where whichever half supplied it is the half an edit writes.
 */
function twoStateWords(binding, { appearance, wordsPath, isActive }, style) {
    const half = isActive ? 'label_active' : 'label_inactive';
    if (binding[half]) return [binding[half], ['bindings', 'show', 'look', half]];
    if (style.label === undefined) return null;
    return [style.label, appearance.label !== undefined ? wordsPath : ['style', 'label']];
}

/**
 * Whether a toggle is lit: its device answers, it has a reading, and the
 * reading's words are the value that means on, apart from case. The press
 * decides the same question its own way (modules/press.js).
 */
export function toggleLit(binding, stateValue, offline) {
    return !offline && stateValue != null && binding.value !== undefined &&
        sameWords(stateValue, binding.value);
}

/** Whether a toggle names words of its own for on or off. */
export function toggleNamesWords(binding) {
    return binding.on_label != null || binding.off_label != null;
}

/**
 * A toggle's words and the field an edit of them writes: its on or off
 * word; an empty one, or none while its device is away, falls back to the
 * button's own label, so an edit of what is showing belongs to whichever one
 * actually drew.
 */
export function toggleWords(elementDef, binding, on, offline) {
    const word = offline ? null : (on ? binding.on_label : binding.off_label);
    if (word == null || word === '') return [elementDef.label || '', ['label']];
    const labelBase = binding._labelBase || PRESS_WORDS;
    return [word, [...labelBase, on ? 'on_label' : 'off_label']];
}
