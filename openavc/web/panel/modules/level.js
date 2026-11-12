/**
 * The level controls, a slider and a fader: a control that sets a number by
 * how far along its travel it is, and shows the number its device reports.
 *
 * Both read one scale (LevelScale): a range, the response curve that turns
 * travel into a share of that range, and the device's own output range,
 * either stretched over the whole travel or reached only inside it (the dead
 * space). A reading comes in through it (`reading`, device to display); what
 * the control sends is the display value, which the server scales onto the
 * output range (`scale_value_forward` in openavc/core/ui_events.py), so the
 * two directions of that rule are written in two languages. Both send through
 * one rate (`liveSender`).
 *
 * The two controls read some settings differently, and each keeps its own
 * reading: the slider takes its range as written and places readings against
 * it read as numbers, the fader reads it as numbers and turns a reversed one
 * round; the slider snaps a moved value and then holds it to the range and
 * the output range, the fader holds it to the output range and then snaps;
 * their readout decimals and rates default differently.
 *
 * The page review reads this module's text for what a slider or fader reads
 * off its definition (tests/test_ui_page_review_mirrors.py credits all of it
 * to both renderers), so a definition is only ever read here through a
 * parameter named `element`, and no DOM node is given that name. A property
 * only one of the two controls reads belongs in that control's renderer in
 * panel.js.
 */

const DEFAULT_DB_RANGE = 60;
const SLIDER_RATE_MS = 100;
// A logarithmic slider gets at least this many positions, so the low end of
// its travel can still be reached finely.
const LOG_MIN_STEPS = 200;
// Every way a drag on a slider ends. A range input is not reliably focused
// during a touch drag, so the pointer and touch ends are listened for too.
const SLIDER_DRAG_ENDS = ['pointerup', 'pointercancel', 'blur', 'touchend', 'touchcancel'];

/** A number held between two others, `low` winning when they cross. */
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

/**
 * Snap a value to the nearest multiple of `step`, then strip the binary float
 * noise that Math.round(value/step)*step leaves behind (a 0.1 step yielding
 * 0.30000000000000004) by rounding to the step's own decimal places. Without
 * it a fractional-step control puts that noise on the wire: the engine only
 * rounds again when a driver declares a decimals rule. A step that is not
 * positive means "don't snap".
 */
export function snapToStep(value, step) {
    if (!(step > 0)) return value;
    const snapped = Math.round(value / step) * step;
    // Decimal places the step implies (0.1 -> 1, 0.25 -> 2, 5 -> 0). Panel
    // steps are clean decimals, so String() never goes exponential here.
    const s = String(step);
    const dot = s.indexOf('.');
    const decimals = dot === -1 ? 0 : s.length - dot - 1;
    return decimals > 0 ? Number(snapped.toFixed(decimals)) : snapped;
}

/**
 * The response curve: physical travel (0..1, bottom to top) to a share of the
 * range (0..1). Linear is the identity. Logarithmic makes the travel linear
 * in decibels, so equal travel is an equal loudness step the way a real audio
 * fader feels, instead of all the audible change crammed into the top of the
 * throw; `dbRange` is how many dB the throw spans (larger is finer down low).
 * Purely a feel: the value handed to the device is unchanged, so the server
 * never sees the curve.
 */
export function responseCurve(travelFrac, response, dbRange) {
    if (response !== 'logarithmic') return travelFrac;
    const D = dbRange > 0 ? dbRange : DEFAULT_DB_RANGE;
    const denom = Math.pow(10, D / 20) - 1;
    if (denom <= 0) return travelFrac;
    return (Math.pow(10, (D * travelFrac) / 20) - 1) / denom;
}

/** The curve backwards: a share of the range (0..1) to travel (0..1), to place a handle or a mark. */
export function responseCurveInverse(valueFrac, response, dbRange) {
    if (response !== 'logarithmic') return valueFrac;
    const D = dbRange > 0 ? dbRange : DEFAULT_DB_RANGE;
    const denom = Math.pow(10, D / 20) - 1;
    if (denom <= 0) return valueFrac;
    const vf = Math.max(0, Math.min(1, valueFrac));
    return (20 / D) * Math.log10(vf * denom + 1);
}

