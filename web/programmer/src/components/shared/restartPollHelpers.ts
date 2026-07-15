// Poll-decision helpers for RestartProgressDialog, split out so the cert-error
// heuristic is unit-testable without React.

/**
 * The health-probe URL for the post-restart server.
 *
 * `targetUrl` is the PAGE the browser lands on after restart, e.g.
 * `https://host:8443/programmer`. The health endpoint is NOT under that SPA
 * mount — it's at the server root, `/api/health`. Appending `/api/health` onto
 * the full `targetUrl` string probes `/programmer/api/health`, which the static
 * mount answers with 404 on every deployment, so the poll never sees a 2xx and
 * always reports a false timeout even though the server is up. Resolving an
 * absolute `/api/health` against `targetUrl` keeps its origin (scheme, host,
 * post-restart port) and replaces the path.
 */
export function healthProbeUrl(targetUrl: string): string {
  return new URL("/api/health", targetUrl).toString();
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
