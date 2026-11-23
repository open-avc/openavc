import type { StartupStatus } from "../api/startupStatus";
import { getTunnelPrefix } from "../api/base";
import { FailedStart } from "../components/shared/RestartProgressDialog";
import { landingUrl } from "../components/shared/restartPollHelpers";
import { secondaryBtn } from "./updatesStyles";

/** The foot of the install dialog while it waits on the restart: a failed
 *  start of the new version, as the restart dialog shows one; past two
 *  minutes with nothing moving, a way out; otherwise what not to do, or that
 *  a start after a failed one is under way. While the update itself reports
 *  an error (said below it), only that last line. */
export function InstallWaitFooter({
  startFailure,
  startingAgain,
  watchdogTripped,
  errored,
  onClose,
}: {
  startFailure: StartupStatus | null;
  startingAgain: boolean;
  watchdogTripped: boolean;
  errored: boolean;
  onClose: () => void;
}) {
  if (startFailure && !errored) {
    const here = { origin: window.location.origin, prefix: getTunnelPrefix() };
    return (
      <FailedStart
        failure={{ status: startFailure, page: landingUrl("/programmer/", here.origin, here) }}
        onClose={onClose}
      />
    );
  }
  if (watchdogTripped && !errored) {
    return (
      <div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          This is taking longer than expected. The server may still be coming back up.
          If this dialog doesn't close within a few minutes, check the server logs.
          It's safe to close this dialog; any update in progress continues on the server.
        </div>
        <button style={secondaryBtn} onClick={onClose}>
          Close
        </button>
      </div>
    );
  }
  return (
    <div style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "center" }}>
      {startingAgain ? "Starting again..." : "Do not close this window or power off the system."}
    </div>
  );
}
