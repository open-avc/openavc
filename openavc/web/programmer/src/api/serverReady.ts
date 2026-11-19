import { BASE } from "./base";
import { askStartupStatus, type StartupStatus } from "./startupStatus";

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
 * throughout (api/startupStatus.ts reads it), so this asks it until it says
 * ready, once per connection. It is not a periodic refresh.
 *
 * Resolves with the status that ended the wait: ready, with the version
 * that started, or failed, with the start's reason, which nothing short of
 * another start changes (each one reconnects the socket, and that connection
 * runs its own wait). Resolves null when `stillWanted` turns false first: the
 * socket dropped or connected again.
 */
export async function waitForServerReady(
  stillWanted: () => boolean,
  intervalMs = POLL_INTERVAL_MS,
): Promise<StartupStatus | null> {
  while (stillWanted()) {
    // Not answering at all: the socket's own reconnect starts a new wait.
    const { status } = await askStartupStatus(`${BASE}/startup-status`, { cache: "no-store" });
    if (status?.phase === "ready" || status?.phase === "failed") {
      return stillWanted() ? status : null;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return null;
}
