import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// A Quick Action button asks what the server read from the driver, in the
// server's words, and runs at once when the server says it asks nothing: the
// strip never works out a question of its own.

const mocks = vi.hoisted(() => ({ invokeDeviceAction: vi.fn() }));
vi.mock("../../api/restClient", () => ({ invokeDeviceAction: mocks.invokeDeviceAction }));

import type { DeviceAction } from "../../api/types";
import { QuickActions } from "./QuickActions";

function button(id: string, label: string, confirm: string | null, params = {}): DeviceAction {
  return { id, kind: "command", command: id, label, confirm, availability: "online", params };
}

function strip(actions: DeviceAction[]) {
  render(<QuickActions deviceId="widget1" actions={actions} connected={true} liveState={{}} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.invokeDeviceAction.mockResolvedValue({});
});

describe("a Quick Action that asks first", () => {
  it("asks the server's sentence and runs only on yes", async () => {
    strip([button("reboot", "Reboot", "Send Reboot?")]);
    fireEvent.click(screen.getByRole("button", { name: /Reboot/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("Send Reboot?");
    expect(mocks.invokeDeviceAction).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
    await waitFor(() => expect(mocks.invokeDeviceAction).toHaveBeenCalledWith("widget1", "reboot", {}));
  });

  it("runs at once when the server says it asks nothing", async () => {
    strip([button("power_on", "Power On", null)]);
    fireEvent.click(screen.getByRole("button", { name: /Power On/ }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => expect(mocks.invokeDeviceAction).toHaveBeenCalledWith("widget1", "power_on", {}));
  });

  it("shows the question above the fields of a button that takes a value", () => {
    strip([button("set_level", "Set Level", "Send Set Level?", { level: { type: "integer" } })]);
    fireEvent.click(screen.getByRole("button", { name: /Set Level/ }));
    expect(screen.getByText("Send Set Level?")).toBeTruthy();
  });
});
