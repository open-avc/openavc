/**
 * The PIN lock and the idle timer.
 *
 * A panel whose project sets a PIN shows the lock screen once a session, and
 * again whenever it has sat idle; the idle timer sends a panel nobody is
 * touching back to its idle page. The two share one rule: a locked panel never
 * runs the idle timer, and unlocking starts it.
 *
 * Who holds the PIN decides how an attempt is checked. Every real panel gets
 * `lock_enabled` and no code -- a panel client is unauthenticated, so the
 * definition it receives is public to the LAN -- and the attempt goes to the
 * server, which answers only the verdict. `lock_code` is only ever present
 * when the Programmer pushed an UNSAVED draft into its Preview iframe, an
 * authenticated surface, and there the attempt is compared locally: otherwise
 * changing the PIN and pressing Preview would test the last SAVED one and tell
 * the programmer their new PIN is wrong.
 *
 * The design canvas never locks and never goes idle. The panel lends what this
 * reads at call time (its settings, pages, edit mode, whether it is offline),
 * where activity goes (its `resetIdleTimer`, which tests replace) and how it
 * goes back to a page.
 */

import { basePath } from './base_path.js';

// The timeout is what turns a hung request into "could not ask" instead of a
// lock screen whose Unlock button never comes back.
const UNLOCK_TIMEOUT_MS = 5000;
const ERROR_SHOWN_MS = 2000;

// What counts as somebody using the panel.
const ACTIVITY = ['mousedown', 'mousemove', 'keydown', 'touchstart', 'scroll'];

const LOCK_SCREEN = `
            <div class="lock-container">
                <div class="lock-icon">\u{1F512}</div>
                <div class="lock-title">Panel Locked</div>
                <input type="password" id="lock-input" class="lock-input" placeholder="Enter PIN" maxlength="6" inputmode="numeric" pattern="[0-9]*" />
                <button id="lock-submit" class="lock-submit">Unlock</button>
                <div id="lock-error" class="lock-error"></div>
            </div>
        `;

/** The lock screen, drawn over everything, and the parts it is read through. */
function drawLockScreen() {
    const overlay = document.createElement('div');
    overlay.id = 'lock-overlay';
    overlay.className = 'lock-overlay';
    overlay.innerHTML = LOCK_SCREEN;
    document.body.appendChild(overlay);
    return {
        overlay,
        input: document.getElementById('lock-input'),
        submit: document.getElementById('lock-submit'),
        error: document.getElementById('lock-error'),
    };
}

/**
 * Say why an attempt did not unlock. "Incorrect PIN" for an unreachable server
 * sends somebody to retype the PIN they already know, over and over, so that
 * one says so and keeps what was typed.
 */
function showRefusal({ input, error }, ok) {
    error.textContent = ok === null
        ? "Can't reach the server — try again"
        : 'Incorrect PIN';
    if (ok === false) input.value = '';
    input.focus();
    setTimeout(() => { error.textContent = ''; }, ERROR_SHOWN_MS);
}

/**
 * Forget what was typed whenever the page is hidden (security on shared
 * displays), for as long as this lock screen is up.
 */
function forgetWhenHidden({ overlay, input, error }) {
    const clearOnHide = () => {
        if (document.hidden) {
            input.value = '';
            error.textContent = '';
        }
    };
    document.addEventListener('visibilitychange', clearOnHide);
    const observer = new MutationObserver(() => {
        if (!document.body.contains(overlay)) {
            document.removeEventListener('visibilitychange', clearOnHide);
            observer.disconnect();
        }
    });
    observer.observe(document.body, { childList: true });
}

/** No idle timeout set, or none worth arming. */
function noTimeout(timeout) {
    return !timeout || timeout <= 0;
}

export class PanelLock {
    /**
     * `host`, each read at call time: `editMode()`, `uiSettings()`, `pages()`,
     * `offline()`, `activity()` (somebody used the panel) and
     * `goIdle(pageId)` (back to that page, overlays and history gone).
     */
    constructor(host) {
        this._host = host;
        this._locked = false;
        // The lock screen is shown once a session, not on every reconnect.
        this._shownThisSession = false;
        this._idleTimer = null;
        this._listening = false;
    }

