import { useLogStore } from "../store/logStore";
import type { SkipNote } from "../store/logStore";

/**
 * Why a trigger's fire or a macro's run did not go ahead, from the server's
 * `trigger.skipped` and `macro.skipped`, kept for the trigger card and the
 * macro editor to show (`SkipLine`).
 *
 * The reason is read from `code` and `message`, the server's reason table
 * (`core/macro_text.py`), and never worded here. Not from `reason`: on a
 * trigger that is the code, on a macro the sentence.
 *
 * A skip is not a run. It never ends the run in flight that refused it, and
 * never becomes the trigger's last-run record, which the server keeps: a busy
 * trigger's cooldown skips would otherwise erase a failure it still holds. A
 * fire the trigger made that its macro refused, that a project change ended
 * while it waited, or that a condition the trigger cannot read stopped, is a
 * run: the record keeps its reason
 * (`trigger.completed`'s `code` and `message`, `triggerRuns.runSkip`).
 */

type Message = Record<string, unknown>;

function noteOf(msg: Message): SkipNote {
  return { code: String(msg.code ?? ""), message: String(msg.message ?? ""), at: Date.now() };
}

const HANDLERS: Record<string, (msg: Message) => void> = {
  "trigger.skipped": (msg) => {
    const store = useLogStore.getState();
    const triggerId = msg.trigger_id as string;
    // Whatever it was waiting in (a delay, a debounce, the queue) has ended.
    if (triggerId in store.triggerPending) store.setTriggerPending(triggerId, null);
    store.setTriggerSkip(triggerId, noteOf(msg));
  },
  "macro.skipped": (msg) => {
    useLogStore.getState().setMacroSkip(msg.macro_id as string, noteOf(msg));
  },
  // A fire supersedes the reason the last one did not go ahead. A Fire now
  // does not: like the trigger's own record, it leaves what the trigger did
  // on its own for the person who opened the card to look at it.
  "trigger.fired": (msg) => {
    if (msg.trigger_type === "test") return;
    useLogStore.getState().setTriggerSkip(msg.trigger_id as string, null);
  },
  "macro.started": (msg) => {
    useLogStore.getState().setMacroSkip(msg.macro_id as string, null);
  },
};

/** Keep or end a skip's note for the message, if it is one of the four. */
export function applySkipMessage(msg: Message): void {
  HANDLERS[msg.type as string]?.(msg);
}
