/**
 * The theme a panel draws with, and the Theme Studio's control picker.
 *
 * A definition names a theme (`theme_id`, or the old light / dark setting);
 * `PanelTheme` fetches it -- or takes the working copy the Theme Studio hands
 * over, which applies within a frame -- and puts its variables on the
 * document as `--panel-*` custom properties, overlaid by the project's
 * `theme_overrides` and then the `accent_color` and `font_family` settings.
 * What it keeps afterwards is what every renderer draws from: the element
 * defaults merged under a control's own style (`styleFor`), and the variables
 * actually applied, which a page with no background of its own resolves the
 * theme's `page_defaults` against (`paintBackground`). A theme that cannot be
 * had falls back to a plain light or dark look.
 *
 * The rule worth knowing before changing anything here: element defaults are
 * merged into what each renderer draws, so a theme that moves them only shows
 * on a redraw, and `apply` asks for one -- through the panel, at call time,
 * because tests replace its `renderCurrentPage`. A redraw is itself a draw that
 * applies the theme, so `apply` does nothing while its own redraw runs. And
 * the first page waits for the theme (`drawWhenReady`), for at most
 * FIRST_THEME_WAIT_MS, rather than drawing everything twice.
 *
 * The panel lends what this reads (`redraw`, `stateArrived` -- whether the
 * first state snapshot is in, which is what a repaint waits for, not whether a
 * page is up -- and the definition's settings), each at call time.
 */

import { assetUrl, basePath } from './base_path.js';
import { drawFadedPicture, gradientCss } from './paint.js';

// How long the first draw waits for the theme before going ahead without it.
// The fetch is same-origin and answers in milliseconds; this only exists so a
// server that accepts the request and never answers cannot leave a panel with
// nothing on it. Past this the page draws unthemed -- what it used to draw
// first anyway -- and repaints when the theme finally lands.
export const FIRST_THEME_WAIT_MS = 1000;

// Theme variables and the custom properties they become. The last four
// (accent_hover, button_border, surface, surface_border) aren't consumed by
// any rule in panel-elements.css today, but are exposed so theme authors and
// user CSS can reference them via var(--panel-*). Hover derives from
// accent/button via CSS filter (no *_hover token). Active button bg derives
// from --panel-accent in CSS (no separate token).
const CSS_VARIABLES = {
    panel_bg: '--panel-bg',
    panel_text: '--panel-text',
    accent: '--panel-accent',
    button_bg: '--panel-button-bg',
    button_text: '--panel-button-text',
    button_border: '--panel-button-border',
    danger: '--panel-danger',
    success: '--panel-success',
    warning: '--panel-warning',
    surface: '--panel-surface',
    surface_border: '--panel-surface-border',
    border_radius: '--panel-border-radius',
};

/** The theme a definition's settings name: its id, else light or dark by the old setting. */
function themeIdOf(settings) {
    return settings.theme_id || (settings.theme === 'light' ? 'light-modern' : 'dark-default');
}

/** A theme from the server, or null when there is none to be had. */
function fetchTheme(themeId) {
    return fetch(`${basePath()}/api/themes/${encodeURIComponent(themeId)}`)
        .then(res => {
            if (!res.ok) return null;
            return res.json().catch(() => null);
        })
        .catch(() => null);
}

/** Set the theme's variables on the document, and its font on the body. */
function setDocumentVariables(vars) {
    const root = document.documentElement;
    for (const [key, cssVar] of Object.entries(CSS_VARIABLES)) {
        if (vars[key] != null) {
            // Numeric theme variables are measurements, and every stored
            // measurement is rem now -- the same unit the stylesheets and
            // the migration speak.
            const val = typeof vars[key] === 'number' ? vars[key] + 'rem' : vars[key];
            root.style.setProperty(cssVar, val);
        }
    }
    if (vars.font_family) {
        document.body.style.fontFamily = vars.font_family;
    }
}

/**
 * The two per-setting overrides, which take priority over the theme's
 * variables: on the document, and in `vars` so a page default reads them too.
 */
function applySettings(vars, settings) {
    if (settings.accent_color) {
        document.documentElement.style.setProperty('--panel-accent', settings.accent_color);
        vars.accent = settings.accent_color;
    }
    if (settings.font_family) {
        document.body.style.fontFamily = settings.font_family;
        vars.font_family = settings.font_family;
    }
}

/**
 * Resolve a theme `"var(name)"` reference to its underlying variable value.
 * Returns the original value unchanged if it isn't a var() string, and null
 * for a reference to a variable that is not there.
 */
