/**
 * The panel's link to the Programmer that frames it: the UI Builder's design
 * canvas (the panel opened with `edit=1`) and its Preview (`preview=1`).
 *
 * Who the Builder is: a parent window on this panel's own origin that opened
 * the panel with one of those two marks. The rule worth knowing before
 * changing anything here: any page can frame /panel (a signage player, a
 * dashboard's card) and any page can add ?preview=1, so the marks decide
 * whether the panel takes its definition from its parent at all
 * (`isBuilderFrame`; anything else framing it gets an ordinary panel), and
 * the origin decides what it will hear and where it will speak. Messages are
 * taken only from the parent on this origin (`listenToBuilder`) and sent only
 * to it, and only when the panel is the Builder's (`postToBuilder`), never
 * with '*', which would hand a framing page the editor's text, element errors
 * and file names. Both directions live here because each was once fixed on
 * its own.
 *
 * `listenToBuilder` is lent `on`, one handler per message the Builder sends:
 * `project(msg)` (a definition: editor-init and editor-project), `page(msg)`,
 * `editText(msg)`, `cancelText(msg)` and `placements(msg)`.
 */

/** Is this panel the Builder's canvas or its Preview, framed by the Programmer? */
export function isBuilderFrame(params, editMode) {
    const framed = !!(window.parent && window.parent !== window);
    return framed && (params.get('preview') === '1' || editMode);
}

/**
 * Send the Builder a message, when this panel is the Builder's (`isBuilder`,
 * the `isBuilderFrame` answer): to the parent on this origin only.
 */
export function postToBuilder(isBuilder, msg) {
    if (!isBuilder) return;
    try { window.parent.postMessage(msg, window.location.origin); } catch (_) { /* ignore */ }
}

/** Hand each message the Builder sends to its handler in `on`. */
export function listenToBuilder(on) {
    window.addEventListener('message', (event) => {
        const msg = fromBuilder(event);
        if (msg) dispatch(msg, on);
    });
}

/** The message, when it comes from the Builder: the parent window, on this origin, an object. */
function fromBuilder(event) {
    if (event.source !== window.parent) return null;
    if (event.origin !== window.location.origin) return null;
    const msg = event.data;
    return msg && typeof msg === 'object' ? msg : null;
}

/** The messages the Builder sends the panel. Anything else is ignored. */
function dispatch(msg, on) {
    switch (msg.type) {
        case 'openavc:editor-init':
        case 'openavc:editor-project':
            on.project(msg);
            break;
        case 'openavc:editor-edit-text':
            on.editText(msg);
            break;
        case 'openavc:editor-cancel-text':
            on.cancelText(msg);
            break;
        case 'openavc:editor-placements':
            on.placements(msg);
            break;
        case 'openavc:editor-page':
            on.page(msg);
            break;
    }
}

/** The definition a project message carries (editor-init, editor-project), or null. */
export function definitionIn(msg) {
    return msg.project?.ui || msg.ui || null;
}

/** Does the message carry this field, even as undefined or null? */
export function carries(msg, field) {
    return Object.prototype.hasOwnProperty.call(msg, field);
}

/**
 * The state the design canvas draws against: the demo values the Builder
 * sends, copied, or none. The canvas has no socket, so this is all it has.
 */
export function demoStateOf(msg) {
    return msg.demoState && typeof msg.demoState === 'object' ? { ...msg.demoState } : {};
}

/**
 * The size each element's text STARTS at on the canvas, in rem: the value
 * the cascade gives it before the element's own font_size, if it has one,
 * is applied inline. The Builder's Style panel shows it as the Font Size
 * placeholder, so the number a person reads there is the number on the
 * canvas, whatever the stylesheet, the theme or a project class decided it.
 * The Builder keeps no copy of the panel's default; this is how it knows.
 *
 * Read by clearing the inline size, asking, and putting it back. There is
 * no other way to ask the cascade what it would have said, and nothing
 * transitions font-size, so the read is exact and nothing flickers. In rem,
 * the unit a project stores and the Builder converts for display: pixels
 * here would be the canvas preset's pixels, not the panel's. `rootPx` is the
 * root's font size in px.
 */
export function startingTextSizes(root, rootPx) {
    const fontSizeRem = {};
    for (const el of root.querySelectorAll('[data-element-id]')) {
        const own = el.style.fontSize;
        if (own) el.style.fontSize = '';
        const px = parseFloat(getComputedStyle(el).fontSize);
        if (own) el.style.fontSize = own;
        // Four decimals, the precision a project stores measurements at, so a
        // size that IS the default compares equal to it in the Builder.
        if (Number.isFinite(px)) fontSizeRem[el.dataset.elementId] = Math.round(px / rootPx * 1e4) / 1e4;
    }
    return fontSizeRem;
}
