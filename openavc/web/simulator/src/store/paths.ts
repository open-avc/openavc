/**
 * Where this UI is mounted, and where the platform is.
 *
 * Its own module so `api` and `session` can both read it without importing
 * each other — they need each other's halves (one wants the credential, the
 * other wants the sign-in URL), and a cycle between them is the kind of thing
 * that works in dev and bites once bundled.
 */

// Always same-origin, but not always at the root: the simulator process serves
// this UI at / on :19500, and the main server proxies it under /simulator/ so
// it is reachable from another machine and through the cloud tunnel. Deriving
// the prefix from the current path means one build works in both places, with
// no environment flag to get wrong.
export const BASE = (() => {
  const marker = "/simulator/";
  const i = window.location.pathname.indexOf(marker);
  return i === -1 ? "" : window.location.pathname.slice(0, i + marker.length - 1);
})();

// The PLATFORM's mount point, one level up from us. Sign-in lives there
// (`/api/auth/session`), not on the simulator process -- that one has no idea
// what a password is. Under the cloud tunnel both carry the same prefix, so
// deriving one from the other keeps them in step.
export const APP_ROOT = BASE.endsWith("/simulator")
  ? BASE.slice(0, -"/simulator".length)
  : "";
