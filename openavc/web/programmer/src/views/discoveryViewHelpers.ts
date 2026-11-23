// Pure logic behind the Discovery view (no React imports) so the
// node-harness regression suite can exercise it directly.

import type { DiscoveredPort } from "../api/discoveryClient";

// A device's Open Ports line: each port with the name the server gave it and
// the project device that holds it. The names are decided on the server, so
// the card, the export and the assistant read the same.
export function openPortsText(ports: DiscoveredPort[]): string {
  if (ports.length === 0) return "None detected";
  return ports
    .map(({ port, label, in_use_by }) => {
      const said = [label, in_use_by ? `in use by ${in_use_by}` : null].filter(Boolean);
      return said.length > 0 ? `${port} (${said.join(", ")})` : String(port);
    })
    .join(", ");
}

// The order devices of one state are listed in: by what each is called (the
// server's `name`, its card's heading), letter case aside.
export function compareScannedNames(a: { name: string }, b: { name: string }): number {
  return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
}

// The id of the project device that connects to a scanned address (its IP,
// or the hostname the scan read), or null. A device's address is in the
// project's connections table; an older project may still hold it in the
// device's own config.
export function projectDeviceAtAddress(
  project: { devices: { id: string; config?: Record<string, unknown> }[];
             connections?: Record<string, Record<string, unknown>> } | null | undefined,
  ip: string,
  hostname: string | null | undefined,
): string | null {
  if (!project) return null;
  const wanted = new Set([ip.toLowerCase()]);
  if (hostname) wanted.add(hostname.toLowerCase());
  for (const d of project.devices) {
    const host = project.connections?.[d.id]?.host ?? d.config?.host;
    if (typeof host === "string" && wanted.has(host.trim().toLowerCase())) return d.id;
  }
  return null;
}

// The snmp_community field for a scan/save payload. The stored community is
// a credential the config endpoint never returns, so a blank input means
// "keep the stored value" and the field must be omitted entirely (undefined
// is dropped by JSON.stringify).
export function snmpCommunityField(input: string): string | undefined {
  return input.trim() === "" ? undefined : input;
}

// Making a device's greeting readable on the Discovery card.
//
// What a scan captures on port 22 or 23 is raw bytes off the wire, and a
// good part of them were never text. A telnet server opens by negotiating
// options — three bytes each, 0xFF then a verb then an option — before it
// says a word, and an SSH server follows its one-line version string with
// the binary key exchange, whose 16-byte random cookie looks different on
// every scan. Printed verbatim, both read as damage: "ÿûÿüÿþÿþ" ahead of a
// welcome line, or a fistful of accented letters after "SSH-2.0-dropbear".
// Nothing is wrong with the device or the scan. We were just showing the
// wire.
//
// This is display only. The server keeps every byte exactly as captured,
// and nothing here runs anywhere near driver matching — probes match on
// the raw bytes long before this text is built.
//
// The rule is deliberately about content rather than protocol: a device
// banner is ASCII, so anything outside printable ASCII is not part of the
// message. That also settles a second oddity, which is that the same
// telnet bytes reach us decoded two different ways — the banner grab uses
// UTF-8 and leaves U+FFFD where a byte wasn't valid, while a driver probe
// uses latin-1 and leaves "ÿû". Both are junk under this rule, so the card
// now reads the same either way.

// Tab, carriage return and newline are separators rather than content.
// They neither survive as-is nor count toward a run of binary.
const SEPARATORS = "\t\r\n";

function isPrintable(ch: string): boolean {
  const code = ch.charCodeAt(0);
  return code >= 0x20 && code <= 0x7e;
}

/**
 * How many unprintable characters in a row mean the text has ended and the
 * rest is a binary payload.
 *
 * Four is chosen against the two things we actually see. SSH puts its
 * packet length, padding length and message type straight after the
 * version string, which is the four-byte run that cuts it in exactly the
 * right place; the random cookie past it can't be relied on, because a
 * cookie byte lands on a printable letter often enough to break any longer
 * run. Telnet's negotiation bursts are three bytes apiece, so a lone one
 * mid-banner collapses to a space instead of truncating the line.
 */
const BINARY_RUN = 4;

export interface CleanedBanner {
  /** The readable part, whitespace collapsed. Empty when there was none. */
  text: string;
  /** True when a binary payload was dropped off the end. */
  hadBinary: boolean;
  /** Unprintable characters inside the part kept (each shown as a space). */
  noise: number;
}

/**
 * Reduce a captured banner to the part a person can read.
 *
 * Leading junk is dropped outright, since a preamble ahead of the first
 * readable character is negotiation or framing every time. After that the
 * first run of {@link BINARY_RUN} unprintable characters ends the message,
 * and everything from the start of that run is dropped. Shorter runs
 * become a single space.
 */
export function cleanBannerText(raw: string): CleanedBanner {
  // Skip the preamble: everything before the first character worth showing.
  let start = 0;
  while (start < raw.length && !isPrintable(raw[start])) start++;

  // Find where the message stops being a message.
  let end = raw.length;
  let runStart = -1;
  let runLength = 0;
  for (let i = start; i < raw.length; i++) {
    const ch = raw[i];
    if (isPrintable(ch)) {
      runStart = -1;
      runLength = 0;
      continue;
    }
    // A separator neither extends a run nor breaks one, so that the
    // "\r\n" ahead of a binary payload doesn't hide it.
    if (SEPARATORS.includes(ch)) continue;
    if (runStart < 0) runStart = i;
    runLength++;
    if (runLength >= BINARY_RUN) {
      end = runStart;
      break;
    }
  }

  const kept = raw.slice(start, end);
  const text = Array.from(kept, (ch) => (isPrintable(ch) ? ch : " "))
    .join("")
    .replace(/\s+/g, " ")
    .trim();

  // Only claim a payload was dropped if something was actually there.
  const hadBinary = end < raw.length && raw.slice(end).trim().length > 0;
  const noise = Array.from(kept).filter((ch) => !isPrintable(ch) && !SEPARATORS.includes(ch)).length;

  return { text, hadBinary, noise };
}

/**
 * Whether a reply reads as text: at least two readable characters, and no
 * more than half as many unprintable ones among them. A binary frame that
 * happens to hold a printable byte or two (a checksummed reply) is not
 * text, and is said by its pattern or its bytes instead.
 */
export function readsAsText(raw: string): boolean {
  const { text, noise } = cleanBannerText(raw);
  const readable = text.replace(/\s/g, "").length;
  return readable >= 2 && noise * 2 <= readable;
}

/**
 * The one-line form for a card: cleaned, capped, and marked with an
 * ellipsis when anything was left off either end of the cap.
 */
export function bannerDisplayText(raw: string, maxLength: number): string {
  const { text, hadBinary } = cleanBannerText(raw);
  if (!text) return "";
  if (text.length > maxLength) return `${text.slice(0, maxLength).trimEnd()}…`;
  return hadBinary ? `${text} …` : text;
}
