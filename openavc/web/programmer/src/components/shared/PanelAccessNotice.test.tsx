import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The one-time notice on a system that was updated with its panels
// connected: it came up as Anyone on the network, and this says so once. It
// is never drawn on a fresh install (approved), and never once the mode is
// switched or Dismiss has been pressed.

vi.mock("../../api/restClient", () => ({
  listPanelDevices: vi.fn(),
  approvePanelDevice: vi.fn(),
  denyPanelDevice: vi.fn(),
  revokePanelDevice: vi.fn(),
  renamePanelDevice: vi.fn(),
  dismissPanelAccessNotice: vi.fn(),
}));
vi.mock("../../store/toastStore", () => ({ showError: vi.fn() }));

import * as api from "../../api/restClient";
import { usePanelDevicesStore } from "../../store/panelDevicesStore";
import { useNavigationStore } from "../../store/navigationStore";
import { PanelAccessNotice } from "./PanelAccessNotice";
import { PANEL_ACCESS_UPGRADE_NOTICE } from "./panelDevicesCopy";

describe("PanelAccessNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePanelDevicesStore.setState({
      access: "open", upgradeNotice: true, pending: [], approved: [], denied: [], loaded: true, error: null,
    });
    useNavigationStore.setState({ activeView: "devices" });
  });

  it("says what open means and where the switch is, with Settings and Dismiss", () => {
    render(<PanelAccessNotice />);
    expect(screen.getByRole("status")).toHaveTextContent(PANEL_ACCESS_UPGRADE_NOTICE);
    expect(PANEL_ACCESS_UPGRADE_NOTICE).toContain("Approved panels only");
    expect(PANEL_ACCESS_UPGRADE_NOTICE).toContain("Settings > Access");
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("is absent on a fresh install, and once the mode is approved", () => {
    usePanelDevicesStore.setState({ access: "open", upgradeNotice: false });
    const { rerender } = render(<PanelAccessNotice />);
    expect(screen.queryByRole("status")).toBeNull();
    usePanelDevicesStore.setState({ access: "approved", upgradeNotice: true });
    rerender(<PanelAccessNotice />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("Settings goes to Settings", async () => {
    render(<PanelAccessNotice />);
    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(useNavigationStore.getState().activeView).toBe("settings");
  });

  it("Dismiss clears it here and tells the server", async () => {
    vi.mocked(api.dismissPanelAccessNotice).mockResolvedValue(undefined);
    render(<PanelAccessNotice />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).toBeNull();
    expect(api.dismissPanelAccessNotice).toHaveBeenCalledTimes(1);
  });

  it("the list and the access push both carry the flag", () => {
    usePanelDevicesStore.getState().applyChange({ reason: "access_changed", access: "open", upgrade_notice: false });
    expect(usePanelDevicesStore.getState().upgradeNotice).toBe(false);
    usePanelDevicesStore.getState().applyChange({ reason: "access_changed", access: "open", upgrade_notice: true });
    expect(usePanelDevicesStore.getState().upgradeNotice).toBe(true);
    // A push without the field means not due, never "unchanged".
    usePanelDevicesStore.getState().applyChange({ reason: "access_changed", access: "approved" });
    expect(usePanelDevicesStore.getState().upgradeNotice).toBe(false);
  });
});
