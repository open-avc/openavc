/**
 * How a style lands on a node: the inline CSS each style key becomes, the
 * picture and effect layers, frameless chrome, and what a state look drops
 * before it paints.
 *
 * Everything here takes a node and plain values. Which style a control is
 * painted with (its own, the theme's, a state's) is the panel's call; this
 * module never reads a control's definition, the panel's state or the theme.
 * The page review's renderer scan (tests/test_ui_page_review_mirrors.py) reads
 * this whole module as part of every control that hands it a node, so a
 * definition read here would be credited to every control type.
 *
 * One rule runs through it: background-image is shared. A gradient is a
 * background image, and so are a background picture and a button's picture.
 * Each picture is drawn over the gradient, never instead of it
 * (`drawOverGradient`), so the gradient a node was last painted with is kept
 * for the picture painted after it (`gradientOf`). Written once per writer, the
 * same composition broke twice, a few lines apart.
 *
 * A style key is written only when it has a value; what counts as a value is
 * per key, and zero is one for some keys and not others (`STYLE_WRITERS`).
 * Adding a style key the panel draws is one row there, in the order it is
 * written.
 */
import { assetUrl } from './base_path.js';

/** The gradient each node was last painted with, read by a picture drawn after it. */
const gradientOf = new WeakMap();

/**
 * Strip from a value interpolated into a CSS declaration (gradient stops,
 * background-size/position) everything that could break out of it and inject
 * another declaration or a url(): semicolons, braces, quotes, comments,
 * url()/image-set(), expression(), and the usual scheme tricks. Parentheses
 * and commas are kept so colour functions like rgb(0,0,0) still work.
 */
