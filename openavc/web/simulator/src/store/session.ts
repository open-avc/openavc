/**
 * How this tab gets a credential.
 *
 * The simulator opens in its own window so you can watch the protocol log
 * while working in the IDE. A new window starts with nothing: the Programmer's
 * session token lives in ITS tab's sessionStorage, not ours. Left alone, our
 * first API call 401s and — because a fresh document is a top-level navigation
 * — the browser answers with its own sign-in dialog, which is exactly the
 * popup we are trying to be rid of.
 *
 * So the opener hands it over. We announce ourselves to `window.opener`, it
 * replies with the token, and we keep it for this tab. Both windows are the
 * same origin, so the message never leaves the page. Nothing goes in the URL,
 * where it would land in history and the server log.
 *
 * The child speaks first on purpose: the parent cannot know when this document
 * is ready to listen, and a parent that just posts after `window.open` races
 * the page load — it wins on a laptop and loses on a Pi.
 *
 * When there is no opener (a bookmark, a reload after a restart) we fall back
 * to asking for the password, the same as the Programmer does in a fresh tab.
 */

import { APP_ROOT } from "./paths";

const STORAGE_KEY = "openavc.simulator.token";
const HELLO = "openavc:simulator-ready";
const TOKEN = "openavc:simulator-token";
const HANDSHAKE_TIMEOUT_MS = 3000;

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // private mode with storage disabled
  }
}

export function setToken(token: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    /* not fatal — the token stays in memory for this page load */
  }
  cached = token;
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* ignore */ }
  cached = null;
}

let cached: string | null = getToken();

// A token the server has already rejected. `window.open` gives the new window
// a COPY of the opener's sessionStorage, so a token from a previous session
// rides along after a server restart -- and without this we would keep asking
// the opener, keep being handed the same dead string, and keep 401ing with no
// way out but a manual reload.
let rejected: string | null = null;

/** Called when the server refuses this token, so we stop trusting it. */
export function rejectCurrentToken(): void {
  rejected = currentToken();
  clearToken();
}

export function currentToken(): string | null {
  return cached ?? getToken();
}

/** Authorization header for API calls, when we have a token. */
export function authHeaders(): Record<string, string> {
  const token = currentToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * WebSocket credentials. A browser cannot set headers on a WS handshake, so
 * the platform carries the token in a subprotocol — the same `auth.bearer.<t>`
 * the Programmer's own socket uses (see `check_ws_auth` on the server).
 */
export function authSubprotocols(): string[] | undefined {
  const token = currentToken();
  return token ? [`auth.bearer.${token}`] : undefined;
}

/**
 * Ask the window that opened us for its session token. Resolves with the
 * token, or null if there is no opener or it never answers.
 */
export function requestTokenFromOpener(): Promise<string | null> {
  const existing = currentToken();
  if (existing && existing !== rejected) return Promise.resolve(existing);
  if (!window.opener) return Promise.resolve(null);

  return new Promise((resolve) => {
    let settled = false;
    const finish = (token: string | null) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      clearTimeout(timer);
      if (token) setToken(token);
      resolve(token);
    };

    const onMessage = (event: MessageEvent) => {
      // Same-origin only. An opener from anywhere else has no business
      // handing this tab a credential.
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: string; token?: string } | null;
      if (data && data.type === TOKEN && typeof data.token === "string") {
        // The opener may hand back the very token we just had refused (its own
        // session died too). Treat that as no answer so we fall through to the
        // password form instead of looping.
        finish(data.token === rejected ? null : data.token);
      }
    };

    const timer = setTimeout(() => finish(null), HANDSHAKE_TIMEOUT_MS);
    window.addEventListener("message", onMessage);
    try {
      window.opener.postMessage({ type: HELLO }, window.location.origin);
    } catch {
      finish(null); // opener gone or cross-origin
    }
  });
}

/**
 * Whether this instance wants a credential at all.
 *
 * A dev checkout with no password set serves its admin surface openly, and the
 * Programmer that opened us therefore has no session token to hand over. Read
 * naively, "no token" looks identical to "not signed in", and we would show a
 * password box for a password that does not exist — nothing typed into it can
 * ever be right, so the simulator becomes unreachable on exactly the posture
 * where the least is protecting it.
 *
 * So we ask the platform, the same way `/programmer` does before deciding
 * whether to draw its own login. "ok" means anonymous is allowed and the
 * control API will answer us without a credential.
 */
export async function credentialRequired(): Promise<boolean> {
  try {
    const res = await fetch(`${APP_ROOT}/api/auth/required`, { method: "GET" });
    if (!res.ok) return true; // can't tell — ask, rather than hang on a 401
    const data = (await res.json()) as { state?: string; required?: boolean };
    return data?.state !== "ok";
  } catch {
    return true; // same: a network error is not evidence the door is open
  }
}

/** Exchange a password for a session token, for the no-opener case.
 *
 * Credentials go in a Basic header, not a JSON body — that is what
 * `/api/auth/session` reads, and it is what the Programmer's own login does
 * (`web/programmer/src/api/auth.ts`). Sending JSON gets a 401 that reads as a
 * wrong password, which is a confusing way to discover the mistake.
 */
export async function signIn(username: string, password: string): Promise<void> {
  const res = await fetch(`${APP_ROOT}/api/auth/session`, {
    method: "POST",
    headers: {
      Authorization:
        "Basic " + btoa(unescape(encodeURIComponent(`${username}:${password}`))),
    },
  });
  if (!res.ok) {
    throw new Error(res.status === 401 ? "Incorrect username or password." : "Sign-in failed.");
  }
  const data = (await res.json()) as { token?: string };
  if (!data.token) throw new Error("Sign-in failed.");
  setToken(data.token);
}
