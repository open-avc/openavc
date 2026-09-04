/**
 * Whether a binding's chosen command can actually run, asked of the platform.
 *
 * The Builder knows the project. It does not know the drivers -- which command
 * takes which parameters, and which of those the device refuses to do without.
 * So a fader bound to a real command with its required parameters left empty
 * saved clean, showed no badge, and passed Validate with "No Issues", while
 * every press on the wall was refused with `'set_fader': 'channel' is required`.
 *
 * The rule is not brought over here to run. It is the platform's, asked for
 * over `POST /api/ui/validate-actions` -- the same rule the runtime enforces
 * and the macro lint reports, for the reason `macroLint.ts` already records: a
 * second copy in TypeScript is what drifts, and only the server knows what the
 * installed drivers declare.
 *
 * Answers are keyed by the action OBJECT that was asked about, not by a path
 * built twice. The caller walks the project once, hands over what it found, and
 * looks the answers back up by identity -- so there is no key format for a
 * walker and a validator to disagree about.
 */
import { request } from "./base";

/** What each asked-about action is wrong about. An action with nothing is absent. */
export type CommandIssues = Map<Record<string, unknown>, string[]>;

/**
 * Ask about these actions. Never throws and never rejects.
 *
 * A failed request answers "nothing known", exactly as an unloaded driver does
 * on the server: Validate is a button somebody pressed, and it must produce its
 * other findings whether or not this one could be asked.
 */
export async function commandIssues(
  actions: Record<string, unknown>[],
): Promise<CommandIssues> {
  const found: CommandIssues = new Map();
  if (!actions.length) return found;
  const body: Record<string, unknown> = {};
  actions.forEach((action, i) => {
    body[String(i)] = action;
  });
  try {
    const reply = await request<{ issues: Record<string, string[]> }>(
      "/ui/validate-actions",
      { method: "POST", body: JSON.stringify({ actions: body }) },
    );
    for (const [index, messages] of Object.entries(reply?.issues ?? {})) {
      const action = actions[Number(index)];
      if (action && Array.isArray(messages) && messages.length) {
        found.set(action, messages);
      }
    }
  } catch {
    // No opinion.
  }
  return found;
}
