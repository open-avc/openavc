import { BASE } from "./base";

const POLL_INTERVAL_MS = 500;

/**
 * Wait until the server is answering requests, for a tab whose socket came
 * back during a restart.
 *
 * The server admits a WebSocket while it is still starting, but until it has
 * finished, every other /api/ call answers 503 "Server is starting up". A
 * reconnect's REST loads made in that window fail, and the tab keeps the
 * project it had: the revision from the previous run, so its next save is
 * refused as a conflict. /api/startup-status is the one route answered
 * throughout (the server's own splash page polls it), so this asks it until
 * it says ready, once per connection. It is not a periodic refresh.
 *
 * Resolves true when the server is ready. Resolves false when `stillWanted`
 * turns false first (the socket dropped or connected again, and that
 * connection runs its own wait) or the server reports that it failed to
 * start, which nothing short of another restart changes.
 */
export async function waitForServerReady(
  stillWanted: () => boolean,
  intervalMs = POLL_INTERVAL_MS,
): Promise<boolean> {
  while (stillWanted()) {
    try {
      const res = await fetch(`${BASE}/startup-status`);
      if (res.ok) {
        const status = (await res.json()) as { ready?: boolean; error?: string | null };
        if (status.ready) return true;
        if (status.error) return false;
      }
    } catch {
      /* not answering at all: the socket's own reconnect starts a new wait */
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return false;
}
