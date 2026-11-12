/**
 * Whether this device may use the panel, and the screen that says why when it
 * may not.
 *
 * Before the panel connects it checks in (`GET /api/panel/access`). A device
 * that is not approved sees a screen in place of the panel, naming the space
 * and the code to approve, with "Approve with the admin password" under it,
 * and the page connects the moment an approval answers. The same screen
 * carries every other reason the panel cannot be used: the system has no room
 * for another panel, the server refused this socket with a reason
 * (`REFUSALS`), or the Remote Access session the page came through has ended.
 * A new reason is a title and a sentence handed to `_show`, which owns what
 * every screen has in common: one at a time, the page's own status hidden
 * under it, and the admin password offered only where it could approve
 * something.
 *
 * The panel page lends a host: `hidePageStatus()` and `showPageStatus()` (the
 * loading text, the connection pill and the offline overlay, which a screen
 * covers) and `endHolds()`. Reconnecting is the page's, and so is the PIN lock,
 * which is a project setting on a panel already admitted.
 */

import { basePath } from './base_path.js';

/** The close code for a panel socket the server took and will not serve:
 *  this device is not approved, or its approval was revoked. Accepted first so
 *  the page can read the code; a refusal before the handshake reaches a browser
 *  as 1006, which looks exactly like a dropped network. */
export const NOT_APPROVED_CLOSE = 4010;

// How often a waiting panel asks whether it has been approved, and how often
// a denied one, or one the system had no room for, asks again.
const POLL_MS = 3000;
const RETRY_MS = 60000;

// Close codes that carry the reason this socket will not be served. No
// reconnect changes the answer, so the screen says it and offers Try again.
const REFUSALS = new Map([
    [4013, ['OpenAVC interface disabled', 'This interface is disabled in product management.']],
    [4014, ['Preview sign-in required', 'Sign in to the Programmer to use Preview.']],
]);

// The check-in's answers that need no more than a title and a sentence. Any
// other status is waiting for approval.
const ANSWERS = new Map([
    ['denied', ['Not approved',
        'This panel was not approved. Ask your AV administrator if you need to use it.']],
    ['unavailable', ['Not accepting new panels',
        'This system is not accepting new panels right now. Ask your AV administrator.']],
]);

// The statuses the cloud refuses a gone session's request with
// (isEndedRemoteSession).
const SESSION_REFUSALS = new Set([401, 403, 404]);

/** True when this page was opened through the cloud's Remote Access
 *  (`/tunnel/<id>/panel/`). */
function inRemoteSession() {
    return basePath().startsWith('/tunnel/');
}

/** `?time_zone=` and the zone this screen runs in, or nothing when it cannot
 *  be read. A panel on the system's own screen sets the system's clock with it
 *  when the two disagree. */
function zoneQuery() {
    let zone = '';
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) { /* no Intl */ }
    return zone ? `?time_zone=${encodeURIComponent(zone)}` : '';
}

function strong(className, text) {
    const node = document.createElement('strong');
    node.className = className;
    node.textContent = text;
    return node;
}

/** The waiting screen's sentence, with the space and the code set apart. */
function waitingSentence(answer) {
    return [
        'This panel needs approval before it can control ',
        strong('panel-access-space', answer.space ? String(answer.space) : 'this space'),
        '. Ask your AV administrator to approve code ',
        strong('panel-access-code', answer.code ? String(answer.code) : ''),
        '.',
    ];
}

export class PanelAccess {
    constructor(host) {
        this.host = host;
        this.overlay = null;    // the screen while it is up
        this.ended = false;     // the Remote Access session this page came through is gone
        this._wake = null;      // ends the current wait between check-ins early
        this._checking = null;  // the check-in loop in flight, if any
    }

    // --- The check-in ---

    /**
     * Ask the server whether this device may connect.
     *
     * Null when it gave no usable answer (unreachable, or an error), which is
     * not a refusal: a refusal is an answer whose status says so.
     */
    async fetchAccess() {
        try {
            const res = await fetch(`${basePath()}/api/panel/access${zoneQuery()}`, { cache: 'no-store' });
            if (!res.ok) return null;
            const data = await res.json();
            return data && typeof data === 'object' ? data : null;
        } catch (err) {
            console.warn('[panel] access check failed:', err);
            return null;
        }
    }

    /**
     * Resolve once this page may open its socket.
     *
     * The first request the page makes, before the plugin list and before
     * the socket, so a device that is not approved sees the waiting screen
     * and nothing else. While it waits it asks again every few seconds; once
     * denied, or when the system had no room for another request, every
     * minute. The poll that answers "approved" carries the real cookie, and
     * the page goes on to connect without a reload. The admin-password form
     * on the screen wakes the poll the moment it succeeds.
     *
     * No answer at all is not a refusal. On the first load the socket's own
     * offline overlay is the honest screen for an unreachable server, so the
     * page goes on and lets it speak. Asked again after a refusal (the server
     * was there a moment ago: it closed the socket on purpose), or while the
     * waiting screen is already up, it keeps asking.
     */
    waitForAccess(options) {
        // One loop at a time: a second caller joins the one in flight rather
        // than starting another, so nothing connects while a check-in is
        // still being answered.
        if (!this._checking) {
            this._checking = this._checkIn(options || {}).finally(() => {
                this._checking = null;
            });
        }
        return this._checking;
    }

