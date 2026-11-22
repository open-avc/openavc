/** What a Programmer tab shows of how the server's start went
 *  (useWebSocket.ts asks /api/startup-status at the first connect and each
 *  time the socket closes, and api/serverReady.ts at a reconnect).
 *
 *  A start that failed keeps the connection out: the server's reason and
 *  what happens next, until it is dismissed or a start succeeds. Said once
 *  for a run of failed starts with the same reason and next step, since each
 *  retry asks again; a different reason, or a run that has stopped retrying
 *  and waits for Start again, replaces it.
 *
 *  A start that went on past part of itself (the cloud connection, the
 *  update checks, the scripts): each part's sentence, once per start, until
 *  it is dismissed. The tab hears the same start again after a network drop
 *  that the server stayed up through, and says nothing more; a start the
 *  tab saw under way (starting, failed or stopping) is a new one. */
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
  // What the notice of this start's problems said ("" when it had none),
  // and that notice; null once a start under way is heard.
  let problemsSaid: string | null = null;
  let problemsShown: string | null = null;
  const takeDown = () => {
    if (shown) useToastStore.getState().removeToast(shown.id);
    shown = null;
  };
  const wentOnPast = (status: StartupStatus) => {
    const said = problemsMessage(status);
    if (said === problemsSaid) return;
    if (problemsShown) useToastStore.getState().removeToast(problemsShown);
    problemsShown = said ? useToastStore.getState().addToast(said, "warning", 0) : null;
    problemsSaid = said;
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
      if (status && status.phase !== "ready") problemsSaid = null;
      if (status?.phase !== "ready") return false;
      takeDown();
      wentOnPast(status);
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

/** The notice of what a start went on past: "" when it went on past nothing. */
export function problemsMessage(status: StartupStatus): string {
  if (status.problems.length === 0) return "";
  return ["Some parts of OpenAVC did not start.", ...status.problems.map((p) => asSentence(p.detail))]
    .join(" ");
}

function asSentence(text: string): string {
  const trimmed = text.trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}
