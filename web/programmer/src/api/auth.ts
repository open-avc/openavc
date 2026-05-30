/**
 * Authentication for the Programmer SPA.
 *
 * The browser's native HTTP Basic auth dialog can't pass credentials to
 * WebSocket upgrade requests, so we manage auth in JS instead:
 *   - Credentials are entered in a login screen and cached in sessionStorage.
 *   - A global fetch interceptor adds `Authorization: Basic <b64>` to every
 *     /api request.
 *   - The WebSocket client sends the password via the `auth.b64.<...>`
 *     Sec-WebSocket-Protocol subprotocol (server-side handler decodes it).
 *   - On any 401 response we clear the cache and dispatch
 *     `openavc:auth-required` so the App can drop back to the login screen.
 */

const STORAGE_KEY = "openavc.programmer.auth";

export interface StoredAuth {
  user: string;
  pass: string;
}

export function getStoredAuth(): StoredAuth | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.user === "string" && typeof parsed?.pass === "string") {
      return parsed;
    }
  } catch {
    /* fall through */
  }
  return null;
}

export function setStoredAuth(user: string, pass: string): void {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ user, pass }));
}

export function clearStoredAuth(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function getAuthHeader(): string | null {
  const a = getStoredAuth();
  if (!a) return null;
  return "Basic " + btoa(`${a.user}:${a.pass}`);
}

/**
 * Returns the WebSocket subprotocol array to authenticate with the cached
 * password, or undefined if no credentials are stored. We always use the
 * base64 form so passwords containing characters outside the HTTP token
 * grammar still work.
 */
export function getAuthSubprotocols(): string[] | undefined {
  const a = getStoredAuth();
  if (!a) return undefined;
  const b64 = btoa(unescape(encodeURIComponent(a.pass)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return [`auth.b64.${b64}`];
}

/**
 * Fires when the server responds 401 to an authenticated request, telling
 * the App to clear state and show the login screen.
 */
export const AUTH_REQUIRED_EVENT = "openavc:auth-required";

function isApiUrl(url: string): boolean {
  // /api, /api/..., or /tunnel/<id>/api/...
  return /(^|\/)api(\/|$|\?)/.test(url);
}

let installed = false;

/**
 * Patches the global `fetch` so every /api request carries an Authorization
 * header derived from the stored password. Call once at app startup.
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

    const attach = isApiUrl(url);
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

    if (attach && res.status === 401) {
      clearStoredAuth();
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    }

    return res;
  };
}

/**
 * Asks the server whether a password is configured. We use this dedicated
 * endpoint instead of probing a protected route because browsers auto-attach
 * cached HTTP Basic credentials to fetches — that would make a protected
 * endpoint return 200 even when the SPA has no usable credentials of its
 * own (which it needs for the WebSocket handshake).
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
