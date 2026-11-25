/**
 * What a command asks before a person sends it by hand: the driver's own
 * sentence trimmed, one plain question for `confirm: true`, nothing for a
 * blank one.
 *
 * The server reads every saved driver this way (`openavc/drivers/command_model.py`,
 * `confirm_sentence`) and serves the answer: the device page's Send reads
 * `command_confirms`, a Quick Action button its action's `confirm`, a device
 * audit its command list's `confirm`. This copy is for what no server has read
 * yet, the Driver Builder's draft: Live Test's question and the two "Ask
 * before" ticks. `tests/fixtures/command_confirm_cases.json` holds both to
 * the same answer, case for case.
 */

/** What `confirm: true` asks: one plain question naming what is sent. */
export const CONFIRM_QUESTION = "Send {label}?";

/** What a `confirm` asks before `label` is sent, or null for nothing (false,
 *  left out, blank, or not a flag or a sentence). */
export function confirmSentence(confirm: unknown, label: string): string | null {
  if (confirm === true) return CONFIRM_QUESTION.replace("{label}", () => label);
  if (typeof confirm === "string") return confirm.trim() || null;
  return null;
}

/** Whether a `confirm` asks anything at all. */
export function asksFirst(confirm: unknown): boolean {
  return confirmSentence(confirm, "") !== null;
}

/** Whether a device-page action's `confirm` decides for its button: false
 *  (ask nothing), true or a sentence. Left out, null or blank, the promoted
 *  command's own `confirm` asks. */
export function setsItsOwnConfirm(confirm: unknown): boolean {
  return typeof confirm === "boolean" || (typeof confirm === "string" && confirm.trim() !== "");
}

/** What Live Test asks before it sends a command of the draft, or null to
 *  send at once. */
export function commandConfirmMessage(
  command: { confirm?: unknown } | null | undefined,
  label: string,
): string | null {
  return confirmSentence(command?.confirm, label);
}
