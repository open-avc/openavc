/**
 * The routing matrix: a switcher's sources and destinations, drawn as a grid
 * of crosspoints, a list of dropdowns or a wall of tiles, and kept showing
 * what the device reports is routed.
 *
 * Each style is a class below that draws its nodes and later updates them,
 * side by side, because the two halves meet through the class names and
 * `data-` attributes the drawing puts on the nodes: a change to one is a
 * change to the other. What every style shares (sending a route, the lock,
 * mute, the source names, the audio line) is MatrixControl.
 *
 * Which source a device's report names is `routeMatches`. It is mirrored by
 * `route_matches` in openavc/ui/matrix_model.py and by matrixModel.ts in the
 * Builder, and the three must agree or the page review judges a matrix the
 * panel does not draw. The sources and destinations arrive already expanded
 * by the server (matrix_model.py), so nothing here expands a pattern.
 *
 * The panel lends a matrix what it needs as a host. Its functions read the
 * panel when called: `read(key)` the current state, `send(frame)`,
 * `sendOwn(frame)` for a frame that names no control (so the panel knows this
 * one sent it), `offline(key)`. Its values are fixed when the matrix is
 * drawn: `orientation` (of the arrangement being drawn) and `remPx`.
 */

// How far a finger may travel on a matrix crosspoint before the gesture stops
// being a tap and becomes a drag-to-route. Deliberately generous: a wall panel
// is touched with a thumb, and a few pixels of travel on the way down is a tap
// by every reasonable reading.
const MATRIX_DRAG_THRESHOLD_PX = 8;

// A crosspoint cell sizes itself to the room the element was given, between
// these two. The floor is the 9mm finger rule the rest of the panel is held
// to; above it a cell grows so a matrix given half a page draws a grid you can
// hit from across the room instead of a postage stamp in the corner. The
// ceiling is where growth stops paying: the dot inside is 16px (20px lit), and
// past ~72px the grid is mostly gap and reads worse, not better.
//
// Below the floor a cell does not shrink -- .matrix-scroll scrolls instead --
// which is what makes the floor a MINIMUM BOX rather than a preference, and is
// why openavc/ui/control_minimums.py can state what a given grid needs.
const MATRIX_CELL_MIN_PX = 44;
const MATRIX_CELL_MAX_PX = 72;

// How much room the destination-name column keeps before it starts ellipsising.
// It has to be DECLARED rather than left to the names, for two reasons that pull
// the same way. A grid's spare room is shared out equally between the tracks that
// can take it, so if the cells could grow, the one label column would get a
// ninth of it and "Main LCD" would draw as "M": the names would lose to the dots
// they label. And a column sized to its content is a column whose width is
// whatever somebody typed, which openavc/ui/control_minimums.py could not state a
// floor for. 80px is the same number .matrix-list-label declares, so the two
// styles reserve the same room for the same names.
const MATRIX_LABEL_MIN_PX = 80;

// A tile in the `tiles` style: one card per destination, naming what is routed
// to it in large type. These are its floor -- it grows into whatever room the
// element has (the tracks are minmax(floor, 1fr)) and never shrinks past them,
// for the same reason a crosspoint does not.
//
// In em of the matrix's text, not px, because a tile exists to hold two lines
// of that text: the destination's name at 0.7857em and what is routed to it at
// 1.2857em, plus its own padding. At the default text size that is 120x91px;
// a matrix given a larger font_size gets taller tiles rather than tiles that
// cut their second line. openavc/ui/control_minimums.py records the px this
// resolves to at the default, as it does for every font-driven part.
const MATRIX_TILE_MIN_W_EM = 4.2857;
const MATRIX_TILE_MIN_H_EM = 3.25;

const LOCKED_ICON = '🔒';
const UNLOCKED_ICON = '🔓';

// What a lock variable reads as "locked", besides `true` itself.
const LOCKED_WORDS = new Set(['true', '1', 'on', 'locked', 'yes']);

const NUMBER_TEXT = /^[+-]?\d+(?:\.\d+)?$/;

// The keys each destination is watched by, in the order they are listed.
const DESTINATION_KEYS = ['route_key', 'audio_route_key', 'label_key', 'lock_key'];


/**
 * How many columns and rows a wall of `count` tiles is drawn in.
 *
 * From the square root, so the wall matches the shape of the screen it is on:
 * wider than it is tall in landscape, taller than it is wide in portrait. Eight
 * tiles are four across and two down on a landscape panel and two across and
 * four down on a portrait one; sixteen are four and four either way.
 *
 * The rule reads as "wide" but has never been about width. It is about a
 * destination's name being legible across a room, and matching the box is what
 * achieves that: on an 800px-wide portrait panel, four across is 200px a tile
 * where two across is 400px.
 *
 * The shape is a function of the COUNT and the ORIENTATION, and not of the box,
 * which is the part worth knowing. A wall that reflowed to whatever width it
 * was given would have no single smallest box, so openavc/ui/control_minimums.py
 * could not state a floor for it -- and the one-column corner of that curve,
 * which is the only rectangle you could publish, tells an author their eight
 * tiles need 576px of height when they draw perfectly in 179. Orientation is
 * knowable without measuring the box, which is what keeps the floor from
 * depending on the box it is judging. Mirrored there as tile_grid_shape and in
 * the Builder's controlMinimums.ts; all three agree or the floor is for a shape
 * nothing renders.
 */
export function matrixTileGridShape(count, orientation) {
    if (count <= 0) return [0, 0];
    const root = Math.max(1, Math.floor(Math.sqrt(count)));
    return orientation === 'portrait'
        ? [root, Math.ceil(count / root)]
        : [Math.ceil(count / root), root];
}

// --- What is routed --------------------------------------------------------

/**
 * A routed-source value in comparable form, or null when nothing is routed.
 *
 * Not parseInt(): parseInt('IN1') is NaN and NaN never equals itself, so a
 * switcher that labels its ports would light no crosspoint and flag every
 * audio route as different from its video.
 *
 * 0 counts as unrouted rather than as port zero. Every routing driver in
 * the corpus numbers its ports from 1, and AV gear conventionally reports
 * 0 for an idle port -- so "audio is on nothing" would otherwise read as
 * "audio is on something else", which is what the audio line means.
 */
