import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * A device a running simulation has no simulator for is kept disconnected on
 * purpose. Its card says why, and the line under it says what brings it back,
 * for product hardware as much as for any other device.
 */

const mocks = vi.hoisted(() => ({ getDevice: vi.fn() }));

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

import { useProjectStore } from "../../store/projectStore";
import { DeviceDetail } from "./DeviceDetail";

const device = { id: "amp", driver: "acme_widget", name: "Lobby Amp", config: {}, enabled: true };
const NEXT_STEP = /Not connecting while simulation runs/;

function offlineForSimulation(failed: boolean) {
  for (const key of Object.keys(liveState)) delete liveState[key];
  Object.assign(liveState, {
    "device.amp.enabled": true,
    "device.amp.connected": false,
    "device.amp.offline_reason": "no_simulator",
    "device.amp.offline_detail": "This device has no simulator. Stop simulation to control it.",
    "device.amp.reconnect_failed": failed,
  });
}

function showPage() {
  render(<DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />);
}

describe("a device simulation keeps disconnected", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useProjectStore.setState({
      project: {
        devices: [device], device_groups: [], macros: [], monitors: [],
        ui: { pages: [], master_elements: [] },
      } as never,
    });
  });

  it("says under the reason what brings it back", async () => {
    offlineForSimulation(false);
    mocks.getDevice.mockResolvedValue({
      id: "amp", name: "Lobby Amp", driver: "acme_widget", connected: false,
      commands: {}, driver_info: {},
    });
    showPage();
    expect(await screen.findByText(NEXT_STEP)).toBeTruthy();
  });

  it("says the same for product hardware, not to check the product", async () => {
    offlineForSimulation(true);
    mocks.getDevice.mockResolvedValue({
      id: "amp", name: "Lobby Amp", driver: "acme_widget", connected: false,
      ownership: "manufacturer", commands: {}, driver_info: {},
    });
    showPage();
    expect(await screen.findByText(NEXT_STEP)).toBeTruthy();
    expect(screen.queryByText(/Check this product's OpenAVC status/)).toBeNull();
  });
});
