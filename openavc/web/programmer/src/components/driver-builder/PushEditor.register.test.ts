import { describe, expect, it } from "vitest";

import { registerFromText, registerToText } from "./PushEditor";

// The register list is edited as one entry per line; the file form it
// writes must be the minimal one the loader reads (a bare string for one
// plain command, dict entries only where a clause needs them) and must
// round-trip whatever a hand-authored driver carries.
describe("register text round-trip", () => {
  it("keeps a single plain command as a string", () => {
    expect(registerFromText("subscribe_device")).toBe("subscribe_device");
    expect(registerToText("subscribe_device")).toBe("subscribe_device");
  });

  it("parses when and each_child clauses into dict entries", () => {
    expect(
      registerFromText(
        "subscribe_device\nsubscribe_channel each_child channel\nsubscribe_meters each_child channel when enable_meters",
      ),
    ).toEqual([
      "subscribe_device",
      { command: "subscribe_channel", each_child: "channel" },
      { command: "subscribe_meters", each_child: "channel", when: "enable_meters" },
    ]);
  });

  it("renders dict entries back to the same lines", () => {
    const value = [
      "a",
      { command: "b", when: "flag" },
      { command: "c", each_child: "zone", when: "flag" },
    ];
    expect(registerToText(value)).toBe("a\nb when flag\nc each_child zone when flag");
    expect(registerFromText(registerToText(value))).toEqual(value);
  });

  it("clears on blank input and ignores blank lines", () => {
    expect(registerFromText("")).toBeUndefined();
    expect(registerFromText(" \n\n")).toBeUndefined();
    expect(registerFromText("\na\n\n")).toBe("a");
  });
});
