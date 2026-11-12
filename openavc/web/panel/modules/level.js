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
