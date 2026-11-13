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
 * and the server's as well (auth.ts, and check_ws_auth() in
 * openavc/api/auth.py), so a change to one is a change to all three. The
 * stored value is the bare token, URL-safe base64, valid as it is in both.
 *
 * The token is read at each request and never kept, so the IDE signing in or
 * out reaches the next one. A panel opened on its own holds no session, and
 * then nothing is added. A header the caller set is never replaced. Whether
 * an address is the API is read from its path alone, never its host: every
 * request the panel makes goes to its own server.
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

/** /api, /api/..., /api?..., or /tunnel/<id>/api/... */
function isApiUrl(url) {
    return /(^|\/)api(\/|$|\?)/.test(url);
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
 * reads. Installed once, before anything fetches.
 */
export function installSessionBridge(win) {
    win.__openavcGetAuthSubprotocol = authSubprotocol;
    const originalFetch = win.fetch.bind(win);
    win.fetch = async (input, init) => {
        const url = urlOf(input);
        const token = sessionToken();
        if (token && isApiUrl(url)) {
            init = withAuthorization(input, init, `Bearer ${token}`);
        }
        return originalFetch(input, init);
    };
}