function resolveVar(value, variables) {
    if (typeof value !== 'string') return value;
    const match = value.match(/^var\(([^)]+)\)$/);
    if (!match) return value;
    const v = variables?.[match[1].trim()];
    return v != null ? v : null;
}

/** The picture half of a theme's page defaults, onto `bg`. */
function addDefaultPicture(bg, defaults, variables) {
    const image = resolveVar(defaults.background_image, variables);
    if (!image) return;
    bg.image = image;
    if (defaults.background_image_size) bg.image_size = defaults.background_image_size;
    if (defaults.background_image_position) bg.image_position = defaults.background_image_position;
    if (defaults.background_image_opacity != null) bg.image_opacity = defaults.background_image_opacity;
}

/** The gradient half of a theme's page defaults, onto `bg`: both ends, or none. */
function addDefaultGradient(bg, gradient, variables) {
    if (!gradient || typeof gradient !== 'object') return;
    const from = resolveVar(gradient.from, variables);
    const to = resolveVar(gradient.to, variables);
    if (from && to) {
        bg.gradient = { from, to, angle: gradient.angle };
    }
}

/**
 * Convert theme.page_defaults (with background_color/_image/_gradient keys
 * and `var(name)` references) into a page.background-shaped object, or null
 * when nothing in it resolves.
 */
function backgroundFromDefaults(defaults, variables) {
    if (!defaults) return null;
    const bg = {};
    const color = resolveVar(defaults.background_color, variables);
    if (color) bg.color = color;
    addDefaultPicture(bg, defaults, variables);
    addDefaultGradient(bg, defaults.background_gradient, variables);
    return Object.keys(bg).length ? bg : null;
}

/** A layer filling the page surface behind its controls. */
function backgroundLayer(className) {
    const layer = document.createElement('div');
    layer.className = className;
    return layer;
}

/** Draw a page.background-shaped object onto a page surface. */
function drawBackground(surface, bg) {
    surface.style.position = 'relative';
    // Solid color
    if (bg.color) {
        surface.style.backgroundColor = bg.color;
    }
    // Background image with opacity
    if (bg.image) {
        const imgLayer = backgroundLayer('panel-page-bg-image');
        drawFadedPicture(imgLayer, {
            url: assetUrl(bg.image),
            size: bg.image_size || 'cover',
            position: bg.image_position || 'center',
            opacity: bg.image_opacity ?? 1,
        });
        surface.prepend(imgLayer);
    }
    // Gradient overlay (renders on TOP of image)
    const gradient = gradientCss(bg.gradient);
    if (gradient) {
        const gradLayer = backgroundLayer('panel-page-bg-gradient');
        gradLayer.style.position = 'absolute';
        gradLayer.style.inset = '0';
        gradLayer.style.zIndex = '1';
        gradLayer.style.pointerEvents = 'none';
        gradLayer.style.background = gradient;
        surface.prepend(gradLayer);
    }
}

/** A page background that sets nothing of its own. */
function setsNothing(bg) {
    return !bg || (!bg.color && !bg.image && !bg.gradient);
}

export class PanelTheme {
    constructor(host) {
        this._host = host;
        // What every renderer merges under a control's own style, by type.
        this.elementDefaults = {};
        // The variables the applied theme is actually drawing from, which is
        // not theme.variables: see _put.
        this.variables = {};
        // The theme in hand, used again while the definition names it.
        this.current = null;
        // The Theme Studio's working copy (takeWorkingCopy).
        this.inline = null;
        // Set while apply's own redraw runs.
        this._applying = false;
        // The first draw has happened, or stopped waiting: see drawWhenReady.
        this.firstSettled = false;
        this._awaitingFirst = false;
    }

    /** The working copy an editor message carries: kept when it is one, dropped when the message names none. */
    takeWorkingCopy(msg) {
        // The Theme Studio sends a live working-copy theme so edits apply
        // within a frame (no fetch). When omitted, fall back to the normal
        // /api/themes/<id> fetch path.
        if (msg.inlineTheme && typeof msg.inlineTheme === 'object') {
            this.inline = msg.inlineTheme;
        } else if (Object.prototype.hasOwnProperty.call(msg, 'inlineTheme')) {
            // Explicitly null/undefined cleared from parent — drop any prior inline theme
            this.inline = null;
        }
    }

