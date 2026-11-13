/**
 * The macro runs this panel can see, and which of them are its own.
 *
 * A running macro is a fact about the room rather than about one screen, so
 * every panel sees every run: the busy state on a button whose press runs it
 * and its step on a progress label are drawn for all of them. A failure is
 * different. A band in a space where nobody touched anything is about somebody
 * else's press, and the only panel that can act on it is the one it was made
 * from. There is nothing on the wire that says who started a macro, and
 * nothing needs to be -- the panel that sent it already knows. So a press
 * CLAIMS every macro its control can run (`claimFrom`, asked by the panel's
 * send()), and a failing step is said only inside a claimed run, once.
 *
 * Nothing on the wire says which run a frame belongs to either. Counting the
 * starts is what tells them apart without a run id: every run announces
 * itself on the same socket, so what is in flight is knowable even though
 * which-is-which is not.
 *
 * Keyed by macro id, which is whatever the author named it: no prototype, so
 * an id like `constructor` reads as not running and `__proto__` is an
 * ordinary key rather than Object.prototype.
 *
 * The panel lends `host`: `elementMap()` and `bindings()`, read at call time
 * (both are rebuilt with the page); `recordSource(node, path)`, where a
 * progress label's words came from for the in-place editor; `showFailure(text)`
 * and `revert()`, which say a failure and put the control last touched back.
 */

// How long a macro this panel started stays this panel's to report a failure
// for, when nothing ever tells us its run ended. Matches the macro engine's own
// ceiling for a queued start, which is the longest a press can legitimately sit
// before it runs.
const MACRO_CLAIM_MAX_AGE_MS = 5 * 60 * 1000;

/** Every macro id a binding tree can run, as strings, wherever it sits in the tree. */
export function macrosReachableFrom(node, found = new Set()) {
    if (Array.isArray(node)) {
        for (const entry of node) macrosReachableFrom(entry, found);
    } else if (node && typeof node === 'object') {
        macrosInObject(node, found);
    }
    return found;
}

function macrosInObject(node, found) {
    if (node.action === 'macro' && node.macro) found.add(String(node.macro));
    for (const value of Object.values(node)) {
        if (value && typeof value === 'object') macrosReachableFrom(value, found);
    }
}

/** Whether a control's PRESS runs this macro: the busy state reads the press alone. */
function pressRuns(elementDef, macroId) {
    const pressActions = elementDef?.bindings?.do?.press;
    if (!pressActions) return false;
    const actions = Array.isArray(pressActions) ? pressActions : [pressActions];
    return actions.some(a => a.action === 'macro' && a.macro === macroId);
}

/**
 * What a progress label says for a run: its step's description, else "Step N
 * of M"; null while there is nothing to show (no run, or one whose steps are
 * not known yet), never "of 0".
 */
function progressText(running) {
    if (!running) return null;
    if (running.description) return running.description;
    return running.total_steps > 0 ? `Step ${running.step_index + 1} of ${running.total_steps}` : null;
}

/** The macros a failure happened inside: its whole call chain, else the macro its step belongs to. */
function failureChain(msg) {
    return Array.isArray(msg.call_chain) && msg.call_chain.length ? msg.call_chain : [msg.macro_id];
}

/** The runs in flight, the claims this panel holds, and what each run draws (held as `app.macroRuns`). */
export class MacroRuns {
    constructor(host) {
        this._host = host;
        this.running = Object.create(null);  // macro_id -> { description, step_index, total_steps }
        this._claims = Object.create(null);  // macro_id -> { at, reported, running } for macros THIS panel started
        this._runs = Object.create(null);    // macro_id -> how many runs of it are in flight (see ended)
    }

    /**
     * Remember that a macro was started FROM THIS PANEL, from a frame it sends.
     *
     * Both doors go through send(), so this is one hook rather than one per
     * interaction: a preset and a custom control send `macro.execute`
     * themselves, and a binding sends `ui.<interaction>` and lets the server
     * look up what that control runs.
     */
    claimFrom(msg) {
        for (const macroId of this._macrosClaimedBy(msg)) this._claim(macroId);
    }

    /** The macros a frame claims: the one it executes, or every one the control it presses can run. */
    _macrosClaimedBy(msg) {
        if (!msg || typeof msg.type !== 'string') return [];
        if (msg.type === 'macro.execute') return msg.macro_id ? [String(msg.macro_id)] : [];
        return msg.type.startsWith('ui.') ? this._controlMacros(msg.element_id) : [];
    }

    /**
     * Every macro the control a press landed on can run, not just the one this
     * interaction would have reached: a finger landed on it, so a failure from
     * any macro it runs is that person's. Which slot fired is the server's
     * business and it is not worth a second copy of the dispatch rules here --
     * an off_action, a hold and a value_map entry are all reachable and all
     * theirs.
     *
     * matrix_config as well as the do bindings, because a destination row may
     * carry its OWN route action list that runs instead of the element's.
     * Nothing under `show` is walked: a label bound to a macro's progress is
     * WATCHING it, which is not the same as having started it.
     */
    _controlMacros(elementId) {
        const def = elementId ? this._host.elementMap()[elementId]?.elementDef : null;
        return def ? macrosReachableFrom([def.bindings?.do, def.matrix_config]) : [];
    }

    _claim(macroId) {
        // A fresh press re-arms the report: pressing again after a failure is
        // somebody asking again, and silence would read as having fixed it.
        this._claims[macroId] = { at: Date.now(), reported: false, running: false };
    }

