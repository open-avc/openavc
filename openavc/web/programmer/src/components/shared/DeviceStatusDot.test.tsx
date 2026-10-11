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
