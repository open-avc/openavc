import type { GroupCommandResult } from "../../store/logStore";

type Member = GroupCommandResult["deviceResults"][number];

/** How one member of a group command step reads: it took the command, the
 *  command reached it but did not finish (partly done, or not confirmed), or
 *  it failed. */
export type MemberTone = "done" | "reached" | "failed";

export function memberTone(member: Member): MemberTone {
  if (member.success) return "done";
  return member.sent ? "reached" : "failed";
}

/** The member chip's tooltip: what happened, in the step's own words. */
export function memberTitle(member: Member): string {
  if (member.success) return "Success";
  return member.message || member.error || "Failed";
}
