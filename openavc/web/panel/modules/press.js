/**
 * A button's press: which frame it sends when it is pressed and when it is
 * let go, in each of its four modes.
 *
 *   tap         -- `ui.press` at the press
 *   hold_repeat -- `ui.press` at the press and again every interval while held
 *   tap_hold    -- nothing at the press; at the release `ui.hold` if it was
 *                  held for the threshold, else `ui.press`
 *   toggle      -- `ui.toggle_off` while the thing it toggles reads as on,
 *                  else `ui.press`; a toggle with no key is a tap
 *
 * and `ui.release` at every release. The release cannot be missed: kiosk
 * WebViews and mobile browsers sometimes swallow the button's own touchend,
 * so the window's release and blur end the press too, and a press that is
 * let go twice sends one release.
 *
 * The rule worth knowing before changing anything here: the toggle's verdict
 * is copied on the server (`resolve_press` in core/ui_events.py), which
 * checks a toggle without pressing it, and tests/test_press_parity.py runs
 * both over one corpus. A change to `pressOf` or `frameAtPress` goes there too.
 *
 * The hold timers are the panel's (`holdTimers`): a page change, a dialog
 * closing, the socket dropping and the panel going to the background each
 * end them, so the panel lends `repeat` and `endHold` to every press.
 */

import { sameWords } from './looks.js';

/**
 * What a button's press binding asks for. The binding is a list of actions,
 * and its mode and the mode's settings come from the first; a press that is
 * one action rather than a list is read the same way.
 *
 * `toggleLook` is the binding a toggle's own indication draws from, with
 * where its two words are authored (`_labelBase`).
 */
export function pressOf(element) {
    const { pressBinding, isList } = firstAction(element);
    return {
        mode: modeOf(pressBinding),
        repeatMs: pressBinding.hold_repeat_ms || 200,
        thresholdMs: pressBinding.hold_threshold_ms || 500,
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
 * Whether a press of a toggle turns it off: the reading and the toggle's
 * value compared as words apart from case, so a driver's boolean `true`
 * matches a `toggle_value` of "true". A key nobody has reported, or a toggle
 * with no value, reads as off; a reading of null is compared as the word
 * "null".
 */
function toggleIsOn(stateValue, toggleValue) {
    return stateValue !== undefined && toggleValue !== undefined && sameWords(stateValue, toggleValue);
}

/**
 * Make `el` press the way `press` says, as the control `element` (its id is
 * read at each send). `host` lends the panel's send, its state, and its hold
 * timers.
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
 * for a tap and a hold-repeat, `ui.toggle_off` or `ui.press` for a toggle,
 * and null for a tap-or-hold (decided at the release) or a mode nobody knows.
 */
export function frameAtPress(press, read) {
    if (press.mode === 'tap' || press.mode === 'hold_repeat') return 'ui.press';
    if (press.mode !== 'toggle') return null;
    const { toggle_key: key, toggle_value: value } = press.binding;
    return toggleIsOn(read(key), value) ? 'ui.toggle_off' : 'ui.press';
}

/** What the press sends, and a hold-repeat's timer started after it. */
function sendAtPress(press, element, host) {
    const type = frameAtPress(press, host.read);
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
