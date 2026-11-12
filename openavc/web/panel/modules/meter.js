/**
 * The level meter: a bar of segments, each in the zone its place in the
 * range falls in (green, yellow, red, coloured by the stylesheet's
 * `[data-zone]` rules so a theme recolours them), lit up to a reading, with
 * the loudest recent reading held as a peak segment for a moment.
 *
 * `LevelMeterControl` draws the bar once and later shows each reading on it.
 * The peak it holds is the control's own: a page redraw draws a new meter
 * and starts again, and a refusal redraw shows the same meter again with its
 * peak kept. The panel keeps the element, its caption, its theme style, the
 * binding, the memo of what was last shown and the unavailable mark.
 *
 * The page review's renderer scan (tests/test_ui_page_review_mirrors.py)
 * credits all of this text to the level meter, the one renderer that hands
 * it a definition, so it reads only the meter's own properties.
 */

import { nothingToShow } from './readings.js';

export class LevelMeterControl {
    /** `style` is the element's style over the theme's level meter defaults. */
    constructor(element, style) {
        this.horizontal = element.orientation === 'horizontal';
        this.min = element.min ?? -60;
        this.max = element.max ?? 0;
        this.segments = style.meter_segments || 20;
        this.showPeak = style.show_peak !== false;
        this.greenTo = style.green_to ?? -12;
        this.yellowTo = style.yellow_to ?? -3;
        this.peakHoldMs = style.peak_hold_ms || 1500;
        this.peakValue = -Infinity;
        this.peakTime = 0;
    }

    /** Draw the bar into `el`, bottom (or left) the range's low end. */
    mount(el) {
        this.bar = document.createElement('div');
        this.bar.className = 'meter-bar';
        for (let i = 0; i < this.segments; i++) {
            const seg = document.createElement('div');
            seg.className = 'meter-segment';
            const segFrac = this.segments > 1 ? i / (this.segments - 1) : 0;
            seg.dataset.zone = this._zoneAt(this.min + segFrac * (this.max - this.min));
            this.bar.appendChild(seg);
        }
        el.appendChild(this.bar);
    }

    /** Light the segments up to a reading and mark the held peak; none lit without one. */
    show(raw, offline) {
        if (nothingToShow(raw, offline)) {
            this._clear();
            return;
        }
        const value = Math.max(this.min, Math.min(this.max, Number(raw)));
        const span = this.max - this.min;
        const litCount = Math.round(this._shareOf(value, span) * this.segments);
        this._holdPeak(value);
        const peakIdx = this.segments > 1 ? Math.round(this._shareOf(this.peakValue, span) * (this.segments - 1)) : 0;
        this._light(litCount, peakIdx);
    }

    /** Where a value sits along the range, 0 to 1; 0 when the range has no width. */
    _shareOf(value, span) {
        return span > 0 ? (value - this.min) / span : 0;
    }

    /** Nothing lit and no peak: cleared rather than frozen at the level it last reported. */
    _clear() {
        this.peakValue = -Infinity;
        for (const s of this.bar.querySelectorAll('.meter-segment')) { s.classList.remove('lit'); s.classList.remove('peak'); }
    }

    _light(litCount, peakIdx) {
        const segs = this.bar.querySelectorAll('.meter-segment');
        for (let i = 0; i < segs.length; i++) {
            segs[i].classList.toggle('lit', i < litCount);
            segs[i].classList.toggle('peak', this.showPeak && i === peakIdx && i >= litCount);
        }
    }

    /** A louder reading becomes the peak at once; a quieter one only once the hold has run out. */
    _holdPeak(value) {
        const now = Date.now();
        if (value > this.peakValue || now - this.peakTime > this.peakHoldMs) {
            this.peakValue = value;
            this.peakTime = now;
        }
    }

    _zoneAt(segValue) {
        if (segValue >= this.yellowTo) return 'red';
        if (segValue >= this.greenTo) return 'yellow';
        return 'green';
    }
}
