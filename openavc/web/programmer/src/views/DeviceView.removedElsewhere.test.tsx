import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * A device removed by something other than this tab (a second Programmer
 * tab, the AI assistant, a cloud config push) leaves the loaded project on a
 * reload. The page showing it goes back to "Select a device" rather than
 * going on asking the server for a device it no longer has. A selection made
 * before the project arrives, or of a device the project never listed, is
 * left alone: nothing was removed.
 */

vi.mock("../api/restClient", () => ({
  listDevices: vi.fn(async () => []),
  listDrivers: vi.fn(async () => []),
}));

vi.mock("./DiscoveryView", () => ({ DiscoveryPanel: () => null }));
vi.mock("./DriverBuilderView", () => ({ DriverPanel: () => null }));
vi.mock("./devices/DeviceDetail", () => ({
  DeviceDetail: ({ deviceId }: { deviceId: string }) => <div>Showing {deviceId}</div>,
}));
vi.mock("./devices/DeviceGroupsPanel", () => ({ DeviceGroupsPanel: () => null }));
vi.mock("./devices/DeviceDialogs", () => ({ AddDeviceDialog: () => null, EditDeviceDialog: () => null }));
vi.mock("./devices/DeviceListItem", () => ({
  DeviceListItem: ({ name, onClick }: { name: string; onClick: () => void }) => (
    <button type="button" onClick={onClick}>{name}</button>
  ),
}));

import { useNavigationStore } from "../store/navigationStore";
import { useProjectStore } from "../store/projectStore";
import { DeviceView } from "./DeviceView";

const amp = { id: "amp", driver: "acme_widget", name: "Lobby Amp", config: {}, enabled: true };
const cam = { id: "cam", driver: "acme_camera", name: "Ceiling Camera", config: {}, enabled: true };

function setDevices(devices: object[] | null) {
  useProjectStore.setState({
    project: devices === null ? null : ({
      devices, device_groups: [], connections: {}, macros: [], monitors: [],
      ui: { pages: [], master_elements: [] },
    } as never),
  });
}

const EMPTY = "Select a device to view details";

describe("a device removed somewhere else", () => {
  beforeEach(() => {
    setDevices([amp, cam]);
  });

  it("takes the page back to Select a device", async () => {
    render(<DeviceView />);
    await userEvent.click(screen.getByRole("button", { name: "Lobby Amp" }));
    expect(screen.getByText("Showing amp")).toBeTruthy();

    act(() => setDevices([cam]));

    expect(screen.queryByText("Showing amp")).toBeNull();
    expect(screen.getByText(EMPTY)).toBeTruthy();
  });

  it("leaves a device that is still there", async () => {
    render(<DeviceView />);
    await userEvent.click(screen.getByRole("button", { name: "Lobby Amp" }));

    act(() => setDevices([amp]));

    expect(screen.getByText("Showing amp")).toBeTruthy();
  });

  it("leaves a device chosen before the project arrived", () => {
    setDevices(null);
    useNavigationStore.setState({ pendingFocus: { type: "device", id: "amp" } as never });
    render(<DeviceView />);
    expect(screen.getByText("Showing amp")).toBeTruthy();

    act(() => setDevices([amp, cam]));

    expect(screen.getByText("Showing amp")).toBeTruthy();
  });

  it("leaves a device the project never listed", () => {
    useNavigationStore.setState({ pendingFocus: { type: "device", id: "runtime_only" } as never });
    render(<DeviceView />);

    act(() => setDevices([cam]));

    expect(screen.getByText("Showing runtime_only")).toBeTruthy();
  });
});
