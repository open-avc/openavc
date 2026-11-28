/**
 * A press and a drag: how each ends however the browser delivers its end,
 * and which frame a press sends when it is pressed and when it is let go, a
 * button's in each of its four modes and a camera preset's, always a tap.
 *
 *   tap         -- `ui.press` at the press
 *   hold_repeat -- `ui.press` at the press and again every interval while held
 *   tap_hold    -- nothing at the press; at the release `ui.hold` if it was
 *                  held for the threshold, else `ui.press`
 *   toggle      -- `ui.toggle_off` while the toggle's own light would be lit
 *                  (modules/toggle.js `toggleLit`: its reading is its value
 *                  and its device answers), else `ui.press`; a toggle with
 *                  no key is a tap
 *
 * and `ui.release` at every release. The release cannot be missed: kiosk
 * WebViews and mobile browsers sometimes swallow the button's own touchend,
 * so the window's release and blur end the press too, and a press that is
 * let go twice sends one release.
 *
 * The rule worth knowing before changing anything here: the toggle's verdict
 * is copied on the server (`resolve_press` in core/ui_events.py), which
 * checks a toggle without pressing it, and tests/test_press_parity.py runs
 * both over one corpus, a device away and a device asleep included. A change
 * to `pressOf` or `frameAtPress` goes there too.
 *
 * The hold timers are the panel's (`holdTimers`): a page change, a dialog
 * closing, the socket dropping and the panel going to the background each
 * end them, so the panel lends `repeat` and `endHold` to every press.
 *
 * A drag (a slider, a fader, a routing matrix's crosspoint) ends on one set
 * of events, `DRAG_ENDS`: a pointer, a mouse or a touch let go, the browser
 * cancelling the pointer or the touch, or the window losing focus with the
 * finger or the mouse still down. A drag that missed its end would stay
 * held, following the next finger anywhere on the page. A crosspoint's drag
 * also follows only the finger that started it (`dragPointer`), so a second
 * finger or a palm neither moves it nor ends it.
 */

import { pressTiming } from './project_values.js';
import { toggleLit } from './toggle.js';

/** The ends of a drag that let it go where it is: a pointer, a mouse (a fader starts on its mousedown) or a touch lifted. */
const DRAG_LET_GO = ['pointerup', 'mouseup', 'touchend'];

/**
 * The ends of a drag that abandon it: the browser cancelling the pointer or
 * the touch (a system gesture, a notification pulled down) and the window
 * losing focus. None carries a point the drag was let go over.
 */
const DRAG_ABANDONED = ['pointercancel', 'touchcancel', 'blur'];

/** Every way a drag ends. A slider listens for them on its own range input, a fader and a crosspoint at the window. */
export const DRAG_ENDS = [...DRAG_LET_GO, ...DRAG_ABANDONED];

/**
 * End a drag at the first of its ends the window hears that `owns` says is
 * its own (every end, when it is not given), once: `onEnd(event, letGo)`,
 * `letGo` false for a cancel or a lost focus. Every listener comes off at
 * that end; the function returned takes them off for a drag something else
 * ended (its page redrawn).
 */
export function endDragAtTheWindow(onEnd, owns = () => true) {
    const end = (e) => {
        if (!owns(e)) return;
        stop();
        onEnd(e, DRAG_LET_GO.includes(e.type));
    };
    const stop = () => {
        for (const type of DRAG_ENDS) window.removeEventListener(type, end);
    };
    for (const type of DRAG_ENDS) window.addEventListener(type, end, { passive: true });
    return stop;
}

/**
 * The one finger, pen or mouse a drag follows: the one whose press, `down`
 * on `target`, started it. Another finger or a palm on the glass is not the
 * drag's, so its move draws nothing and its lift ends nothing. Told apart:
 *
 *   a pointer event -- by its pointerId;
 *   a touch event   -- by the drag's own touch, learnt from the first
 *                      touchstart `target` hears after a touch's press (a
 *                      browser sends a finger's pointerdown and then its
 *                      touchstart, and numbers the two apart); a lift is the
 *                      drag's when its own touch is no longer on the glass,
 *                      lifted now or lifted with no event of its own;
 *   a mouse event   -- the drag's only while its pointer is the primary one,
 *                      the only one a browser makes mouse events for.
 *
 * The window losing focus, and a touch event while the drag's own touch is
 * not known, are every drag's: nothing tells them apart, and a drag that let
 * one pass could miss its only end.
 *
 * `owns(e)` says whether an event is the drag's, `pointOf(e)` where the
 * drag's pointer was for it (a lifted touch carries its point in
 * changedTouches; null for a touch that lifted with no event of its own),
 * and `stop()` stops listening for the touch.
 */