    /** Put a theme on the page, and redraw it if that changed how elements look.
     *
     *  Returns a promise that settles once a theme (or the fallback) has been
     *  applied, so the first draw can wait for it rather than drawing the whole
     *  page twice. `repaint: false` is for exactly that caller: it is about to
     *  draw the page itself, so there is nothing yet to repaint.
     */
    apply(settings, { repaint = true } = {}) {
        const themeId = themeIdOf(settings);
        const overrides = settings.theme_overrides || {};

        if (this._applying) return Promise.resolve();

        const before = JSON.stringify(this.elementDefaults || {});
        const repaintIfLookChanged = () => this._repaintIfChanged(before, repaint);

        // The Theme Studio's working copy, applied synchronously without
        // hitting the network so picker drags reflect within a frame instead
        // of after a round-trip; else the theme already in hand.
        const held = this._held(themeId);
        if (held) {
            this._put(held, overrides, settings);
            this.current = held;
            repaintIfLookChanged();
            return Promise.resolve();
        }

        return fetchTheme(themeId)
            .then(theme => {
                if (theme) {
                    this._put(theme, overrides, settings);
                    this.current = theme;
                } else {
                    this._fallBack(settings);
                }
                repaintIfLookChanged();
            })
            .catch(() => this._fallBack(settings));
    }

    /** Draw the page for a definition that has just arrived: now, or the first time, once the theme is here. */
    drawWhenReady() {
        // The first definition is the cold start, and the theme that decides
        // what every element looks like is still one fetch away -- so wait for
        // it here rather than draw the whole page and draw it again when it
        // lands. Only here: the panel's renderCurrentPage() itself still draws
        // when called, which is what every other caller (and every test) expects.
        if (this.firstSettled) {
            this._host.redraw();
        } else if (!this._awaitingFirst) {
            this._awaitingFirst = true;
            this._drawWhenThemeArrives();
        }
    }

    /** A control's style with its type's theme defaults under it. */
    styleFor(elementType, elementStyle) {
        const defaults = this.elementDefaults[elementType] || {};
        return { ...defaults, ...elementStyle };
    }

    /**
     * Draw a page's background onto its surface: its own, or -- when it sets
     * none -- the theme's page_defaults, so a page that does not opt out looks
     * like the active theme.
     */
    paintBackground(surface, bg) {
        if (setsNothing(bg)) {
            bg = backgroundFromDefaults(this.current?.page_defaults, this.variables);
        }
        if (!bg) return;
        drawBackground(surface, bg);
    }

    /** The theme in hand for this id: the Theme Studio's working copy first, else the one applied last. */
    _held(themeId) {
        if (this.inline && this.inline.id === themeId) return this.inline;
        if (this.current && this.current.id === themeId) return this.current;
        return null;
    }

    /** Redraw the page when element defaults moved: they only take effect on a draw. */
    _repaintIfChanged(before, repaint) {
        if (!repaint) return;
        if (before === JSON.stringify(this.elementDefaults || {})) return;
        if (!this._host.stateArrived()) return;
        this._applying = true;
        this._host.redraw();
        this._applying = false;
    }

    /** Draw the first page once the theme is in hand -- but draw it regardless.
     *
     *  Everything an element looks like comes from a theme that is one fetch
     *  away, and `apply` returns before it lands. Drawing first and again
     *  when it arrives is what made a custom control cost two frame loads
     *  instead of one, and rebuilding every frame on a page is the most
     *  expensive thing the panel does.
     *
     *  A theme that never answers must not leave a blank panel, so the wait is
     *  capped: past it the page draws with no theme -- which is what the
     *  discarded first pass used to draw -- and repaints when the theme lands.
     */
    _drawWhenThemeArrives() {
        const drawFirst = () => {
            if (this.firstSettled) return;
            this.firstSettled = true;
            this._host.redraw();
        };
        const giveUpWaiting = setTimeout(drawFirst, FIRST_THEME_WAIT_MS);
        this.apply(this._host.settings() || {}, { repaint: false }).then(() => {
            clearTimeout(giveUpWaiting);
            // Already drew without it: this is now an ordinary theme change to
            // a page that is up, which is the repaint apply normally does.
            if (this.firstSettled) this._host.redraw();
            else drawFirst();
        });
    }

    /** Apply a theme's variables, the overrides and the two settings, and keep what they make. */
    _put(theme, overrides, settings) {
        const vars = { ...theme.variables, ...overrides };
        setDocumentVariables(vars);
        applySettings(vars, settings);
        // Store element defaults for use in rendering
        this.elementDefaults = theme.element_defaults || {};
        // ...and the variables every element ends up drawing from: the theme's
        // own, overlaid with the project's theme_overrides and then the two
        // per-setting overrides. Elements get these for free because they
        // read the --panel-* custom properties set here; page_defaults do not,
        // because their `var(name)` references are resolved in JS. Resolving
        // those against theme.variables painted the page in the theme's colour
        // while every element on it honoured the override.
        this.variables = vars;
    }

