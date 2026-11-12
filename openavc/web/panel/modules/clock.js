/**
 * The clock: the time or the date written in a format of tokens, or a
 * duration counted down to a moment, up from one, or down through a meeting.
 *
 * `ClockControl` draws its display and says what it shows at a moment, one
 * method per mode. `ClockTicker` is the panel's one interval: every clock on
 * screen is shown once a second by it, a page redraw stops them all (`stop`)
 * and a dismissed overlay stops its own (`releaseIn`). It also remembers when
 * each meeting started, which outlives a redraw, so a meeting keeps counting
 * from its start when the page is drawn again.
 *
 * The page review's renderer scan (tests/test_ui_page_review_mirrors.py)
 * credits all of this text to the clock, the one renderer that hands it a
 * definition. The Builder's clock fields (ClockFields.tsx) show the same
 * default formats as their placeholder: change the two together.
 */

/**
 * The format a mode draws when the author wrote none. The three durations
 * never read theirs: a duration is always hours, minutes and seconds.
 */
const DEFAULT_FORMATS = {
    time: 'h:mm A', date: 'MMM D, YYYY', datetime: 'MMM D, YYYY h:mm A',
    countdown: 'HH:mm:ss', elapsed: 'HH:mm:ss', meeting: 'mm:ss',
};
/** "12" and "24" are documented shorthands for 12- and 24-hour time, not tokens. */
const FORMAT_SHORTHANDS = { '12': 'h:mm A', '24': 'HH:mm' };

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const FULL_DAYS = { Sun: 'Sunday', Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday' };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const FULL_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

const two = (n) => String(n).padStart(2, '0');

/** A duration as h:mm:ss, or mm:ss under an hour. */
function formatDuration(ms) {
    const totalSec = Math.floor(ms / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) return `${h}:${two(m)}:${two(s)}`;
    return `${two(m)}:${two(s)}`;
}

/** The date's parts in a named time zone; throws for a zone Intl does not know. */
function zonedParts(date, timezone) {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: timezone, hour12: false, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', weekday: 'short',
    }).formatToParts(date);
    const get = (type) => parts.find(p => p.type === type)?.value || '';
    return {
        year: parseInt(get('year')), month: parseInt(get('month')), day: parseInt(get('day')),
        hour: parseInt(get('hour')), minute: parseInt(get('minute')), second: parseInt(get('second')),
        weekday: get('weekday'),
    };
}

function localParts(d) {
    return {
        year: d.getFullYear(), month: d.getMonth() + 1, day: d.getDate(),
        hour: d.getHours(), minute: d.getMinutes(), second: d.getSeconds(),
        weekday: DAYS[d.getDay()],
    };
}

/** The date in a format, in `timezone` when Intl knows it, else in local time. */
function formatDateTime(date, format, timezone) {
    let d = date;
    if (timezone) {
        try {
            d = zonedParts(date, timezone);
            return applyFormat(d, format);
        } catch (e) {
            // Fall through to local time
        }
    }
    return applyFormat(localParts(d), format);
}

/**
 * Each token replaced in this order: a longer token before the shorter one
 * inside it, and the single letters only where they stand as a word.
 */
function applyFormat(d, format) {
    const h24 = d.hour;
    const h12 = h24 % 12 || 12;
    const ampm = h24 < 12 ? 'AM' : 'PM';
    return format
        .replace(/dddd/g, FULL_DAYS[d.weekday] || d.weekday || '')
        .replace(/ddd/g, d.weekday || '')
        .replace(/HH/g, two(h24))
        .replace(/\bH\b/g, String(h24))
        .replace(/hh/g, two(h12))
        .replace(/\bh\b/g, String(h12))
        .replace(/mm/g, two(d.minute))
        .replace(/ss/g, two(d.second))
        .replace(/\bA\b/g, ampm)
        .replace(/\ba\b/g, ampm.toLowerCase())
        .replace(/YYYY/g, String(d.year))
        .replace(/MMMM/g, FULL_MONTHS[d.month - 1] || '')
        .replace(/MMM/g, MONTHS[d.month - 1] || '')
        .replace(/MM/g, two(d.month))
        .replace(/\bM\b/g, String(d.month))
        .replace(/DD/g, two(d.day))
        .replace(/\bD\b/g, String(d.day));
}

