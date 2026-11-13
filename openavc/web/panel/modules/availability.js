/**
 * Whether what a control shows can be believed, and whether pressing it can
 * land: the device a state key reports from, the devices a binding reads, the
 * unreachable and asleep rule, the two marks that say so on a control, and
 * which bindings a change of state reaches.
 *
 * The facts are `device.<id>.connected` (strictly `false` is down) and
 * `device.<id>.asleep` (strictly `true`); `devicesFlipped` watches the same
 * two keys, so a new reachability fact is added here, in all three places.
 *
 * The two marks, and why they are not the same mark:
 *
 * - A `show` binding on an unreachable device means "this number is not
 *   worth believing": the value is masked and the element is drained of
 *   colour (`device-offline`), because a confident-looking reading is the lie.
 *   A reading is spoiled by ONE bad source, so it marks on any.
 * - A `do` action on an unreachable device means "pressing this will be
 *   refused": the label, the colour and the artwork are all still correct,
 *   and only the availability is not. It gets the same dashed edge
 *   (`action-offline`) and keeps everything else, which is what stops a page
 *   going grey because one device is rebooting. An action still does
 *   something as long as one target is there -- a System On macro touching
 *   eight devices is not unavailable because a display is off -- so it marks
 *   only when NOTHING it can reach is up.
 *
 * NEVER in the design canvas. Everything here is a RUNTIME condition, not a
 * property of the page being drawn, and the canvas is fed the IDE's live
 * state -- so on a bench where the gear is not plugged in yet, every bound
 * control would draw dimmed and dashed and the author could not see the
 * colours, the handle or the artwork they are placing. The Preview button is
 * the honest answer for "what does the room see": it runs the same renderer
 * over a real WebSocket.
 *
 * `action_devices` and `wake_devices` are resolved on the server
 * (`ui/action_devices.py`); following a macro into a group is not something
 * the renderer can do. Preview draws the Builder's copy of the page, which
 * never carries them, so there they come from the server's own definition,
 * matched by element id (`takeServed`): they follow an edit once the Builder
 * saves it.
 *
 * The panel lends what it holds (`host`), each read at call time: its state
 * object is replaced wholesale by a snapshot, and its element map by a redraw.
 */

// The `device.<id>.*` properties the PLATFORM maintains rather than the device
// reports. They describe the device -- whether it is reachable, what it is
// called, whether it is enabled -- so they stay true while it is unreachable
// and must keep rendering. `connected` above all: an LED bound to it IS the
// offline report, and blanking that would hide the one honest thing on the
// page. Everything else under `device.` came from the far end of a wire that
// is no longer there. Kept in step with what core/device_manager.py writes.
const DEVICE_PLATFORM_PROPS = new Set([
    'connected', 'enabled', 'name', 'offline_detail', 'offline_reason',
    'orphan_reason', 'orphaned', 'paused', 'paused_for', 'reconnect_attempt',
    'reconnect_failed', 'restarting', 'asleep', 'wake_command',
]);

// The same set, for a child entity's reserved props. A child carries the
// platform's own account of itself under `device.<id>.<type>.<local>.<prop>`,
// and those four keep telling the truth while the parent is away exactly as
// the device's own pair does -- so a label bound to one must keep rendering.
//
// Until this existed the split rule was accidental rather than decided: the
// device's `offline_reason` is three segments and was exempt, its child's is
// five and was not, so a panel bound to a child's fault drew `--` at precisely
// the moment the child had something to say.
const CHILD_PLATFORM_PROPS = new Set([
    'online', 'offline_reason', 'offline_detail', 'label',
]);

/**
 * The device a state key reports FROM, or null when it names none.
 *
 * `device.<id>.<prop>` is the whole rule, and a child key carries the same
 * second segment (`device.<id>.<type>.<local>.<prop>`), so one split covers
 * both. A key naming one of the platform-maintained props is deliberately
 * not attributed to the device: those keep telling the truth when it is
 * gone, and a panel bound to them is reporting the fault, not hiding it.
 */
export function deviceIdForKey(key) {
    if (typeof key !== 'string' || !key.startsWith('device.')) return null;
    const parts = key.split('.');
    if (parts.length < 3 || namesPlatformProp(parts)) return null;
    return parts[1] || null;
}

/** Whether a split `device.` key names one of the platform's own props, a device's or a child's. */
function namesPlatformProp(parts) {
    if (parts.length === 3) return DEVICE_PLATFORM_PROPS.has(parts[2]);
    // `device.<id>.<type>.<local>.<prop>` — the child's own reserved props
    // are the platform's account of the child, not a reading from the far
    // end of the wire, so they are exempt for the same reason.
    if (parts.length === 5) return CHILD_PLATFORM_PROPS.has(parts[4]);
    return false;
}

