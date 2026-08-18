import { describe, it, expect } from "vitest";

import { bannerDisplayText, cleanBannerText } from "./discoveryViewHelpers";

// The fixtures below are bytes captured from a real scanned device: a
// Dante Bluetooth wallplate answering on ports 22 and 23. Each is run
// through both decodes the server uses, because the card is fed by two
// paths that disagree about the same bytes — the banner grab decodes
// UTF-8 and leaves U+FFFD where a byte wasn't valid, while a driver
// probe decodes latin-1 and leaves the byte's own character. A reader
// should not be able to tell which one produced the line.

function bytes(hex: string): Uint8Array {
  const clean = hex.replace(/\s+/g, "");
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

function ascii(text: string): Uint8Array {
  return Uint8Array.from(text, (ch) => ch.charCodeAt(0));
}

function join(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

/** What port_scanner.grab_banner produces: utf-8, errors="replace". */
const asUtf8 = (b: Uint8Array) => new TextDecoder("utf-8").decode(b);
/** What probe_runner produces: latin-1, every byte its own codepoint. */
const asLatin1 = (b: Uint8Array) => Array.from(b, (c) => String.fromCharCode(c)).join("");

// Port 23. Four telnet negotiation commands (WILL suppress-go-ahead,
// WONT echo, DONT echo, DONT binary) ahead of the greeting.
const TELNET = join(
  bytes("fffb03 fffc01 fffe01 fffe00"),
  ascii(
    "*".repeat(16) + "Welcome " + "*".repeat(14) + "\r\n" +
    " ".repeat(9) + "FW version: V1.00.12" + "\r\n" + "*".repeat(37),
  ),
);

// Port 22. The version string, then the binary key exchange: packet
// length, padding length, message type 20, a 16-byte random cookie, and
// the length-prefixed algorithm list.
const SSH = join(
  ascii("SSH-2.0-dropbear_2022.83\r\n"),
  bytes("000001dc 0a 14 f9463fd4db975614411b0ee3c1e188b8 000000bb"),
  ascii("curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp521,ec"),
);

describe("cleanBannerText", () => {
  it("drops the telnet negotiation and keeps the greeting", () => {
    const { text, hadBinary } = cleanBannerText(asUtf8(TELNET));
    expect(text).toBe(
      "*".repeat(16) + "Welcome " + "*".repeat(14) + " FW version: V1.00.12 " + "*".repeat(37),
    );
    expect(hadBinary).toBe(false);
  });

  it("reads the same whichever decode delivered the telnet bytes", () => {
    expect(cleanBannerText(asLatin1(TELNET))).toEqual(cleanBannerText(asUtf8(TELNET)));
  });

  it("stops an SSH banner at the end of the version string", () => {
    const { text, hadBinary } = cleanBannerText(asUtf8(SSH));
    expect(text).toBe("SSH-2.0-dropbear_2022.83");
    expect(hadBinary).toBe(true);
  });

  it("reads the same whichever decode delivered the SSH bytes", () => {
    expect(cleanBannerText(asLatin1(SSH))).toEqual(cleanBannerText(asUtf8(SSH)));
  });

  it("never leaves a replacement character or a control code behind", () => {
    for (const raw of [asUtf8(TELNET), asLatin1(TELNET), asUtf8(SSH), asLatin1(SSH)]) {
      expect(cleanBannerText(raw).text).not.toMatch(/[^\x20-\x7e]/);
    }
  });

  it("leaves an already readable banner alone", () => {
    const { text, hadBinary } = cleanBannerText("PJLINK 0");
    expect(text).toBe("PJLINK 0");
    expect(hadBinary).toBe(false);
  });

  it("collapses a short burst of unprintable bytes rather than truncating", () => {
    // A lone telnet command mid-greeting is three bytes, under the run
    // that means the message has ended.
    const { text, hadBinary } = cleanBannerText("READY\xff\xfb\x01OK");
    expect(text).toBe("READY OK");
    expect(hadBinary).toBe(false);
  });

  it("returns nothing for a response that was binary throughout", () => {
    // The caller uses this to fall through to the matched pattern
    // rather than quoting noise back at the user.
    expect(cleanBannerText("\x00\x01\x02\x03\x04\x05\x06\x07").text).toBe("");
  });

  it("keeps a line break from hiding the payload behind it", () => {
    const { text, hadBinary } = cleanBannerText("HELLO\r\n\x00\x01\x02\x03");
    expect(text).toBe("HELLO");
    expect(hadBinary).toBe(true);
  });
});

describe("bannerDisplayText", () => {
  it("marks an SSH banner as having more behind it", () => {
    expect(bannerDisplayText(asUtf8(SSH), 200)).toBe("SSH-2.0-dropbear_2022.83 …");
  });

  it("marks a banner cut short by the cap", () => {
    expect(bannerDisplayText(asUtf8(TELNET), 24)).toBe("*".repeat(16) + "Welcome…");
  });

  it("stays empty when there was nothing to read", () => {
    expect(bannerDisplayText("\x00\x01\x02\x03", 200)).toBe("");
  });
});
