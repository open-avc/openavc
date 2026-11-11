/**
 * The Theme Studio's status line and the hold on its file buttons. A file action
 * (Save Changes, Save as Custom, Delete, Duplicate, an import and the two
 * answers to an import that collided) runs through `attempt`: the buttons are
 * held until it ends, and the line then says what it did, or why it failed.
 * Delete, Overwrite and Keep both leave the line already showing until they end;
 * the others clear it as they start.
 *
 * An action is its `work`, which waits on the server, and its `done`, which acts
 * on the answer (holds the theme reloaded, chooses the theme it made, refreshes
 * the list) and returns the line. `done`, the line and the end of the hold happen
 * in one moment, with no wait between them, and that is behaviour, not style: the
 * Builder draws the Studio on a newly chosen theme as soon as it is told, and the
 * Studio's load of that theme clears the line, so a wait between choosing and
 * saying would change whether the line is seen.
 *
 * A failure is said from what was thrown, read in the one `catch` here
 * (`parseApiError`: the server's sentence for a refusal, else the error's own
 * message), and worded by the action: `orStepFailed` names the step only when
 * that says nothing, `importFailed` always names it first. An information line
 * clears itself after two and a half seconds; an error stays until something
 * replaces it.
 */
import { useEffect, useState } from "react";
import { parseApiError } from "../../api/errors";

export interface StatusLine {
  kind: "info" | "error";
  text: string;
}

const INFO_SHOWN_MS = 2500;

/** What was thrown, or else the step that failed: "Save failed". */
export const orStepFailed = (step: string) => (said: string) => said || `${step} failed`;

/** "Import failed: " and what was thrown. */
export const importFailed = (said: string) => `Import failed: ${said}`;

export interface FileAction<T> {
  /** What the action waits on. */
  work: () => Promise<T>;
  /** What it does with the answer, and the line that says so (null: say nothing). */
  done: (answer: T) => string | null;
  /** The line for a failure, from what was thrown. */
  failure: (said: string) => string;
  /** Leave the line already showing until the action ends. */
  keepLine?: boolean;
}

export function useStatusLine() {
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState<StatusLine | null>(null);

  useEffect(() => {
    if (!line || line.kind === "error") return;
    const timer = setTimeout(() => setLine(null), INFO_SHOWN_MS);
    return () => clearTimeout(timer);
  }, [line]);

  /** Run a file action with the buttons held; true when it did not fail. */
  async function attempt<T>({ work, done, failure, keepLine = false }: FileAction<T>): Promise<boolean> {
    setBusy(true);
    if (!keepLine) setLine(null);
    let ok = true;
    try {
      const said = done(await work());
      if (said !== null) setLine({ kind: "info", text: said });
    } catch (e) {
      setLine({ kind: "error", text: failure(parseApiError(e)) });
      ok = false;
    }
    setBusy(false);
    return ok;
  }

  return { busy, line, say: setLine, attempt };
}
