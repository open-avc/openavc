import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeviceStatusDot } from "./DeviceStatusDot";
import { awayBanner, type DeviceAway } from "../../views/devices/deviceAway";

function titleOf(el: React.ReactElement): string | null {
  const { container } = render(el);
  return container.querySelector("span")?.getAttribute("title") ?? null;
}

describe("DeviceStatusDot", () => {
  it("names a Device Audit that holds the device", () => {
    expect(titleOf(<DeviceStatusDot connected={false} paused pausedFor="audit" />)).toBe(
      "Paused for a Device Audit",
    );
  });

  it("names Live Test that holds the device", () => {
    expect(titleOf(<DeviceStatusDot connected={false} paused pausedFor="live_test" />)).toBe(
      "Paused for Live Test",
    );
  });

  it("says Paused when nothing names the holder", () => {
    expect(titleOf(<DeviceStatusDot connected={false} paused />)).toBe("Paused");
  });
});

describe("DeviceStatusDot asleep", () => {
  function colorOf(el: React.ReactElement): string | null {
    const { container } = render(el);
    return container.querySelector("span")?.style.backgroundColor ?? null;
  }

  it("says Asleep instead of Disconnected", () => {
    expect(titleOf(<DeviceStatusDot connected={false} asleep />)).toBe("Asleep");
  });

  it("is drawn in the muted colour, not the fault red", () => {
    expect(colorOf(<DeviceStatusDot connected={false} asleep />)).toBe("var(--text-muted)");
    expect(colorOf(<DeviceStatusDot connected={false} />)).toBe("var(--color-error)");
  });

  it("gives way to a pause and to a restart", () => {
    expect(titleOf(<DeviceStatusDot connected={false} asleep paused pausedFor="audit" />)).toBe(
      "Paused for a Device Audit",
    );
    expect(titleOf(<DeviceStatusDot connected={false} asleep restarting />)).toBe("Restarting");
  });
});

describe("DeviceStatusDot and the device page", () => {
  const TITLES: Record<string, string> = {
    orphaned: "Driver not installed",
    paused: "Paused for Live Test",
    restarting: "Restarting",
    asleep: "Asleep",
  };

  it("names the same way of being away as the page's banner, for every combination", () => {
    for (let bits = 0; bits < 32; bits++) {
      const flags = {
        orphaned: Boolean(bits & 1), paused: Boolean(bits & 2), restarting: Boolean(bits & 4),
        asleep: Boolean(bits & 8), connected: Boolean(bits & 16),
      };
      const away: DeviceAway = {
        ...flags, pausedFor: "live_test", offlineReason: "", offlineDetail: "", reconnectFailed: false,
      };
      const banner = awayBanner(away, true, 0);
      const expected = banner ? TITLES[banner] : flags.connected ? "Connected" : "Disconnected";
      const title = titleOf(<DeviceStatusDot {...flags} pausedFor="live_test" />);
      expect([bits, title]).toEqual([bits, expected]);
    }
  });
});