export function sanitizeCssValue(value) {
    if (typeof value !== 'string') return String(value ?? '');
    return value.replace(/expression\s*\(/gi, '')
        .replace(/javascript\s*:/gi, '')
        .replace(/behavior\s*:/gi, '')
        .replace(/@import/gi, '')
        .replace(/url\s*\(/gi, '')
        .replace(/image-set\s*\(/gi, '')
        .replace(/\/\*/g, '')
        .replace(/\*\//g, '')
        .replace(/[;{}"']/g, '')
        .replace(/\\/g, '')
        .replace(/[\r\n]/g, '');
}

/** URL schemes a picture may come from; no scheme (a relative URL) is allowed too. */
const PICTURE_SCHEMES = new Set(['http', 'https', 'data']);

/**
 * A URL made safe to put inside url("..."): an allowed scheme (http:, https:,
 * data:image/, or none), and the characters that could close the string or
 * the parenthesis percent-encoded. Structural URL characters (:/?&=%#) stay,
 * so real asset URLs keep working. Mapped by char code rather than with
 * encodeURIComponent, which leaves ( ) ' unescaped -- exactly the breakout
 * characters.
 */
export function sanitizeCssUrl(url) {
    if (typeof url !== 'string') return '';
    const trimmed = url.trim();
    const lower = trimmed.toLowerCase();
    if (lower.startsWith('javascript:') || lower.startsWith('vbscript:')) return '';
    if (lower.startsWith('data:') && !lower.startsWith('data:image/')) return '';
    const scheme = trimmed.match(/^([a-z][a-z0-9+.-]*):/i);
    if (scheme && !PICTURE_SCHEMES.has(scheme[1].toLowerCase())) return '';
    return trimmed.replace(/[\\"'()\s]/g,
        (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0'));
}

const hasBothStops = (gradient) => Boolean(gradient && gradient.from && gradient.to);

/**
 * A gradient as CSS, or null when it lacks either stop. An angle that is
 * missing or does not read as a non-zero number is 180 (top to bottom), so an
 * angle of 0 draws as 180. Shared by a control's style and a page's background.
 */
export function gradientCss(gradient) {
    if (!hasBothStops(gradient)) return null;
    const angle = parseFloat(gradient.angle != null ? gradient.angle : 180) || 180;
    return `linear-gradient(${angle}deg, `
        + `${sanitizeCssValue(gradient.from)}, ${sanitizeCssValue(gradient.to)})`;
}

/**
 * Draw a picture as the node's own background image, over the gradient the
 * node was painted with when it has one. The gradient layer always covers and
 * centres; the picture takes the size and position given.
 */
function drawOverGradient(node, imageCss, size, position) {
    const gradient = gradientOf.get(node) || null;
    node.style.backgroundImage = gradient ? `${imageCss}, ${gradient}` : imageCss;
    node.style.backgroundSize = gradient ? `${size}, cover` : size;
    node.style.backgroundPosition = gradient ? `${position}, center` : position;
    node.style.backgroundRepeat = 'no-repeat';
}

/**
 * Fill a layer with a faded picture: a background picture whose opacity is
 * below 1 on a control, and a page's own picture. An opacity that does not
 * read as a non-zero number draws as 1.
 */
export function drawFadedPicture(layer, { url, size, position, opacity }) {
    layer.style.position = 'absolute';
    layer.style.inset = '0';
    layer.style.zIndex = '0';
    layer.style.pointerEvents = 'none';
    layer.style.backgroundImage = `url("${sanitizeCssUrl(url)}")`;
    layer.style.backgroundSize = sanitizeCssValue(size);
    layer.style.backgroundPosition = sanitizeCssValue(position);
    layer.style.backgroundRepeat = 'no-repeat';
    layer.style.opacity = String(parseFloat(opacity) || 1);
}

/**
 * The faded background picture: a child layer, since CSS cannot fade a
 * background image alone. One per node, reused, because a state look paints
 * on every change and a new layer each time would stack them. The node is
 * made a positioning context only when nothing has placed it already, and the
 * content is lifted above the layer the same way: a child that is placed
 * already (a container's controls) paints above it as it is.
 */
function drawFadedBackground(node, picture) {
    if (!node.style.position) node.style.position = 'relative';
    let layer = node.querySelector(':scope > .panel-bg-image-layer');
    if (!layer) {
        layer = document.createElement('div');
        layer.className = 'panel-bg-image-layer';
        node.prepend(layer);
    }
    drawFadedPicture(layer, picture);
    for (const child of Array.from(node.children)) {
        if (child === layer || isPlaced(child)) continue;
        child.style.position = 'relative';
        child.style.zIndex = '1';
    }
}

/** A child that is stacked or positioned already, and paints above the layer as it is. */
const isPlaced = (child) => Boolean(child.style.zIndex || child.style.position);

/**
 * The background: a gradient, else a flat colour, then a background picture
 * over either. Written as the two longhands, never the `background`
 * shorthand: the shorthand would put the gradient where a picture goes and
 * reset the colour behind it. Both are decided on every call, so a state that
 * names a flat colour does not keep the previous state's gradient.
 */
function paintBackground(node, style) {
    const gradient = gradientCss(style.background_gradient);
    gradientOf.set(node, gradient);
    if (gradient) {
        node.style.backgroundImage = gradient;
        node.style.backgroundColor = style.bg_color || 'transparent';
    } else {
        node.style.backgroundImage = '';
        if (style.bg_color) node.style.backgroundColor = style.bg_color;
    }
    if (style.background_image) paintBackgroundPicture(node, style);
}

/** A background picture: over the gradient, or on a layer of its own when it is faded. */
function paintBackgroundPicture(node, style) {
    const size = style.background_size || 'cover';
    const picture = {
        url: assetUrl(style.background_image),
        size: size === 'stretch' ? '100% 100%' : size,
        position: style.background_position || 'center',
        opacity: style.background_opacity != null ? style.background_opacity : 1,
    };
    if (picture.opacity < 1) {
        drawFadedBackground(node, picture);
        return;
    }
    drawOverGradient(node, `url("${sanitizeCssUrl(picture.url)}")`,
        sanitizeCssValue(picture.size), sanitizeCssValue(picture.position));
}

/** A text alignment as the flex alignment that places the content. */
const JUSTIFY = { left: 'flex-start', center: 'center', right: 'flex-end' };
const ALIGN_ITEMS = { top: 'flex-start', center: 'center', bottom: 'flex-end' };

function paintAlignment(node, style) {
    if (style.text_align) {
        node.style.justifyContent = JUSTIFY[style.text_align] || 'center';
        node.style.textAlign = style.text_align;
    }
    if (style.vertical_align) {
        node.style.alignItems = ALIGN_ITEMS[style.vertical_align] || 'center';
    }
}

/**
 * A border, only where a width is named: a control whose border colour comes
 * from the stylesheet (a button's --panel-button-border) keeps it. Hairlines
 * stay visible: a 1px border is 0.0714rem, a third of a pixel on a small
 * phone, so the width never goes below 1px while a thick one still scales.
 */
function paintBorder(node, style) {
    if (!style.border_width) return;
    node.style.borderWidth = `max(1px, ${style.border_width}rem)`;
    node.style.borderStyle = style.border_style || 'solid';
    if (style.border_color) node.style.borderColor = style.border_color;
}

/** The named shadows; any other value is written as CSS. */
const SHADOW_PRESETS = {
    sm: '0 0.1429rem 0.2857rem rgba(0,0,0,0.2)',
    md: '0 0.2857rem 0.5714rem rgba(0,0,0,0.3)',
    lg: '0 0.5714rem 1.1429rem rgba(0,0,0,0.4)',
    inset: 'inset 0 0.1429rem 0.2857rem rgba(0,0,0,0.3)',
};

/** A glow takes the text colour, else the default accent. */
function paintShadow(node, style) {
    const shadow = style.box_shadow;
    if (!shadow || shadow === 'none') return;
    node.style.boxShadow = shadow === 'glow'
        ? `0 0 0.8571rem ${style.text_color || 'rgba(33,150,243,0.5)'}`
        : SHADOW_PRESETS[shadow] || shadow;
}

/**
 * Margin or padding: one value for all four sides, which a vertical or a
 * horizontal value overrides on its own sides; or, with no single value, each
 * of those on its own two sides.
 */
function spacing(side) {
    return (node, style) => {
        const all = style[side];
        const vertical = style[`${side}_vertical`];
        const horizontal = style[`${side}_horizontal`];
        if (all != null) {
            node.style[side] = `${vertical != null ? vertical : all}rem `
                + `${horizontal != null ? horizontal : all}rem`;
            return;
        }
        if (vertical != null) {
            node.style[`${side}Top`] = vertical + 'rem';
            node.style[`${side}Bottom`] = vertical + 'rem';
        }
        if (horizontal != null) {
            node.style[`${side}Left`] = horizontal + 'rem';
            node.style[`${side}Right`] = horizontal + 'rem';
        }
    };
}

/** Written when the key is set and not empty: zero is no value. */
const named = (v) => !!v;
/** Written whenever the key is there: zero is a value. */
const present = (v) => v != null;
const asIs = (v) => v;
const rem = (v) => v + 'rem';

/**
 * Every style key the panel draws, in the order it is written. A row is a
 * plain key (`[key, CSS property, written when, value]`; a property starting
 * `--` is a custom property the control's parts read) or a writer for keys
 * that go together.
 */
const STYLE_WRITERS = [
    paintBackground,
    ['text_color', 'color', named, asIs],
    ['font_size', 'fontSize', named, rem],
    ['font_weight', 'fontWeight', named, asIs],
    ['border_radius', 'borderRadius', present, rem],
    paintAlignment,
    paintBorder,
    paintShadow,
    spacing('margin'),
    spacing('padding'),
    ['text_transform', 'textTransform', named, asIs],
    ['letter_spacing', 'letterSpacing', named, rem],
    ['line_height', 'lineHeight', named, String],
    ['white_space', 'whiteSpace', named, asIs],
    ['transition_duration', 'transitionDuration', present, (v) => v + 'ms'],
    ['overflow', 'overflow', named, asIs],
    ['opacity', 'opacity', present, String],
    // The accent and surface a control's parts (thumb, fill, handle, track)
    // read through --el-*, over the theme's --panel-accent / --panel-surface.
    ['accent_color', '--el-accent', named, asIs],
    ['track_color', '--el-surface', named, asIs],
    ['track_color', '--el-surface-border', named, asIs],
];

function writeKey(node, style, [key, property, isValue, toCss]) {
    const value = style[key];
    if (!isValue(value)) return;
    if (property.startsWith('--')) node.style.setProperty(property, toCss(value));
    else node.style[property] = toCss(value);
}

/**
 * Write a style onto a node. Only the keys the style names are written: what
 * a previous style wrote and this one does not name stays, which is why a
 * state look drops its colours first (`clearStateColours`).
 */
export function applyStyle(node, style) {
    if (!style) return;
    for (const writer of STYLE_WRITERS) {
        if (typeof writer === 'function') writer(node, style);
        else writeKey(node, style, writer);
    }
}

/**
 * Drop what a state look writes inline (background colour, text colour,
 * opacity) where the resting style has nothing to put back, before the look
 * paints. Without it a look that goes inactive keeps the last state's colours:
 * a picker shows several inputs lit at once, a Blank All button stays orange
 * after it is restored. A look that may write another property has to be
 * taught here too.
 *
 * Pass the THEMED resting style: most control types take their theme colour
 * inline from it, and clearing one without putting it back would drop to the
 * bare CSS. A button's theme colour comes from CSS, so clearing restores it.
 */
export function clearStateColours(node, rest) {
    if (!node.style) return;
    if (!rest.bg_color && !rest.background_gradient) node.style.backgroundColor = '';
    if (!rest.text_color) node.style.color = '';
    if (rest.opacity == null) node.style.opacity = '';
}

/** Hide a button's chrome, so its picture is the button. */
export function applyFrameless(node) {
    node.style.backgroundColor = 'transparent';
    node.style.backgroundImage = 'none';
    node.style.borderWidth = '0';
    node.style.borderStyle = 'none';
    node.style.borderColor = 'transparent';
    node.style.boxShadow = 'none';
}

/** A picture's fit as CSS: `fill` stretches it to the box. */
const fitCss = (fit) => sanitizeCssValue(fit === 'fill' ? '100% 100%' : fit);

/** The blend values that composite nothing with the tint (a mask has a layer of its own). */
const BLENDLESS = new Set(['none', 'normal', 'mask']);

/**
 * Which effect layer a button's picture needs: 'mask', 'blend' (any blend
 * mode, written as given), 'fade' for an opacity below 1 alone, or null to
 * draw it on the node itself.
 */
function effectOf(blend, opacity) {
    if (blend === 'mask') return 'mask';
    if (blend && !BLENDLESS.has(blend)) return 'blend';
    return opacity < 1 ? 'fade' : null;
}

/** The tint fills the picture's shape. */
function maskLayer(layer, { urlCss, sizeCss }, tintColor) {
    layer.style.backgroundColor = tintColor;
    layer.style.webkitMaskImage = urlCss;
    layer.style.maskImage = urlCss;
    layer.style.webkitMaskSize = sizeCss;
    layer.style.maskSize = sizeCss;
    layer.style.webkitMaskPosition = 'center';
    layer.style.maskPosition = 'center';
    layer.style.webkitMaskRepeat = 'no-repeat';
    layer.style.maskRepeat = 'no-repeat';
}

/** The picture itself, composited with the tint by a blend mode, or (no mode) only faded. */
function pictureLayer(layer, { urlCss, sizeCss }, blendMode, tintColor) {
    layer.style.backgroundImage = urlCss;
    if (blendMode) layer.style.backgroundColor = tintColor;
    layer.style.backgroundSize = sizeCss;
    layer.style.backgroundPosition = 'center';
    layer.style.backgroundRepeat = 'no-repeat';
    if (blendMode) layer.style.backgroundBlendMode = blendMode;
}

/**
 * Draw a button's picture on an effect layer: under the text and icons
 * (z-index -1 inside the isolated node), over the node's own background, which
 * goes back to its gradient (or none). The tint is on the layer, not the node,
 * so a frameless button still tints.
 */
function drawOnEffectLayer(node, picture, { effect, blend, opacity, tintColor }) {
    node.style.backgroundImage = gradientOf.get(node) || 'none';
    node.style.position = 'relative';
    node.style.isolation = 'isolate';
    const layer = document.createElement('div');
    layer.className = 'panel-button-image-layer';
    layer.style.position = 'absolute';
    layer.style.inset = '0';
    layer.style.pointerEvents = 'none';
    layer.style.zIndex = '-1';
    if (opacity < 1) layer.style.opacity = String(opacity);
    if (effect === 'mask') maskLayer(layer, picture, tintColor);
    else pictureLayer(layer, picture, effect === 'blend' ? blend : null, tintColor);
    node.prepend(layer);
}

/**
 * Draw a button's picture, replacing the one drawn before (safe to call on
 * every state change): on the node itself, over its gradient, or on an effect
 * layer for a mask, a blend or an opacity below 1. The tint defaults to the
 * text colour (currentColor), so a mask or a blend always draws something
 * even where frameless has made the background transparent.
 */
export function applyImageEffect(node, imageRef, options = {}) {
    const url = assetUrl(imageRef);
    if (!url) return;
    const opacity = options.opacity != null ? Number(options.opacity) : 1;
    const picture = { urlCss: `url("${sanitizeCssUrl(url)}")`, sizeCss: fitCss(options.fit || 'cover') };

    node.querySelector(':scope > .panel-button-image-layer')?.remove();
    node.style.webkitMaskImage = '';
    node.style.maskImage = '';

    const blend = options.blend || 'none';
    const effect = effectOf(blend, opacity);
    if (effect) {
        drawOnEffectLayer(node, picture,
            { effect, blend, opacity, tintColor: options.tintColor || 'currentColor' });
        return;
    }
    drawOverGradient(node, picture.urlCss, picture.sizeCss, 'center');
    node.style.isolation = '';
}

/**
 * Retint a button's effect layer in place. Only a mask or a blend fills with
 * the tint; a layer that only fades the picture is left alone.
 */
export function updateImageTint(node, tintColor) {
    const layer = node.querySelector(':scope > .panel-button-image-layer');
    if (layer && fillsWithTint(layer)) layer.style.backgroundColor = tintColor;
}

const fillsWithTint = (layer) => Boolean(
    layer.style.maskImage || layer.style.webkitMaskImage || layer.style.backgroundBlendMode);