    /** Is a lock configured for this panel? */
    configured() {
        const settings = this._host.uiSettings() || {};
        return settings.lock_enabled === true || !!settings.lock_code;
    }

    /** Is this the right PIN? true, false, or null for "could not ask". */
    async check(attempt) {
        const uiSettings = this._host.uiSettings();
        const drafted = uiSettings?.lock_code;
        if (drafted) return attempt === drafted;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), UNLOCK_TIMEOUT_MS);
        try {
            const res = await fetch(`${basePath()}/api/panel/unlock`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: attempt }),
                signal: controller.signal,
            });
            if (!res.ok) return null;
            const data = await res.json();
            return data?.success === true;
        } catch (err) {
            console.warn('[panel] unlock check failed:', err);
            return null;
        } finally {
            clearTimeout(timer);
        }
    }

    /**
     * A definition arrived. The server resends one on every (re)connect, so a
     * transient socket drop must NOT lock a panel the operator already
     * unlocked: the lock screen comes up at most once a session here, and idle
     * brings it back explicitly. A project edit that cleared the PIN while a
     * panel sat locked takes the lock screen away, so it cannot get stuck.
     */
    reconcile() {
        if (this._host.editMode()) return;
        if (!this.configured()) {
            const overlay = document.getElementById('lock-overlay');
            if (overlay) overlay.remove();
            this._locked = false;
            return;
        }
        if (!this._shownThisSession) {
            this._shownThisSession = true;
            this.show();
        }
    }

    /** Put the lock screen up, unless it is already up. */
    show() {
        if (this._host.editMode()) return;
        if (!this.configured()) return;
        if (document.getElementById('lock-overlay')) return;
        this._locked = true;
        const screen = drawLockScreen();
        const attempt = { checking: false };
        const tryUnlock = () => this._tryUnlock(screen, attempt);
        screen.submit.addEventListener('click', tryUnlock);
        screen.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') tryUnlock();
        });
        forgetWhenHidden(screen);
        screen.input.focus();
    }

    /** Start the idle timer again, unless the panel is offline, locked or has no timeout. */
    resetIdle() {
        if (this._idleTimer) clearTimeout(this._idleTimer);
        // Never armed while disconnected: it would navigate a dead panel and
        // stack a lock screen over the offline overlay.
        if (this._host.offline()) return;
        const timeout = this._host.uiSettings()?.idle_timeout_seconds;
        if (noTimeout(timeout)) return;
        if (this._locked) return;
        this._idleTimer = setTimeout(() => this._goIdle(), timeout * 1000);
    }

    /** The panel went offline: the idle timer stops until somebody uses it again. */
    stopIdle() {
        if (this._idleTimer) {
            clearTimeout(this._idleTimer);
            this._idleTimer = null;
        }
    }

    /** Every touch, key, move and scroll on the page counts as activity, once listened for. */
    listenForActivity() {
        if (this._listening) return;
        this._listening = true;
        for (const type of ACTIVITY) {
            document.addEventListener(type, () => this._host.activity(), { passive: true });
        }
    }

    async _tryUnlock(screen, attempt) {
        if (attempt.checking) return;   // Enter and the button both fire; one attempt.
        attempt.checking = true;
        screen.submit.disabled = true;
        try {
            const ok = await this.check(screen.input.value);
            if (!document.body.contains(screen.overlay)) return;
            if (ok === true) this._unlock(screen.overlay);
            else showRefusal(screen, ok);
        } finally {
            attempt.checking = false;
            screen.submit.disabled = false;
        }
    }

    _unlock(overlay) {
        overlay.remove();
        this._locked = false;
        this._host.activity();
    }

    /**
     * Nobody has touched the panel: back to the idle page, and locked again
     * when a PIN is set. The idle page is checked against the pages there are,
     * so a deleted or renamed one resolves to the first page rather than to
     * whatever a redraw would fall back to.
     */
    _goIdle() {
        this._host.goIdle(this._idlePage());
        if (this.configured()) this.show();
    }

    _idlePage() {
        const idlePage = this._host.uiSettings()?.idle_page || 'main';
        const pages = this._host.pages() || [];
        if (pages.length && !pages.some(p => p.id === idlePage)) return pages[0].id;
        return idlePage;
    }
}