    /** `macro.started`: one more run in flight, busy and on its first step. */
    started(msg) {
        this._runs[msg.macro_id] = (this._runs[msg.macro_id] || 0) + 1;
        // A claim made before this frame is a press waiting for its
        // run; now it has one. Until it does, an ending run belongs to
        // somebody else and must not take the claim with it.
        if (this._claims[msg.macro_id]) {
            this._claims[msg.macro_id].running = true;
        }
        this.running[msg.macro_id] = {
            description: '',
            step_index: 0,
            total_steps: msg.total_steps || 0,
        };
        this.showBusy(msg.macro_id);
        this.showProgress(msg.macro_id);
    }

    /** `macro.progress`: the step a run is on. */
    progress(msg) {
        // Only a step's own message says which step is running. A Wait
        // Until's waiting / satisfied / timeout, a group command's result
        // and a condition's answer carry none of it, and overwriting
        // from them read "Step 1 of 0" for as long as the wait lasted.
        const run = this.running[msg.macro_id];
        if (run) {
            if ('description' in msg) run.description = msg.description || '';
            if (typeof msg.step_index === 'number') run.step_index = msg.step_index;
            if (typeof msg.total_steps === 'number') run.total_steps = msg.total_steps;
        }
        this.showProgress(msg.macro_id);
    }

    /**
     * `macro.completed`, `macro.error` or `macro.cancelled`: one run of a
     * macro ended. Forget it only when they all have.
     *
     * Keyed by macro id alone, the first ending run cleared the busy state
     * and, worse, threw away the claim: press "System On" while a schedule is
     * already running it, that run finishes, and the failure of the run
     * somebody is standing there waiting for arrives to a panel that has
     * forgotten it ever asked. Silence, which is what this whole surface
     * exists to end. A start that never happens -- the macro's own overlap
     * guard refused it -- leaves the claim to age out, the case
     * MACRO_CLAIM_MAX_AGE_MS was always for.
     */
    ended(msg) {
        this._endRun(msg.macro_id);
        this.showBusy(msg.macro_id);
        this.showProgress(msg.macro_id);
    }

    _endRun(macroId) {
        const left = Math.max(0, (this._runs[macroId] || 0) - 1);
        if (left) {
            this._runs[macroId] = left;
            return;
        }
        delete this._runs[macroId];
        delete this.running[macroId];
        // Only a claim that has seen its run start. One made a moment ago is a
        // press whose run has not been announced yet, and the frame that just
        // arrived is somebody else's.
        const claim = this._claims[macroId];
        if (claim && claim.running) delete this._claims[macroId];
    }

    /**
     * `macro.step_error`: a step of a macro this panel started failed. Say
     * so, once.
     *
     * ONE message per run, the first: a macro that cannot reach a device on
     * step 2 usually cannot reach it on step 3 either, and three sentences
     * swapping through one band in a tenth of a second is unreadable. The
     * first names the thing to go and fix.
     */
    stepFailed(msg) {
        const claims = this._liveClaims(failureChain(msg));
        if (!claims.length || claims.some(claim => claim.reported)) return;
        for (const claim of claims) claim.reported = true;
        this._host.showFailure(msg.message || msg.error);
        // A step that never reached its device is the same failure as a refused
        // command, arriving on a different frame: the fader that ran the macro
        // is still showing the number somebody dragged it to. Nothing on a
        // macro frame says which control started it, so this takes the
        // last-touched fallback -- which is what claimed the macro in the first
        // place.
        this._host.revert();
    }

    /**
     * The claims this panel holds on the macros a failure happened INSIDE, not
     * just the macro the step belongs to: press "System On", which calls
     * "Projector On", and the frame names the sub-macro nobody at the panel
     * has heard of.
     */
    _liveClaims(chain) {
        const claims = [];
        for (const macroId of chain) {
            const claim = this._claims[macroId];
            if (!claim) continue;
            // A claim is normally cleared when its run ends. This covers the
            // one that never sees an end -- the start was throttled, or the
            // socket dropped mid-run -- so a schedule firing the same macro
            // hours later cannot draw a message nobody asked for.
            if (Date.now() - claim.at > MACRO_CLAIM_MAX_AGE_MS) {
                delete this._claims[macroId];
                continue;
            }
            claims.push(claim);
        }
        return claims;
    }

    /** Busy on the buttons whose PRESS runs this macro, while any run of it is in flight. */
    showBusy(macroId) {
        for (const entry of Object.values(this._host.elementMap())) {
            if (!pressRuns(entry.elementDef, macroId)) continue;
            if (macroId in this.running) {
                entry.el.classList.add('macro-busy');
                entry.el.setAttribute('data-macro-busy', macroId);
            } else {
                entry.el.classList.remove('macro-busy');
                entry.el.removeAttribute('data-macro-busy');
            }
        }
    }

    /** The step a run is on, on every label showing this macro's progress (`source: "macro_progress"`). */
    showProgress(macroId) {
        for (const b of this._host.bindings()) {
            if (b.type !== 'macro_progress' || b.binding.macro !== macroId) continue;
            const text = progressText(this.running[macroId]);
            if (text !== null) {
                b.element.textContent = text;
                // The step description comes from the macro, not the author.
                this._host.recordSource(b.element, null);
            } else {
                b.element.textContent = b.binding.idle_text || '';
                this._host.recordSource(b.element, ['bindings', 'show', 'value', 'idle_text']);
            }
        }
    }
}
