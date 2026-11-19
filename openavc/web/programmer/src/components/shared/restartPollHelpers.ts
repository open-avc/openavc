// Poll-decision helpers for RestartProgressDialog, split out so the
// addresses it waits at and the cert-error heuristic are unit-testable
// without React. The wait itself is restartWait.ts.

/**
 * The health-probe URL for a given server origin: `/api/health` at the
 * server root. The Updates page's wait reads it; the restart dialog asks the
 * server's status instead (`statusProbeUrl`).
 */
export function healthProbeUrl(origin: string): string {
  return new URL("/api/health", origin).toString();
}

/** Where the page is: its origin, and the path prefix its own requests go
 *  through there (the cloud's remote access, `api/base.ts`; "" direct). */
export interface PageAt {
  origin: string;
  prefix: string;
}

/** `path` at `origin`: through the page's own prefix at the page's own
 *  origin, where the page's own requests go through it; any other origin is
 *  a direct address of the server. */
function at(path: string, origin: string, page: PageAt): string {
  return new URL(`${origin === page.origin ? page.prefix : ""}${path}`, origin).toString();
}

/** Where the restart dialog asks the server's status at `origin`. */
export function statusProbeUrl(origin: string, page: PageAt): string {
  return at("/api/startup-status", origin, page);
}

/** The page to open at `origin` once the server is back there. */
export function landingUrl(path: string, origin: string, page: PageAt): string {
  return at(path, origin, page);
}

/** Select one expected origin; an unrelated healthy listener is not a fallback. */
export function restartOrigin(current: string, original: string | null, saved: string): string {
  return original === current ? saved : current;
}

/** Configured listener on the same hostname, normalized for IPv6/default ports. */
export function configuredOrigin(
  config: { tls?: { enabled?: boolean; port?: number }; network?: { http_port?: number } } | null,
  current: string,
): string {
  const url = new URL(current);
  url.protocol = config?.tls?.enabled ? "https:" : "http:";
  url.port = String(config?.tls?.enabled ? (config.tls.port ?? 8443) : (config?.network?.http_port ?? 8080));
  return url.origin;
}

/** The listener this server is running, on the page's host: its HTTPS one
 *  for an https page when one runs, else its HTTP one, from the ports the
 *  server reports (`_listen`). A saved port does not move the running
 *  listener until the restart, so this, not the saved config, says whether
 *  the page reached the server directly. The saved config when the server
 *  does not report its ports. */
export function runningOrigin(
  config: {
    tls?: { enabled?: boolean; port?: number };
    network?: { http_port?: number };
    _listen?: { http_port?: number; https_port?: number | null };
  } | null,
  current: string,
): string {
  const ports = config?._listen;
  if (typeof ports?.http_port !== "number") return configuredOrigin(config, current);
  const url = new URL(current);
  if (url.protocol === "https:" && ports.https_port) {
    url.port = String(ports.https_port);
  } else {
    url.protocol = "http:";
    url.port = String(ports.http_port);
  }
  return url.origin;
}

/** `origin` with its host replaced, keeping its scheme and port: where the
 *  same listener answers on another address of this computer. */
export function withHost(origin: string, host: string): string {
  const url = new URL(origin);
  url.hostname = host;
  return url.origin;
}

/**
 * The origins a restart waits at, best first: the expected one, then any
 * other address a change of who can connect makes reachable. Only expected
 * addresses, never the page's own as a fallback: an unrelated healthy
 * listener is not this server. A browser the change cuts off waits only at
 * the new choice's own addresses (`alsoTry`).
 */
export function waitOrigins(targetOrigin: string, alsoTry: string[], cutOff: boolean): string[] {
  return [...new Set([cutOff ? "" : targetOrigin, ...alsoTry].filter(Boolean))];
}

/**
 * How to ask the status at one origin: a read the page can see at any
 * origin, since the server lets a page on its own network read its status
 * from another address (another port, scheme or address of this computer).
 * A refused or unreachable address rejects. No credential: the status is
 * open.
 */
export function probeInit(): RequestInit {
  return { cache: "no-store", credentials: "omit", mode: "cors" };
}

// A browser that a change of who can connect cuts off keeps trying the new
// choice's addresses this many times (one a second) before it stops and says
// where to open the Programmer instead. Long enough for a normal restart.
export const HANDOFF_ATTEMPTS = 25;

// Consecutive fetch failures before we consider that the browser might be
// rejecting the new self-signed cert (rather than the server still being down).
export const CERT_ERROR_THRESHOLD = 5;

// ...but only once polling has run long enough that a healthy restart would have
// come back. A normal HTTP->HTTPS restart rebinds within a few seconds; requiring
// this many poll attempts first stops a slow-but-healthy restart (server still
// rebinding, so every fetch throws) from being misread as a cert rejection —
// which would wrongly push the user to install a CA certificate they may not need.
export const CERT_ERROR_MIN_ATTEMPTS = 15;

/**
 * Whether persistent poll failures should be attributed to the browser
 * rejecting the new cert rather than the server still coming up. Requires the
 * page to expect a new cert, enough consecutive failures, AND that polling has
 * run past the window a normal restart needs — so transient port-rebind
 * failures early in the restart don't trip the cert-error state.
 */
export function shouldEnterCertError(
  expectsNewCert: boolean,
  consecutiveFailures: number,
  attempt: number,
): boolean {
  return (
    expectsNewCert &&
    consecutiveFailures >= CERT_ERROR_THRESHOLD &&
    attempt >= CERT_ERROR_MIN_ATTEMPTS
  );
}
