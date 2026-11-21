/**
 * ui.<element id>.* overrides: what a script or macro sets on a control while
 * the program runs, drawn over whatever the control's own bindings drew.
 *
 * OVERRIDES is the list: the five properties a script can set, the order they
 * are put back and drawn in, and for each what it keeps of the control as first
 * drawn, how it draws and how it is put back. The Programmer's key picker offers
 * the same five from `uiOverrideProperties.ts`, and
 * tests/test_ui_override_properties_mirror.py holds the two lists equal, so a
 * sixth property is a row here, a row there and a line in the scripting
 * reference.
 *
 * The rule worth knowing before changing anything here: a cleared override
 * gives the control back what it would show had the override never been set.
 * Each control is snapshotted the first time overrides are drawn on it (its
 * colours, opacity, display and words as drawn), the snapshot puts back what a
 * cleared key was covering, and then the control's own bindings draw again from
 * current state over it -- a look may have drawn something else since -- and
 * the overrides still set go on top. The snapshot lives as long as the control
 * the page drew: a redraw draws new controls and takes new snapshots.
 *
 * The panel lends what this reads (state, the in-place editor's hold on a
 * control's words, its label writer and rich-text formatter, and the redraw of
 * one control's bindings), each at call time.
 */

import { labelSpan, nameAfterWords } from './words.js';
import { richText } from './label.js';

/** Words that came from no authored field: an override's own. */
const NO_SOURCE = null;

/** A value a script set: anything but undefined and null, which are a cleared key. */
const isSet = (v) => v !== undefined && v !== null;

/** An override that writes one style property, put back from the snapshot. */
function styleOverride(property) {
    return {
        capture: (el) => ({ [property]: el.style[property] }),
        putBack: (el, base) => { el.style[property] = base[property]; },
        draw: (el, value) => { el.style[property] = String(value); return true; },
    };
}

/**
 * The six, in the order they are put back and drawn. `capture` is what a
 * row keeps of the control as first drawn, which is all its `putBack` reads;
 * `draw` answers whether it drew anything: a control with no words to
 * replace takes no label.
 */
export const OVERRIDES = [
    {
        name: 'label',
        capture: (el, elementDef) => wordsAsDrawn(el, elementDef),
        putBack: (el, base, ui) => ui.writeWords(el, base.words, { text: base.label, path: base.labelPath, rich: base.labelRich }),
        // Runtime words with no authored home. The editor refuses these and
        // says which key is setting them: writing the element's own label here
        // would be a silent no-op, because this override would still be
        // covering it. Preserves the image layer and other element children.
        draw: (el, value, base, ui) => {
            if (!base.words) return false;
            ui.writeWords(el, base.words, { text: String(value), path: NO_SOURCE });
            return true;
        },
    },
    { name: 'bg_color', ...styleOverride('backgroundColor') },
    { name: 'text_color', ...styleOverride('color') },
    { name: 'opacity', ...styleOverride('opacity') },
    {
        name: 'visible',
        capture: (el) => ({ display: el.style.display }),
        // Hand display back to a visible_when binding if one governs this
        // element (the redraw after a clear re-asserts it); otherwise restore
        // the rendered base.
        putBack: (el, base, ui, elementDef) => {
            el.style.display = elementDef?.bindings?.show?.visible_when ? '' : base.display;
        },
        draw: (el, value) => {
            el.style.display = (value === false || value === 'false') ? 'none' : '';
            return true;
        },
    },
    {
        name: 'url',
        // Web element only: the page its frame shows. This is what makes a
        // web element controllable out of the box -- state.set ui.<id>.url
        // works with no binding authored and no variable declared, the same
        // standing start every other override gives a control.
        // 仅 web 元素：其帧显示的页面。这让 web 元素开箱即受控——
        // state.set ui.<id>.url 无需创作绑定、无需声明变量，与其他覆盖字段
        // 给控件的"从零起点"一致。
        //
        // The entry runs on every control, so it gates: `draw` reads the
        // node's element type (draw receives no elementDef; the draw tree
        // writes it), and capture/putBack read elementDef's. A cleared key
        // puts the element's own url back and lets the redraw re-assert a
        // binding's answer, so override > binding > static.
        // 该条目会作用于每个控件，因此需要门控：draw 读节点的元素类型
        // （draw 不收 elementDef，绘制树会写入），capture/putBack 读
        // elementDef 的。键被清除时先放回元素自身的 url，再由重绘重新确认
        // 绑定的答案——优先级 override > binding > static。
        capture: (el, elementDef) => ({
            url: elementDef?.type === 'web' ? String(elementDef.url || '') : undefined,
        }),
        putBack: (el, base, ui, elementDef) => {
            if (elementDef?.type !== 'web' || !base?.url) return false;
            const iframe = el._pluginIframe;
            if (!iframe) return false;
            iframe.src = base.url;
            return true;
        },
        draw: (el, value) => {
            if (el.dataset.elementType !== 'web') return false;
            const iframe = el._pluginIframe;
            if (!iframe) return false;
            iframe.src = String(value);
            return true;
        },
    },
];

