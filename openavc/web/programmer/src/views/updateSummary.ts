/** What the update notice says above its list: how many changes loading the
 *  project made, and how many issues Validate lists in it now.
 *
 *  The issue count is Validate's own, not the server's notes, so the notice
 *  and the report its button opens say the same number. Kept apart from the
 *  card so the wording can be tested without mounting the Dashboard. */
import type { UpdateNote } from "../api/projectClient";

function counted(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** "2 changes; 1 issue to review", either half left out at zero; "" for
 *  neither (no change made and Validate not yet answered, or clean). */
export function updateSummary(notes: UpdateNote[], issueCount: number | null): string {
  const changes = notes.filter((note) => note.kind === "changed").length;
  const parts = [
    changes ? counted(changes, "change") : "",
    issueCount ? `${counted(issueCount, "issue")} to review` : "",
  ];
  return parts.filter(Boolean).join("; ");
}
