import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * The bulk-delete bar holds Delete back only for product hardware, which the
 * running system names. Asking it again after the project changes kept the
 * last answer out of sight, so every change put "Delete is unavailable ..."
 * over a selection of ordinary devices until the answer came back, and a
 * failed answer left it there with Delete off for good.
 */

const mocks = vi.hoisted(() => ({ listDevices: vi.fn() }));

vi.mock("../api/restClient", () => ({
  listDevices: mocks.listDevices,
  listDrivers: vi.fn(async () => []),
  deleteDevice: vi.fn(async () => ({ status: "deleted" })),
}));

// The panels beside the list fetch on their own; none of them is under test.
vi.mock("./DiscoveryView", () => ({ DiscoveryPanel: () => null }));
vi.mock("./DriverBuilderView", () => ({ DriverPanel: () => null }));
vi.mock("./devices/DeviceDetail", () => ({ DeviceDetail: () => null }));
vi.mock("./devices/DeviceGroupsPanel", () => ({ DeviceGroupsPanel: () => null }));
vi.mock("./devices/DeviceDialogs", () => ({ AddDeviceDialog: () => null, EditDeviceDialog: () => null }));
vi.mock("./devices/DeviceListItem", () => ({
  DeviceListItem: ({ name }: { name: string }) => <span>{name}</span>,
}));

import { useProjectStore } from "../store/projectStore";
import { DeviceView } from "./DeviceView";

const amp = { id: "amp", driver: "acme_widget", name: "Lobby Amp", config: {}, enabled: true };
const cam = { id: "cam", driver: "acme_camera", name: "Ceiling Camera", config: {}, enabled: true };

function setDevices(devices: object[]) {
  useProjectStore.setState({
    project: {
      devices, device_groups: [], connections: {}, macros: [], monitors: [],
      ui: { pages: [], master_elements: [] },
    } as never,
  });
}

function running(...ids: string[]) {
  return ids.map((id) => ({ id, name: id, driver: "acme_widget", connected: false }));
}

async function selectFirstDevice() {
  await userEvent.click(screen.getByRole("button", { name: /Select/ }));
  await userEvent.click(screen.getAllByTitle("Select device")[0]);
}

function deleteButton() {
  return screen.getByRole("button", { name: "Delete" }) as HTMLButtonElement;
}

const UNAVAILABLE = /Delete is unavailable/;

describe("the bulk-delete bar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setDevices([amp, cam]);
  });

  it("stays quiet across a project change while the running system is asked again", async () => {
    mocks.listDevices.mockResolvedValueOnce(running("amp", "cam"));
    render(<DeviceView />);
    await selectFirstDevice();
    await waitFor(() => expect(deleteButton().disabled).toBe(false));

    mocks.listDevices.mockReturnValueOnce(new Promise(() => {}));
    act(() => setDevices([amp, { ...cam, name: "Rear Camera" }]));

    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
    expect(deleteButton().disabled).toBe(false);
  });

  it("leaves Delete to the server when the running system cannot be asked", async () => {
    mocks.listDevices.mockRejectedValueOnce(new Error("Network error"));
    render(<DeviceView />);
    await selectFirstDevice();
    await waitFor(() => expect(deleteButton().disabled).toBe(false));
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });

  it("still holds Delete back for product hardware, and says why", async () => {
    mocks.listDevices.mockResolvedValueOnce([
      ...running("amp"),
      { ...running("cam")[0], ownership: "manufacturer" },
    ]);
    render(<DeviceView />);
    await userEvent.click(screen.getByRole("button", { name: /Select/ }));
    await userEvent.click(screen.getAllByTitle("Select device")[1]);
    expect(await screen.findByText(/Delete is unavailable while the selection includes product hardware/)).toBeTruthy();
    expect(deleteButton().disabled).toBe(true);
  });
});