/** Controls whose words are the control itself rather than a caption. */
const OWN_WORDS_TYPES = new Set(['button', 'page_nav', 'camera_preset', 'label']);

/**
 * Where a control's words are, for a `ui.<id>.label` override: the caption
 * on a control that carries one (a slider, a dropdown, a meter, a keypad),
 * the control itself where its words are the control (a button, a page
 * nav, a camera preset, a label). Null for a control with no words to
 * replace -- an image, a clock, a frame, or a captioned control given no
 * caption -- and the override draws nothing there.
 */
function overrideWordsNode(el, elementDef) {
    if (el._captionNode) return el._captionNode;
    return OWN_WORDS_TYPES.has(elementDef?.type) ? el : null;
}

/**
 * The words a node draws, leaving icons and image layers out: the span
 * beside an icon when there is one, else the text sitting in the node.
 */
function drawnWords(node) {
    const span = labelSpan(node);
    if (span) return span.textContent;
    let text = '';
    for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) text += child.nodeValue;
    }
    return text;
}

/**
 * Whether a control's words are a label drawn as rich text: put back from
 * its source, so its **bold** comes back bold rather than as asterisks.
 */
function drawnAsRichText(el, words, elementDef) {
    if (words !== el || elementDef?.type !== 'label') return false;
    return !!elementDef.style?.white_space && !labelSpan(el);
}

/** The words a cleared label override gives back, or null for a control with none. */
function wordsToGiveBack(words, rich, elementDef) {
    if (!words) return null;
    return rich ? String(elementDef.text ?? '') : drawnWords(words);
}

/** A control's words as first drawn: where they are, what they say, and the field they came from. */
function wordsAsDrawn(el, elementDef) {
    const words = overrideWordsNode(el, elementDef);
    const rich = drawnAsRichText(el, words, elementDef);
    return {
        words,
        label: wordsToGiveBack(words, rich, elementDef),
        labelRich: rich,
        labelPath: elementDef?.type === 'label' ? ['text'] : ['label'],
    };
}

/** The control as first drawn: what each row keeps, which is what a cleared override puts back. */
function snapshot(el, elementDef) {
    return Object.assign({}, ...OVERRIDES.map((o) => o.capture(el, elementDef)));
}

export class UiOverrides {
    constructor(host) {
        this._host = host;
        // Per drawn control (the panel's element map entry): its snapshot and
        // the overrides drawn on it now.
        this._drawn = new WeakMap();
    }

    /** Draw every control's overrides from current state, putting back what a cleared one covered. */
    apply(elementMap) {
        const state = this._host.state();
        for (const [elementId, entry] of Object.entries(elementMap)) {
            const el = entry.el;
            if (!el || !el.style) continue;
            const prefix = `ui.${elementId}.`;
            const values = OVERRIDES.map((o) => state[prefix + o.name]);
            this._applyTo(elementId, entry, values);
        }
    }

    _applyTo(elementId, entry, values) {
        const { el, elementDef } = entry;
        // Lazily snapshot the rendered base so an override can be reverted
        // when its state key is later deleted. Without this, a script/macro
        // that sets ui.<id>.* and then clears it can't visually revert the
        // element until a full page re-render (one-way-invariant violation).
        let drawn = this._drawn.get(entry);
        if (!drawn) {
            drawn = { base: snapshot(el, elementDef), applied: new Set() };
            this._drawn.set(entry, drawn);
        }
        const { base, applied } = drawn;

        // First, put back what a cleared override was covering.
        let cleared = false;
        OVERRIDES.forEach((o, i) => {
            if (isSet(values[i]) || !applied.has(o.name)) return;
            o.putBack(el, base, this, elementDef);
            applied.delete(o.name);
            cleared = true;
        });
        // The snapshot is the element as first drawn, and a look or a
        // toggle may have drawn something else since: a Blank All button
        // lit orange, coloured by a script, then uncoloured, went back to
        // the grey it had before the screens were blanked. So the
        // element's own bindings draw again from current state, over the
        // snapshot, and what shows is what it would show had the override
        // never been set. The snapshot stays the answer only for what no
        // binding draws.
        if (cleared) this._host.redraw(elementId);

        // Then every override still set, over whatever was drawn.
        OVERRIDES.forEach((o, i) => {
            if (isSet(values[i]) && o.draw(el, values[i], base, this)) applied.add(o.name);
        });
    }

    /**
     * Write a control's words for an override, unless an edit holds them.
     *
     * Beside an icon the words go in its span, so the icon stays on the side
     * it was drawn. A rich-text label's bold and italic runs are words too,
     * so they go with the rest, and `rich` draws the text as rich text again.
     */
    writeWords(el, node, { text, path, rich = false }) {
        if (this._host.holds(el)) return;
        const span = labelSpan(node);
        if (span) {
            this._host.setLabelText(span, text, path);
            if (node.tagName === 'BUTTON') nameAfterWords(node, text);
            return;
        }
        node.querySelectorAll(':scope > strong, :scope > em').forEach((n) => n.remove());
        this._host.setLabelText(node, rich ? '' : text, path);
        if (rich) node.insertAdjacentHTML('beforeend', richText(text));
    }
}