export function routeValue(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'number') return routedNumber(v);
    return routedText(String(v).trim());
}

/** A number a device reported: 0 and the non-finite ones mean nothing is routed. */
function routedNumber(v) {
    return Number.isFinite(v) && v !== 0 ? v : null;
}

/** Text a device reported: a number written out compares as that number. */
function routedText(s) {
    if (s === '' || s === '0') return null;
    if (NUMBER_TEXT.test(s)) return Number(s);
    return s.toLowerCase();
}

/** The single digit run in a string, or null when it does not have exactly one. */
function routeDigits(s) {
    if (typeof s !== 'string') return null;
    const runs = s.match(/\d+/g);
    return runs && runs.length === 1 ? Number(runs[0]) : null;
}

/**
 * Do two routed-source values name the same source?
 *
 * Equality first, so identical values can never read as a mismatch. Then a
 * single embedded number, so a device reporting 'IN2' or 'HDMI 3' can still
 * light its crosspoint -- but only when there is exactly one digit run, so
 * nothing is guessed from a source NAME ('Laptop') or a value that happens
 * to carry two numbers ('1080p60').
 */
export function routeMatches(a, b) {
    const x = routeValue(a), y = routeValue(b);
    if (x === null || y === null) return false;
    if (x === y) return true;
    const dx = typeof x === 'number' ? x : routeDigits(x);
    const dy = typeof y === 'number' ? y : routeDigits(y);
    return dx !== null && dx === dy;
}

/**
 * What a source looks like when the DEVICE names it.
 *
 * Usually the same thing it is routed by, which is why a source carries one
 * value. Two vocabularies is a real shape though: at_atdm_0604a is routed by
 * sending "0" and reports back "Mic". One value cannot be both -- and worse,
 * "0" is exactly what routeValue reads as "nothing is routed", so that
 * source could never light. `value` is what gets SENT, `report_value` is
 * what gets MATCHED, and omitting it makes them the same.
 */
export function sourceReports(src) {
    if (!src) return undefined;
    return src.report_value === undefined || src.report_value === null
        ? src.value : src.report_value;
}

/** Whether a lock variable's value means "locked". */
export function lockEngaged(v) {
    if (typeof v === 'boolean') return v;
    if (v === null || v === undefined) return false;
    return LOCKED_WORDS.has(String(v).trim().toLowerCase());
}

/** The source a device's report names, or null when it names none of them. */
export function routedSource(sources, routed) {
    if (routeValue(routed) === null) return null;
    return sources.find(s => routeMatches(sourceReports(s), routed)) || null;
}

/**
 * What one destination is showing right now, as three cases rather than two.
 *
 * A destination routed to something the matrix does not list is a different
 * fact about the space from one routed to nothing -- the first usually means a
 * port was left out of the list or patched at the rack since -- and the one
 * thing a panel must never do is report the wrong one confidently. Subsets are
 * the normal way to author a matrix, so "the device is on a port you left out"
 * is not an edge case.
 */
export function routeState(sources, routed) {
    const value = routeValue(routed);
    if (value === null) return { state: 'none' };
    const src = routedSource(sources, routed);
    return src ? { state: 'listed', source: src }
               : { state: 'unlisted', raw: String(routed) };
}

/**
 * What this row is called: the name somebody typed, else the device's own,
 * else its position. `read(key)` is the panel's current state.
 *
 * Same precedence as `child_display_name` on the server, which is THE answer
 * to what a port is called -- the integrator's label first, because a rack
 * label of `DEC-04` is not what belongs on a panel, and a name typed in the
 * matrix picker must be drawn even on a device that reports its own port
 * names. A tile and its chooser both come through here, so they cannot
 * disagree.
 *
 * The picker holds up its end -- it writes `label` only when the author
 * changed it -- and so does the resolver, which does not stamp "Out 2" onto
 * an entry that names a `label_key`. Without those two, this precedence would
 * freeze whatever the device happened to be called at setup.
 */
export function entryLabel(entry, read) {
    if (!entry) return '';
    const authored = entry.label;
    if (isNamed(authored)) return String(authored);
    const live = entry.label_key ? read(entry.label_key) : undefined;
    if (isNamed(live)) return String(live);
    // Nobody has named this port -- not here and not on the device. "Out 3"
    // beats the id it would otherwise read as, which on an AVoIP endpoint is
    // a MAC address.
    return String(entry._caption || entry.value);
}

function isNamed(v) {
    return v !== undefined && v !== null && String(v) !== '';
}

// --- Things a matrix leaves outside itself ---------------------------------

/** Take down the tile wall's source chooser, wherever it was opened from. */
function closeMatrixChooser() {
    document.querySelectorAll('.matrix-chooser').forEach(node => node.remove());
}

/** End every drag-to-route still in progress under `root`, sending nothing. */
export function endMatrixGestures(root) {
    for (const el of root.querySelectorAll('.panel-matrix')) {
        if (el._matrixDragCleanup) el._matrixDragCleanup();
    }
}

/**
 * Remove what a matrix put on document.body: a drag line and a source
 * chooser. Both live outside the element, so a re-render does not take them
 * with it.
 */
export function clearMatrixOverlays() {
    document.querySelectorAll('.matrix-drag-line').forEach(el => el.remove());
    closeMatrixChooser();
}

function crosspointRow(header) {
    const nodes = [];
    let node = header;
    do {
        nodes.push(node);
        node = node.nextElementSibling;
    } while (node && !node.classList.contains('matrix-output-header'));
    return nodes;
}

// --- The control -----------------------------------------------------------

/**
 * The matrix for one element, in the style it asks for. `style` is the
 * element's themed style, so crosspoint colours come from the theme too.
 */
export function createMatrix(element, style, host) {
    const Style = STYLES.get(element.matrix_style || 'crosspoint') || CrosspointMatrix;
    return new Style(element, style, host);
}

function div(className) {
    const node = document.createElement('div');
    node.className = className;
    return node;
}

/** Where a destination names its audio source when that differs from the video. */
function audioSourceNode(tag, o) {
    const audio = document.createElement(tag);
    audio.className = 'matrix-audio-source';
    audio.dataset.audioIdx = String(o);
    audio.hidden = true;
    return audio;
}

