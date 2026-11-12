/**
 * The project's own CSS: a control's `css_class`, and the project stylesheet
 * (`ui.custom_css`) that styles it.
 *
 * `applyCssClass` puts a control's classes on its node. `ProjectStylesheet`
 * keeps the stylesheet in one style node, appended to the head so it comes
 * after panel.css and panel-elements.css, rewritten only when its text
 * changes, and every declaration in it raised to !important (`raisePriority`).
 *
 * The rule worth knowing before changing anything here: applyStyle writes a
 * control's colours, corner radius, borders, shadows and text sizes inline --
 * from the THEME as much as from anything the author set on that one element
 * -- and an inline style beats a stylesheet rule that does not say
 * !important. So the plain, obvious rule somebody writes first
 * ('.brand-button { background: #8AB493 }') did nothing at all, silently,
 * which is the worst possible introduction to a feature. Making them type
 * !important was never protecting anything: the properties it applies to are
 * exactly the properties they are trying to change. So the panel adds it. The
 * project keeps the author's text verbatim -- the sheet is theirs, raising its
 * priority is ours -- and the parsing is the browser's own, through the CSSOM,
 * rather than a regex over CSS that would be wrong about comments, strings and
 * nesting within the week.
 *
 * What this deliberately does NOT solve is a control that changes its own
 * look to show state -- a button that goes green when the projector is on
 * writes that colour inline at runtime, and now a class rule for the same
 * property outranks it, so the button stops reporting. That conflict exists
 * either way (an author who typed !important got the same result), so it is
 * answered where it can be answered clearly: the Builder warns when a class
 * on an element sets a property that element's own feedback also sets.
 */

const STYLE_NODE_ID = 'panel-custom-css';

/** A control's `css_class` on its node, token by token. */
export function applyCssClass(el, element) {
    const raw = element?.css_class;
    if (!el || typeof raw !== 'string') return;
    // Space-separated, like the class attribute it becomes. Tokens are
    // filtered rather than handed straight to classList, which throws on an
    // empty or whitespace-bearing name and would take the whole page down
    // over one stray space in a hand-written field.
    for (const name of raw.split(/\s+/)) {
        if (!name) continue;
        try {
            el.classList.add(name);
        } catch {
            console.warn(`[panel] ignoring invalid css_class '${name}' on element '${element?.id}'`);
        }
    }
}

/**
 * The project stylesheet's one style node, reused, and only rewritten when
 * the text actually changes: this runs on every page render, and reassigning
 * textContent re-parses the sheet and forces a restyle even when nothing
 * differs. The priority pass only has to run when the text is new, because
 * CSSOM edits live on the parsed sheet and survive until it is re-parsed.
 */
export class ProjectStylesheet {
    constructor() {
        this._node = null;
    }

    apply(css) {
        const text = typeof css === 'string' ? css : '';
        let node = this._node;
        if (!node || !node.isConnected) {
            node = document.getElementById(STYLE_NODE_ID);
        }
        if (!text) {
            if (node) node.remove();
            this._node = null;
            return;
        }
        if (!node) {
            node = document.createElement('style');
            node.id = STYLE_NODE_ID;
            document.head.appendChild(node);
        }
        this._node = node;
        if (node.textContent !== text) {
            node.textContent = text;
            raisePriority(node);
        }
    }
}

// CSSRule.KEYFRAMES_RULE, with the literal as a fallback for anything that
// doesn't expose the constant.
const keyframesRule = () => (window.CSSRule && window.CSSRule.KEYFRAMES_RULE) || 7;

/**
 * Give the project stylesheet the last word: every declaration in it re-set
 * as !important, inside @media / @supports and friends too.
 *
 * The one thing it must not touch is @keyframes: !important inside a
 * keyframe is ignored by the spec, and writing it there can drop the
 * declaration outright, so an animation would break rather than win.
 */
function raisePriority(node) {
    const sheet = node.sheet;
    if (!sheet) return;
    try {
        raiseRules(sheet.cssRules, keyframesRule());
    } catch {
        // A sheet the browser won't let us read is a sheet we leave alone;
        // the author's CSS still applies, just at its normal priority.
    }
}

function raiseRules(rules, keyframes) {
    for (const rule of rules) {
        if (rule.type === keyframes) continue;
        if (rule.style) raiseDeclarations(rule.style);
        // @media / @supports and friends hold rules of their own.
        if (rule.cssRules) raiseRules(rule.cssRules, keyframes);
    }
}

/**
 * Re-set one rule's declarations as important.
 *
 * This works on the declaration TEXT rather than property by property, and
 * that is not a style preference -- it is the only thing that survives a
 * custom property inside a shorthand. `background: var(--brand)` cannot be
 * expanded into longhands until the variable is substituted, so the browser
 * lists background-color and friends but hands back an empty string for
 * every one of them. Setting each of those "values" doesn't raise the
 * declaration, it DELETES it: the first version of this shipped a worked
 * example whose green never appeared, and the rule in the sheet had simply
 * lost its background line.
 *
 * If the rebuilt text doesn't parse back to the same number of
 * declarations, the original goes back untouched. A stylesheet at normal
 * priority is a disappointment; a stylesheet with rules silently missing is
 * a bug hunt.
 */
function raiseDeclarations(decls) {
    const original = decls.cssText;
    if (!original) return;
    const parts = splitDeclarations(original);
    if (parts.length === 0) return;
    const raised = parts
        .map(d => (/!\s*important\s*$/i.test(d) ? d : `${d} !important`))
        .join('; ') + ';';
    decls.cssText = raised;
    if (splitDeclarations(decls.cssText).length !== parts.length) {
        decls.cssText = original;
    }
}

/**
 * Split a declaration list on the semicolons that actually separate it.
 *
 * The text comes from the browser's own serializer, so it is already
 * normalized -- no comments, no surprises -- and splitting it needs to
 * respect only two things: a `;` inside a quoted string, and a `;` inside
 * parentheses (a data: URI carries one).
 */
function splitDeclarations(text) {
    return new DeclarationReader().read(text);
}

/** Reads a declaration list a character at a time, knowing when it is inside a string or parentheses. */
class DeclarationReader {
    constructor() {
        this.parts = [];
        this.buffer = '';
        this.quote = null;
        this.depth = 0;
    }

    read(text) {
        for (let i = 0; i < text.length; i++) {
            const ch = text[i];
            if (ch === '\\') {
                // An escape carries its next character verbatim, quote included.
                this.buffer += ch + (text[i + 1] || '');
                i++;
            } else {
                this._take(ch);
            }
        }
        this.parts.push(this.buffer);
        return this.parts.map(p => p.trim()).filter(Boolean);
    }

    _take(ch) {
        if (this.quote) {
            this.buffer += ch;
            if (ch === this.quote) this.quote = null;
        } else if (ch === ';' && this.depth === 0) {
            this.parts.push(this.buffer);
            this.buffer = '';
        } else {
            this.buffer += ch;
            this._track(ch);
        }
    }

    /** A character outside a string that opens one, or opens or closes parentheses. */
    _track(ch) {
        if (ch === '"' || ch === "'") this.quote = ch;
        else if (ch === '(') this.depth++;
        else if (ch === ')') this.depth = Math.max(0, this.depth - 1);
    }
}
