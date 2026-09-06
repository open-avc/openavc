/**
 * A reading nobody is hearing any more.
 *
 * The device page keeps the last value it heard while the device is
 * unreachable, and that is deliberate: the panel blanks such a reading because
 * a room-facing control must not assert a wrong number, but this page is where
 * somebody diagnoses, and "it was 511 when we lost it" is often what they came
 * for. What was missing is the qualifier. An unmarked `511` in a LEVEL column
 * reads as current, and the Offline banner that would say otherwise is a
 * hundred state keys further up a page that has scrolled past it.
 *
 * So: mark, do not remove. The value dims and says what it is.
 *
 * Only the driver's OWN readings are marked. `connected`, a child's `online`,
 * the fault codes and the friendly label are the platform's statements about
 * the device rather than readings from it, and they are true right now —
 * calling those "last heard" would be nonsense. Each caller knows which of its
 * keys are which: the device page has the driver's declared `state_variables`,
 * and a child's four reserved props are named in the contract.
 */

export const staleValueStyle: React.CSSProperties = {
  color: "var(--text-muted)",
};

export const DEVICE_STALE_TITLE =
  "The last value this device reported before it stopped answering. Not a current reading.";

export const CHILD_STALE_TITLE =
  "The last value reported before this stopped answering. Not a current reading.";

/** The words beside a stale value, where there is room for them. A column of
 *  three numbers has no such room and carries the dimming plus the row's own
 *  status mark instead. */
export function LastHeard() {
  return (
    <span
      data-testid="last-heard"
      style={{
        marginLeft: 6,
        fontSize: 11,
        fontStyle: "italic",
        color: "var(--text-muted)",
        whiteSpace: "nowrap",
      }}
    >
      last heard
    </span>
  );
}
