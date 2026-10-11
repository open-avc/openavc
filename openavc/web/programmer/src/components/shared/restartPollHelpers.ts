// Poll-decision helpers for RestartProgressDialog, split out so the cert-error
// heuristic is unit-testable without React.

/**
 * The health-probe URL for a given server origin.
 *
 * The health endpoint is at the server root, `/api/health` — NOT under the
 * `/programmer` SPA mount (probing `/programmer/api/health` gets a 404 on every
 * deployment, so the poll would never see a 2xx and would always time out).
 * Resolving an absolute `/api/health` against `origin` keeps its scheme/host/
 * port and sets the correct path.
 */
export function healthProbeUrl(origin: string): string {
  return new URL("/api/health", origin).toString();
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