    async _checkIn({ afterRefusal = false }) {
        for (;;) {
            const answer = await this.fetchAccess();
            if (answer === null) {
                if (!afterRefusal && !this.overlay) return;
                await this._sleep(POLL_MS);
                continue;
            }
            if (answer.access === 'open' || answer.status === 'approved') {
                this.hide();
                return;
            }
            this.showWaiting(answer);
            await this._sleep(answer.status === 'pending' ? POLL_MS : RETRY_MS);
        }
    }

    /** A wait between check-ins that `wake` can end early. */
    _sleep(ms) {
        return new Promise((resolve) => {
            const done = () => {
                clearTimeout(timer);
                if (this._wake === done) this._wake = null;
                resolve();
            };
            const timer = setTimeout(done, ms);
            this._wake = done;
        });
    }

    /** Ask again now, rather than when the current wait ends. */
    wake() {
        if (this._wake) this._wake();
    }

    // --- The screen ---

    /** The screen for a check-in's answer: waiting for approval, denied, or a
     *  system with no room for another panel. */
    showWaiting(answer) {
        const plain = ANSWERS.get(answer.status);
        if (plain) this._show(answer.status, plain[0], [plain[1]]);
        else this._show('pending', 'Waiting for approval', waitingSentence(answer));
    }

    /**
     * Say why the server refused this socket, with a way to try again, when
     * `code` is a close that carries a reason. False for any other close.
     */
    showRefusal(code) {
        const refusal = REFUSALS.get(code);
        if (!refusal) return false;
        const overlay = this._show('unavailable', refusal[0], [refusal[1]]);
        const retry = document.createElement('button');
        retry.className = 'panel-access-submit';
        retry.textContent = 'Try again';
        retry.onclick = () => location.reload();
        overlay.querySelector('.panel-access-container').appendChild(retry);
        return true;
    }

    /** Take the screen down, and give the page its own status back. */
    hide() {
        const overlay = this.overlay;
        this.overlay = null;
        if (!overlay) return;
        overlay.remove();
        this.host.showPageStatus();
    }

    /**
     * Put the screen up, or refill the one that is up: `status` for its
     * `data-status`, a title, and the words of its sentence. It covers the
     * offline overlay (this page is refused on purpose, not cut off) and the
     * lock screen (a panel that is not admitted has no interface to lock),
     * and is drawn in the :root default colours because no theme has been
     * received. The admin password is offered to a device waiting or denied;
     * anywhere else there is nothing it could approve.
     */
    _show(status, title, words) {
        this.host.hidePageStatus();
        const overlay = this._screenInPage();
        overlay.dataset.status = status;
        overlay.querySelector('.panel-access-title').textContent = title;
        const message = overlay.querySelector('.panel-access-message');
        message.textContent = '';
        message.append(...words);
        this._offerPassword(overlay, status === 'pending' || status === 'denied');
        return overlay;
    }

    /** The screen that is up, or a new one when there is none in the page. */
    _screenInPage() {
        if (this.overlay && document.body.contains(this.overlay)) return this.overlay;
        const overlay = document.createElement('div');
        overlay.id = 'panel-access-overlay';
        overlay.className = 'panel-access-overlay';
        const box = document.createElement('div');
        box.className = 'panel-access-container';
        const title = document.createElement('div');
        title.className = 'panel-access-title';
        const message = document.createElement('div');
        message.className = 'panel-access-message';
        box.append(title, message, ...this._passwordForm());
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        this.overlay = overlay;
        return overlay;
    }

    /** Offer the password route behind its link, or take it away. A form
     *  somebody already opened stays open. */
    _offerPassword(overlay, offered) {
        const link = overlay.querySelector('.panel-access-claim-link');
        const form = overlay.querySelector('.panel-access-claim');
        if (!offered) {
            link.hidden = true;
            form.hidden = true;
        } else if (form.hidden) {
            link.hidden = false;
        }
    }

    // --- Approve with the admin password ---

