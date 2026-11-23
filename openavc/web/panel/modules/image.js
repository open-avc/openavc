/**
 * The picture an Image shows, and the one note it shows in the picture's
 * place when the picture cannot be loaded.
 *
 * An Image keeps ONE <img> and at most one "Image not found" note for its
 * whole life, because a state look swaps the picture many times: a new note
 * per failure would stack them, and a picture that loads after one that
 * failed has to take the note away again. No picture at all removes both, so
 * an Image whose pictures all live in its states draws nothing until one of
 * them is chosen. What it holds is kept on the Image's node (`_pictureSrc`,
 * `_pictureImg`, `_pictureMissing`), so a page drawn again starts it afresh.
 *
 * Only the Image hands this module its definition (its `label`, the
 * picture's alternative text, and its `object_fit`): the page review's
 * renderer scan (tests/test_ui_page_review_mirrors.py) credits the whole
 * module to every control that does.
 */

import { assetUrl } from './base_path.js';

const NOTE_STYLE = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--panel-text);opacity:0.5;font-size:0.8571rem;';

/** Show `src` in the Image `node` drawn from `elementDef`, or no picture when `src` names none (assetUrl). */
export function showPicture(node, elementDef, src) {
    if (node._pictureSrc === src) return;
    node._pictureSrc = src;
    const url = assetUrl(src);
    if (!url) {
        node._pictureImg?.remove();
        node._pictureImg = null;
        dropNote(node);
        return;
    }
    const img = node._pictureImg || newPicture(node, elementDef);
    img.src = url;
}

/**
 * The Image's one <img>, first in it, shown once it loads and hidden behind the
 * note when it cannot. Its answer counts only while it is still the Image's
 * picture: one taken away for a state with none may still be loading, and its
 * late load or failure says nothing about the picture showing now.
 */
function newPicture(node, elementDef) {
    const img = document.createElement('img');
    img.alt = elementDef.label || 'Panel image';
    img.loading = 'lazy';
    if (elementDef.object_fit) img.style.objectFit = elementDef.object_fit;
    const whileShown = (answer) => () => {
        if (node._pictureImg === img) answer();
    };
    img.onload = whileShown(() => {
        img.style.display = '';
        dropNote(node);
    });
    img.onerror = whileShown(() => {
        img.style.display = 'none';
        showNote(node);
    });
    node.prepend(img);
    node._pictureImg = img;
    return img;
}

/** The note, made once and kept, naming the picture the Image is showing now. */
function showNote(node) {
    let note = node._pictureMissing;
    if (!note) {
        note = document.createElement('div');
        note.textContent = 'Image not found';
        note.style.cssText = NOTE_STYLE;
        node.appendChild(note);
        node._pictureMissing = note;
    }
    note.title = node._pictureSrc || '';
}

function dropNote(node) {
    node._pictureMissing?.remove();
    node._pictureMissing = null;
}