/**
 * The four fields a binding names state by, read here for its devices and in
 * `changeReaches` for a change: a whole key, a visible_when's keys, a matrix's
 * concrete keys, a list's pattern. A new one is added in both.
 */
function keysNamed(binding) {
    const keys = [];
    if (binding.key) keys.push(binding.key);
    if (Array.isArray(binding._keys)) keys.push(...binding._keys);
    if (Array.isArray(binding._patterns)) keys.push(...binding._patterns);
    if (binding.key_pattern) keys.push(binding.key_pattern);
    return keys;
}

/** Every device a binding reads from, or null. Cached on the binding: bindings are rebuilt with the page. */
export function bindingDeviceIds(b) {
    if (b._deviceIds !== undefined) return b._deviceIds;
    const ids = new Set(keysNamed(b.binding || {}).map(deviceIdForKey).filter(Boolean));
    b._deviceIds = ids.size ? [...ids] : null;
    return b._deviceIds;
}

/**
 * The devices whose reachability a change of state moved, or null for none:
 * a device's `connected`, and its own `asleep` (three segments, not a
 * child's), which lands just after `connected` drops and decides whether a
 * wake control's reading still counts.
 */
export function devicesFlipped(changedKeys) {
    let flipped = null;
    for (const k of changedKeys || []) {
        const id = reachabilityOf(k);
        if (id !== null) (flipped || (flipped = new Set())).add(id);
    }
    return flipped;
}

/** The device whose reachability a changed key reports, or null when it reports none. */
function reachabilityOf(key) {
    if (typeof key !== 'string' || !key.startsWith('device.')) return null;
    const parts = key.split('.');
    if (key.endsWith('.connected')) return parts[1];
    return key.endsWith('.asleep') && parts.length === 3 ? parts[1] : null;
}

/**
 * Whether a change of state reaches a binding: a device it reads from came
 * or went (`flipped`, from devicesFlipped), so the value is the same and what
 * it is worth has changed; or one of the keys it reads moved.
 */
export function changeReaches(b, changedKeys, flipped) {
    const ids = flipped && bindingDeviceIds(b);
    if (ids && ids.some((id) => flipped.has(id))) return true;
    // Each kind of field the binding carries has to be hit; one naming no key
    // at all is drawn anyway.
    return wholeKeysHit(b.binding, changedKeys) && patternsHit(b.binding, changedKeys);
}

/** A key read whole moved: any of a visible_when's keys (which stand in for a key), else its key. */
function wholeKeysHit(binding, changedKeys) {
    const keys = binding?._keys;
    if (keys) return keys.some((k) => changedKeys.includes(k));
    const key = binding?.key;
    return !key || changedKeys.includes(key);
}

/**
 * A key under a pattern moved: a matrix's concrete keys, else a list's
 * pattern, each matched on its prefix before the first `*` (a concrete key is
 * its own prefix).
 */
function patternsHit(binding, changedKeys) {
    const patterns = binding?._patterns;
    if (patterns) return patterns.some((p) => prefixHit(p, changedKeys));
    const pattern = binding?.key_pattern;
    return !pattern || prefixHit(pattern, changedKeys);
}

function prefixHit(pattern, changedKeys) {
    const prefix = pattern.replace(/\*.*$/, '');
    return changedKeys.some((k) => k.startsWith(prefix));
}

/** Every page element of a resolved definition, then its master elements. */
function definitionElements(ui) {
    const pages = Array.isArray(ui?.pages) ? ui.pages : [];
    const masters = Array.isArray(ui?.master_elements) ? ui.master_elements : [];
    return pages.flatMap(p => (Array.isArray(p?.elements) ? p.elements : [])).concat(masters);
}

/** Element id -> the devices its press reaches (`key`), from a resolved definition. */
function devicesById(ui, key) {
    const byId = new Map();
    for (const element of definitionElements(ui)) {
        if (element?.id && Array.isArray(element[key])) byId.set(element.id, element[key]);
    }
    return byId;
}

/**
 * The room as the panel's controls draw it (held as `app.availability`).
 *
 * `host`: `state()`, `editMode()` and `elementMap()`, each read at call time.
 */
export class Availability {
    constructor(host) {
        this._host = host;
        // Preview only: element_id -> the devices its press reaches, from the
        // server's ui.definition (the Builder's copy never carries them).
        this.servedActionDevices = null;
        // Preview only: element_id -> the devices its press reaches through a
        // command that works while they are offline (`wake_devices`).
        this.servedWakeDevices = null;
    }