/**
 * A caption for each row nobody has named, in the same words the resolver uses
 * for a row with no live key at all -- so a matrix reads the same either way.
 * It is not stored as the entry's `label` because a STORED name outranks the
 * device's own (see entryLabel), and an invented one would then beat the
 * endpoint's real name the moment somebody typed it into the rack. Written in
 * place: the routing compares entries by identity, so they are never copied.
 */
function captionUnnamed(entries, prefix) {
    entries.forEach((entry, i) => {
        if (entry && !entry.label) entry._caption = `${prefix} ${i + 1}`;
    });
}

/**
 * Whether the mute column is drawn: on unless turned off, and only with a
 * mute_route binding wired up -- otherwise a press sends a route command the
 * engine has no action for. The Programmer warns next to "Show Mute" when
 * this keeps the buttons hidden.
 */
function showsMute(element, config) {
    return config.show_mute !== false && !!element.bindings?.do?.mute_route;
}

/**
 * A crosspoint cell's size. An authored cell_size pins the cell at exactly
 * that size; with nothing authored the cell fits itself to the box between
 * the floor and the ceiling, so a matrix given half a page draws a grid
 * worth touching.
 */
function cellSizes(style, remPx) {
    const authoredPx = style.cell_size ? style.cell_size * remPx : null;
    return {
        authoredPx,
        minPx: authoredPx ?? MATRIX_CELL_MIN_PX,
        maxPx: authoredPx ?? MATRIX_CELL_MAX_PX,
    };
}

class MatrixControl {
    constructor(element, style, host) {
        const config = element.matrix_config || {};
        this.element = element;
        this.config = config;
        this.host = host;
        this.el = null;
        this.scrollWrap = null;
        this.sources = Array.isArray(config.sources) ? config.sources : [];
        this.destinations = Array.isArray(config.destinations) ? config.destinations : [];
        captionUnnamed(this.sources, 'In');
        captionUnnamed(this.destinations, 'Out');
        // Off unless asked for: a lock nobody wanted costs a whole column.
        this.showLock = config.show_lock === true;
        this.showMute = showsMute(element, config);
        this.activeColor = style.crosspoint_active_color || '#4CAF50';
        this.inactiveColor = style.crosspoint_inactive_color || '#333333';
        this.cells = cellSizes(style, host.remPx);
        this.mutedOutputs = new Set();
        // A lock only this panel remembers is not a lock: it is forgotten when
        // the page redraws, and neither another panel nor the rack hears of
        // it. So a destination's lock lives in a VARIABLE and every panel
        // reading that key agrees about it. It has to be a variable
        // specifically: `var.` and `plugin.` are the only prefixes an
        // unauthenticated panel may write (state_store's
        // PANEL_WRITABLE_PREFIXES), and a panel is the thing doing the locking.
        // A destination with no lock_key keeps a lock in this panel's memory
        // instead of losing the button, and the page review says so.
        this.localLocks = new Set();
    }

    /** Draw the matrix into `el`, after whatever the panel already put there. */
    mount(el) {
        this.el = el;
        const presets = this.config.presets || [];
        if (presets.length > 0) el.appendChild(this.presetBar(presets));
        this.scrollWrap = div('matrix-scroll');
        const legend = this.draw(this.scrollWrap);
        el.appendChild(this.scrollWrap);
        if (legend) el.appendChild(legend);
        this.applyLocks();
    }

    /**
     * Every key this matrix reads, listed by name rather than as a glob: a
     * live-name key per source, then for each destination its route, audio,
     * name and lock keys.
     */
    watchedKeys() {
        const watched = [];
        for (const src of this.sources) if (src.label_key) watched.push(src.label_key);
        for (const dest of this.destinations) {
            for (const field of DESTINATION_KEYS) if (dest[field]) watched.push(dest[field]);
        }
        return watched;
    }

    /**
     * Which destinations' own device cannot be reached. Per destination, not
     * per matrix: one matrix can span several switchers, and going quiet about
     * the live ones because a third is down would be its own lie.
     */
    offlineDestinations() {
        return this.destinations.map(
            d => !!(d.route_key && this.host.offline(d.route_key)));
    }

    /**
     * Show what the device reports. Each destination reads its OWN key, which
     * is what lets one matrix span several devices or several routing planes
     * of one device. The RAW value is kept: the device decides what a routed
     * source looks like, and routeMatches decides whether two of them name
     * the same thing. A destination that is offline reads as unreported.
     */
    update(destOffline) {
        const routes = this.destinations.map(
            (d, i) => (d.route_key && !destOffline[i] ? this.host.read(d.route_key) : undefined));
        this.updateAudioSources(routes);
        this.updateNames();
        this.applyLocks();
        this.updateRoutes(routes);
    }

    label(entry) {
        return entryLabel(entry, this.host.read);
    }

    // The one place that turns "the user chose this input for that output"
    // into messages, so the tap, the drag, the dropdown and the chooser
    // cannot disagree on whether audio follows. input and output are the
    // SOURCE's and DESTINATION's own values, not row and column numbers; the
    // server finds the destination by its value to see whether it overrides
    // the element's route action.
    sendRoute(input, output) {
        const send = this.host.send;
        send({ type: 'ui.route', element_id: this.element.id, input, output });
        if (this.config.audio_follow_video && this.element.bindings?.do?.audio_route) {
            send({ type: 'ui.route', element_id: this.element.id, input, output, audio: true });
        }
    }

    sendMute(o, dest) {
        const wasMuted = this.mutedOutputs.has(o);
        if (wasMuted) this.mutedOutputs.delete(o); else this.mutedOutputs.add(o);
        const send = this.host.send;
        send({ type: 'ui.route', element_id: this.element.id, output: dest.value, mute: !wasMuted });
        if (this.config.audio_follow_video && this.element.bindings?.do?.audio_mute_route) {
            send({
                type: 'ui.route', element_id: this.element.id,
                output: dest.value, mute: !wasMuted, audio: true,
            });
        }
    }

    lockKey(o) {
        return this.destinations[o] && this.destinations[o].lock_key;
    }