/** A value in state set to something: not missing, null or blank. */
const isSet = (value) => value !== undefined && value !== null && value !== '';

/** A date from a state value or a written time, or null when it does not read as one. */
function dateOf(value) {
    const date = value != null ? new Date(value) : null;
    return date && !isNaN(date.getTime()) ? date : null;
}

export class ClockControl {
    /**
     * `read(key)` is the panel's state now; `ticker` remembers when each
     * meeting started. The countdown's and elapsed time's keys and the
     * target time are read off the definition at each tick; the rest once.
     */
    constructor(element, read, ticker) {
        this.element = element;
        this.read = read;
        this.ticker = ticker;
        this.mode = element.clock_mode || 'time';
        const rawFormat = element.format || DEFAULT_FORMATS[this.mode] || 'h:mm A';
        this.format = FORMAT_SHORTHANDS[rawFormat] || rawFormat;
        this.timezone = element.timezone || undefined;
        this.durationMin = element.duration_minutes || 60;
    }

    mount(el) {
        this.display = document.createElement('div');
        this.display.className = 'clock-display';
        el.appendChild(this.display);
    }

    /** Show what the clock reads now. */
    show() {
        this.display.textContent = this.text(new Date());
    }

    text(now) {
        switch (this.mode) {
            case 'countdown': return this._countdown(now);
            case 'elapsed': return this._elapsed(now);
            case 'meeting': return this._meeting(now);
            default: return formatDateTime(now, this.format, this.timezone);
        }
    }

    /** To a live state key, or the written target time when the key holds nothing. */
    _countdown(now) {
        const key = this.element.bindings?.show?.value?.key || this.element.start_key;
        const stateVal = key ? this.read(key) : null;
        const target = dateOf(isSet(stateVal) ? stateVal : this.element.target_time);
        return target ? formatDuration(Math.max(0, target - now)) : '--:--:--';
    }

    /** Up from the moment in the start key. */
    _elapsed(now) {
        const key = this.element.start_key;
        const start = dateOf(key ? this.read(key) : null);
        return start ? formatDuration(Math.max(0, now - start)) : '00:00:00';
    }

    /** Down through the meeting's length from when this clock first showed it, then past it. */
    _meeting(now) {
        const remaining = this.durationMin * 60 * 1000 - (now - this.ticker.meetingStart(this.element.id, now));
        return remaining > 0 ? formatDuration(remaining) : '-' + formatDuration(Math.abs(remaining));
    }
}

export class ClockTicker {
    constructor() {
        this._clocks = [];
        this._interval = null;
        /** Element id -> when its meeting started. */
        this._meetingStarts = {};
    }

    /** Show a clock now and once a second until it is released; `node` is its element. */
    add(node, clock) {
        clock.show();
        this._clocks.push({ node, clock });
        if (!this._interval) {
            this._interval = setInterval(() => {
                for (const c of this._clocks) c.clock.show();
            }, 1000);
        }
    }

    /** Stop the clocks drawn inside `container` (a dismissed overlay). */
    releaseIn(container) {
        this._clocks = this._clocks.filter((c) => !container.contains(c.node));
    }

    /** Stop every clock and the interval (a page redraw draws them again). */
    stop() {
        if (this._interval) {
            clearInterval(this._interval);
            this._interval = null;
        }
        this._clocks = [];
    }

    /** When the meeting on element `id` started: `now`, the first time it is asked. */
    meetingStart(id, now) {
        if (!this._meetingStarts[id]) this._meetingStarts[id] = now;
        return this._meetingStarts[id];
    }
}
