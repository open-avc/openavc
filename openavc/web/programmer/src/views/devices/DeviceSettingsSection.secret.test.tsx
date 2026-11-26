import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A setting the driver marks secret (an admin PIN) is a login, entered here
// while the device is online: its value reads masked and its box is a
// password box, as every other form that takes a login has it.

const mocks = vi.hoisted(() => ({
  getDeviceSettings: vi.fn(),
  setDeviceSetting: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getDeviceSettings: mocks.getDeviceSettings,
  setDeviceSetting: mocks.setDeviceSetting,
}));

import { DeviceSettingsSection } from "./DeviceSettingsSection";

beforeEach(() => {
  mocks.getDeviceSettings.mockReset().mockResolvedValue({
    device_id: "lock1",
    settings: {
      admin_pin: { type: "string", label: "Admin PIN", secret: true, state_key: "admin_pin", current_value: "4821" },
      room_name: { type: "string", label: "Room Name", state_key: "room_name", current_value: "Lobby" },
    },
    pending: {},
  });
  mocks.setDeviceSetting.mockReset().mockResolvedValue({ success: true });
});

describe("DeviceSettingsSection: a secret setting", () => {
  it("reads masked beside a plain one", async () => {
    render(<DeviceSettingsSection deviceId="lock1" connected />);
    await screen.findByText("Admin PIN");
    expect(screen.queryByText("4821")).toBeNull();
    expect(screen.getByText("Lobby")).toBeTruthy();
  });

  it("is edited in a password box the browser does not fill", async () => {
    const user = userEvent.setup();
    const { container } = render(<DeviceSettingsSection deviceId="lock1" connected />);
    await screen.findByText("Admin PIN");
    await user.click(screen.getAllByTitle("Edit setting")[0]);
    const box = container.querySelector("input") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect(box.getAttribute("autocomplete")).toBe("new-password");
  });

  it("leaves a plain setting's box as text", async () => {
    const user = userEvent.setup();
    const { container } = render(<DeviceSettingsSection deviceId="lock1" connected />);
    await screen.findByText("Room Name");
    await user.click(screen.getAllByTitle("Edit setting")[1]);
    expect((container.querySelector("input") as HTMLInputElement).type).toBe("text");
  });
});