    isLocked(o) {
        const key = this.lockKey(o);
        return key ? lockEngaged(this.host.read(key)) : this.localLocks.has(o);
    }

    toggleLock(o) {
        const key = this.lockKey(o);
        if (!key) {
            if (this.localLocks.has(o)) this.localLocks.delete(o); else this.localLocks.add(o);
            this.applyLocks();
            return;
        }
        // No optimistic flip: the state change is what every panel sees, so
        // the one that pressed it shows the same thing the others do.
        this.host.sendOwn({ type: 'state.set', key, value: !lockEngaged(this.host.read(key)) });
    }

    /** A destination's lock button. A tile's stops its tap reaching the tile. */
    lockButton(o, stopPropagation) {
        const btn = document.createElement('button');
        btn.className = 'matrix-lock-btn';
        btn.dataset.lockIdx = String(o);
        btn.textContent = UNLOCKED_ICON;
        btn.title = 'Lock output';
        btn.addEventListener('click', (e) => {
            if (stopPropagation) e.stopPropagation();
            this.toggleLock(o);
        });
        return btn;
    }

    /** A destination's mute button; `afterPress` runs once the press is sent. */
    muteButton(o, { stopPropagation = false, afterPress = null } = {}) {
        const btn = document.createElement('button');
        btn.className = 'matrix-mute-btn';
        btn.textContent = 'M';
        btn.title = 'Mute output';
        btn.addEventListener('click', (e) => {
            if (stopPropagation) e.stopPropagation();
            this.sendMute(o, this.destinations[o]);
            btn.classList.toggle('muted', this.mutedOutputs.has(o));
            if (afterPress) afterPress();
        });
        return btn;
    }

    /** Draw every lock, keyed or not, from one place. */
    applyLocks() {
        this.el.querySelectorAll('[data-lock-idx]').forEach(btn => {
            const engaged = this.isLocked(parseInt(btn.dataset.lockIdx));
            btn.textContent = engaged ? LOCKED_ICON : UNLOCKED_ICON;
            btn.classList.toggle('locked', engaged);
            btn.title = engaged ? 'Unlock output' : 'Lock output';
            this.showLockOnRow(btn, engaged);
        });
    }

    /** What a lock does to the rest of its destination's row, beyond the button. */
    showLockOnRow() {}

    /**
     * Every destination's row, as `[index, nodes]`, for marking a destination
     * whose device cannot be reached. A style whose destinations sit in a real
     * row container answers with that container.
     */
    destinationRows() {
        return this.rowsIn(this.rowSelector());
    }

    rowsIn(rowSel) {
        const rows = [];
        this.el.querySelectorAll(this.nameSelector()).forEach(node => {
            const row = node.closest(rowSel);
            if (row) rows.push([parseInt(node.dataset.outputIdx), [row]]);
        });
        return rows;
    }

    presetBar(presets) {
        const presetBar = div('matrix-presets');
        for (const preset of presets) {
            const btn = document.createElement('button');
            btn.className = 'matrix-preset-btn';
            btn.textContent = preset.name || 'Preset';
            btn.addEventListener('click', () => {
                // A macro frame names no element, so the failure band would
                // otherwise place itself against whatever was touched before
                // this -- and cover the preset bar somebody still has a finger on.
                if (preset.macro) this.host.sendOwn({ type: 'macro.execute', macro_id: preset.macro });
            });
            presetBar.appendChild(btn);
        }
        return presetBar;
    }

    /**
     * The name of what the AUDIO is on, whenever it differs from the video.
     * Only when BOTH are routed and they disagree: an unreported or idle audio
     * port is an absence, not a disagreement. Stated by name, because the
     * audience is standing at a touch screen with no pointer to hover.
     */
    updateAudioSources(routes) {
        this.el.querySelectorAll('.matrix-audio-source').forEach(node => {
            const idx = parseInt(node.dataset.audioIdx);
            const raw = this.audioReport(this.destinations[idx]);
            const video = routeValue(routes[idx]);
            const audio = routeValue(raw);
            const differs = video !== null && audio !== null && !routeMatches(video, audio);
            node.hidden = !differs;
            if (!differs) return;
            const src = routedSource(this.sources, raw);
            const name = src ? this.label(src) : String(raw);
            node.textContent = `Audio: ${name}`;
            node.title = `Audio is on ${name}`;
        });
    }

    audioReport(dest) {
        return dest?.audio_route_key && !this.host.offline(dest.audio_route_key)
            ? this.host.read(dest.audio_route_key) : undefined;
    }

    /**
     * Live names, written to the [data-label-text] child where there is one
     * (the crosspoint's header has siblings) and to the node itself otherwise.
     * Through entryLabel, not straight off label_key: the authored name wins
     * where there is one, and this pass runs after the drawing. A style's
     * destination names are the nodes its nameSelector() picks, never every
     * `data-output-idx`: the list's <select> carries one too, and setting
     * textContent on a <select> destroys every <option> in it.
     */
    updateNames() {
        const applyLabel = (node, entry) => {
            if (!entry) return;
            const text = this.label(entry);
            if (text === '') return;
            (node.querySelector('[data-label-text]') || node).textContent = text;
        };
        this.el.querySelectorAll('[data-input-idx]').forEach(h => {
            applyLabel(h, this.sources[parseInt(h.dataset.inputIdx)]);
        });
        this.el.querySelectorAll(this.nameSelector()).forEach(h => {
            applyLabel(h, this.destinations[parseInt(h.dataset.outputIdx)]);
        });
    }
}

/**
 * A list row's dropdown is disabled while its destination is locked or muted:
 * unlocking must not re-enable a muted one, nor unmuting a locked one. The
 * mute is recorded on the node as `data-muted`.
 */
function settleSelect(select, locked) {
    select.disabled = locked || select.dataset.muted === '1';
}

/** One dropdown per destination. */
class ListMatrix extends MatrixControl {
    nameSelector() {
        return '.matrix-list-label[data-output-idx]';
    }

    rowSelector() {
        return '.matrix-list-row';
    }

    showLockOnRow(btn, engaged) {
        const row = btn.closest('.matrix-list-row');
        const sel = row && row.querySelector('.matrix-list-select');
        if (sel) settleSelect(sel, engaged);
    }