export function dragPointer(down, target) {
    const touch = learnTouch(down, target);
    return {
        owns: (e) => ownsEvent(e, down, touch.id),
        pointOf(e) {
            if (!e.changedTouches) return e;
            return touch.id === null ? e.changedTouches[0] : (touchIn(e.changedTouches, touch.id) || null);
        },
        stop: () => touch.stop(),
    };
}

/** The touch a press made: its number (null while not known), learnt from the first touchstart `target` hears after a touch's press. */
function learnTouch(down, target) {
    const touch = { id: null, stop: () => {} };
    if (down.pointerType !== 'touch') return touch;
    const learn = (e) => {
        const own = Array.from(e.changedTouches || []).find((t) => target.contains(t.target));
        if (own) touch.id = own.identifier;
    };
    target.addEventListener('touchstart', learn, { once: true, passive: true });
    touch.stop = () => target.removeEventListener('touchstart', learn);
    return touch;
}

/** The touch numbered `id` among `touches`, or undefined. */
const touchIn = (touches, id) => Array.from(touches || []).find((t) => t.identifier === id);

/** Whether `e` is the drag's that `down` pressed, its own touch numbered `touchId`. */
function ownsEvent(e, down, touchId) {
    if (e.type.startsWith('pointer')) return e.pointerId === down.pointerId;
    if (e.type.startsWith('touch')) return ownsTouchEvent(e, touchId);
    if (e.type.startsWith('mouse')) return down.isPrimary !== false;
    return true;
}

/** A touch event is the drag's when its own touch is not known, lifted now, or no longer on the glass. */
function ownsTouchEvent(e, touchId) {
    if (touchId === null || touchIn(e.changedTouches, touchId)) return true;
    return !touchIn(e.touches, touchId);
}

/**
 * Controls whose press is a tap whatever their first action says: a camera
 * preset's editor offers actions and no mode. core/ui_events.py
 * `TAP_ONLY_TYPES` is the server's copy.
 */
const TAP_ONLY_TYPES = new Set(['camera_preset']);

/**
 * What a control's press binding asks for. The binding is a list of actions,
 * and its mode and the mode's settings come from the first; a press that is
 * one action rather than a list is read the same way. A blank hold or repeat
 * timing is the project schema's (modules/project_values.js `pressTiming`),
 * which the Builder's fields show.
 *
 * `toggleLook` is the binding a toggle's own indication draws from, with
 * where its two words are authored (`_labelBase`).
 */
export function pressOf(element) {
    const { pressBinding, isList } = firstAction(element);
    return {
        mode: TAP_ONLY_TYPES.has(element.type) ? 'tap' : modeOf(pressBinding),
        repeatMs: pressTiming(pressBinding.hold_repeat_ms, 'hold_repeat_ms'),
        thresholdMs: pressTiming(pressBinding.hold_threshold_ms, 'hold_threshold_ms'),
        // Read again at each press: the toggle compares what it says then.
        binding: pressBinding,
        toggleLook: toggleLookOf(pressBinding, isList),
    };
}

/** The first action of a press binding (`{}` when there is none), and whether the binding is a list. */
function firstAction(element) {
    const pressActions = element.bindings?.do?.press || [];
    const isList = Array.isArray(pressActions);
    return { pressBinding: (isList ? pressActions[0] : pressActions) || {}, isList };
}

/** A press's mode: its own, else a tap; a toggle with nothing to compare is a tap. */
function modeOf(pressBinding) {
    const mode = pressBinding.mode || 'tap';
    return (mode === 'toggle' && !pressBinding.toggle_key) ? 'tap' : mode;
}

/** The binding a toggle's own indication draws from. */
function toggleLookOf(pressBinding, isList) {
    return {
        key: pressBinding.toggle_key,
        value: pressBinding.toggle_value,
        on_label: pressBinding.on_label,
        off_label: pressBinding.off_label,
        _labelBase: isList ? ['bindings', 'do', 'press', 0] : ['bindings', 'do', 'press'],
    };
}

