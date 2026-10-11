import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// A key's first value has nothing before it. The change list showed that as
// the word "null" ("null -> 85.5"); it shows the same mark the device page,
// the Dashboard and the panel use for no value.

vi.mock("../../api/restClient", () => ({
  getStateHistory: vi.fn(async () => [
    { key: "device.amp.level", old_value: null, new_value: 85.5, source: "device", timestamp: 1_760_000_000 },
  ]),
}));

import { ActivitySubTab } from "./ActivityView";

describe("State > Activity", () => {
  it("shows a first value with no word for the missing old one", async () => {
    render(<ActivitySubTab />);
    await screen.findByText("85.5");
    expect(screen.queryByText("null")).toBeNull();
    expect(screen.getByText("—")).toBeTruthy();
  });
});
