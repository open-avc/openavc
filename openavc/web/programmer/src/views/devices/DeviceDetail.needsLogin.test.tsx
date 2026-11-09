import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * A device whose login is not saved on this system (offline reason
 * `login_missing`) reads "Needs login" on its page in place of the Offline
 * banner, with a box for each setting that waits and, where the driver ships
 * one, "Use the driver's default". A save goes through the device's settings
 * save and the page asks the server again which settings still wait.
 */

const MARKER = "<saved on this system>";

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  getProject: vi.fn(),
  updateDevice: vi.fn(async () => ({ status: "updated", device_id: "wb1" })),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  getProject: mocks.getProject,
  updateDevice: mocks.updateDevice,
  deleteDevice: vi.fn(),
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

vi.mock("./ChildEntities", () => ({ ChildEntities: () => null }));
vi.mock("./DeviceStateLog", () => ({ DeviceStateLog: () => null }));
vi.mock("./DeviceProtocolLog", () => ({ DeviceProtocolLog: () => null }));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./InstalledEquipment", () => ({ InstalledEquipment: () => null }));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { useProjectStore } from "../../store/projectStore";
import { ApiError } from "../../api/errors";
import { DeviceDetail } from "./DeviceDetail";
import { DeviceListItem } from "./DeviceListItem";

const SENTENCE =
  "No login for this device is saved on this system. Enter it on the device's page in the Programmer.";

const PROJECT = {
  devices: [{ id: "wb1", driver: "acme_power", name: "Rack Power", config: { poll_interval: 30 }, enabled: true }],
  connections: { wb1: { host: "192.0.2.10", username: "admin", password: MARKER } },
  device_groups: [], macros: [], monitors: [],
  ui: { pages: [], master_elements: [] },
};

function driverInfo(defaultPassword?: string) {
  return {
    config_schema: {
      host: { type: "string", label: "IP Address" },
      password: { type: "string", secret: true, label: "Password" },
    },
    default_config: defaultPassword ? { port: 23, password: defaultPassword } : { port: 23 },
  };
}

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, next);
}

function showPage() {
  render(
    <DeviceDetail deviceId="wb1" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />,
  );
}

const WAITING = {
  "device.wb1.enabled": true,
  "device.wb1.connected": false,
  "device.wb1.offline_reason": "login_missing",
  "device.wb1.offline_detail": SENTENCE,
  "device.wb1.reconnect_failed": true,
};

describe("a device that needs its login", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useProjectStore.setState({ project: PROJECT as never, dirty: true });
    mocks.getProject.mockResolvedValue(PROJECT);
    mocks.getDevice.mockResolvedValue({
      id: "wb1", name: "Rack Power", driver: "acme_power", connected: false, enabled: true,
      commands: {}, driver_info: driverInfo(), needs_login: ["password"],
    });
  });

  it("says Needs login with a box for the login, in place of the Offline banner", async () => {
    setLive(WAITING);
    showPage();

    expect(await screen.findByText("Needs login")).toBeTruthy();
    const box = screen.getByLabelText("Password") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect(box.autocomplete).toBe("new-password");
    expect(box.value).toBe("");
    expect(screen.queryByText("Offline")).toBeNull();
    // The banner says it in its own words; the device's sentence is for
    // places with no box to type in (a panel, the assistant).
    expect(within(screen.getByTestId("needs-login-banner")).queryByText(SENTENCE)).toBeNull();
    // No default shipped, so none is offered.
    expect(screen.queryByRole("button", { name: "Use the driver's default" })).toBeNull();
  });

  it("saves the login through the device's settings and asks again what still waits", async () => {
    setLive(WAITING);
    showPage();
    const box = await screen.findByLabelText("Password");
    expect(mocks.getDevice).toHaveBeenCalledTimes(1);

    fireEvent.change(box, { target: { value: "Hunter2-Acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Save login" }));

    await waitFor(() => expect(mocks.updateDevice).toHaveBeenCalledWith("wb1", {
      config: { poll_interval: 30, password: "Hunter2-Acme" },
    }));
    await waitFor(() => expect(mocks.getDevice).toHaveBeenCalledTimes(2));
    // The store took the server's copy of the device, never the value typed.
    expect(JSON.stringify(useProjectStore.getState().project)).not.toContain("Hunter2-Acme");
  });

  it("takes a device the running system no longer has after the save as not running", async () => {
    setLive(WAITING);
    showPage();
    const box = await screen.findByLabelText("Password");
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.getDevice.mockRejectedValueOnce(new ApiError(404, JSON.stringify({ detail: "Device 'wb1' not found" })));

    fireEvent.change(box, { target: { value: "Hunter2-Acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Save login" }));

    await waitFor(() => expect(mocks.getDevice).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("Needs login")).toBeNull());
    logged.mockRestore();
  });

  it("offers the driver's default where it ships one, and saves it without showing it", async () => {
    mocks.getDevice.mockResolvedValue({
      id: "wb1", name: "Rack Power", driver: "acme_power", connected: false, enabled: true,
      commands: {}, driver_info: driverInfo("acme-factory"), needs_login: ["password"],
    });
    setLive(WAITING);
    showPage();

    const button = await screen.findByRole("button", { name: "Use the driver's default" });
    expect(screen.queryByText(/acme-factory/)).toBeNull();
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
    fireEvent.click(button);

    await waitFor(() => expect(mocks.updateDevice).toHaveBeenCalledWith("wb1", {
      config: { poll_interval: 30, password: "acme-factory" },
    }));
  });

  it("shows the Offline banner, not Needs login, once the reason is something else", async () => {
    setLive({ ...WAITING, "device.wb1.offline_reason": "unreachable", "device.wb1.offline_detail": "Can't reach 192.0.2.10." });
    showPage();
    await screen.findByRole("button", { name: /Test/ });

    expect(screen.getByText("Offline")).toBeTruthy();
    expect(screen.queryByText("Needs login")).toBeNull();
  });

  it("says Needs login in the device list too", () => {
    setLive(WAITING);
    render(
      <DeviceListItem deviceId="wb1" name="Rack Power" driver="acme_power" selected={false} enabled onClick={() => {}} />,
    );
    expect(screen.getByText("Needs login")).toBeTruthy();
  });

  it("says nothing of a login in the device list for a device that is simply offline", () => {
    setLive({ ...WAITING, "device.wb1.offline_reason": "unreachable" });
    render(
      <DeviceListItem deviceId="wb1" name="Rack Power" driver="acme_power" selected={false} enabled onClick={() => {}} />,
    );
    expect(screen.queryByText("Needs login")).toBeNull();
  });
});
