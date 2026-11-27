/**
 * What the person who ran a macro by hand is told when it did not go cleanly.
 *
 * THE sentences for a run's answer (`POST /macros/{id}/execute`, and Fire
 * now's `POST /triggers/{id}/test`): the macro editor's Test button and a
 * button action's Test read `runTrouble`, and Fire now says `didNotStart`
 * after what the macro was handed. Why a run did not start is the server's
 * `message`, from its reason table, never a guess here.
 */

import type { RunAnswer } from "../../api/restClient";

export type { RunAnswer };

/** A run the macro refused: that it did not start, then the server's reason.
 *  An answer without one names no cause. */
export function didNotStart(answer: RunAnswer): string {
  const head = "The macro didn't start.";
  return answer.message ? `${head} ${answer.message}` : head;
}

const TROUBLE: Record<string, (answer: RunAnswer) => string> = {
  failed: () => "The macro ran, but some of its steps didn't. The log says which ones and why.",
  cancelled: () => "The macro was cancelled before it finished, so its later steps didn't run.",
  skipped: (answer) => didNotStart(answer),
};

/** What to tell the person, or null for a run that went cleanly or is still
 *  going (the progress bar shows that one). */
export function runTrouble(answer: RunAnswer): string | null {
  return TROUBLE[answer.status]?.(answer) ?? null;
}
