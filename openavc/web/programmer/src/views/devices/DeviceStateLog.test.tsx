import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";

// The device page's State Changes tab. What shipped broken: it only listed
// changes it watched happen while it was open, so three changes made before
// the tab was opened left it reading "No state changes yet", and a value's
// first appearance was never listed at all. It now reads the server's change
// history for the device, the same record State > Activity shows.

const mocks = vi.hoisted(() => ({
  history: [] as unknown[],
  liveState: {} as Record<string, unknown>,
  getStateHistory: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getStateHistory: (...args: unknown[]) => mocks.getStateHistory(...args),
}));

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) =>
    selector({ liveState: mocks.liveState }),
}));

import { DeviceStateLog } from "./DeviceStateLog";

const T = 1_760_000_000;

beforeEach(() => {
  mocks.liveState = { "device.amp.level": 85.5, "device.amp.power": "on" };
  mocks.history = [
    { key: "device.amp.level", old_value: null, new_value: 85.5, source: "device", timestamp: T },
    { key: "device.other.level", old_value: 1, new_value: 2, source: "device", timestamp: T + 1 },
    { key: "device.amp.power", old_value: "off", new_value: "on", source: "device", timestamp: T + 2 },
  ];
  mocks.getStateHistory.mockReset();
  mocks.getStateHistory.mockImplementation(async () => mocks.history);
});

describe("State Changes", () => {
  it("lists what changed before the tab was opened, first values included", async () => {
    render(<DeviceStateLog deviceId="amp" />);
    const level = await screen.findByText("level");
    const row = level.closest("tr")!;
    expect(within(row).getByText("—")).toBeTruthy();
    expect(within(row).getByText("85.5")).toBeTruthy();
    expect(screen.getByText("power")).toBeTruthy();
    expect(screen.queryByText("No state changes yet. Interact with the device to see live updates.")).toBeNull();
    // Another device's changes stay off this device's page.
    expect(screen.getAllByRole("row")).toHaveLength(3);
    // Only this device's changes are asked for.
    expect(mocks.getStateHistory).toHaveBeenCalledWith(100, "device.amp.");
  });

  it("follows the changes made while it is open, asking the server only once", async () => {
    const { rerender } = render(<DeviceStateLog deviceId="amp" />);
    await screen.findByText("power");
    mocks.liveState = { ...mocks.liveState, "device.amp.mute": true };
    rerender(<DeviceStateLog deviceId="amp" />);
    const mute = (await screen.findByText("mute")).closest("tr")!;
    expect(within(mute).getByText("—")).toBeTruthy();
    expect(within(mute).getByText("Yes")).toBeTruthy();
    mocks.liveState = { ...mocks.liveState, "device.amp.level": 80 };
    rerender(<DeviceStateLog deviceId="amp" />);
    await screen.findByText("80");
    // The history route shares a remote session's standard rate budget.
    expect(mocks.getStateHistory).toHaveBeenCalledTimes(1);
  });

  it("lists a change once when it reaches the socket after the history was read", async () => {
    // The history already holds power -> on, but the live value still says off.
    mocks.liveState = { "device.amp.level": 85.5, "device.amp.power": "off" };
    const { rerender } = render(<DeviceStateLog deviceId="amp" />);
    await screen.findByText("power");
    mocks.liveState = { "device.amp.level": 85.5, "device.amp.power": "on" };
    rerender(<DeviceStateLog deviceId="amp" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getAllByText("power")).toHaveLength(1);
  });

  it("says so when the device has changed nothing", async () => {
    mocks.history = [];
    render(<DeviceStateLog deviceId="amp" />);
    expect(await screen.findByText("No recent changes for this device.")).toBeTruthy();
  });
});
