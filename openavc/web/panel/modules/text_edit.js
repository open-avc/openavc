/**
 * Editing a control's words in place on the design canvas (edit mode only).
 *
 * The Builder's canvas IS the panel document, in an iframe, with a transparent
 * overlay of hit boxes on top of it. A double-click out there arrives as
 * `openavc:editor-edit-text` naming an element, and the text the author is
 * looking at becomes editable where it sits -- the real node, the real font,
 * the real box, so nothing shifts when the edit commits.
 *
 * What is edited is always the authored SOURCE, never the rendered form. A
 * label drawing "Amp draw: 0.08 A" opens as the literal "Amp draw: {value} A";
 * one drawing bold text opens as "**bold**". That is what makes plain-text
 * editing correct everywhere and a rich-text editor unnecessary.
 *
 * The renderers and the editor meet through two attributes on the drawn nodes,
 * both written and read only here: which authored field produced the words on
 * screen (`data-avc-text-src`, a JSON path from the element definition, written
 * through `recordSource`), and which node holds a caption
 * (`data-avc-text-host`, through `markCaption`). The only moment the source is
 * known is the moment the words are written, so every writer says it then.
 *
 * Two doors read them, and they agree on one gate: the report to the Builder
 * says which drawn elements may be edited and why one cannot (`offers`), and
 * an edit only opens on what the report offered (`begin`). One `TextEditor`
 * per panel; it holds the edit in flight, so opening a second edit closes the
 * first and the words being edited are frozen against live state (`holds`).
 */

const SOURCE = 'data-avc-text-src';
const CAPTION = 'data-avc-text-host';

/**
 * The controls whose text is edited on the canvas, and nothing else.
 *
 * Not every control that draws words belongs here. The gesture is for text
 * somebody actually retypes: a button's words, a label, and the CAPTION on the
 * controls that carry one -- a dropdown's label, a gauge's label, a fader's
 * label. Those captions are a node of their own holding one field, which is
 * what makes them safe.
 *
 * Deliberately absent:
 *
 * - `matrix`, whose row names come from a generator plus overrides and a
 *   naming rule copied in four places. Its setup dialog owns them.
 * - `camera_preset`, which draws `preset_number` and `label` joined by a
 *   newline in ONE text node, so an edit would hand back "3\nWide Shot" and
 *   have to parse it apart again.
 * - `clock`, `image`, `plugin` and `custom`, which draw computed values,
 *   somebody else's markup, or no words at all.
 *
 * Checked at both doors, so a control cannot become editable as a side effect
 * of gaining a binding -- which is exactly how a camera preset used to slip
 * in, through a look recording a source its renderer never composed alone.
 */
const IN_PLACE_TEXT_TYPES = new Set([
    // Words that ARE the control.
    'button', 'label',
    // A caption beside the control, in a node of its own.
    'group', 'status_led', 'slider', 'fader', 'level_meter',
    'gauge', 'select', 'text_input', 'list', 'keypad',
    // A nav button is a button with words on it. Its text falls back to the
    // target page id, so it opens EMPTY with that id behind it as a
    // placeholder: committing the fallback would freeze a copy of a name that
    // should keep following the page (placeholderFor).
    'page_nav',
]);

const FROM_THE_ROOM = 'These words come from the room, not from the element\'s own '
    + 'settings, so they cannot be changed here.';

/**
 * The caret ring and the placeholder, injected once per panel and only in
 * edit mode. Deliberately NOT in panel-elements.css: that stylesheet ships to
 * real glass, where none of this exists. The snap overlay makes the same
 * choice.
 */
const EDIT_STYLES = `
            [data-avc-text-editing] {
                outline: 2px solid #42a5f5;
                outline-offset: 1px;
                cursor: text;
                min-width: 1ch;
                white-space: pre-wrap;
                /* What is being edited is the stored string, so it is shown as
                   stored. A group caption is drawn uppercase by the panel
                   stylesheet; typing "Audio" into a box reading "AUDIO" and
                   watching it stay capitalised reads as the edit being
                   ignored. The transform comes back on commit.

                   !important because the rule it has to beat is a two-class
                   selector (.panel-group .group-label) and this is one
                   attribute. Scoped to an element mid-edit, in edit mode
                   only, so it reaches nothing a panel ever draws. */
                text-transform: none !important;
            }
            [data-avc-text-editing]:empty::before,
            [data-avc-text-editing][data-avc-text-placeholder]:empty::before {
                content: attr(data-avc-text-placeholder);
                opacity: 0.4;
            }
        `;

