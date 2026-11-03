import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeviceStatusDot } from "./DeviceStatusDot";

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
