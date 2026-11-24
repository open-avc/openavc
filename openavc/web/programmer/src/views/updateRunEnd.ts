/**
 * What the Updates page says when the server restarted on the version it was
 * on: how the update or rollback ended, as the server's start settled it and
 * its Update History row says (`system.update_last_result*`). Without an end
 * to read, only that the version did not change.
 */
export function sameVersionRestartSentence(liveState: Record<string, unknown>): string {
  const end = String(liveState["system.update_last_result"] ?? "success");
  const message = String(liveState["system.update_last_result_message"] ?? "");
  const unsettled = end !== "success" && message !== "";
  if (unsettled) return message + ".";
  return "The server restarted but the version did not change (still v" +
    String(liveState["system.version"] ?? "") + "). Check Update History.";
}
