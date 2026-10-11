import { describe, expect, it } from "vitest";

import { pausedBannerText } from "./pausedText";

describe("pausedBannerText", () => {
  it("names a Device Audit and when the device comes back", () => {
    expect(pausedBannerText("audit")).toBe(
      "Paused for a Device Audit. It reconnects when the audit finishes.",
    );
  });

  it("names Live Test and when the device comes back", () => {
    expect(pausedBannerText("live_test")).toBe(
      "Paused for Live Test in the Driver Builder. It reconnects when you leave the Test tab.",
    );
  });

  it("points at Resume when nothing names the holder", () => {
    expect(pausedBannerText(undefined)).toBe("Paused. Press Resume to reconnect it.");
  });
});
