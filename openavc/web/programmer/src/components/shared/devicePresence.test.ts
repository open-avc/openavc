import { describe, expect, it } from "vitest";

import {
  AWAY_ASKED_FOR,
  PRESENCE_LABEL,
  PRESENCE_WORDS,
  presenceIn,
  presenceKey,
  presenceNote,
  presenceWord,
} from "./devicePresence";

describe("presenceWord", () => {
  it.each(PRESENCE_WORDS)("reads %s as itself", (word) => {
    expect(presenceWord(word)).toBe(word);
  });

  it("reads a key not published yet, or a value it does not know, as connecting", () => {
    for (const value of [undefined, null, "", "Online", "held", true, 1, {}]) {
      expect([value, presenceWord(value)]).toEqual([value, "connecting"]);
    }
  });
});

describe("presenceIn", () => {
  it("reads the device's own key", () => {
    const live = { [presenceKey("proj")]: "paused", [presenceKey("amp")]: "online" };
    expect(presenceIn(live, "proj")).toBe("paused");
    expect(presenceIn(live, "amp")).toBe("online");
    expect(presenceIn(live, "disp")).toBe("connecting");
  });

  it("reads the word alone, whatever the parts say", () => {
    const live = {
      "device.proj.connected": true,
      "device.proj.asleep": true,
      "device.proj.paused": true,
      [presenceKey("proj")]: "offline",
    };
    expect(presenceIn(live, "proj")).toBe("offline");
  });
});

describe("AWAY_ASKED_FOR", () => {
  it("is a restart, a sleep and a pause", () => {
    expect(PRESENCE_WORDS.filter((w) => AWAY_ASKED_FOR.has(w))).toEqual(["restarting", "asleep", "paused"]);
  });
});

describe("presenceNote", () => {
  it("names every word a picker's dot cannot say, and no other", () => {
    expect(Object.fromEntries(PRESENCE_WORDS.map((w) => [w, presenceNote(w)]))).toEqual({
      disabled: "Disabled",
      waiting_for_driver: "Driver not installed",
      online: "",
      restarting: "Restarting",
      asleep: "Asleep",
      paused: "Paused",
      offline: "",
      connecting: "",
    });
  });

  it("is the label wherever it says anything", () => {
    for (const word of PRESENCE_WORDS) {
      const note = presenceNote(word);
      if (note) expect(note).toBe(PRESENCE_LABEL[word]);
    }
  });
});
