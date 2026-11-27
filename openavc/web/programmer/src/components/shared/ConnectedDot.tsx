/**
 * The filled/hollow dot that says whether a device is talking to us, as it is
 * drawn inside a picker row.
 *
 * The device dropdowns spelled this with the literal characters U+25CF and
 * U+25CB inside an `<option>`, because an `<option>` can hold nothing else. A
 * picker row can, so it gets a real dot that lines up with the text and takes
 * a colour.
 */
import type { ReactNode } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { PRESENCE_LABEL, presenceIn, type PresenceWord } from "./devicePresence";

export function ConnectedDot({
  presence,
}: {
  /** The device's presence word. Filled while connected, hollow for every
   *  other word; the tooltip says which. */
  presence: PresenceWord;
}) {
  const connected = presence === "online";
  return (
    <span
      aria-hidden
      title={PRESENCE_LABEL[presence]}
      style={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        flexShrink: 0,
        background: connected ? "#10b981" : "transparent",
        border: connected ? "none" : "1px solid var(--text-muted)",
        boxSizing: "border-box",
      }}
    />
  );
}

/**
 * Ready to hand to `deviceOptions`' `prefix`.
 *
 * Reads the store imperatively, exactly as the `<option>` lists it replaces
 * did: the dot is a snapshot taken when the list is built, so a device that
 * connects while the panel is open does not repaint until it is reopened.
 * Pre-existing behaviour, kept deliberately rather than fixed in passing.
 */
export function connectedDot(deviceId: string): ReactNode {
  return <ConnectedDot presence={presenceIn(useConnectionStore.getState().liveState, deviceId)} />;
}
