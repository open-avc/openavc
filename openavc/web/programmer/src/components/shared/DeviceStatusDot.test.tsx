import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeviceStatusDot } from "./DeviceStatusDot";
import { PRESENCE_WORDS, type PresenceWord } from "./devicePresence";
import { awayBanner } from "../../views/devices/deviceAway";

function dot(el: React.ReactElement): HTMLSpanElement {
  const { container } = render(el);
  return container.querySelector("span") as HTMLSpanElement;
}

const titleOf = (el: React.ReactElement) => dot(el).getAttribute("title");

describe("DeviceStatusDot", () => {
  it("names a Device Audit that holds the device", () => {
    expect(titleOf(<DeviceStatusDot presence="paused" pausedFor="audit" />)).toBe("Paused for a Device Audit");
  });

  it("names Live Test that holds the device", () => {
    expect(titleOf(<DeviceStatusDot presence="paused" pausedFor="live_test" />)).toBe("Paused for Live Test");
  });

  it("says Paused when nothing names the holder", () => {
    expect(titleOf(<DeviceStatusDot presence="paused" />)).toBe("Paused");
  });
});

describe("DeviceStatusDot, word by word", () => {
  const LOOK: Record<PresenceWord, { title: string; color: string }> = {
    disabled: { title: "Disabled", color: "var(--text-muted)" },
    waiting_for_driver: { title: "Driver not installed", color: "var(--color-warning, #f59e0b)" },
    online: { title: "Connected", color: "var(--color-success)" },
    restarting: { title: "Restarting", color: "var(--color-warning, #f59e0b)" },
    asleep: { title: "Asleep", color: "var(--text-muted)" },
    paused: { title: "Paused for Live Test", color: "var(--color-warning, #f59e0b)" },
    offline: { title: "Offline", color: "var(--color-error)" },
    connecting: { title: "Offline", color: "var(--color-error)" },
  };

  it.each(PRESENCE_WORDS)("draws %s its own way", (word) => {
    const span = dot(<DeviceStatusDot presence={word} pausedFor="live_test" />);
    const color = word === "waiting_for_driver" ? span.style.borderBottom.replace(/^\S+ solid /, "") : span.style.backgroundColor;
    expect({ title: span.getAttribute("title"), color }).toEqual(LOOK[word]);
  });

  it("draws a missing driver as a triangle and every other word as a dot", () => {
    for (const word of PRESENCE_WORDS) {
      const triangle = dot(<DeviceStatusDot presence={word} />).style.width === "0px";
      expect([word, triangle]).toEqual([word, word === "waiting_for_driver"]);
    }
  });
});

describe("DeviceStatusDot and the device page", () => {
  const TITLES: Record<string, string> = {
    orphaned: "Driver not installed",
    paused: "Paused for Live Test",
    restarting: "Restarting",
    asleep: "Asleep",
  };

  it("names the same way of being away as the page's banner, for every word", () => {
    for (const word of PRESENCE_WORDS) {
      const banner = awayBanner({ presence: word, offlineReason: "", offlineDetail: "" }, 0);
      const title = titleOf(<DeviceStatusDot presence={word} pausedFor="live_test" />);
      if (banner) expect([word, title]).toEqual([word, TITLES[banner]]);
    }
  });
});