/** The page showing, by id, or undefined. */
function pageShowing(ui, pageId) {
    const pages = (ui && ui.pages) || [];
    return pages.find(p => p && p.id === pageId);
}

/** The master elements, which every page draws. */
function masterElements(ui) {
    return (ui && ui.master_elements) || [];
}

/** The element type for every id drawn on the page showing, a master's last. */
function drawnTypes(ui, pageId) {
    const types = {};
    // A page is found only when it is an object, so `?.` reads it as `&&` did.
    const page = pageShowing(ui, pageId);
    for (const list of [page?.elements || [], masterElements(ui)]) {
        for (const el of list) {
            if (el && el.id) types[el.id] = el.type;
        }
    }
    return types;
}

/**
 * The authored definition for an id drawn on the page showing, the page's own
 * before a master's. A page's elements are a flat array with parent
 * references, so this is a scan.
 */
function drawnDefinition(ui, pageId, elementId) {
    const page = pageShowing(ui, pageId);
    const flat = (page && Array.isArray(page.elements)) ? page.elements : [];
    const isIt = e => e && e.id === elementId;
    return flat.find(isIt) || masterElements(ui).find(isIt);
}

/** The sentence for words a script or macro is setting, or null when none is. */
function scriptedWords(state, elementId) {
    const override = state[`ui.${elementId}.label`];
    if (override === undefined || override === null) return null;
    return `This text is being set by a script or macro through `
        + `ui.${elementId}.label, not by the element's Label. Change the `
        + `Label in Properties, or clear that key to show it again.`;
}

/**
 * The node whose text an author edits, which is not always the element: the
 * caption the renderer marked, wherever it nested it; then a button's
 * icon-adjacent span; then the element itself, which is right only for a
 * control whose words ARE its whole content.
 */
function textHostFor(el) {
    return el.querySelector(`[${CAPTION}]`)
        || el.querySelector(':scope > .panel-label-span')
        || el;
}

/** The recorded source path on a drawn element, or null when it has none it can read. */
function recordedPath(raw) {
    let path;
    try {
        path = JSON.parse(raw);
    } catch {
        return null;
    }
    return Array.isArray(path) ? path : null;
}

/**
 * The authored string at a recorded source path. Undefined when the path leads
 * nowhere, which is the "author has not written this yet" case and opens an
 * empty editor rather than refusing.
 */
function authoredTextAt(elementDef, path) {
    let node = elementDef;
    for (const seg of path) {
        if (node == null || typeof node !== 'object') return undefined;
        node = node[seg];
    }
    return node == null ? undefined : String(node);
}

/**
 * What to show behind an empty editor: the fallback the renderer would draw if
 * this field stayed empty. A `page_nav` with no label draws its target page
 * id, a `camera_preset` draws "Preset". Those are visible but NOT authored, and
 * committing one as authored text freezes a copy -- rename the page afterwards
 * and the button keeps the old word. So they open empty with the fallback as a
 * placeholder: Enter-unchanged is a no-op, typing creates the field.
 */
function placeholderFor(elementDef) {
    if (elementDef.type === 'page_nav') return elementDef.target_page || 'No Target';
    if (elementDef.type === 'camera_preset') return 'Preset';
    return '';
}

/**
 * Put a string in at the caret, replacing any selection. `execCommand` first
 * because it is the only insertion the browser folds into contenteditable's
 * own undo stack, so ctrl+Z inside an edit still steps back through what was
 * typed. The Selection path is the fallback for when that finally goes away.
 */
function insertTextAtCaret(text) {
    if (document.execCommand && document.execCommand('insertText', false, text)) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    const node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
}

/**
 * What the author actually typed, read blind to the CSS.
 *
 * NOT `innerText`, which is CSS-aware: a group's caption is styled
 * `text-transform: uppercase`, so `innerText` hands back "AUDIO" for a caption
 * authored as "Audio" -- and committing that would have retyped every group
 * caption in the project in capitals, once, permanently, the first time
 * anybody edited one.
 *
 * `textContent` alone would lose the line breaks a multi-line label is
 * allowed, so a `<br>` and a block the browser wrapped a line in each count as
 * a newline.
 */
function editedText(host) {
    let out = '';
    for (const node of host.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) out += node.textContent;
        else if (node.nodeName === 'BR') out += '\n';
        else out += lineBefore(out) + node.textContent;
    }
    return out;
}

/** The break a block's text starts with: none at the start or after one. */
function lineBefore(out) {
    return out !== '' && !out.endsWith('\n') ? '\n' : '';
}

