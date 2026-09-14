/**
 * Authentication for the Programmer SPA.
 *
 * The browser never keeps the admin password. The login screen exchanges it
 * for a short-lived session token (POST /api/auth/session) and only that
 * token is cached, in sessionStorage:
 *   - A global fetch interceptor adds `Authorization: Bearer <token>` to
 *     every same-origin /api request.
 *   - The WebSocket client sends the token via the `auth.bearer.<token>`
 *     Sec-WebSocket-Protocol subprotocol (browsers can't set headers on
 *     WebSocket upgrades).
 *   - A 401 on any of those clears the cache and dispatches
 *     `openavc:auth-required` so the App can drop back to the login screen.
 *     The mint endpoint itself is the exception: its 401 means the password
 *     just typed was wrong, not that a session died.
 *
 * The server invalidates tokens on password change and restart, and expiry
 * is sliding — an active session stays signed in, an idle one ages out.
 */

import { getTunnelPrefix } from "./base";

const STORAGE_KEY = "openavc.programmer.session";

/** Pre-token versions stored `{user, pass}` under this key. Never read it —
 *  just make sure no raw password lingers in the browser after an upgrade. */
const LEGACY_STORAGE_KEY = "openavc.programmer.auth";

export function getSessionToken(): string | null {
  try {
    sessionStorage.removeItem(LEGACY_STORAGE_KEY);
    const token = sessionStorage.getItem(STORAGE_KEY);
    return token || null;
  } catch {
    return null;
  }
}

export function hasSession(): boolean {
  return getSessionToken() !== null;
}

export function setSessionToken(token: string): void {
  sessionStorage.setItem(STORAGE_KEY, token);
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* storage unavailable — nothing cached anyway */
  }
}

export function getAuthHeader(): string | null {
  const token = getSessionToken();
  return token ? `Bearer ${token}` : null;
}

/**
 * Returns the WebSocket subprotocol array carrying the session token, or
 * undefined when signed out. Tokens are URL-safe base64, which is already
 * valid subprotocol grammar — no extra encoding needed.
 */
export function getAuthSubprotocols(): string[] | undefined {
  const token = getSessionToken();
  return token ? [`auth.bearer.${token}`] : undefined;
}

/**
 * Exchange the password for a session token and cache it. The password
 * lives only in this call — it is sent once, as Basic auth, to the mint
 * endpoint and never stored.
 */
export async function loginWithPassword(
  user: string,
  pass: string,
): Promise<{ ok: boolean; status: number }> {
  const res = await fetch(`${getTunnelPrefix()}/api/auth/session`, {
    method: "POST",
    headers: {
      Authorization:
        "Basic " + btoa(unescape(encodeURIComponent(`${user}:${pass}`))),
    },
  });
  if (!res.ok) return { ok: false, status: res.status };
  const data = await res.json();
  if (typeof data?.token !== "string" || !data.token) {
    return { ok: false, status: res.status };
  }
  setSessionToken(data.token);
  return { ok: true, status: res.status };
}

/** Revoke the session server-side, then forget it locally. */
export async function logout(): Promise<void> {
  const header = getAuthHeader();
  if (header) {
    try {
      await fetch(`${getTunnelPrefix()}/api/auth/session`, {
        method: "DELETE",
        headers: { Authorization: header },
      });
    } catch {
      /* offline logout still clears the local session */
    }
  }
  clearSession();
}

/**
 * Fires when the server responds 401 to an authenticated request, telling
 * the App to clear state and show the login screen.
 */
export const AUTH_REQUIRED_EVENT = "openavc:auth-required";

