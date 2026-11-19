/** What a Programmer tab shows when its connection comes back to a start
 *  that failed (useWebSocket.ts, from api/serverReady.ts): the server's
 *  reason and what happens next, until it is dismissed or a start succeeds.
 *  Said once for a run of failed starts with the same reason and next step,
 *  since each retry reconnects the tab; a different reason, or a run that
 *  has stopped retrying and waits for Start again, replaces it. */
import { useToastStore } from "../store/toastStore";
import type { StartupStatus } from "../api/startupStatus";

export interface StartFailureNotice {
  /** What a reconnect's wait ended on (null: the connection it waited for
   *  is gone): shows or takes down the notice, and says whether the server
   *  is ready to load from. */
  heard: (status: StartupStatus | null) => boolean;
}

export function startFailureNotice(): StartFailureNotice {
  let shown: { id: string; said: string } | null = null;
  const takeDown = () => {
    if (shown) useToastStore.getState().removeToast(shown.id);
    shown = null;
  };
  const failed = (status: StartupStatus) => {
    const said = failureMessage(status);
    if (shown?.said === said) return;
    takeDown();
    shown = { id: useToastStore.getState().addToast(said, "error", 0), said };
  };
  return {
    heard(status) {
      if (status?.phase === "failed") failed(status);
      if (status?.phase !== "ready") return false;
      takeDown();
      return true;
    },
  };
}

/** The notice's words, as the server's own page says them. */
export function failureMessage(status: StartupStatus): string {
  const parts = ["OpenAVC could not start."];
  if (status.error) parts.push(asSentence(status.error));
  if (status.held) parts.push("Fix the problem, then reload this page and press Start again.");
  else if (status.retryIn !== null) parts.push("It tries again by itself.");
  return parts.join(" ");
}

function asSentence(text: string): string {
  const trimmed = text.trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}
