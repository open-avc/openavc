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

/**
 * Ordered, de-duplicated origins to probe for the restarted server.
 *
 * `configOrigin` is derived from the saved config (scheme + the configured
 * HTTP/HTTPS port) — authoritative for DIRECT access, where a port or protocol
 * change is a real address change the browser must follow. But behind a reverse
 * proxy or the cloud tunnel the config's internal port is not how the browser
 * reaches the server at all; there the CURRENT page origin is the only valid
 * address, and a settings restart leaves it unchanged.
 *
 * Probing both — current origin first, except on a protocol switch where the
 * old origin is going away — covers every deployment without having to detect
 * which one we're in: direct port/scheme changes still succeed via
 * `configOrigin`, proxy/tunnel restarts succeed via `currentOrigin`.
 */
export function candidateOrigins(
  configOrigin: string,
  currentOrigin: string,
  isProtocolSwitch: boolean,
): string[] {
  const ordered = isProtocolSwitch
    ? [configOrigin]
    : [currentOrigin, configOrigin];
  return [...new Set(ordered)];
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
