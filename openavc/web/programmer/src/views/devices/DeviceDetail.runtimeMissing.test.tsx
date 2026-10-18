import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * A device whose driver fails while it loads is in the project but not in
 * the running system: GET /api/devices/{id} answers 404. The page still
 * offers what works from the project alone (Edit, Duplicate, Delete), so
 * the device can be fixed or removed. Product hardware, which the running
 * system names, still never offers them.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  deleteDevice: vi.fn(async () => ({ status: "deleted" })),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  deleteDevice: mocks.deleteDevice,
  getDeviceSettings: vi.fn(async () => ({ settings: {} })),
  installCommunityDriver: vi.fn(),
  reconnectDevice: vi.fn(),
  resumeDevice: vi.fn(),
  sendCommand: vi.fn(),
  setDeviceSetting: vi.fn(),
  testDeviceConnection: vi.fn(),
}));
vi.mock("../../api/auditClient", () => ({ getAuditDeviceTarget: vi.fn() }));

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

// The page's sections that fetch on their own; none of them is under test.
vi.mock("./ChildEntities", () => ({ ChildEntities: () => null }));
vi.mock("./DeviceStateLog", () => ({ DeviceStateLog: () => null }));
vi.mock("./DeviceProtocolLog", () => ({ DeviceProtocolLog: () => null }));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./InstalledEquipment", () => ({ InstalledEquipment: () => null }));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { ApiError } from "../../api/errors";
import { useProjectStore } from "../../store/projectStore";
import { DeviceDetail } from "./DeviceDetail";

const device = {
  id: "amp",
  driver: "acme_widget",
  name: "Lobby Amp",
  config: { host: "10.0.0.5" },
  enabled: true,
};

function showPage() {
  const onEdit = vi.fn();
  const onDeleted = vi.fn();
  render(
    <DeviceDetail
      deviceId="amp"
      onEdit={onEdit}
      onDeleted={onDeleted}
      onDuplicate={() => {}}
    />,
  );
  return { onEdit, onDeleted };
}

describe("a device the running system does not have", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useProjectStore.setState({
      project: {
        devices: [device], device_groups: [], macros: [], monitors: [],
        ui: { pages: [], master_elements: [] },
      } as never,
    });
    mocks.getDevice.mockRejectedValue(
      new ApiError(404, JSON.stringify({ detail: "Device 'amp' not found" })),
    );
  });

  it("can still be edited, under its project name", async () => {
    const { onEdit } = showPage();
    await userEvent.click(await screen.findByRole("button", { name: /Edit/ }));
    expect(onEdit).toHaveBeenCalledWith(device);
    expect(screen.getByRole("heading", { name: "Lobby Amp" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Duplicate/ })).toBeTruthy();
  });

  it("can still be deleted", async () => {
    const { onDeleted } = showPage();
    await userEvent.click(await screen.findByRole("button", { name: /Delete/ }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, Delete" }));
    expect(mocks.deleteDevice).toHaveBeenCalledWith("amp");
    expect(onDeleted).toHaveBeenCalledWith("amp");
  });

  it("drops what only a running device offers once it stops running", async () => {
    mocks.getDevice.mockResolvedValueOnce({
      id: "amp", name: "Lobby Amp", driver: "acme_widget", connected: false,
      commands: {}, driver_info: {},
    });
    liveState["device.amp.enabled"] = true;
    const { rerender } = render(
      <DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />,
    );
    await screen.findByRole("button", { name: /Test/ });

    // An edit the driver cannot load: the device leaves the running system.
    delete liveState["device.amp.enabled"];
    rerender(
      <DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />,
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: /Test/ })).toBeNull());
    expect(screen.getByRole("button", { name: /Edit/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Delete/ })).toBeTruthy();
  });

  it("offers nothing while the running system has not answered", () => {
    mocks.getDevice.mockReturnValue(new Promise(() => {}));
    showPage();
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  });

  it("does not offer Edit or Delete for product hardware", async () => {
    mocks.getDevice.mockResolvedValue({
      id: "amp", name: "Lobby Amp", driver: "acme_widget", connected: false,
      ownership: "manufacturer", commands: {}, driver_info: {},
    });
    showPage();
    await screen.findByRole("button", { name: /Test/ });
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  });
});
