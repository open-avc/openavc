/**
 * The gauge: an arc that fills from its start to where a reading falls in the
 * range, coloured by the zone the reading is in, with the reading written in
 * the middle.
 *
 * `GaugeControl` draws its nodes once and later shows each reading on them,
 * so the arc's geometry, its colours and the readout's rule live beside each
 * other on the control instead of in closures copied onto a binding record.
 * The panel keeps the element, its caption, its theme style, the binding, the
 * memo of what was last shown and the unavailable mark.
 *
 * The page review's renderer scan (tests/test_ui_page_review_mirrors.py)
 * credits all of this text to the gauge, the one renderer that hands it a
 * definition, so it reads only the gauge's own properties.
 */

import { nothingToShow } from './readings.js';

const SVG = 'http://www.w3.org/2000/svg';
/** The drawing's own units: a 120 by 120 box, scaled to the element. */
const SIZE = 120;

/** An SVG node with its attributes written in the order given. */
function svgNode(tag, attributes) {
    const node = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    return node;
}

export class GaugeControl {
    /**
     * `style` is the element's style over the theme's gauge defaults;
     * `decimals` is the readout's fixed decimal count, or null when the
     * author set none.
     */
    constructor(element, style, decimals) {
        this.min = element.min ?? 0;
        this.max = element.max ?? 100;
        this.unit = element.unit || '';
        const arcAngle = element.arc_angle ?? 240;
        this.zones = element.zones || null;
        this.decimals = decimals;
        this.colour = style.gauge_color || '#4CAF50';
        this.trackColour = style.gauge_bg_color || '#333333';
        this.width = style.gauge_width || 8;
        this.showValue = style.show_value !== false;
        this.showTicks = style.show_ticks !== false;
        this.tickCount = style.tick_count || 5;
        this.textColour = style.text_color || '#ffffff';
        this.centre = SIZE / 2;
        this.radius = (SIZE - this.width * 2) / 2;
        this.startAngle = (270 - arcAngle / 2) * Math.PI / 180;
        this.endAngle = (270 + arcAngle / 2) * Math.PI / 180;
    }

    /** Draw the gauge into `el`: the track, the fill (empty), the ticks and the readout. */
    mount(el) {
        const svg = document.createElementNS(SVG, 'svg');
        svg.setAttribute('viewBox', `0 0 ${SIZE} ${SIZE}`);
        svg.style.width = '100%';
        svg.style.height = '100%';
        svg.style.maxWidth = '100%';
        svg.style.maxHeight = '100%';
        svg.appendChild(svgNode('path', {
            d: this._arc(this.startAngle, this.endAngle),
            fill: 'none', stroke: this.trackColour, 'stroke-width': this.width, 'stroke-linecap': 'round',
        }));
        this.fill = svgNode('path', {
            fill: 'none', stroke: this.colour, 'stroke-width': this.width, 'stroke-linecap': 'round',
            'data-role': 'gauge-fg',
        });
        svg.appendChild(this.fill);
        if (this.showTicks && this.tickCount > 1) this._drawTicks(svg);
        this.readout = svgNode('text', {
            x: this.centre, y: this.centre + 4, 'text-anchor': 'middle',
            fill: this.textColour, 'font-size': '16', 'font-weight': '600',
        });
        this.readout.textContent = this.showValue ? `--${this.unit}` : '';
        this.readout.dataset.role = 'gauge-value';
        svg.appendChild(this.readout);
        el.appendChild(svg);
    }

    /** Show a reading: the arc to its place, the zone's colour, the number. */
    show(raw, offline) {
        if (nothingToShow(raw, offline)) {
            // Never a frozen last reading and never an arc at zero.
            this.fill.setAttribute('d', '');
            if (this.showValue) this.readout.textContent = `--${this.unit}`;
            return;
        }
        const value = Math.max(this.min, Math.min(this.max, Number(raw)));
        const frac = this.max > this.min ? (value - this.min) / (this.max - this.min) : 0;
        const angle = this.startAngle + frac * (this.endAngle - this.startAngle);
        this.fill.setAttribute('d', frac > 0.001 ? this._arc(this.startAngle, angle) : '');
        this.fill.setAttribute('stroke', this._colourAt(value));
        if (this.showValue) this.readout.textContent = `${this._readingText(value)}${this.unit}`;
    }

    /** The colour of the first zone a value falls in, else the gauge's own. */
    _colourAt(value) {
        if (!this.zones || !this.zones.length) return this.colour;
        for (const z of this.zones) {
            if (value >= z.from && value <= z.to) return z.color;
        }
        return this.colour;
    }

    /**
     * Unset decimals mean one decimal with trailing zeros dropped, what a
     * gauge has always drawn; a set count is a fixed width, the way the
     * slider and fader readouts behave.
     */
    _readingText(value) {
        return this.decimals != null
            ? value.toFixed(this.decimals)
            : String(Math.round(value * 10) / 10);
    }

    _drawTicks(svg) {
        for (let i = 0; i <= this.tickCount; i++) {
            const frac = i / this.tickCount;
            const angle = this.startAngle + frac * (this.endAngle - this.startAngle);
            const inner = this._point(angle, this.radius - this.width);
            const outer = this._point(angle, this.radius + this.width / 2);
            svg.appendChild(svgNode('line', {
                x1: inner.x, y1: inner.y, x2: outer.x, y2: outer.y, stroke: '#666', 'stroke-width': '1',
            }));
        }
    }

    _point(angle, r) {
        return { x: this.centre + r * Math.cos(angle), y: this.centre + r * Math.sin(angle) };
    }

    /** The path of the arc between two angles, on the gauge's radius. */
    _arc(from, to) {
        const r = this.radius;
        const s = this._point(from, r);
        const e = this._point(to, r);
        const largeArc = to - from > Math.PI ? 1 : 0;
        return `M ${s.x} ${s.y} A ${r} ${r} 0 ${largeArc} 1 ${e.x} ${e.y}`;
    }
}