/** The drawn element a node belongs to, or the node itself before it is placed. */
function elementRoot(el) {
    if (!el.closest) return el;
    return el.closest('[data-element-id]') || el;
}

/** Shift-Enter in a label, which may hold several lines. */
function breaksALine(e, edit) {
    return edit.multiline && e.shiftKey;
}

/** Select everything in the node that just took the caret. */
function selectAllOf(host) {
    host.focus();
    const range = document.createRange();
    range.selectNodeContents(host);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
}

export class TextEditor {
    /**
     * `host` is what the editor reads from its panel, each at call time:
     * `editMode()`, `state()`, `ui()` (the definition being drawn), `page()`
     * (the page showing) and `post(message)` (to the Builder).
     */
    constructor(host) {
        this._host = host;
        // The edit in flight, or null. Held here rather than per element so the
        // freeze in `holds` is one comparison, and so opening a second edit
        // closes the first.
        this._edit = null;
        this._stylesInstalled = false;
        // Bound once: add/removeEventListener only match on identity, and a
        // fresh arrow per edit would leak a listener onto every node touched.
        this._onKey = (e) => this._key(e);
        this._onBlur = () => this.finish(true);
    }

    /**
     * Record WHICH authored field produced the text now on screen, as a JSON
     * array rather than a dotted string because a segment can be a state VALUE
     * -- `show.look.states["2.5"].label` is a legal slot. `null` means the text
     * is real but nothing authored it: a runtime `ui.<id>.label` override, a
     * computed value, a positional fallback; the editor refuses those rather
     * than writing somewhere that would not show. Edit mode only: a wall panel
     * carries no editor and pays nothing for this.
     */
    recordSource(el, path) {
        if (!this._host.editMode()) return;
        if (!el || el.nodeType !== Node.ELEMENT_NODE) return;
        const host = elementRoot(el);
        if (path == null) host.removeAttribute(SOURCE);
        else host.setAttribute(SOURCE, JSON.stringify(path));
    }

    /**
     * Mark the node a caption's words sit in. A caption is NOT findable by
     * class -- `.group-label`, `.gauge-label` and `.list-label` have one, but a
     * fader's, a dropdown's, a keypad's and a status LED's are a bare <label>
     * or <div> with none -- and without the mark, opening a fader replaced the
     * whole control with the caption text.
     */
    markCaption(captionNode) {
        if (this._host.editMode() && captionNode) captionNode.dataset.avcTextHost = '1';
    }

    /** Is an edit in flight on this element? Live state must not rewrite its words. */
    holds(el) {
        return !!this._edit && this._edit.el === el;
    }

    /**
     * Which drawn elements have words an author can edit here, and, for those
     * whose words a script is setting, why not. Reported rather than derived
     * from a table of types because it is per ELEMENT: an icon-only button
     * draws no words, a bound label draws a format string, and a button whose
     * label a script is overriding has no authored source at all. A control
     * that draws no text at all says nothing, because nothing is expected of
     * it.
     */
    offers(root) {
        const editableText = [];
        const textRefusals = {};
        const types = drawnTypes(this._host.ui(), this._host.page());
        for (const el of root.querySelectorAll('[data-element-id]')) {
            const id = el.dataset.elementId;
            if (!IN_PLACE_TEXT_TYPES.has(types[id])) continue;
            if (el.hasAttribute(SOURCE)) {
                editableText.push(id);
                continue;
            }
            const why = scriptedWords(this._host.state(), id);
            if (why) textRefusals[id] = why;
        }
        return { editableText, textRefusals };
    }

    /** Open an edit on an element's words, or say why it cannot be. */
    begin(elementId) {
        this._installStyles();
        const target = this._target(elementId);
        if (!target) return;
        if (this._edit) this.finish(true);
        const raw = target.el.getAttribute(SOURCE);
        if (raw === null) {
            this._refuse(elementId);
            return;
        }
        const path = recordedPath(raw);
        if (path) this._open(elementId, target, path);
    }

    /** End the edit in flight: the rendered form goes back, and a changed commit is sent. */
    finish(commit) {
        const edit = this._edit;
        if (!edit) return;
        this._edit = null;
        const { host } = edit;
        host.removeEventListener('keydown', this._onKey);
        host.removeEventListener('blur', this._onBlur);
        const typed = editedText(host).replace(/\n$/, '');
        host.contentEditable = edit.restore.editable || 'inherit';
        delete host.dataset.avcTextEditing;
        delete host.dataset.avcTextPlaceholder;
        // Put the rendered form back either way. On a commit the Builder
        // writes the project and the repost redraws this element properly;
        // until that lands, what was on screen before is a truer picture than
        // the source string the author was just typing into.
        host.innerHTML = edit.restore.html;
        if (!commit || typed === edit.before) return;
        this._host.post({
            type: 'openavc:editor-text-commit',
            elementId: edit.elementId,
            path: edit.path,
            value: typed,
        });
    }