    draw(scrollWrap) {
        const list = div('matrix-list');
        for (let o = 0; o < this.destinations.length; o++) list.appendChild(this.row(o));
        scrollWrap.appendChild(list);
        return null;
    }

    row(o) {
        const dest = this.destinations[o];
        const row = div('matrix-list-row');
        const outLabel = document.createElement('span');
        outLabel.className = 'matrix-list-label';
        outLabel.textContent = dest.label || `Out ${o + 1}`;
        outLabel.dataset.outputIdx = String(o);
        row.appendChild(outLabel);
        if (dest.audio_route_key) row.appendChild(audioSourceNode('span', o));
        const select = this.select(o, dest);
        if (this.showLock) row.appendChild(this.lockButton(o, false));
        if (this.showMute) {
            row.appendChild(this.muteButton(o, {
                afterPress: () => {
                    select.dataset.muted = this.mutedOutputs.has(o) ? '1' : '';
                    settleSelect(select, this.isLocked(o));
                },
            }));
        }
        row.appendChild(select);
        return row;
    }

    select(o, dest) {
        const select = document.createElement('select');
        select.className = 'matrix-list-select';
        // A select shows its first option when nothing has selected one, so
        // a destination routed to NOTHING would read as routed to whatever is
        // listed first. This is what it shows instead, and it is disabled so
        // it can never be chosen and sent.
        const idle = document.createElement('option');
        idle.className = 'matrix-list-idle';
        idle.value = '';
        idle.textContent = '—';
        idle.disabled = true;
        select.appendChild(idle);
        for (let i = 0; i < this.sources.length; i++) select.appendChild(this.option(i));
        // A device reporting a source this matrix does not list has no option
        // to select. This one carries whatever the device actually said, and
        // is only shown when that happens.
        const unlisted = document.createElement('option');
        unlisted.className = 'matrix-list-unlisted';
        unlisted.value = '';
        unlisted.disabled = true;
        unlisted.hidden = true;
        select.appendChild(unlisted);
        // A browser picks the first ENABLED option on its own, so the
        // placeholder has to be selected explicitly or the row claims the
        // first source.
        select.selectedIndex = 0;
        select.dataset.outputIdx = String(o);
        select.addEventListener('change', () => {
            if (this.isLocked(o)) return;
            // By the option's own index into the source list, never by the
            // select's: two of the options are not sources.
            const opt = select.selectedOptions[0];
            const src = opt && opt.dataset.sourceIdx !== undefined
                ? this.sources[parseInt(opt.dataset.sourceIdx)] : null;
            if (src) this.sendRoute(src.value, dest.value);
        });
        return select;
    }

    option(i) {
        const opt = document.createElement('option');
        // The option's value is the source's own value as text, because a DOM
        // option value is always a string. The typed value is read back off
        // the source list on change, so a device expecting the number 3 is
        // not sent "3".
        opt.value = String(this.sources[i].value);
        opt.textContent = this.label(this.sources[i]);
        opt.dataset.sourceIdx = String(i);
        return opt;
    }

    /**
     * The option whose source the device's report names, so a device reporting
     * 'IN2' still moves the dropdown to Input 2.
     */
    selectSource(sel, source) {
        const i = this.sources.indexOf(source);
        const hit = sel.querySelector(`option[data-source-idx="${i}"]`);
        if (hit) sel.selectedIndex = hit.index;
    }

    updateRoutes(routes) {
        this.el.querySelectorAll('.matrix-list-select').forEach(sel => {
            this.updateOptionNames(sel);
            this.showRoute(sel, routeState(this.sources, routes[parseInt(sel.dataset.outputIdx)]));
        });
    }

    /** Live SOURCE names reach the dropdown's options here. */
    updateOptionNames(sel) {
        for (const opt of sel.options) {
            if (opt.dataset.sourceIdx === undefined) continue;
            const entry = this.sources[parseInt(opt.dataset.sourceIdx)];
            const text = entry ? this.label(entry) : '';
            if (text !== '') opt.textContent = text;
        }
    }

    showRoute(sel, routed) {
        const unlisted = sel.querySelector('.matrix-list-unlisted');
        if (routed.state === 'listed') {
            this.selectSource(sel, routed.source);
            if (unlisted) unlisted.hidden = true;
        } else if (routed.state === 'unlisted' && unlisted) {
            // A source this matrix does not list. The first option, which is
            // what a <select> shows on its own, would name a source that is
            // not routed.
            unlisted.textContent = `${routed.raw} (not listed)`;
            unlisted.hidden = false;
            sel.selectedIndex = unlisted.index;
        } else {
            sel.selectedIndex = 0;
            if (unlisted) unlisted.hidden = true;
        }
    }
}

/**
 * One card per destination, saying in large type what is on it.
 *
 * Nobody standing in a space wants a grid of dots: they want to know what is
 * on the main display, and to change it. So the sources are not on the wall
 * at all. A tap opens them as a chooser over the panel, which is also what
 * lets this style's floor ignore the source count: sixteen sources cost a
 * tile wall nothing, where they cost a crosspoint grid sixteen columns.
 */
class TileMatrix extends MatrixControl {
    nameSelector() {
        return '.matrix-tile-dest[data-output-idx]';
    }

    rowSelector() {
        return '.matrix-tile';
    }

    showLockOnRow(btn, engaged) {
        const tile = btn.closest('.matrix-tile');
        if (tile) tile.classList.toggle('locked', engaged);
    }

    draw(scrollWrap) {
        const wall = div('matrix-tiles');
        const [tileCols, tileRows] = matrixTileGridShape(
            this.destinations.length, this.host.orientation);
        if (tileCols > 0) {
            // minmax(floor, 1fr): a tile never draws below the floor the
            // review states, and takes the whole box when there is more.
            wall.style.gridTemplateColumns =
                `repeat(${tileCols}, minmax(${MATRIX_TILE_MIN_W_EM}em, 1fr))`;
            wall.style.gridTemplateRows =
                `repeat(${tileRows}, minmax(${MATRIX_TILE_MIN_H_EM}em, 1fr))`;
        }
        for (let o = 0; o < this.destinations.length; o++) wall.appendChild(this.tile(o));
        scrollWrap.appendChild(wall);
        return null;
    }

