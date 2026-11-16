import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";

/**
 * A product that describes itself (equipment) can describe settings OpenAVC
 * does not use, or make mistakes OpenAVC works around. The device runs; its
 * page names each kind once, in the server's sentence, and says who corrects
 * it.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  getProject: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  getProject: mocks.getProject,
  updateDevice: vi.fn(),
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

const liveState: Record<string, unknown> = {
  "device.amp.enabled": true,
  "device.amp.connected": true,
};
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

const PROJECT = {
  devices: [{ id: "amp", driver: "openavc_equipment", name: "Amp", config: {}, enabled: true }],
  connections: { amp: { host: "192.0.2.20" } },
  device_groups: [], macros: [], monitors: [],
  ui: { pages: [], master_elements: [] },
};

const UNUSED = "This device describes settings OpenAVC does not use: web_ui, routing.";
const WORKED_AROUND =
  "This device describes itself with mistakes OpenAVC works around: " +
  "State variable 'counter': unknown type 'bool'; use 'boolean'.";

function device(notices?: string[]) {
  return {
    id: "amp", name: "Amp", driver: "openavc_equipment", connected: true, enabled: true,
    commands: {}, driver_info: { config_schema: {} },
    ...(notices ? { model_notices: notices } : {}),
  };
}

function showPage() {
  render(<DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />);
}

describe("a product's notes on its own description", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useProjectStore.setState({ project: PROJECT as never, dirty: false });
    mocks.getProject.mockResolvedValue(PROJECT);
  });

  it("names each kind once and says who corrects it", async () => {
    mocks.getDevice.mockResolvedValue(device([UNUSED, WORKED_AROUND]));
    showPage();
    const banner = await screen.findByTestId("model-notices-banner");
    expect(within(banner).getByText(UNUSED)).toBeTruthy();
    expect(within(banner).getByText(WORKED_AROUND)).toBeTruthy();
    expect(within(banner).getByText("Its manufacturer can correct this in the product's firmware.")).toBeTruthy();
  });

  it("shows nothing for a product with nothing to say", async () => {
    mocks.getDevice.mockResolvedValue(device());
    showPage();
    expect(await screen.findAllByText("Amp")).toBeTruthy();
    expect(screen.queryByTestId("model-notices-banner")).toBeNull();
  });
});