/** The numbers a fader's scale is marked with: three to seven round ones, and both ends. */
export function scaleMarks(min, max) {
    const range = max - min;
    if (range === 0) return [min];
    const rawStep = range / 5;
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const nice = [1, 2, 2.5, 5, 10].find(n => n * mag >= rawStep) * mag;
    const marks = [];
    const start = Math.ceil(min / nice) * nice;
    for (let v = start; v <= max + nice * 0.01; v += nice) {
        const rounded = Math.round(v * 1e6) / 1e6;
        if (rounded >= min && rounded <= max) marks.push(rounded);
    }
    if (marks.length === 0 || marks[0] > min) marks.unshift(min);
    if (marks[marks.length - 1] < max) marks.push(max);
    return marks;
}

/** A level value as its readout: `decimals` places (whole when none), then the unit. */
export function formatLevel(value, decimals, unit) {
    const n = Number(value);
    const s = decimals > 0 ? n.toFixed(decimals) : String(Math.round(n));
    return unit ? `${s} ${unit}` : s;
}

/** The readout of a level nobody has reported: dashes, with the unit. */
export function unknownText(unit) {
    return unit ? `-- ${unit}` : '--';
}

/**
 * What a slider and a fader both read off their definition. `defaults` are
 * the two each assumes for itself: readout `decimals` and the send `rateMs`.
 */
export function levelSettings(element, defaults) {
    return {
        step: element.step ?? 1,
        unit: element.unit || '',
        response: element.response || 'linear',
        dbRange: element.response_db_range != null ? Number(element.response_db_range) : DEFAULT_DB_RANGE,
        decimals: element.display_decimals != null ? Number(element.display_decimals) : defaults.decimals,
        sendOnRelease: element.send_on_release === true,
        rateMs: element.send_throttle_ms != null ? Number(element.send_throttle_ms) : defaults.rateMs,
        outputMin: element.output_min,
        outputMax: element.output_max,
        scaleToFull: element.scale_to_full !== false,
    };
}

/**
 * A control's scale: its display range (`min`..`max`), the response curve
 * along its travel, and the device's output range. Built from a range and
 * the settings levelSettings read.
 */
export class LevelScale {
    constructor(min, max, settings) {
        this.min = min;
        this.max = max;
        this.span = max - min;
        this.response = settings.response;
        this.dbRange = settings.dbRange;
        this.outputMin = settings.outputMin;
        this.outputMax = settings.outputMax;
        this.scaleToFull = settings.scaleToFull;
    }

    /** Whether the device's output range is reached inside the travel rather than across it: the dead space. */
    get limited() {
        return this.outputMin != null && this.outputMax != null && !this.scaleToFull;
    }

    /** The display value at `travel` (0..1) along the control. */
    valueAt(travel) {
        return this.min + responseCurve(travel, this.response, this.dbRange) * this.span;
    }

    /** How far along its travel (0..1) the control shows `value`. */
    travelOf(value) {
        return responseCurveInverse((value - this.min) / this.span, this.response, this.dbRange);
    }

    /** `value` held inside the device's output range. */
    toOutput(value) {
        return clamp(value, this.outputMin, this.outputMax);
    }

    /**
     * A device's reading as a display value. With none, null, except in the
     * design canvas (`design`): there a sample in the middle of the usable
     * range, so an author sees the handle, fill and readout before choosing a
     * binding or connecting equipment. The sample never enters state.
     */
    reading(raw, design) {
        if (raw !== undefined && raw !== null) return this.fromDevice(Number(raw));
        if (!design) return null;
        const low = this.limited ? Math.max(this.min, this.outputMin) : this.min;
        const high = this.limited ? Math.min(this.max, this.outputMax) : this.max;
        return low + (high - low) / 2;
    }

    /** A device value on the display range: stretched back from the output range, or as it is in dead-space mode. */
    fromDevice(deviceValue) {
        if (this.outputMin == null || this.outputMax == null) return deviceValue;
        if (this.scaleToFull === false) return deviceValue;
        const outputRange = this.outputMax - this.outputMin;
        if (outputRange === 0) return this.min;
        const frac = (deviceValue - this.outputMin) / outputRange;
        return this.min + frac * this.span;
    }
}

