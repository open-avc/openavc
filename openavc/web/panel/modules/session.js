/**
 * The Programmer's sign-in, carried by a panel the IDE embeds.
 *
 * The UI Builder's canvas and its Preview are this panel in a same-origin,
 * unsandboxed iframe, so it shares the tab's sessionStorage, where the IDE
 * keeps its session token (openavc/web/programmer/src/api/auth.ts is the
 * parent half). The token is what lets the frame's /api requests and its
 * socket pass on an instance with a password: without it every call answers
 * 401, which makes the browser pop its own sign-in dialog inside the frame,
 * and the panel gate refuses the socket for want of an approved cookie.
 *
 * The rule worth knowing before changing anything here: the storage key and
 * the two forms the token travels in -- `Authorization: Bearer <token>` on a
 * request, the `auth.bearer.<token>` WebSocket subprotocol -- are the IDE's
 * and the server's as well (auth.ts; in openavc/api/auth.py,
 * programmer_authority reads the header and websocket_authority both), so a
 * change to one is a change to all three. tests/test_session_bridge_parity.py
 * holds the key and both forms equal to the IDE's and runs the server's own
 * readers on what this module sends. The stored value is the bare token,
 * URL-safe base64, valid as it is in both.
 *
 * The token is read at each request and never kept, so the IDE signing in or
 * out reaches the next one. A panel opened on its own holds no session, and
 * then nothing is added. A header the caller set is never replaced. The
 * session goes only to this panel's own server: an address is resolved
 * against the page, and carries it only when it has the page's origin and an
 * /api path (the IDE's isSameOriginApiUrl, the same rule), so an address on
 * another host, or one naming /api only in its query or fragment, gets
 * nothing, and one that will not parse gets nothing.
 */

const STORAGE_KEY = 'openavc.programmer.session';

/** The token the IDE keeps for this tab, or null: none, an empty one, or a
 *  storage the browser refuses to read. */
function sessionToken() {
    try {
        const token = sessionStorage.getItem(STORAGE_KEY);
        return token || null;
    } catch (_) {
        return null;
    }
}

/** The session as the socket's subprotocol, or null with none. Mirrors
 *  getAuthSubprotocols() in the Programmer. */
function authSubprotocol() {
    const token = sessionToken();
    return token ? `auth.bearer.${token}` : null;
}

/** An address on this page's own server whose path is /api, /api/...,
 *  or /tunnel/<id>/api/...: the IDE's isSameOriginApiUrl. */
function isSameOriginApiUrl(url, baseHref) {
    let base;
    let resolved;
    try {
        base = new URL(baseHref);
        resolved = new URL(url, base);
    } catch (_) {
        return false;
    }
    if (resolved.origin !== base.origin) return false;
    return /(^|\/)api(\/|$)/.test(resolved.pathname);
}

/** The address a fetch was asked for: a path, a URL, or a Request's own. */
function urlOf(input) {
    if (typeof input === 'string') return input;
    if (input instanceof URL) return input.toString();
    return input.url;
}

/** The caller's options with the session's Authorization header among their
 *  headers (their own, else a Request's), unless they already set one. */
function withAuthorization(input, init, auth) {
    const headers = new Headers(
        init?.headers ??
            (input instanceof Request ? input.headers : undefined),
    );
    if (!headers.has('Authorization')) {
        headers.set('Authorization', auth);
    }
    return { ...(init || {}), headers };
}

/**
 * Carry the session on this window's /api requests, and offer it to the
 * socket as `window.__openavcGetAuthSubprotocol`, which the panel's connect
 * reads. Installed once, before anything fetches. `win` is the window whose
 * fetch is wrapped and where the subprotocol is published; the storage and
 * the Headers, Request and URL it reads are this module's own (the same
 * window, in the panel).
 */
export function installSessionBridge(win) {
    win.__openavcGetAuthSubprotocol = authSubprotocol;
    const originalFetch = win.fetch.bind(win);
    win.fetch = async (input, init) => {
        const url = urlOf(input);
        const token = sessionToken();
        if (token && isSameOriginApiUrl(url, win.location.href)) {
            init = withAuthorization(input, init, `Bearer ${token}`);
        }
        return originalFetch(input, init);
    };
}