    /** The Builder's cancel: the edit in flight, if any, ends uncommitted. */
    cancel() {
        this.finish(false);
    }

    /**
     * The page is about to be replaced, including whatever node the edit was
     * holding. Let go of it first: a commit from a detached node would write
     * text the author can no longer see.
     */
    release() {
        if (!this._edit) return;
        this._edit = null;
        this._host.post({ type: 'openavc:editor-text-closed' });
    }

    _installStyles() {
        if (this._stylesInstalled) return;
        this._stylesInstalled = true;
        const style = document.createElement('style');
        style.textContent = EDIT_STYLES;
        document.head.appendChild(style);
    }

    /**
     * The rendered node and the authored definition for an id, when it is a
     * type the gesture is for. Document-scoped rather than #panel-root: an
     * overlay page draws into its own surface, and only one page is in the
     * document at a time.
     */
    _target(elementId) {
        const el = document.querySelector(`[data-element-id="${CSS.escape(elementId)}"]`);
        if (!el) return null;
        const elementDef = drawnDefinition(this._host.ui(), this._host.page(), elementId);
        if (!elementDef || !IN_PLACE_TEXT_TYPES.has(elementDef.type)) return null;
        return { el, elementDef };
    }

    /**
     * Nothing authored is drawing here: the renderer wrote words it computed (a
     * value readout, a runtime override), or this type draws no editable text.
     */
    _refuse(elementId) {
        this._host.post({
            type: 'openavc:editor-text-refused',
            elementId,
            reason: scriptedWords(this._host.state(), elementId) || FROM_THE_ROOM,
        });
    }

    _open(elementId, { el, elementDef }, path) {
        const host = textHostFor(el);
        const source = authoredTextAt(elementDef, path);
        const edit = {
            elementId,
            path,
            host,
            el,
            // Everything the host was showing, so a cancel puts it back exactly
            // rather than re-rendering the page around it.
            restore: { html: host.innerHTML, editable: host.contentEditable },
            before: source === undefined ? '' : source,
            multiline: elementDef.type === 'label',
        };
        this._edit = edit;
        // Plain text, and plaintext-only where the browser has it: what is
        // being edited is a source string, so a pasted <b> has no meaning here.
        host.contentEditable = 'plaintext-only';
        if (host.contentEditable !== 'plaintext-only') host.contentEditable = 'true';
        host.textContent = edit.before;
        host.dataset.avcTextEditing = '1';
        if (!edit.before) host.dataset.avcTextPlaceholder = placeholderFor(elementDef);
        selectAllOf(host);
        host.addEventListener('keydown', this._onKey);
        host.addEventListener('blur', this._onBlur);
        this._host.post({
            type: 'openavc:editor-text-open',
            elementId,
            path,
            source: edit.before,
            multiline: edit.multiline,
        });
    }

    _key(e) {
        const edit = this._edit;
        // Mid-composition Enter belongs to the IME, not to us.
        if (!edit || e.isComposing) return;
        const ending = this._keyEnds(e, edit);
        if (ending) {
            e.preventDefault();
            if (ending.stop) e.stopPropagation();
            this.finish(ending.commit);
            return;
        }
        // A text-only button holds its words in the <button> itself, and a
        // button's own answer to the space bar is "press me" -- so a label
        // typed as "Mic Mute" committed as "MicMute" and the space was gone
        // with no sign it had been dropped. Insert it ourselves.
        if (e.key === ' ' && edit.host.closest('button')) {
            e.preventDefault();
            insertTextAtCaret(' ');
        }
    }

    /**
     * Whether a key ends the edit, and how. Escape cancels. Enter commits, but
     * a label may hold several lines, so there shift-Enter breaks the line --
     * the same bargain every editor of this shape makes; single-line types
     * commit on either. Tab commits rather than inserting one: a caret in a
     * control's caption has nowhere to put a tab.
     */
    _keyEnds(e, edit) {
        if (e.key === 'Escape') return { commit: false, stop: true };
        if (e.key === 'Enter') return breaksALine(e, edit) ? null : { commit: true, stop: true };
        if (e.key === 'Tab') return { commit: true, stop: false };
        return null;
    }
}
