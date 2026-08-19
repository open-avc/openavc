/**
 * What the platform says is incomplete about the macros on screen.
 *
 * The failure this exists for: a step saves half-built, the IDE says "Saved",
 * and in the room that step quietly does nothing. Because the save was clean
 * there is no reason to ever open that macro again, so the search goes to the
 * projector, the cable and the network. So the mark belongs on the LIST row as
 * much as inside the open editor.
 *
 * The rules are not here. They are the platform's, asked for over
 * `POST /api/macros/validate` -- the same ones the cloud AI's macro tools run,
 * and the only place that knows which macro actions a plugin registered at
 * runtime. A second copy of them in TypeScript is the disease this campaign
 * already cured once.
 */
import { useEffect, useRef, useState } from "react";
import type { MacroConfig } from "../../api/types";
import type { MacroIssue } from "../../api/stateClient";
import * as api from "../../api/restClient";

export type { MacroIssue };

/** Issues by macro id. A macro with none is simply absent. */
export type MacroIssuesById = Record<string, MacroIssue[]>;

/** Long enough that typing a step's fields is one request, not one per key. */
const LINT_DEBOUNCE_MS = 1000;

/**
 * What gets posted: ids and the two lists the rules read, nothing else.
 *
 * Renaming a macro, changing its cancel group or toggling stop-on-error cannot
 * change a single finding, so they must not spend a request either -- this is
 * what the change signature is taken over.
 */
export function lintPayload(
  macros: MacroConfig[],
): { id: string; steps: unknown[]; triggers: unknown[] }[] {
  return macros.map((m) => ({
    id: m.id,
    steps: m.steps ?? [],
    triggers: m.triggers ?? [],
  }));
}

/** The issues on one row of one list. */
export function issuesAt(
  issues: MacroIssue[] | undefined,
  scope: "step" | "trigger",
  index: number,
): MacroIssue[] {
  return (issues ?? []).filter((i) => i.scope === scope && i.index === index);
}

/** What the editor calls each place a path can lead into. */
const NESTED_LABELS: Record<string, string> = {
  then_steps: "Then step",
  else_steps: "Else step",
  conditions: "Condition",
};

/**
 * Which row a message is about, in the words the editor puts on screen.
 *
 * One-based, because the editor draws a numbered list somebody is reading
 * rather than an array they are indexing. A problem inside a conditional says
 * which branch it is in ("Step 2 → Then step 1"): the sentence alone cannot,
 * and a conditional has two branches that look alike.
 */
export function issueLabel(issue: MacroIssue): string {
  const head = `${issue.scope === "trigger" ? "Trigger" : "Step"} ${issue.index + 1}`;
  const parts: string[] = [head];
  // Skip the first segment: that is the row `head` already names.
  for (const segment of issue.path.split(".").slice(1)) {
    const match = /^([a-z_]+)\[(\d+)\]$/.exec(segment);
    if (!match) continue;
    const label = NESTED_LABELS[match[1]];
    if (label) parts.push(`${label} ${Number(match[2]) + 1}`);
  }
  return parts.join(" → ");
}

/** "1 step", "2 steps and 1 trigger" -- the summary line's subject. */
export function issueSummary(issues: MacroIssue[]): string {
  const steps = new Set(issues.filter((i) => i.scope === "step").map((i) => i.index));
  const triggers = new Set(issues.filter((i) => i.scope === "trigger").map((i) => i.index));
  const parts: string[] = [];
  if (steps.size) parts.push(`${steps.size} step${steps.size === 1 ? "" : "s"}`);
  if (triggers.size) parts.push(`${triggers.size} trigger${triggers.size === 1 ? "" : "s"}`);
  return parts.join(" and ");
}

/**
 * Keep a lint result for every macro passed in, refreshed when one changes.
 *
 * Debounced rather than per keystroke, and the first pass runs immediately:
 * a project you open and never touch is exactly the one carrying a macro that
 * has been doing nothing for months.
 *
 * A failed request keeps the last result. A dropped connection says nothing
 * about the macros, and clearing the marks on one would read as "you fixed it".
 */
export function useMacroLint(macros: MacroConfig[]): MacroIssuesById {
  const [issues, setIssues] = useState<MacroIssuesById>({});
  const macrosRef = useRef(macros);
  macrosRef.current = macros;
  const seq = useRef(0);
  const ranOnce = useRef(false);

  const signature = JSON.stringify(lintPayload(macros));

  useEffect(() => {
    const token = ++seq.current;
    // Nothing to ask about, and asking anyway would spend the first (immediate)
    // pass on the empty moment before the project has loaded.
    if (macros.length === 0) {
      setIssues({});
      return;
    }
    const run = () => {
      api
        .validateMacros(lintPayload(macrosRef.current))
        .then((result) => {
          // Latest wins: a slow reply must not overwrite a newer one.
          if (token !== seq.current) return;
          const next: MacroIssuesById = {};
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
