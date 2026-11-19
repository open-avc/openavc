import { BASE } from "./base";
import { askStartupStatus, type StartupStatus } from "./startupStatus";

const POLL_INTERVAL_MS = 500;

/**
 * Wait until the server is answering requests, for a tab whose socket came
 * back during a restart.
 *
 * Until the server's start has finished, every /api/ call but a few answers
 * 503 "Server is starting up". This server serves a socket only once it is
 * ready (it closes one with NOT_READY_CLOSE until then, api/wsClient.ts), so
 * the wait ends at its first answer; an earlier release, reached after a
 * rollback, admits the socket while still starting. A reconnect's REST loads
 * made in that window would fail, and the tab would keep the project it had:
 * the revision from the previous run, so its next save is refused as a
 * conflict. /api/startup-status is the one route answered throughout
 * (api/startupStatus.ts reads it), so this asks it until it says ready, once
 * per connection. It is not a periodic refresh.
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
