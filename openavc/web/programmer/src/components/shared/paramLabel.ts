/**
 * The name a person sees for a command parameter: its label when the driver
 * declares a non-blank one, as written; else its key made readable, so a field
 * reads "Input ID" rather than `input_id`.
 *
 * The key splits into words at `_`, `-`, spaces and a camelCase step (letters
 * and digits stay together); a word in ACRONYMS takes its usual spelling, any
 * other word is capitalised; a last word in UNITS, after another word, goes in
 * brackets ("Timeout (ms)", "Gain (dB)").
 *
 * openavc/drivers/param_labels.py is the same rule for the text the server
 * writes. Both run tests/fixtures/param_label_cases.json, so change the two
 * together.
 */

import type { DriverParamDef } from "../../api/types";

export const ACRONYMS: Record<string, string> = {
  api: "API", av: "AV", cec: "CEC", dmx: "DMX", dsp: "DSP",
  edid: "EDID", gpio: "GPIO", hdmi: "HDMI", http: "HTTP",
  https: "HTTPS", id: "ID", ids: "IDs", ip: "IP", ipv4: "IPv4",
  ipv6: "IPv6", ir: "IR", json: "JSON", led: "LED", mac: "MAC",
  mqtt: "MQTT", ndi: "NDI", osc: "OSC", osd: "OSD", ptz: "PTZ",
  rgb: "RGB", sdi: "SDI", ssid: "SSID", tcp: "TCP", udp: "UDP",
  uid: "UID", uri: "URI", url: "URL", urls: "URLs", usb: "USB",
  uuid: "UUID", xml: "XML",
};

export const UNITS: Record<string, string> = {
  db: "dB", hz: "Hz", khz: "kHz", mhz: "MHz", ms: "ms",
  pct: "%", percent: "%", sec: "sec", secs: "secs",
};

const has = (table: Record<string, string>, word: string) =>
  Object.prototype.hasOwnProperty.call(table, word);

/** `key` as words a person reads (the rule above). */
export function readableKey(key: string): string {
  const words = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return key;
  const shown = words.map((w) => {
    const low = w.toLowerCase();
    return has(ACRONYMS, low) ? ACRONYMS[low] : w.slice(0, 1).toUpperCase() + w.slice(1).toLowerCase();
  });
  const last = words[words.length - 1].toLowerCase();
  if (has(UNITS, last)) {
    shown[shown.length - 1] = words.length > 1 ? `(${UNITS[last]})` : UNITS[last];
  }
  return shown.join(" ");
}

/** The parameter's label when it declares a non-blank one, else its key made readable. */
export function paramLabel(key: string, def: Partial<DriverParamDef> | undefined | null): string {
  const label: unknown = def?.label;
  if (typeof label === "string" && label.trim()) return label.trim();
  return readableKey(key);
}
