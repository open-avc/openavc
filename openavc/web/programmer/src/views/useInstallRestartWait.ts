/** How the Updates page's install dialog waits on the restart an update or a
 *  rollback ends with.
 *
 *  The restart invalidates this browser's session token, so the Programmer
 *  socket reconnects refused and its snapshot never arrives. The server's
 *  startup status needs no credential, names the version that started, and
 *  says why a start failed while the server retries it, holds for Start
 *  again or rolls the update back, so the dialog asks it every two seconds
 *  through the page's own path (remote access reaches the system that way,
 *  not the cloud). A release too old to name its version there still names
 *  it in /api/health, which a rollback to one is read from. */
import { useEffect, useState } from "react";
import { BASE } from "../api/base";
import { askStartupStatus, type StartupStatus } from "../api/startupStatus";

// Fast enough that the dialog closes promptly after a swap, slow enough that
// a server rebinding its port isn't hammered while it starts.
const POLL_INTERVAL_MS = 2000;

/** What one answer means for the dialog: ready, with the version that
 *  started (null from a release too old to name it there); a start that
 *  failed, to say why; a start under way; or nothing heard (the server
 *  between processes). */
export type RestartHeard =
  | { kind: "ready"; version: string | null }
  | { kind: "failed"; status: StartupStatus }
  | { kind: "starting" }
  | { kind: "unheard" };

export function restartHeard(status: StartupStatus | null): RestartHeard {
  if (!status) return { kind: "unheard" };
  if (status.phase === "ready") return { kind: "ready", version: status.version || null };
  if (status.phase === "failed") return { kind: "failed", status };
  return { kind: "starting" };
}

export interface RestartWait {
  /** A start of the new version that failed, as the server says it. */
  startFailure: StartupStatus | null;
  /** A start under way after one that failed. */
  startingAgain: boolean;
}

/** The running version from /api/health; "" when it does not answer. */
async function versionFromHealth(): Promise<string> {
  try {
    const res = await fetch(`${BASE}/health`, { cache: "no-store" });
    if (!res.ok) return "";
    return String((await res.json())?.version ?? "");
  } catch {
    return "";
  }
}

async function ask(): Promise<RestartHeard> {
  const { status } = await askStartupStatus(`${BASE}/startup-status`, { cache: "no-store" });
  return restartHeard(status);
}

const NOTHING_SHOWN: RestartWait = { startFailure: null, startingAgain: false };

/** What the dialog shows after one answer, from what it showed before. */
function nextShown(was: RestartWait, answer: RestartHeard, failedBefore: boolean): RestartWait {
  if (answer.kind === "failed") return { startFailure: answer.status, startingAgain: false };
  if (answer.kind === "starting" && failedBefore) return { startFailure: null, startingAgain: true };
  return { startFailure: was.startFailure, startingAgain: false };
}

/** One run of the wait: each poll's answer, what it has seen, and whether
 *  it was stopped. */
class RestartWaiter {
  cancelled = false;
  private failedBefore = false;
  private readonly show: (next: (was: RestartWait) => RestartWait) => void;
  private readonly onVersion: (version: string) => void;

  constructor(
    show: (next: (was: RestartWait) => RestartWait) => void,
    onVersion: (version: string) => void,
  ) {
    this.show = show;
    this.onVersion = onVersion;
  }

  async poll(): Promise<void> {
    const answer = await ask();
    if (this.cancelled || answer.kind === "unheard") return;
    if (answer.kind === "failed") this.failedBefore = true;
    const failedBefore = this.failedBefore;
    this.show((was) => nextShown(was, answer, failedBefore));
    if (answer.kind === "ready") await this.settle(answer.version);
  }

  private async settle(version: string | null): Promise<void> {
    const known = version ?? (await versionFromHealth());
    if (!this.cancelled && known) this.onVersion(known);
  }
}

/** While `active`, ask the server's status, keep the failed start to show,
 *  and hand `onVersion` each version heard once it is ready; the caller
 *  decides whether that version settles the update. `onVersion` must keep
 *  its identity between renders. */
export function useInstallRestartWait(
  active: boolean,
  onVersion: (version: string) => void,
): RestartWait {
  const [shown, setShown] = useState<RestartWait>(NOTHING_SHOWN);
  useEffect(() => {
    if (!active) return;
    setShown(NOTHING_SHOWN);
    const waiter = new RestartWaiter(setShown, onVersion);
    const timer = window.setInterval(() => void waiter.poll(), POLL_INTERVAL_MS);
    return () => {
      waiter.cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, onVersion]);
  return shown;
}
