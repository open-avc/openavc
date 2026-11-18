/**
 * Which handlers in this project's scripts are waiting for an event nothing
 * sends, and which timers stop at the script's next edit.
 *
 * The failure this exists for is the script half of the dead control: a
 * handler decorated `@on_event("custom.start")` in a project where no macro
 * step, no control action and no other script ever emits that name. Nothing
 * raises, nothing is logged, and the handler below the decorator can be
 * perfectly correct -- so the hunt goes to the projector, the cable and the
 * network. That shape shipped in a starter template for months.
 *
 * The rules are not here. They are the platform's, asked for over
 * `POST /api/scripts/validate`, and they could not be here: the answer depends
 * on every macro, every control binding and every other script on disk, none
 * of which the editor is holding.
 *
 * Every script is asked about, not just the open one, because the mark belongs
 * on the file list as much as inside the editor -- a handler that has been
 * doing nothing for months is in a file nobody has opened. Only the open one
 * carries its text, since that is the only one that can be unsaved.
 */
import { useEffect, useRef, useState } from "react";
import type { ScriptIssue } from "../../api/stateClient";
import * as api from "../../api/restClient";

export type { ScriptIssue };

/** Issues by script id. A script with none is simply absent. */
export type ScriptIssuesById = Record<string, ScriptIssue[]>;

/** Long enough that typing is one request, not one per keystroke. */
const LINT_DEBOUNCE_MS = 1000;

/**
 * What gets posted: every script's id, and the open one's unsaved text.
 *
 * A script's own emits count towards every other script's handlers, so naming
 * them all is not politeness -- leaving one out could invent a warning about a
 * handler the missing file emits for.
 */
export function lintPayload(
  scriptIds: string[],
  openId: string | null,
  openSource: string,
): { id: string; source?: string }[] {
  return scriptIds.map((id) =>
    id === openId ? { id, source: openSource } : { id },
  );
}

/** The issues on one line of the open file, for a marker. */
export function issuesAtLine(
  issues: ScriptIssue[] | undefined,
  line: number,
): ScriptIssue[] {
  return (issues ?? []).filter((i) => i.line === line);
}

/** The script list's mark for one script: its words and its tooltip. */
export interface IssueBadge {
  text: string;
  title: string;
}

function counted(n: number, one: string, many: string): string {
  return n === 1 ? `1 ${one}` : `${n} ${many}`;
}

/** Each kind's words on the script list: one, many, and the hover. */
const BADGE_WORDS: Record<ScriptIssue["kind"], { one: string; many: string; title: string }> = {
  no_emitter: {
    one: "handler with no emitter",
    many: "handlers with no emitter",
    title: "Nothing in this project emits its event. Open the script to see which, and where to emit it.",
  },
  startup_timer: {
    one: "timer stops at the next edit",
    many: "timers stop at the next edit",
    title: "Started only when the server starts. Open the script to see where to start it instead.",
  },
  platform_event: {
    one: "system. event name",
    many: "system. event names",
    title: "Names starting with system. are OpenAVC's own. Open the script to see the custom. name to use.",
  },
};

/** What the script list says about a script's issues, worded by their kind
 *  when they are all one kind, and counted as warnings when they are not. */
export function issueBadge(issues: ScriptIssue[]): IssueBadge {
  const kind = issues[0]?.kind ?? "no_emitter";
  if (issues.some((i) => i.kind !== kind)) {
    return {
      text: counted(issues.length, "warning", "warnings"),
      title: "Open the script to see each one.",
    };
  }
  const words = BADGE_WORDS[kind];
  return { text: counted(issues.length, words.one, words.many), title: words.title };
}

/**
 * Keep a lint result for every script, refreshed as the open one is edited.
 *
 * Debounced rather than per keystroke, and the first pass runs immediately: a
 * project you open and never touch is exactly the one carrying a handler that
 * has been doing nothing since it was written.
 *
 * A failed request keeps the last result. A dropped connection says nothing
 * about the scripts, and clearing the marks would read as "you fixed it".
 */
export function useScriptLint(
  scriptIds: string[],
  openId: string | null,
  openSource: string,
): ScriptIssuesById {
  const [issues, setIssues] = useState<ScriptIssuesById>({});
  const payloadRef = useRef<{ id: string; source?: string }[]>([]);
  payloadRef.current = lintPayload(scriptIds, openId, openSource);
  const seq = useRef(0);
  const ranOnce = useRef(false);

  const signature = JSON.stringify(payloadRef.current);

  useEffect(() => {
    const token = ++seq.current;
    if (scriptIds.length === 0) {
      setIssues({});
      return;
    }
    const run = () => {
      api
        .validateScripts(payloadRef.current)
        .then((result) => {
          // Latest wins: a slow reply must not overwrite a newer one.
          if (token !== seq.current) return;
          const next: ScriptIssuesById = {};
          for (const [id, entry] of Object.entries(result)) {
            if (entry.issues.length) next[id] = entry.issues;
          }
          setIssues(next);
        })
        .catch(() => {
          /* keep the last result */
        });
    };
    if (!ranOnce.current) {
      ranOnce.current = true;
      run();
      return;
    }
    const handle = setTimeout(run, LINT_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [signature]);

  return issues;
}