/**
 * How a level control puts its value on the wire (`sendValue(value)`). While
 * it moves, at most one value per `rateMs`: the first movement goes out at
 * once, and a movement inside the interval is held and sent (the newest one
 * only) when the interval ends, so the stream always lands where the control
 * stopped. A rate of 0 sends every movement. `release(v)` ends a gesture: it
 * sends v now and drops anything held, so nothing stale can follow it. On a
 * control that streams (`streaming`), a release that only repeats the value
 * the stream just sent is skipped, so a tap or an arrow key press is one
 * command, not two. Each timer is handed to `keepTimer`, for a page redraw to
 * clear. Times come from performance.now(), never Date.now(): a wall clock
 * that steps (a time sync at boot) would hold the stream for as long as the
 * step.
 */
export function liveSender(sendValue, rateMs, streaming, keepTimer) {
    const rate = Number(rateMs) > 0 ? Number(rateMs) : 0;
    let lastSentAt = -Infinity;
    let timer = null;
    let held;
    let streamed;   // what the stream last sent, forgotten at each release
    const put = (v) => {
        lastSentAt = performance.now();
        sendValue(v);
    };
    const cancel = () => {
        if (timer) { clearTimeout(timer); timer = null; }
    };
    return {
        live: (v) => {
            const wait = rate - (performance.now() - lastSentAt);
            if (wait <= 0) {
                cancel();
                streamed = v;
                put(v);
                return;
            }
            held = v;
            if (!timer) {
                timer = setTimeout(() => { timer = null; streamed = held; put(held); }, wait);
                keepTimer(timer);
            }
        },
        release: (v) => {
            const repeat = streaming && !timer && streamed === v;
            cancel();
            streamed = undefined;
            if (!repeat) put(v);
        },
    };
}

/**
 * What a slider and a fader share: the host they borrow from the panel, the
 * settings both read, their value binding's key, the readout and the sender.
 * A subclass sets `scale`.
 */
class LevelControl {
    constructor(element, host, settings) {
        this.host = host;
        this.step = settings.step;
        this.unit = settings.unit;
        this.decimals = settings.decimals;
        this.sendOnRelease = settings.sendOnRelease;
        this.valueKey = element.bindings?.show?.value?.key;
        this.sender = liveSender(host.sendValue, settings.rateMs, !settings.sendOnRelease, host.keepTimer);
    }

    /** `value` as the readout shows it. */
    format(value) {
        return formatLevel(value, this.decimals, this.unit);
    }

    /** What the control is first drawn showing: its device's reading, or the design sample; null with neither. */
    startingReading() {
        const raw = this.valueKey ? this.host.read(this.valueKey) : undefined;
        return this.scale.reading(raw, this.host.design());
    }
}

/** How many positions a slider's range input has: one per step, and never fewer than one (or LOG_MIN_STEPS). */
function travelSteps(span, step, response) {
    const rawSteps = step > 0 ? Math.round(span / step) : 0;
    return response === 'logarithmic' ? Math.max(rawSteps, LOG_MIN_STEPS) : Math.max(rawSteps, 1);
}

/**
 * A slider: a native range input over a fill. The input runs in a POSITION
 * domain (0..steps) rather than in display values, because a native thumb is
 * always linear in its own value and only the travel can carry the curve. For
 * a linear response a position is one value step.
 */
export class SliderControl extends LevelControl {
    constructor(element, host) {
        const settings = levelSettings(element, { decimals: null, rateMs: SLIDER_RATE_MS });
        super(element, host, settings);
        this.scale = new LevelScale(element.min ?? 0, element.max ?? 100, settings);
        // A reading is placed against the range read as numbers; the travel
        // is drawn against the range as written.
        this.readings = new LevelScale(parseFloat(element.min ?? 0), parseFloat(element.max ?? 100), settings);
        if (this.decimals == null) this.decimals = this.step < 1 ? 1 : 0;
        this.steps = travelSteps(this.scale.span, this.step, settings.response);
        this.vertical = element.orientation === 'vertical';
        this.showValue = element.style?.show_value === true;
        this.label = element.label || element.id;
    }

    /** Draw the track, its fill, the range input and the readout into `el`. */
    mount(el) {
        const wrapper = document.createElement('div');
        wrapper.className = 'slider-track-wrapper';
        const track = document.createElement('div');
        track.className = 'slider-track';
        this.fill = document.createElement('div');
        this.fill.className = 'slider-fill';
        track.appendChild(this.fill);
        wrapper.appendChild(track);
        this.input = this.drawInput();
        this.drawStart();
        this.listen();
        wrapper.appendChild(this.input);
        el.appendChild(wrapper);
        if (this.valueDisplay) el.appendChild(this.valueDisplay);
    }