    /** Preview's copy of what each press reaches, from the server's definition. */
    takeServed(ui) {
        this.servedActionDevices = devicesById(ui, 'action_devices');
        this.servedWakeDevices = devicesById(ui, 'wake_devices');
    }

    /**
     * Whether a device is known to be unreachable right now.
     *
     * Strictly `=== false`: an absent `connected` key means nobody has said
     * either way -- a panel drawn before its first snapshot -- and that must
     * render normally rather than as a page full of dead controls.
     */
    deviceOffline(deviceId) {
        if (this._host.editMode()) return false;
        return this._host.state()[`device.${deviceId}.connected`] === false;
    }

    /** True while the device is asleep on purpose (never in the design canvas). */
    deviceAsleep(deviceId) {
        if (this._host.editMode()) return false;
        return this._host.state()[`device.${deviceId}.asleep`] === true;
    }

    /** True when a key's device is known to be unreachable right now. */
    keyOffline(key) {
        const id = deviceIdForKey(key);
        return id ? this.deviceOffline(id) : false;
    }

    /** The devices an element's press reaches through a command that works offline. */
    wakeDevices(elementDef) {
        const list = this.servedWakeDevices
            ? this.servedWakeDevices.get(elementDef?.id)
            : elementDef?.wake_devices;
        return Array.isArray(list) ? list : [];
    }

    /**
     * Whether this binding's reading can be believed.
     *
     * One exception: a device put to sleep on purpose (`device.<id>.asleep`)
     * keeps the readings of a control whose press wakes it. That press goes
     * out with the device off the network (`wake_devices`, resolved on the
     * server from the drivers' `available_offline`), and its feedback is the
     * true "off", so the Power On button stays live while every other control
     * bound to the device greys out.
     */
    readingOffline(b) {
        const ids = bindingDeviceIds(b);
        if (!ids) return false;
        const wakes = this.wakeDevices(b.elementDef);
        return ids.some(id => this.deviceOffline(id) && !(wakes.includes(id) && this.deviceAsleep(id)));
    }

    /**
     * Mark the element a binding draws into as unavailable, or clear it, and
     * say which. Judged from the binding unless `offline` is given (a
     * renderer that remembers its last reading judges first, then marks).
     *
     * Tallied per element rather than toggled, because one element can carry
     * several bindings (a select's value and its look) which may name
     * different devices -- the control is unavailable while ANY of them is.
     */
    markReading(b, offline = this.readingOffline(b)) {
        const host = this._host.elementMap()[b.elementDef?.id]?.el || b.element;
        if (!host || !host.classList) return offline;
        const tally = host._offlineBindings || (host._offlineBindings = new Set());
        if (offline) tally.add(b); else tally.delete(b);
        host.classList.toggle('device-offline', tally.size > 0);
        return offline;
    }

    /**
     * Mark controls whose ACTIONS cannot land. Not a binding: an action-only
     * button has none, which is exactly why it used to carry no mark at all.
     */
    markActions() {
        for (const [id, entry] of Object.entries(this._host.elementMap())) {
            const el = entry?.el;
            if (el && el.classList) this._markAction(el, this._actionDevices(id, entry));
        }
    }

    /** The devices a control's press reaches: Preview's served copy, else its own definition's. */
    _actionDevices(id, entry) {
        return this.servedActionDevices
            ? this.servedActionDevices.get(id)
            : entry?.elementDef?.action_devices;
    }

    _markAction(el, devices) {
        if (!Array.isArray(devices) || !devices.length) {
            // Preview re-reads this without a re-render when the server's
            // definition moves, so a control that now reaches nothing has
            // to lose a mark it was drawn with.
            el.classList.remove('action-offline');
            return;
        }
        const stranded = devices.every(id => this.deviceOffline(id));
        // Never both: an element already carrying the reading mark has
        // been drained, and adding a second dashed edge over it would draw
        // the border twice.
        el.classList.toggle(
            'action-offline',
            stranded && !el.classList.contains('device-offline'),
        );
    }

    /**
     * The reading mark, one destination at a time. A matrix is the one
     * control that can be half true, so it is marked per row rather than per
     * element: every node of a destination's row takes the mark.
     */
    markMatrixRows(matrix, destOffline) {
        for (const [index, nodes] of matrix.destinationRows()) {
            const offline = !!destOffline[index];
            for (const node of nodes) node.classList.toggle('device-offline', offline);
        }
    }
}
