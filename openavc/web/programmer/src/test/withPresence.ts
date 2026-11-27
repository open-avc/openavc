/**
 * The presence word the server publishes beside a device's parts, for a test
 * that sets the parts. The server writes `device.<id>.presence` in the same
 * batch as each part it is made of, so live state never holds one without the
 * other; a test that set only the parts would be testing a state no running
 * system is in.
 *
 * The first word in PRESENCE_WORDS whose part holds names it, as
 * core/device_presence.py's `presence_word` does. A test that sets the word
 * itself keeps it.
 */
import { PRESENCE_WORDS, type PresenceWord } from "../components/shared/devicePresence";

type Part = (prop: string) => unknown;

const HOLDS: Record<Exclude<PresenceWord, "connecting">, (part: Part) => boolean> = {
  disabled: (part) => part("enabled") === false,
  waiting_for_driver: (part) => Boolean(part("orphaned")),
  online: (part) => Boolean(part("connected")),
  restarting: (part) => Boolean(part("restarting")),
  asleep: (part) => Boolean(part("asleep")),
  paused: (part) => Boolean(part("paused")),
  offline: (part) => Boolean(part("offline_reason")),
};

const DEVICE_KEY = /^device\.([^.]+)\.[^.]+$/;

export function withPresence(state: Record<string, unknown>): Record<string, unknown> {
  const out = { ...state };
  const ids = new Set(Object.keys(state).flatMap((key) => DEVICE_KEY.exec(key)?.[1] ?? []));
  for (const id of ids) {
    const key = `device.${id}.presence`;
    if (key in out) continue;
    const part: Part = (prop) => state[`device.${id}.${prop}`];
    out[key] = PRESENCE_WORDS.find((word) => word !== "connecting" && HOLDS[word](part)) ?? "connecting";
  }
  return out;
}