    tile(o) {
        const dest = this.destinations[o];
        // A div rather than a <button>: the lock and mute are real buttons
        // and a button cannot contain one.
        const tile = div('matrix-tile');
        tile.dataset.destIdx = String(o);
        tile.setAttribute('role', 'button');
        tile.setAttribute('tabindex', '0');
        tile.appendChild(this.head(o, dest));
        const routed = div('matrix-tile-source');
        routed.dataset.tileIdx = String(o);
        routed.textContent = '—';
        tile.appendChild(routed);
        if (dest.audio_route_key) tile.appendChild(audioSourceNode('div', o));
        const open = () => { if (!this.isLocked(o)) this.openSourceChooser(o); };
        tile.addEventListener('click', open);
        tile.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });
        return tile;
    }

    head(o, dest) {
        const head = div('matrix-tile-head');
        const name = document.createElement('span');
        name.className = 'matrix-tile-dest';
        name.dataset.outputIdx = String(o);
        const nameText = document.createElement('span');
        nameText.dataset.labelText = '';
        nameText.textContent = dest.label || `Out ${o + 1}`;
        name.appendChild(nameText);
        head.appendChild(name);
        if (this.showLock) head.appendChild(this.lockButton(o, true));
        if (this.showMute) head.appendChild(this.muteButton(o, { stopPropagation: true }));
        return head;
    }

    /**
     * The source chooser a tile opens: over the whole panel rather than inside
     * the element, because the element is a wall of destinations and may be
     * small. Fixed positioning also means it works the same in the Builder
     * canvas, which is an iframe of this renderer.
     */
    openSourceChooser(o) {
        closeMatrixChooser();
        const dest = this.destinations[o];
        const routed = routeState(
            this.sources, dest.route_key ? this.host.read(dest.route_key) : undefined);

        const overlay = div('matrix-chooser');
        // The sheet lives on document.body, so nothing this matrix was given
        // reaches it by inheritance. Pin it to the size the matrix resolved to
        // and the sheet's em sizes follow an authored font_size the same way
        // the tiles do.
        overlay.style.fontSize = getComputedStyle(this.el).fontSize;
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeMatrixChooser();
        });

        const sheet = div('matrix-chooser-sheet');
        const title = div('matrix-chooser-title');
        title.textContent = this.label(dest);
        sheet.appendChild(title);
        sheet.appendChild(this.chooserGrid(dest, routed));
        const cancel = document.createElement('button');
        cancel.className = 'matrix-chooser-cancel';
        cancel.textContent = 'Cancel';
        cancel.addEventListener('click', () => closeMatrixChooser());
        sheet.appendChild(cancel);

        overlay.appendChild(sheet);
        document.body.appendChild(overlay);
    }

    chooserGrid(dest, routed) {
        const grid = div('matrix-chooser-grid');
        for (let i = 0; i < this.sources.length; i++) {
            const src = this.sources[i];
            const btn = document.createElement('button');
            btn.className = 'matrix-chooser-source';
            btn.dataset.sourceIdx = String(i);
            btn.textContent = this.label(src);
            if (routed.state === 'listed' && routed.source === src) {
                btn.classList.add('active');
                btn.style.borderColor = this.activeColor;
                btn.style.color = this.activeColor;
            }
            btn.addEventListener('click', () => {
                this.sendRoute(src.value, dest.value);
                closeMatrixChooser();
            });
            grid.appendChild(btn);
        }
        return grid;
    }

    updateRoutes(routes) {
        this.el.querySelectorAll('.matrix-tile-source').forEach(node => {
            const routed = routeState(this.sources, routes[parseInt(node.dataset.tileIdx)]);
            node.classList.toggle('unlisted', routed.state === 'unlisted');
            node.classList.toggle('idle', routed.state === 'none');
            if (routed.state === 'listed') {
                node.textContent = this.label(routed.source);
                node.style.color = this.activeColor;
                node.title = '';
            } else if (routed.state === 'unlisted') {
                node.textContent = routed.raw;
                node.style.color = '';
                node.title = `${routed.raw} is not one of this matrix's sources`;
            } else {
                node.textContent = '—';
                node.style.color = '';
                node.title = '';
            }
        });
    }
}

/** A grid of crosspoints: a row per destination, a column per source. */
class CrosspointMatrix extends MatrixControl {
    constructor(element, style, host) {
        super(element, style, host);
        // Shared by every cell of this matrix: the line a drag draws, and the
        // column the gesture in progress started from.
        this.drag = { line: null, startInput: null };
    }

    nameSelector() {
        return '.matrix-output-header[data-output-idx]';
    }

    /**
     * The crosspoint has no element standing for a row: it is a flat CSS grid
     * -- a corner, one header per source, then for each destination its name
     * followed by one cell per source. So a row is the header plus the
     * siblings up to the next header. Marking only the name would leave a row
     * of live-looking crosspoints beside it.
     */
    destinationRows() {
        return Array.from(this.el.querySelectorAll(this.nameSelector()),
            header => [parseInt(header.dataset.outputIdx), crosspointRow(header)]);
    }

    draw(scrollWrap) {
        const table = div('matrix-grid');
        this.sizeGrid(table);
        table.appendChild(div('matrix-corner'));
        this.appendColumnHeaders(table);
        for (let o = 0; o < this.destinations.length; o++) this.appendRow(table, o);
        scrollWrap.appendChild(table);
        return this.legend();
    }