    /** No theme to be had: the two settings, and a basic light look when it asks for light. */
    _fallBack(settings) {
        const root = document.documentElement;
        if (settings.accent_color) {
            root.style.setProperty('--panel-accent', settings.accent_color);
        }
        if (settings.font_family) {
            document.body.style.fontFamily = settings.font_family;
        }
        // Basic light/dark fallback
        if (settings.theme === 'light') {
            root.style.setProperty('--panel-bg', '#f5f5f5');
            root.style.setProperty('--panel-text', '#212121');
            root.style.setProperty('--panel-button-bg', '#e0e0e0');
            root.style.setProperty('--panel-button-text', '#424242');
        }
        this.elementDefaults = {};
    }
}

// --- The Theme Studio's control picker ---------------------------------------

/**
 * The name the picker shows for each kind of control, which is the name of the
 * Theme Studio section its click opens (ELEMENT_SECTIONS in the Builder's
 * elementControls.ts; tests/test_theme_studio_names_mirror.py holds the two
 * equal). A kind with no section shows its type.
 */
export const THEME_STUDIO_NAMES = {
    button: 'Button', label: 'Label', slider: 'Slider', fader: 'Fader',
    select: 'Select', text_input: 'Text Input', status_led: 'Status LED',
    gauge: 'Gauge', level_meter: 'Level Meter', list: 'List', matrix: 'Matrix',
    group: 'Group', image: 'Image', clock: 'Clock',
    page_nav: 'Page Nav', camera_preset: 'Camera Preset', keypad: 'Keypad',
};

const PICKED_OUTLINE = '2px solid rgba(33,150,243,0.7)';

/**
 * Theme Studio direct manipulation on the design canvas: the control under the
 * pointer is outlined and named, and a click on one tells the Builder which
 * (`openavc:theme-element-click`, through `post`) instead of reaching it. One
 * per drawn page surface, each with its own name tag in the document.
 */
export class ThemeStudioPicker {
    constructor(surface, post) {
        this._surface = surface;
        this._post = post;
        this._hovered = null;
        this._tag = document.createElement('div');
        this._tag.style.cssText = [
            'position: fixed', 'padding: 2px 6px', 'font-size: 10px', 'font-weight: 600',
            'background: rgba(0,0,0,0.8)', 'color: #fff', 'border-radius: 3px',
            'pointer-events: none', 'z-index: 9999', 'display: none', 'white-space: nowrap',
        ].join(';');
        document.body.appendChild(this._tag);
        surface.addEventListener('mousemove', (e) => this._move(e));
        surface.addEventListener('mouseleave', () => this._leave());
        surface.addEventListener('click', (e) => this._click(e), true);
    }

    /** The placed control a node belongs to, or null for the page itself. */
    _controlOf(target) {
        let el = target;
        while (el && el !== this._surface) {
            if (el.dataset && el.dataset.elementType) return el;
            el = el.parentElement;
        }
        return null;
    }

    _move(e) {
        const control = this._controlOf(e.target);
        if (control === this._hovered) {
            if (control) this._placeTag(control);
            return;
        }
        this._unmark();
        this._hovered = control;
        if (control) {
            control.style.outline = PICKED_OUTLINE;
            control.style.outlineOffset = '-2px';
            const type = control.dataset.elementType;
            this._tag.textContent = THEME_STUDIO_NAMES[type] || type;
            this._tag.style.display = 'block';
            this._placeTag(control);
        } else {
            this._tag.style.display = 'none';
        }
    }

    _leave() {
        if (this._hovered) {
            this._unmark();
            this._hovered = null;
        }
        this._tag.style.display = 'none';
    }

    _click(e) {
        const control = this._controlOf(e.target);
        if (!control) return;
        e.preventDefault();
        e.stopPropagation();
        if (this._hovered) {
            this._unmark();
            this._hovered = null;
        }
        this._tag.style.display = 'none';
        this._post({
            type: 'openavc:theme-element-click',
            elementType: control.dataset.elementType,
            elementId: control.dataset.elementId,
        });
    }

    /** The name tag centred above the control. */
    _placeTag(control) {
        const r = control.getBoundingClientRect();
        this._tag.style.left = (r.left + r.width / 2 - this._tag.offsetWidth / 2) + 'px';
        this._tag.style.top = (r.top - 22) + 'px';
    }

    _unmark() {
        if (!this._hovered) return;
        this._hovered.style.outline = '';
        this._hovered.style.outlineOffset = '';
    }
}
