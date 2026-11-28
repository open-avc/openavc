/**
 * The theme a panel draws with, and the Theme Studio's control picker.
 *
 * A definition names a theme (`theme_id`, or with none the theme the project
 * schema gives its light / dark setting: `themeIdOf`, modules/project_values.js);
 * `PanelTheme` fetches it -- or takes the working copy the Theme Studio hands
 * over, which applies within a frame -- and puts its variables on the
 * document as `--panel-*` custom properties, with the project settings the
 * theme rule's table names painted over them (`theme_overrides`, then
 * `accent_color` and `font_family`). A theme goes on whole: a variable it
 * leaves out is taken off, so the stylesheet's own draws, and a theme that
 * cannot be had goes on the same way as one with no variables (the light
 * built-in's for a project set to light), the settings still over it. What it
 * keeps afterwards is what every renderer draws from: the element defaults
 * merged under a control's own style (`styleFor`), and the variables actually
 * applied, which a page with no background of its own resolves the theme's
 * `page_defaults` against (`paintBackground`).
 *
 * Themes asked for one after another may answer in any order: an answer goes
 * on only while its theme is still the one asked for last, and was asked for
 * since that theme last changed on the server (`takeChange`,
 * `takeSavedCount`).
 *
 * The rule worth knowing before changing anything here: element defaults are
 * merged into what each renderer draws, so a theme that moves them only shows
 * on a redraw, and `apply` asks the panel for one (`repaint`). Whether a page
 * is drawn again is the panel's to decide, at its one door into a page draw
 * (`renderCurrentPage`): a page already up is, a theme put on by the draw in
 * progress is that draw. A redraw is itself a draw that applies the theme, so
 * `apply` does nothing while its own redraw runs. And the first page waits for
 * the theme (`drawWhenReady`), for at most FIRST_THEME_WAIT_MS, rather than
 * drawing everything twice.
 *
 * The panel lends what this asks and reads (`draw`, `repaint` and the
 * definition's settings), each at call time, because tests replace its
 * `renderCurrentPage`.
 */

import { assetUrl, basePath } from './base_path.js';
import { drawFadedPicture, gradientCss } from './paint.js';
import { chosenWord, themeIdOf } from './project_values.js';
import { THEME_VALUES } from './theme_values.gen.js';

// How long the first draw waits for the theme before going ahead without it.
// The fetch is same-origin and answers in milliseconds; this only exists so a
// server that accepts the request and never answers cannot leave a panel with
// nothing on it. Past this the page draws unthemed -- what it used to draw
// first anyway -- and repaints when the theme finally lands.
export const FIRST_THEME_WAIT_MS = 1000;