    /**
     * The grid's tracks. minmax() is what does the fitting: a track cannot go
     * below the floor (so a starved grid scrolls rather than drawing
     * untouchable cells) and grows toward the ceiling with whatever room is
     * spare. The stylesheet's `justify-content: start` keeps the label column
     * from swallowing that spare room first.
     *
     * The COLUMNS are flexible rather than capped per track, and the cap is
     * the grid's own max-width (label floor + every cell at its ceiling + the
     * extra columns + the gaps). A grid hands spare room out equally to every
     * track that can still grow, so capped cell tracks would leave the one
     * name column a ninth of it beside eight dot columns. A flexible track is
     * sized LAST, after the name column has grown to its longest name, so the
     * names are served first and the dots take what is left, never less than
     * the floor. An authored cell_size still pins the track at that size.
     */
    sizeGrid(table) {
        const inputCount = this.sources.length;
        const outputCount = this.destinations.length;
        // The lock and mute columns are touch targets, so they get the same
        // floor as a crosspoint.
        const extraColDefs = [];
        if (this.showLock) extraColDefs.push(`${MATRIX_CELL_MIN_PX}px`);
        if (this.showMute) extraColDefs.push(`${MATRIX_CELL_MIN_PX}px`);
        const { authoredPx, minPx, maxPx } = this.cells;
        const cellTrack = `minmax(${minPx}px, ${maxPx}px)`;
        const colTrack = authoredPx ? cellTrack : `minmax(${minPx}px, 1fr)`;
        // The label column keeps MATRIX_LABEL_MIN_PX and grows to the longest
        // name when there is room. `auto` would not: its base size is
        // min-content, and .matrix-header is `overflow: hidden`, which makes
        // that zero.
        const labelTrack = `minmax(${MATRIX_LABEL_MIN_PX}px, max-content)`;
        // repeat(0, ...) is not valid CSS, and an empty matrix is reachable.
        const cellCols = inputCount > 0 ? `repeat(${inputCount}, ${colTrack})` : '';
        table.style.gridTemplateColumns =
            `${labelTrack} ${cellCols} ${extraColDefs.join(' ')}`.replace(/\s+/g, ' ').trim();
        table.style.gridTemplateRows = outputCount > 0
            ? `auto repeat(${outputCount}, ${cellTrack})` : 'auto';
        if (!authoredPx) {
            // One 1px gap between each pair of tracks (.matrix-grid gap).
            const gaps = inputCount + extraColDefs.length;
            table.style.maxWidth = `${MATRIX_LABEL_MIN_PX + inputCount * maxPx
                + extraColDefs.length * MATRIX_CELL_MIN_PX + gaps}px`;
        }
    }

    /**
     * The top row: each source's column NUMBER, never its name. A name does
     * not fit a column a finger wide, so the names go to the legend under the
     * grid, where there is room to read them. Then the lock and mute headers.
     */
    appendColumnHeaders(table) {
        for (let i = 0; i < this.sources.length; i++) {
            const header = div('matrix-header matrix-input-header');
            header.textContent = String(i + 1);
            table.appendChild(header);
        }
        if (this.showLock) table.appendChild(toggleColumnHeader(LOCKED_ICON));
        if (this.showMute) table.appendChild(toggleColumnHeader('M'));
    }

    appendRow(table, o) {
        table.appendChild(this.rowHeader(o));
        for (let i = 0; i < this.sources.length; i++) table.appendChild(this.cell(i, o));
        if (this.showLock) table.appendChild(toggleCell(this.lockButton(o, false)));
        if (this.showMute) table.appendChild(toggleCell(this.muteButton(o)));
    }

    /**
     * A destination's name, then the name of its audio source when that
     * differs, then what the device says is routed here when it is not one of
     * this matrix's sources -- without which that row would look exactly like
     * a destination routed to nothing. The name is in its own
     * [data-label-text] span so a live name does not wipe its siblings.
     */
    rowHeader(o) {
        const dest = this.destinations[o];
        const outHeader = div('matrix-header matrix-output-header');
        outHeader.dataset.outputIdx = String(o);
        const outLabelText = document.createElement('span');
        outLabelText.dataset.labelText = '';
        outLabelText.textContent = this.label(dest);
        outHeader.appendChild(outLabelText);
        if (dest.audio_route_key) outHeader.appendChild(audioSourceNode('span', o));
        const unlisted = document.createElement('span');
        unlisted.className = 'matrix-unlisted';
        unlisted.dataset.unlistedIdx = String(o);
        unlisted.hidden = true;
        outHeader.appendChild(unlisted);
        return outHeader;
    }

    cell(i, o) {
        const src = this.sources[i];
        const dest = this.destinations[o];
        const cell = div('matrix-cell');
        cell.setAttribute('aria-label',
            `Route ${src.label || `input ${i + 1}`} to ${dest.label || `output ${o + 1}`}`);
        const dot = div('matrix-crosspoint');
        dot.style.backgroundColor = this.inactiveColor;
        // data-input / data-output carry the VALUES, which is what the route
        // comparison needs; the -idx pair carries the row and column, which is
        // what a typed value is read back by. A DOM dataset is strings only,
        // and a device that wants the number 3 must not be sent "3".
        dot.dataset.input = String(src.value);
        dot.dataset.output = String(dest.value);
        dot.dataset.sourceIdx = String(i);
        dot.dataset.destIdx = String(o);
        // What the device calls this source, which is what a report is matched
        // against. Usually identical to data-input (see sourceReports).
        dot.dataset.reports = String(sourceReports(src));
        cell.addEventListener('pointerdown', (e) => this.startGesture(e, cell, i, o));
        cell.appendChild(dot);
        return cell;
    }

    /** Which name each numbered column is, under the grid where it fits. */
    legend() {
        const legend = div('matrix-legend');
        for (let i = 0; i < this.sources.length; i++) {
            const item = document.createElement('span');
            item.className = 'matrix-legend-item';
            item.dataset.inputIdx = String(i);
            const num = document.createElement('span');
            num.className = 'matrix-legend-num';
            num.textContent = String(i + 1);
            const name = document.createElement('span');
            name.dataset.labelText = '';
            name.textContent = this.label(this.sources[i]);
            item.appendChild(num);
            item.appendChild(name);
            legend.appendChild(item);
        }
        return legend;
    }

    startGesture(e, cell, i, o) {
        if (this.isLocked(o)) return;
        const gesture = new RouteGesture(this, e, cell, o);
        this.drag.startInput = i;
        gesture.listen();
    }

    /** Route the source in column `srcIdx` to the destination in row `destIdx`, unless that row is locked. */
    routeCell(srcIdx, destIdx) {
        if (!Number.isInteger(destIdx) || this.isLocked(destIdx)) return;
        const from = this.sources[srcIdx], to = this.destinations[destIdx];
        if (!from || !to) return;
        this.sendRoute(from.value, to.value);
    }

