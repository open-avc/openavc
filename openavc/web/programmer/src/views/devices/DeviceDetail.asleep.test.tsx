import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * A device a command put to sleep (sleeps_device) reads "Asleep. Power On
 * wakes it." on its page, in place of the Offline banner and its
 * troubleshooting: nothing is wrong, and the sentence says what wakes it.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
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
import { DeviceDetail } from "./DeviceDetail";

const SENTENCE = "Asleep. Power On wakes it.";

function showPage() {
  render(
    <DeviceDetail
      deviceId="disp"
      onEdit={() => {}}
      onDeleted={() => {}}
      onDuplicate={() => {}}
    />,
  );
}

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, next);
}

describe("a device that is asleep", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useProjectStore.setState({
      project: {
        devices: [{ id: "disp", driver: "acme_display", name: "Lobby Display", config: {}, enabled: true }],
        device_groups: [], macros: [], monitors: [],
        ui: { pages: [], master_elements: [] },
      } as never,
    });
    mocks.getDevice.mockResolvedValue({
      id: "disp", name: "Lobby Display", driver: "acme_display", connected: false,
      enabled: true, commands: { power_on: { label: "Power On" } }, driver_info: {},
    });
  });

  it("says what wakes it instead of showing the Offline banner", async () => {
    setLive({
      "device.disp.enabled": true,
      "device.disp.connected": false,
      "device.disp.asleep": true,
      "device.disp.wake_command": "power_on",
      "device.disp.offline_reason": null,
      "device.disp.offline_detail": SENTENCE,
    });
    showPage();
    await screen.findByRole("button", { name: /Test/ });

    const banner = screen
      .getAllByText(SENTENCE)
      .map((el) => el.closest('[role="status"]'))
      .find(Boolean);
    expect(banner).toBeTruthy();
    expect(screen.queryByText("Offline")).toBeNull();
    expect(screen.getByTitle("Asleep")).toBeTruthy();
  });

  it("shows the Offline banner once it is no longer asleep", async () => {
    setLive({
      "device.disp.enabled": true,
      "device.disp.connected": false,
      "device.disp.asleep": null,
      "device.disp.offline_reason": "unreachable",
      "device.disp.offline_detail": "Nothing answered at 10.0.0.9.",
    });
    showPage();
    await screen.findByRole("button", { name: /Test/ });

    expect(screen.getByText("Offline")).toBeTruthy();
    expect(screen.queryByTitle("Asleep")).toBeNull();
  });
});
