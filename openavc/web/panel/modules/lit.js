/**
 * The look a control wears while it is lit: a toggle that is on, a page nav
 * whose page is showing. The accent, filled, with a text colour picked to
 * read on it.
 *
 * The accent rather than a colour of our own, so it follows the theme and
 * the panel's own accent setting, and a control whose style names its own
 * accent lights in that. Deliberately no border colour: the unlit pass puts
 * the control's own style back, and modules/paint.js `clearStateColours`
 * drops only what a state may write (background, text colour, opacity), so a
 * property added here has to be taught there too, or a control ends up
 * wearing half of a state it is no longer in.
 *
 * No control's definition is read here: the caller hands over the control's
 * own style.
 */

/** Relative luminance of the dark text colour, #111111. */
const DARK = 0.00518;

/** The lit look over a control whose own style is `ownStyle`. */
export function litStyle(ownStyle) {
    const accent = ownStyle?.accent_color
        // Read from the inline custom property the theme sets rather than
        // through getComputedStyle: this runs in the binding pass, and a
        // computed read there costs a style flush on every state batch.
        // The fallback is the same colour the :root rule in
        // panel-elements.css carries, so a panel that never loaded a theme
        // lights in the accent it is already drawn with.
        || document.documentElement.style.getPropertyValue('--panel-accent').trim()
        || '#2196F3';
    const style = { bg_color: accent };
    const text = readableTextOn(accent);
    if (text) style.text_color = text;
    return style;
}

/**
 * Near-black or white, whichever reads on this background, picked by WCAG
 * contrast rather than a lightness guess, because the built-in accents run
 * from #1976D2 to #00E676 and one answer cannot serve both.
 *
 * Null when the colour cannot be read, and the control then keeps its own
 * text colour: hex and rgb()/rgba() are what every theme variable and every
 * colour picker in the IDE write, and resolving a named colour or hsl()
 * would mean asking the browser through a canvas, which the binding pass
 * cannot afford.
 */
export function readableTextOn(color) {
    const rgb = rgbChannels(color);
    if (!rgb) return null;
    const lum = relativeLuminance(rgb);
    const onWhite = 1.05 / (lum + 0.05);
    const onDark = (lum + 0.05) / (DARK + 0.05);
    return onDark >= onWhite ? '#111111' : '#ffffff';
}

/** [r, g, b] 0-255 from a hex or rgb()/rgba() colour, or null. */
export function rgbChannels(color) {
    if (typeof color !== 'string') return null;
    const v = color.trim().toLowerCase();
    const hex = v.match(/^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/);
    if (hex) return hexChannels(hex[1]);
    const rgb = v.match(/^rgba?\(([^)]+)\)$/);
    return rgb ? functionChannels(rgb[1]) : null;
}

/** The red, green and blue of a hex colour's digits, short or long form. */
function hexChannels(h) {
    const wide = h.length > 4;
    const pair = (i) => wide ? h.slice(i * 2, i * 2 + 2) : h[i] + h[i];
    return [0, 1, 2].map(i => parseInt(pair(i), 16));
}

/** The red, green and blue inside rgb()/rgba(), numbers or percentages, or null. */
function functionChannels(inside) {
    const parts = inside.split(/[,/\s]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const out = parts.slice(0, 3).map(channel);
    return out.every(c => c !== null) ? out : null;
}

/** One channel, 0-255, from a number or a percentage; null when it is not a number. */
function channel(s) {
    const n = s.endsWith('%') ? (parseFloat(s) / 100) * 255 : parseFloat(s);
    return isNaN(n) ? null : Math.max(0, Math.min(255, Math.round(n)));
}

/** WCAG relative luminance of [r, g, b]. */
export function relativeLuminance([r, g, b]) {
    const lin = [r, g, b].map(c => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}