/**
 * True when `url` is the session-mint endpoint — the one door whose 401 means
 * "that password is wrong", not "the credential you were holding is dead".
 *
 * `loginWithPassword` posts the typed password here as Basic auth, so a typo
 * comes back 401 like any other refusal. Routing that through
 * AUTH_REQUIRED_EVENT made the sign-in screen announce "Your session ended."
 * to somebody who had never had a session, directly above the correct
 * "Wrong username or password" — two sentences disagreeing about the cause on
 * the first screen a new user sees.
 *
 * Matched by path, which also covers the DELETE (logout): a session already
 * gone when you asked to leave is not worth a banner, and `logout()` clears
 * the session itself.
 */
function isCredentialMintUrl(url: string, baseHref: string): boolean {
  try {
    return new URL(url, baseHref).pathname.endsWith("/api/auth/session");
  } catch {
    return false;
  }
}

/**
 * True when `url` resolves (against `baseHref`) to the same origin AND an
 * /api path — /api, /api/..., or /tunnel/<id>/api/....
 *
 * The origin anchor is the security boundary: the credential must never ride
 * a request to another host, no matter what that URL's path looks like. A
 * path-only match would attach the session token to e.g.
 * https://elsewhere.example/api/... issued by any script running in the SPA
 * (a plugin UI bundle, injected code). Unparseable URLs get no credential.
 *
 * Exported for tests; the interceptor binds baseHref to the live location.
 */
export function isSameOriginApiUrl(url: string, baseHref: string): boolean {
  let base: URL;
  let resolved: URL;
  try {
    base = new URL(baseHref);
    resolved = new URL(url, base);
  } catch {
    return false;
  }
  if (resolved.origin !== base.origin) return false;
  return /(^|\/)api(\/|$)/.test(resolved.pathname);
}

let installed = false;

/**
 * Patches the global `fetch` so every /api request carries an Authorization
 * header derived from the cached session token. Call once at app startup.
 */
export function installFetchAuth(): void {
  if (installed) return;
  installed = true;

  const original = window.fetch.bind(window);

  window.fetch = async (input, init) => {
    let url: string;
    if (typeof input === "string") {
      url = input;
    } else if (input instanceof URL) {
      url = input.toString();
    } else {
      url = input.url;
    }

    const attach = isSameOriginApiUrl(url, window.location.href);
    let finalInit = init;

    if (attach) {
      const auth = getAuthHeader();
      if (auth) {
        const headers = new Headers(
          init?.headers ??
            (input instanceof Request ? input.headers : undefined),
        );
        if (!headers.has("Authorization")) {
          headers.set("Authorization", auth);
        }
        finalInit = { ...(init || {}), headers };
      }
    }

    const res = await original(input, finalInit);

    // A refused attempt to GET a credential must not destroy the one we hold,
    // and must not be reported as an expiry — see isCredentialMintUrl.
    if (
      attach &&
      res.status === 401 &&
      !isCredentialMintUrl(url, window.location.href)
    ) {
      clearSession();
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    }

    return res;
  };
}

/**
 * Asks the server whether a password is configured. We use this dedicated
 * endpoint instead of probing a protected route because browsers auto-attach
 * cached HTTP Basic credentials to fetches — that would make a protected
 * endpoint return 200 even when the SPA has no usable session of its own
 * (which it needs for the WebSocket handshake).
 *
 * Returns:
 *   - "ok"       — no credential configured and anonymous allowed; skip login
 *   - "setup"    — unclaimed shipped instance; show the first-run setup screen
 *   - "required" — a credential is set; show the login screen
 *   - "error"    — network error; show the login screen so the user can retry
 */
export async function probeAuth(): Promise<"ok" | "setup" | "required" | "error"> {
  try {
    const prefix = window.location.pathname.split("/programmer")[0] || "";
    const res = await fetch(`${prefix}/api/auth/required`, { method: "GET" });
    if (!res.ok) return "ok"; // older servers without the endpoint — assume open
    const data = await res.json();
    if (data?.state === "setup") return "setup";
    // `state` is the modern signal; fall back to the boolean for older servers.
    if (data?.state === "required" || data?.required) return "required";
    return "ok";
  } catch {
    return "error";
  }
}