/** A map of named values (a theme, its variables, an override map), or null for anything else. */
function namedValues(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

/** A theme from the server, or null when there is none to be had. */
function fetchTheme(themeId) {
    return fetch(`${basePath()}/api/themes/${encodeURIComponent(themeId)}`)
        .then(res => {
            if (!res.ok) return null;
            return res.json().catch(() => null);
        })
        .then(namedValues)
        .catch(() => null);
}

/**
 * What a definition draws when its theme cannot be had: no variables, so the
 * stylesheet's own (each variable's fallback in the theme rule's table), or
 * the light built-in's for a project set to light.
 */
function fallbackTheme(settings) {
    const variables = settings.theme === 'light' ? THEME_VALUES.light_fallback : {};
    return { id: null, variables, element_defaults: {} };
}

/** A value that leaves the theme's own in place: none, or empty text. */
function leavesTheTheme(value) {
    return value == null || value === '';
}

/**
 * The variables a theme draws with: its own, with the project's settings over
 * them in the order the theme rule's table gives (`theme_overrides` naming its
 * own variables, the others one each). A setting or override left blank
 * leaves the theme's value.
 */
function variablesOver(theme, settings) {
    const vars = { ...namedValues(theme.variables) };
    for (const { setting, variable } of THEME_VALUES.settings_over_theme) {
        const given = variable === null ? namedValues(settings[setting]) : { [variable]: settings[setting] };
        for (const [name, value] of Object.entries(given || {})) {
            if (!leavesTheTheme(value)) vars[name] = value;
        }
    }
    return vars;
}

/**
 * A variable's value as CSS: a number is a measurement, and every stored
 * measurement is rem -- the unit the stylesheets and the migration speak.
 * None is empty, which takes the value off.
 */
function cssValue(value) {
    if (leavesTheTheme(value)) return '';
    return typeof value === 'number' ? `${value}rem` : String(value);
}

/**
 * Put the variables on the document: each one the theme rule's table names as
 * its custom property, and the font on the body. One the variables leave out
 * is taken off, so the stylesheet's own draws rather than the last theme's.
 * Hover and the active button's colour derive from these in CSS; a few are
 * used by no rule in panel-elements.css and are there for theme authors and
 * user CSS, through var(--panel-*).
 */
function putOnDocument(vars) {
    const root = document.documentElement;
    for (const { name, css_property: property } of THEME_VALUES.variables) {
        const value = cssValue(vars[name]);
        if (!property) document.body.style.fontFamily = value;
        else if (value) root.style.setProperty(property, value);
        else root.style.removeProperty(property);
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

/**
 * A page picture's size as CSS: the schema's word for it (one this version
 * does not know draws as the one the table names). Stretch fills the page, as
 * a button's picture does (paint.js): the word itself is no size a browser
 * draws.
 */
export function pagePictureSize(bg) {
    const size = chosenWord('PageBackground', 'image_size', bg.image_size);
    return size === 'stretch' ? '100% 100%' : size;
}

/** Draw a page.background-shaped object onto a page surface. */
function drawBackground(surface, bg) {
    surface.style.position = 'relative';
    // Solid color
    if (bg.color) {
        surface.style.backgroundColor = bg.color;
    }
    // Background image with opacity; a reference that names no picture draws none.
    const imageUrl = assetUrl(bg.image);
    if (imageUrl) {
        const imgLayer = backgroundLayer('panel-page-bg-image');
        drawFadedPicture(imgLayer, {
            url: imageUrl,
            size: pagePictureSize(bg),
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
        // The applied theme's page_defaults, which a page with no background
        // of its own takes (paintBackground).
        this.pageDefaults = null;
        // The theme in hand, used again while the definition names it.
        this.current = null;
        // The last theme asked for and the settings over it (apply): an
        // answer for any other theme is for nobody.
        this._asked = { themeId: null, settings: {} };
        // How many times a saved theme has changed under the panel: an answer
        // asked for before the last change is the file as it was.
        this._changes = 0;
        // The Theme Studio's working copy (takeWorkingCopy).
        this.inline = null;
        // The Builder's count of saved-theme changes last seen (takeSavedCount).
        this._savedCount = undefined;
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

    /**
     * The Builder's count of saved-theme changes, on an editor message
     * (`themesVersion`): when it moves, the theme in hand is let go, so the
     * draw the message brings fetches it again. The Builder's canvas has no
     * socket of its own to hear `themes.changed` on (takeChange). The
     * working copy stays.
     */
    takeSavedCount(msg) {
        if (!Object.prototype.hasOwnProperty.call(msg, 'themesVersion')) return;
        if (this._savedCount !== undefined && msg.themesVersion !== this._savedCount) this._letGo();
        this._savedCount = msg.themesVersion;
    }

    /** Put a theme on the page, and redraw it if that changed how it looks.
     *
     *  Returns a promise that settles once a theme (or the fallback) has been
     *  applied, so the first draw can wait for it rather than drawing the whole
     *  page twice. It never rejects. `repaint: false` is for exactly that
     *  caller: it is about to draw the page itself, so there is nothing yet to
     *  repaint.
     *
     *  Themes are asked for one after another and answer in any order: an
     *  answer goes on only while the theme it is for is still the one asked
     *  for last, and was asked for since that theme last changed, with the
     *  settings asked for last over it.
     */
    apply(settings, { repaint = true } = {}) {
        if (this._applying) return Promise.resolve();
        const themeId = themeIdOf(settings);
        this._asked = { themeId, settings };

        // The Theme Studio's working copy, applied synchronously without
        // hitting the network so picker drags reflect within a frame instead
        // of after a round-trip; else the theme already in hand.
        const held = this._held(themeId);
        if (held) {
            this.current = held;
            this._putOn(held, settings, repaint);
            return Promise.resolve();
        }

        const changes = this._changes;
        return fetchTheme(themeId)
            .then(theme => {
                if (changes !== this._changes || this._asked.themeId !== themeId) return;
                if (theme) this.current = theme;
                const asked = this._asked.settings;
                this._putOn(theme || fallbackTheme(asked), asked, repaint);
            })
            .catch(err => console.warn('[panel] the page could not be drawn again with its theme:', err));
    }

    /** Draw the page for a definition that has just arrived: now, or the first time, once the theme is here. */
    drawWhenReady() {
        // The first definition is the cold start, and the theme that decides
        // what every element looks like is still one fetch away -- so wait for
        // it here rather than draw the whole page and draw it again when it
        // lands. Only here: the panel's renderCurrentPage() itself still draws
        // when called, which is what every other caller (and every test) expects.
        if (this.firstSettled) {
            this._host.draw();
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
        if (setsNothing(bg)) bg = this._pageBackground();
        if (!bg) return;
        drawBackground(surface, bg);
    }

    /**
     * One of the project's saved themes changed on the server (saved,
     * imported or deleted, or a whole project replaced: `themes.changed`,
     * `themeId` naming it, or none when any may have). The theme in hand is
     * used again while the definition names it, so the page would keep its
     * old look, or the fallback when the theme was missing, until it was
     * reloaded: when the theme the definition names changed, it is fetched
     * again and the page redrawn in place on it. The Theme Studio's working
     * copy stays. Nothing happens before the first draw, which fetches the
     * theme itself. Not for the Builder's canvas or Preview, which take the
     * theme from the Builder.
     */
    takeChange(themeId) {
        if (!this.firstSettled) return;
        const settings = this._host.settings() || {};
        if (themeId != null && themeId !== themeIdOf(settings)) return;
        this._letGo();
        this.apply(settings, { repaint: false }).then(() => this._host.draw());
    }

    /** A saved theme changed: the theme in hand goes, and so does every answer already on its way. */
    _letGo() {
        this.current = null;
        this._changes += 1;
    }

    /** The theme in hand for this id: the Theme Studio's working copy first, else the one applied last. */
    _held(themeId) {
        if (this.inline && this.inline.id === themeId) return this.inline;
        if (this.current && this.current.id === themeId) return this.current;
        return null;
    }

    /** The page background a page with none of its own draws: the applied theme's page_defaults. */
    _pageBackground() {
        return backgroundFromDefaults(this.pageDefaults, this.variables);
    }

    /**
     * What a theme changes on a page only when the page is drawn: its
     * element defaults, and the background a page with none of its own takes
     * (resolved here, not by CSS). Every variable reaches the page at once.
     */
    _look() {
        return JSON.stringify([this.elementDefaults, this._pageBackground()]);
    }

    /** Put a theme on, and ask for the page again when that moved how it looks. */
    _putOn(theme, settings, repaint) {
        const before = this._look();
        this._put(theme, settings);
        if (repaint && before !== this._look()) this._redraw();
    }

    /** The page again for a theme just put on; a redraw that fails leaves the next theme free to go on. */
    _redraw() {
        this._applying = true;
        try {
            this._host.repaint();
        } finally {
            this._applying = false;
        }
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
            this._host.draw();
        };
        const giveUpWaiting = setTimeout(drawFirst, FIRST_THEME_WAIT_MS);
        this.apply(this._host.settings() || {}, { repaint: false }).then(() => {
            clearTimeout(giveUpWaiting);
            // Already drew without it: this is now an ordinary theme change to
            // a page that is up, which is the repaint apply normally does.
            if (this.firstSettled) this._host.repaint();
            else drawFirst();
        });
    }

    /**
     * Put one theme on whole, with the project's settings over its variables,
     * and keep what it makes. The same for a theme fetched, one handed over
     * and the fallback for one that cannot be had.
     */
    _put(theme, settings) {
        // The variables every element ends up drawing from: the theme's own
        // with the project's settings over them. Elements get these for free
        // because they read the --panel-* custom properties set here;
        // page_defaults do not, because their `var(name)` references are
        // resolved in JS. Resolving those against theme.variables painted the
        // page in the theme's colour while every element on it honoured the
        // override.
        this.variables = variablesOver(theme, settings);
        putOnDocument(this.variables);
        this.elementDefaults = namedValues(theme.element_defaults) || {};
        this.pageDefaults = namedValues(theme.page_defaults);
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
 * per drawn page surface, each with its own name tag in the document, which
 * `remove` takes away when that surface ends.
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

    /** The name tag goes with the canvas draw that made it (the panel calls this when its surface ends). */
    remove() {
        this._tag.remove();
    }
}
