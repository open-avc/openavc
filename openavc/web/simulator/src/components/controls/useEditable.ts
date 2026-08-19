import { useState } from "react";

/**
 * Let a control show what the user is doing, not what the server last said.
 *
 * Every control here was bound straight to `device.state`, which meant the
 * thumb could not move until the write had gone to the server and the change
 * come back over the WebSocket. Under any latency at all the control fights
 * the hand holding it: it sits still, then jumps when the replies land. Typing
 * was worse -- each character round-tripped, so "true" could not be typed into
 * a boolean at all.
 *
 * The rule is narrow on purpose: while the user is interacting, the control
 * shows what they did; the moment they stop, it shows the device again. That
 * second half is what keeps it honest. A control that kept its own value until
 * the server happened to agree would stick forever the first time a device
 * clamped or rounded a write -- and simulated devices do exactly that -- so
 * the draft is released on release, not on agreement.
 */
export function useEditable<T>(serverValue: T) {
  const [draft, setDraft] = useState<{ value: T } | null>(null);

  return {
    /** What to render: the draft while interacting, else the device's value. */
    value: draft ? draft.value : serverValue,
    /** The user changed it. Hold this until they are done. */
    edit: (value: T) => setDraft({ value }),
    /** They are done. Show the device again. */
    commit: () => setDraft(null),
    editing: draft !== null,
  };
}