/**
 * Make `el` press the way `press` says, as the control `element` (its id is
 * read at each send). `host` lends the panel's send, its state, whether a
 * key's reading can be believed for this control (`readingOffline`), and its
 * hold timers.
 */
export function attachPress(el, element, press, host) {
    let pressTime = 0;
    let pressActive = false;

    const onRelease = (e) => {
        // The button's own release and the window's both come here; only the
        // first one for a press does anything.
        if (!pressActive) return;
        pressActive = false;
        cancelIfAllowed(e);
        el.classList.remove('pressing');
        sendAtRelease(press, element, Date.now() - pressTime, host);
    };
    const onPress = (e) => {
        e.preventDefault();
        el.classList.add('pressing');
        pressTime = Date.now();
        pressActive = true;
        endAtTheWindow(onRelease);
        sendAtPress(press, element, host);
    };

    el.addEventListener('mousedown', onPress);
    el.addEventListener('mouseup', onRelease);
    el.addEventListener('mouseleave', () => {
        // A hold ends when the pointer leaves; the press itself is ended by
        // the window's release.
        el.classList.remove('pressing');
        host.endHold(element.id);
    });
    el.style.touchAction = 'none';
    el.addEventListener('touchstart', onPress);
    el.addEventListener('touchend', onRelease);
    el.addEventListener('touchcancel', onRelease);
}

/** A release the browser lets the panel cancel is cancelled; a blur, or a
 *  touch the browser already scrolled with, is not. */
function cancelIfAllowed(e) {
    if (!e || !e.cancelable) return;
    if (e.preventDefault) e.preventDefault();
}

/**
 * The window's release, cancel and blur end the press wherever the browser
 * delivered them, once: each takes all four away again. A hold-repeat that
 * outlived the finger would send its press forever.
 */
function endAtTheWindow(onRelease) {
    const winEnd = (ev) => {
        winCleanup();
        onRelease(ev);
    };
    const winCleanup = () => {
        window.removeEventListener('mouseup', winEnd);
        window.removeEventListener('touchend', winEnd);
        window.removeEventListener('touchcancel', winEnd);
        window.removeEventListener('blur', winEnd);
    };
    window.addEventListener('mouseup', winEnd);
    window.addEventListener('touchend', winEnd, { passive: true });
    window.addEventListener('touchcancel', winEnd, { passive: true });
    window.addEventListener('blur', winEnd);
}

/** The frame a press of `element` sends. */
const frame = (type, element) => ({ type, element_id: element.id });

/**
 * The frame a press sends at once, reading state through `read`: `ui.press`
 * for a tap and a hold-repeat, null for a tap-or-hold (decided at the
 * release) or a mode nobody knows, and for a toggle `ui.toggle_off` while it
 * reads as on the way its light does (`toggleLit`), else `ui.press`.
 * `offline(key)` says the key's reading cannot be believed (its device is
 * away), so a toggle drawn off presses on whatever its device said last.
 */
export function frameAtPress(press, read, offline = () => false) {
    if (press.mode === 'tap' || press.mode === 'hold_repeat') return 'ui.press';
    if (press.mode !== 'toggle') return null;
    const { toggle_key: key, toggle_value: value } = press.binding;
    return toggleLit({ value }, read(key), offline(key)) ? 'ui.toggle_off' : 'ui.press';
}

/** What the press sends, and a hold-repeat's timer started after it. */
function sendAtPress(press, element, host) {
    const type = frameAtPress(press, host.read, (key) => host.readingOffline(element, key));
    if (type) host.send(frame(type, element));
    if (press.mode === 'hold_repeat') {
        host.repeat(element.id, press.repeatMs, () => host.send(frame('ui.press', element)));
    }
}

/** What the release sends after `heldMs`: the hold ended or decided, then the release. */
function sendAtRelease(press, element, heldMs, host) {
    if (press.mode === 'hold_repeat') {
        host.endHold(element.id);
    } else if (press.mode === 'tap_hold') {
        host.send(frame(heldMs >= press.thresholdMs ? 'ui.hold' : 'ui.press', element));
    }
    host.send(frame('ui.release', element));
}
