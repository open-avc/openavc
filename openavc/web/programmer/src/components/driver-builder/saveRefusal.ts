import type { DriverDefinition } from "../../api/types";
import type { ValidationIssue } from "./validateDriver";

/** A save the server refused: its whole message, the errors it listed, and
 *  the draft it refused. */
export interface SaveRefusal {
  text: string;
  errors: string[];
  draft: DriverDefinition;
}

function joinAnd(items: string[]): string {
  return items.length < 2
    ? items.join("")
    : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/**
 * What the editor's header says about the last save.
 *
 * The issue lists under the tabs show the server's own messages word for
 * word, so when every error a refusal listed is already in them the header
 * names the tabs instead of repeating the errors. While the lists do not show
 * them yet (a check still on its way) the whole refusal stays. Once the draft
 * is edited the refusal no longer describes it, and the lists are what count.
 */
export function saveErrorText(
  error: string | null,
  refusal: SaveRefusal | null,
  draft: DriverDefinition,
  issues: ValidationIssue[],
  tabs: { id: string; label: string }[],
): string | null {
  if (!error) return null;
  if (!refusal || refusal.text !== error || refusal.errors.length === 0) return error;
  if (refusal.draft !== draft) return null;
  const shown = new Map<string, string>();
  for (const issue of issues) {
    if (issue.severity === "error") shown.set(issue.message, issue.section);
  }
  if (!refusal.errors.every((e) => shown.has(e))) return error;
  const sections = new Set(refusal.errors.map((e) => shown.get(e)));
  const labels = tabs.filter((t) => sections.has(t.id)).map((t) => t.label);
  if (labels.length !== sections.size) return error;
  const what = refusal.errors.length === 1 ? "the error" : "the errors";
  const where = labels.length === 1 ? `the ${labels[0]} tab` : `the ${joinAnd(labels)} tabs`;
  return `The driver was not saved. Fix ${what} on ${where}.`;
}
