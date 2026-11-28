/**
 * What a device's state adds up to, as the IDE reads it: THE reader of the
 * word the server publishes as `device.<id>.presence`.
 *
 * The server works the word out (core/device_presence.py holds the words,
 * their order and what each means) and writes it in the same batch as every
 * part it is made of. Every IDE surface that says whether a device is up,
 * away or in trouble reads that word through here and draws it its own way:
 * the status dot, the device page's banner, the device list's filters, the
 * Dashboard, the child-entity helper and the device pickers. None of them
 * combines `connected`, `paused`, `restarting`, `asleep`, `orphaned` or
 * `enabled` itself, which is how one paused device came to read amber on its
 * page, "offline" in the list and red on the Dashboard.
 *
 * tests/test_ide_presence_mirror.py holds these words to the server's and
 * fails an IDE module that reads those parts again. The cloud service's pages
 * run this file as it stands, to show each device's word and label.
 */

/** The words, in the server's order: the first that holds names the word. */
export const PRESENCE_WORDS = [
  "disabled",
  "waiting_for_driver",
  "online",
  "restarting",
  "asleep",
  "paused",
  "offline",
  "connecting",
] as const;

export type PresenceWord = (typeof PRESENCE_WORDS)[number];

const KNOWN: ReadonlySet<string> = new Set(PRESENCE_WORDS);

/** The live-state key that holds a device's word. */
export function presenceKey(deviceId: string): string {
  return `device.${deviceId}.presence`;
}

/** The word a published value holds. Anything else, the key not published
 *  yet among them, reads as `connecting`: not connected, and no fault known. */
export function presenceWord(value: unknown): PresenceWord {
  return typeof value === "string" && KNOWN.has(value) ? (value as PresenceWord) : "connecting";
}

/** A device's word, out of the flat live-state map. */
export function presenceIn(liveState: Record<string, unknown>, deviceId: string): PresenceWord {
  return presenceWord(liveState[presenceKey(deviceId)]);
}

/**
 * Away because someone asked for it: a command restarting it, a command that
 * put it to sleep, or Live Test or a Device Audit holding it. The IDE draws
 * these apart from a fault: not red, not among the Dashboard's disconnected
 * devices, and a child's `parent_offline` under one is expected.
 *
 * The server's AWAY_ON_PURPOSE, which decides whether an offline alert fires,
 * holds the same words; there a pause stays quiet for its first hour only.
 * Here the question is only how to draw the device, and the hour does not
 * change that.
 */
export const AWAY_ASKED_FOR: ReadonlySet<PresenceWord> = new Set<PresenceWord>([
  "restarting",
  "asleep",
  "paused",
]);

/**
 * No command sent now goes out, one that works while the device is offline
 * (a Wake-on-LAN Power On) included: no driver runs the device, or Live Test
 * or a Device Audit holds it. The server's TAKES_NO_COMMAND, in the server's
 * order. A device asleep or restarting when it was paused keeps that word,
 * and the server refuses its commands too.
 */
export const TAKES_NO_COMMAND: readonly PresenceWord[] = [
  "disabled",
  "waiting_for_driver",
  "paused",
];

/** What a person reads for the word, in a tooltip or beside a name.
 *  `connecting` reads as offline: not connected, and with no look of its own
 *  yet. A pause names who holds it where the holder is known (pausedText.ts). */
export const PRESENCE_LABEL: Record<PresenceWord, string> = {
  disabled: "Disabled",
  waiting_for_driver: "Driver not installed",
  online: "Connected",
  restarting: "Restarting",
  asleep: "Asleep",
  paused: "Paused",
  offline: "Offline",
  connecting: "Offline",
};

const SAID_BY_THE_DOT: ReadonlySet<PresenceWord> = new Set<PresenceWord>(["online", "offline", "connecting"]);

/** Said beside a device's name in a picker, where a dot tells only connected
 *  from offline: the label for every other word, else "". */
export function presenceNote(word: PresenceWord): string {
  return SAID_BY_THE_DOT.has(word) ? "" : PRESENCE_LABEL[word];
}