    /**
     * "Approve with the admin password": a link that reveals the form, and
     * the form itself. The password goes to the server once, as HTTP Basic on
     * the claim route, and is never kept. A wrong one answers 401 with no
     * browser challenge, so the page shows its own sentence. The username
     * rides along because the instance checks it whenever one is set, which
     * first-run setup always does; it is prefilled with the one setup offers.
     */
    _passwordForm() {
        const link = document.createElement('a');
        link.className = 'panel-access-claim-link';
        link.href = '#';
        link.textContent = 'Approve with the admin password';

        const form = document.createElement('form');
        form.className = 'panel-access-claim';
        form.hidden = true;
        form.setAttribute('autocomplete', 'off');
        const user = document.createElement('input');
        user.className = 'panel-access-input';
        user.type = 'text';
        user.name = 'username';
        user.value = 'admin';
        user.setAttribute('aria-label', 'Username');
        user.setAttribute('autocomplete', 'username');
        const pass = document.createElement('input');
        pass.className = 'panel-access-input';
        pass.type = 'password';
        pass.name = 'password';
        pass.placeholder = 'Admin password';
        pass.setAttribute('aria-label', 'Admin password');
        pass.setAttribute('autocomplete', 'new-password');
        const submit = document.createElement('button');
        submit.type = 'submit';
        submit.className = 'panel-access-submit';
        submit.textContent = 'Approve';
        const error = document.createElement('div');
        error.className = 'panel-access-error';
        form.append(user, pass, submit, error);

        link.addEventListener('click', (e) => {
            e.preventDefault();
            link.hidden = true;
            form.hidden = false;
            pass.focus();
        });

        let claiming = false;
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (claiming) return;   // Enter and the button both fire; one attempt.
            claiming = true;
            submit.disabled = true;
            error.textContent = '';
            try {
                const result = await this.claimWithPassword(user.value, pass.value);
                if (result.ok) {
                    // The approved cookie is on that response; the next
                    // check-in answers "approved" and the page connects.
                    this.wake();
                } else {
                    error.textContent = result.message;
                    pass.value = '';
                    pass.focus();
                }
            } finally {
                claiming = false;
                submit.disabled = false;
            }
        });
        return [link, form];
    }

    async claimWithPassword(username, password) {
        // URI-encode before base64: btoa is a Latin-1 encoder and the server
        // reads the header as UTF-8, so a password with an accent would go
        // out wrong and one above Latin-1 would make btoa throw.
        const token = btoa(unescape(encodeURIComponent(`${username}:${password}`)));
        try {
            const res = await fetch(`${basePath()}/api/panel/access/claim`, {
                method: 'POST',
                headers: { Authorization: `Basic ${token}`, Accept: 'application/json' },
                cache: 'no-store',
            });
            if (res.ok) return { ok: true };
            let detail = null;
            try {
                detail = (await res.json()).detail;
            } catch (_) { /* not JSON */ }
            if (res.status === 401) return { ok: false, message: 'That password is not correct.' };
            return {
                ok: false,
                message: typeof detail === 'string' && detail
                    ? detail
                    : 'This panel could not be approved. Try again.',
            };
        } catch (err) {
            console.warn('[panel] claim failed:', err);
            return { ok: false, message: "Can't reach the system. Try again." };
        }
    }

    // --- Remote Access ---

    /** After the socket drops. Through Remote Access the drop may be the
     *  session itself ending, which no reconnect can bring back, so ask once;
     *  on the system's own address there is no session to end. */
    checkSessionAfterDrop() {
        if (inRemoteSession()) this.probeRemoteSession();
    }

    /**
     * Ask the server once whether the session is still there. The panel's own
     * check-in is the question: it is open, so the system answers it without
     * a credential, and a tunneled request creates no device record.
     */
    async probeRemoteSession() {
        try {
            const res = await fetch(`${basePath()}/api/panel/access`, { cache: 'no-store' });
            if (await this.isEndedRemoteSession(res)) this.showRemoteSessionEnded();
        } catch (_) {
            // Unreachable, or an answer that could not be read: not an answer
            // about the session. The reconnect goes on.
        }
    }

    /**
     * True when `res` is the cloud refusing a request for a Remote Access
     * session that is gone, rather than the system answering it. The same
     * rule as isEndedRemoteSession in the Programmer
     * (openavc/web/programmer/src/api/auth.ts). The system's API answers every
     * error as JSON; the cloud's tunnel refusals are bare text: a 404 for a
     * session that was closed or timed out, a 401 for an access token that is
     * missing or expired, and a 403 saying remote access ended when the
     * account lost it. Its other 403 (a request outside the session's scope)
     * and its 502 (the system is not connected) leave the session standing.
     */
    async isEndedRemoteSession(res) {
        if (!inRemoteSession()) return false;
        if (!SESSION_REFUSALS.has(res.status)) return false;
        if ((res.headers.get('content-type') || '').includes('json')) return false;
        if (res.status !== 403) return true;
        return (await res.text()).startsWith('Remote access ended');
    }

    /** The session this page came through is gone: say so in place of the
     *  panel, and stop reconnecting. Nothing on the page can bring it back. */
    showRemoteSessionEnded() {
        if (this.ended) return;
        this.ended = true;
        this.host.endHolds();
        this._show('ended', 'Remote session ended',
            ['This remote session has ended. Open it again from the portal.']);
    }
}