    drawInput() {
        const input = document.createElement('input');
        input.type = 'range';
        input.setAttribute('aria-label', this.label);
        input.min = 0;
        input.max = this.steps;
        input.step = 1;
        return input;
    }

    /** The starting position, fill, spoken value and readout. */
    drawStart() {
        this.input.value = this.positionOf(this.startingReading() ?? this.scale.min);
        this.fillFromInput();
        const start = this.valueAtPosition(parseFloat(this.input.value));
        this.input.setAttribute('aria-valuetext', this.format(start));
        this.valueDisplay = null;
        if (this.showValue) {
            this.valueDisplay = document.createElement('div');
            this.valueDisplay.className = 'slider-value';
            this.valueDisplay.textContent = this.format(start);
        }
    }

    listen() {
        const input = this.input;
        input.addEventListener('input', () => this.moved());
        // 'change' fires when the value is committed: a release, a key.
        input.addEventListener('change', () => this.committed());
        // A drag in progress keeps readings off the thumb (the panel checks
        // `_dragging` before showing one).
        input.addEventListener('pointerdown', () => { input._dragging = true; });
        const endDrag = () => { input._dragging = false; };
        for (const type of SLIDER_DRAG_ENDS) input.addEventListener(type, endDrag);
    }

    /** The thumb moved: redraw, and stream the value unless the control sends on release only. */
    moved() {
        const input = this.input;
        // Dead-space mode: the thumb cannot travel past the device's output limits.
        if (this.scale.limited) {
            const lo = this.positionOf(this.scale.outputMin);
            const hi = this.positionOf(this.scale.outputMax);
            input.value = clamp(parseFloat(input.value), Math.min(lo, hi), Math.max(lo, hi));
        }
        this.fillFromInput();
        const v = this.valueAtPosition(parseFloat(input.value));
        input.setAttribute('aria-valuetext', this.format(v));
        if (this.valueDisplay) this.valueDisplay.textContent = this.format(v);
        if (!this.sendOnRelease) this.sender.live(v);
    }

    /** The release sends the final value: the one command on send-on-release, where a live drag lands otherwise. */
    committed() {
        const v = this.valueAtPosition(parseFloat(this.input.value));
        if (this.valueDisplay) this.valueDisplay.textContent = this.format(v);
        this.sender.release(v);
    }

    /** The input position (0..steps) that shows display value `value`. */
    positionOf(value) {
        const travel = this.scale.span !== 0 ? this.scale.travelOf(value) : 0;
        return clamp(Math.round(travel * this.steps), 0, this.steps);
    }

    /** The display value at input position `pos`: on the curve, snapped to the step, held to the range and any dead space. */
    valueAtPosition(pos) {
        let v = this.scale.valueAt(this.steps > 0 ? pos / this.steps : 0);
        if (this.step > 0) v = snapToStep(v, this.step);
        v = clamp(v, this.scale.min, this.scale.max);
        if (this.scale.limited) v = this.scale.toOutput(v);
        return v;
    }

    fillFromInput() {
        this.fillTo(this.steps > 0 ? (parseFloat(this.input.value) / this.steps) * 100 : 0);
    }

    fillTo(pct) {
        if (this.vertical) this.fill.style.height = pct + '%';
        else this.fill.style.width = pct + '%';
    }

    /** Show the device's reading, or that there is none. */
    show(raw, offline) {
        const value = this.readings.reading(raw, this.host.design());
        if (offline || value === null) {
            this.showUnknown();
            return;
        }
        const input = this.input;
        input.classList.remove('no-reading');
        const pos = this.positionOf(value);
        input.value = pos;
        input.setAttribute('aria-valuetext', this.format(value));
        this.fillTo(this.steps > 0 ? (pos / this.steps) * 100 : 0);
        if (this.valueDisplay) this.valueDisplay.textContent = this.format(value);
    }

    /**
     * Nothing to read: the device unreachable, the key gone, or a live device
     * that has not reported it yet. The fill empties, the thumb is hidden (a
     * thumb at the bottom is a claim of minimum) and the readout says so
     * rather than printing the range's floor as a value.
     */
    showUnknown() {
        const input = this.input;
        input.value = this.positionOf(this.readings.min);
        input.removeAttribute('aria-valuetext');
        input.classList.add('no-reading');
        this.fillTo(0);
        if (this.valueDisplay) this.valueDisplay.textContent = unknownText(this.unit);
    }
}
