// Pure logic behind the Discovery view (no React imports) so the
// node-harness regression suite can exercise it directly.

// Generic protocol fallbacks for ports the backend baseline + driver
// catalog don't already cover. Vendor-specific labels (Samsung MDC,
// Crestron CIP, PJLink, etc.) come from `portLabels` in the discovery
// store — the backend builds that map from loaded drivers + community
// catalog so it stays in sync with the catalog without UI changes.
export const PORT_LABELS: Record<number, string> = {
  23: "Telnet",
  80: "HTTP",
  443: "HTTPS",
  5900: "VNC",
  8080: "HTTP alt",
  9090: "HTTP alt",
};

// Merge the generic PORT_LABELS fallbacks with the driver/catalog-supplied
// labels from the backend. The dynamic labels win: a driver that declares
// port 5900/9090 carries the vendor name, which must not be shadowed by the
// generic "VNC" / "HTTP alt" fallback.
export function mergePortLabels(dynamic: Record<number | string, string>): Record<number, string> {
  const merged: Record<number, string> = { ...PORT_LABELS };
  for (const [k, v] of Object.entries(dynamic)) {
    merged[Number(k)] = v;
  }
  return merged;
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

  return { text, hadBinary };
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