    dropLine() {
        if (this.drag.line) { this.drag.line.remove(); this.drag.line = null; }
    }

    updateRoutes(routes) {
        this.el.querySelectorAll('.matrix-crosspoint').forEach(dot => {
            // data-reports, not data-input: what the device CALLS this source,
            // the same thing on all but a couple of drivers and the only thing
            // that can be compared on those.
            const isActive = routeMatches(
                dot.dataset.reports, routes[parseInt(dot.dataset.destIdx)]);
            dot.style.backgroundColor = isActive ? this.activeColor : this.inactiveColor;
            dot.classList.toggle('active', isActive);
            const what = `${dot.dataset.input} to ${dot.dataset.output}`;
            dot.setAttribute('aria-label',
                isActive ? `Active: ${what}` : `Inactive: ${what}`);
        });
        // A row whose device reports a source this matrix does not list lights
        // no dot, which is what a row routed to NOTHING looks like too. Saying
        // which one it is takes a word.
        this.el.querySelectorAll('.matrix-unlisted').forEach(node => {
            const routed = routeState(
                this.sources, routes[parseInt(node.dataset.unlistedIdx)]);
            node.hidden = routed.state !== 'unlisted';
            if (routed.state === 'unlisted') {
                node.textContent = routed.raw;
                node.title = `${routed.raw} is not one of this matrix's sources`;
            }
        });
    }
}

function toggleColumnHeader(text) {
    const header = div('matrix-header');
    header.textContent = text;
    header.style.fontSize = '0.7143em';
    return header;
}

function toggleCell(button) {
    const cell = div('matrix-cell matrix-toggle');
    cell.appendChild(button);
    return cell;
}

/**
 * One pointer sequence on a crosspoint, and the ONE route it sends:
 *
 *   tap      -> route this cell
 *   drag     -> route the cell released over
 *   scrolled -> nothing; the finger was moving the grid
 *
 * A cell with a click handler as well as a drag would send a tap twice, and
 * routing on any drag would route on the drag that SCROLLS an oversized grid,
 * which is the very gesture a matrix bigger than its box forces on you.
 */
class RouteGesture {
    constructor(matrix, e, cell, o) {
        this.matrix = matrix;
        this.o = o;
        this.startX = e.clientX;
        this.startY = e.clientY;
        this.startLeft = matrix.scrollWrap.scrollLeft;
        this.startTop = matrix.scrollWrap.scrollTop;
        const rect = cell.getBoundingClientRect();
        this.originX = rect.left + rect.width / 2;
        this.originY = rect.top + rect.height / 2;
        this.dragging = false;
        this.onMove = (me) => this.move(me);
        this.onUp = (ue) => this.finish(ue, false);
        this.onCancel = () => this.finish(null, true);
    }

    listen() {
        document.addEventListener('pointermove', this.onMove);
        document.addEventListener('pointerup', this.onUp);
        document.addEventListener('pointercancel', this.onCancel);
        this.matrix.el._matrixDragCleanup = () => this.finish(null, true);
    }

    /** The grid moved under the finger, so the gesture was a scroll wherever it ends. */
    scrolled() {
        const wrap = this.matrix.scrollWrap;
        return wrap.scrollLeft !== this.startLeft || wrap.scrollTop !== this.startTop;
    }

    move(me) {
        if (this.scrolled()) { this.matrix.dropLine(); this.dragging = false; return; }
        const dx = me.clientX - this.startX, dy = me.clientY - this.startY;
        if (!this.dragging && Math.hypot(dx, dy) < MATRIX_DRAG_THRESHOLD_PX) return;
        this.dragging = true;
        this.drawLine(me);
    }

    drawLine(me) {
        const drag = this.matrix.drag;
        if (!drag.line) {
            drag.line = document.createElement('div');
            drag.line.className = 'matrix-drag-line';
            drag.line.style.cssText = `
                position: fixed; pointer-events: none; z-index: 999;
                height: 2px; width: 0;
                background: ${this.matrix.activeColor};
                border-radius: 1px;
                transform-origin: 0 0;
                left: ${this.originX}px;
                top: ${this.originY}px;
            `;
            document.body.appendChild(drag.line);
        }
        const lx = me.clientX - this.originX, ly = me.clientY - this.originY;
        drag.line.style.width = Math.hypot(lx, ly) + 'px';
        drag.line.style.transform = `rotate(${Math.atan2(ly, lx) * 180 / Math.PI}deg)`;
    }

    finish(ue, cancelled) {
        const matrix = this.matrix;
        document.removeEventListener('pointermove', this.onMove);
        document.removeEventListener('pointerup', this.onUp);
        document.removeEventListener('pointercancel', this.onCancel);
        matrix.el._matrixDragCleanup = null;
        matrix.dropLine();
        const wasDragging = this.dragging;
        // Row and column indices, not port numbers -- index 0 is a real
        // source, so every guard below asks whether it is a number rather
        // than whether it is truthy.
        const srcIdx = matrix.drag.startInput;
        this.dragging = false;
        matrix.drag.startInput = null;
        // A cancelled pointer is the browser saying it took this gesture over
        // to scroll with; a moved scrollbar says the same thing after the fact.
        if (cancelled || this.scrolled()) return;
        if (srcIdx === null) return;
        matrix.routeCell(srcIdx, wasDragging ? releasedOver(ue) : this.o);
    }
}

/** The destination row of the crosspoint a drag was released over, or null. */
function releasedOver(ue) {
    const cp = crosspointAt(document.elementFromPoint(ue.clientX, ue.clientY));
    if (!cp || cp.dataset.destIdx === undefined) return null;
    return parseInt(cp.dataset.destIdx);
}

/** The crosspoint at a node: the dot itself, or the one in the cell around it. */
function crosspointAt(target) {
    if (!target || !target.closest) return null;
    return target.closest('.matrix-crosspoint')
        || target.closest('.matrix-cell')?.querySelector('.matrix-crosspoint');
}

const STYLES = new Map([['list', ListMatrix], ['tiles', TileMatrix]]);
