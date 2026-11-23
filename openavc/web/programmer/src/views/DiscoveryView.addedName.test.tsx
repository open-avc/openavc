import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A device added from a scan whose driver has settings to set up says it was
// added under the name the server gave it, once that dialog closes, not under
// its driver's name.

const mocks = vi.hoisted(() => ({
  results: {} as Record<string, unknown>,
  project: { devices: [] as unknown[], connections: {} as Record<string, unknown> },
  add: vi.fn(async () => ({ status: "ok", device_id: "acme_amp_10_0_0_9", name: "Acme AX-9" })),
  load: vi.fn(async () => {}),
}));

vi.mock("../api/restClient", () => ({
  discoveryGetSubnets: vi.fn(async () => ({ subnets: [] })),
  discoveryGetConfig: vi.fn(async () => ({
    snmp_enabled: true,
    snmp_community_set: false,
    gentle_mode: false,
  })),
  discoveryGetResults: vi.fn(async () => mocks.results),
  getSystemConfig: vi.fn(async () => ({ network: {} })),
  getNetworkAdapters: vi.fn(async () => ({ adapters: [] })),
  listDrivers: vi.fn(async () => [
    {
      id: "acme_amp",
      name: "Acme Amp",
      manufacturer: "Acme",
      category: "audio",
      commands: {},
      default_config: { host: "", port: 9443 },
      config_schema: {
        host: { type: "string", label: "Host", required: true },
        port: { type: "integer", label: "Port", default: 9443 },
      },
    },
  ]),
  fetchCommunityDrivers: vi.fn(async () => []),
  discoveryStartScan: vi.fn(async () => ({})),
  discoveryCancelScan: vi.fn(async () => ({})),
  discoverySetConfig: vi.fn(async () => ({})),
  getDriverHelp: vi.fn(async () => ({ setup: "Enter the username and password its documentation gives." })),
  discoveryAddDevice: mocks.add,
}));

vi.mock("../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: unknown) => unknown) => selector({ project: mocks.project }),
    { getState: () => ({ project: mocks.project, load: mocks.load }) },
  ),
}));

vi.mock("../store/navigationStore", () => ({
  useNavigationStore: (selector: (s: unknown) => unknown) =>
    selector({ navigate: vi.fn(), setPendingDeviceId: vi.fn() }),
}));

vi.mock("../store/toastStore", () => ({ showError: vi.fn() }));

vi.mock("../components/shared/DeviceSettingsSetupDialog", () => ({
  DeviceSettingsSetupDialog: ({ onClose }: { onClose: () => void }) => (
    <button onClick={onClose}>Finish setup</button>
  ),
  hasDriverSetupSettings: () => true,
}));

import { DiscoveryPanel } from "./DiscoveryView";
import { useDiscoveryStore } from "../store/discoveryStore";

const SCANNED = {
  ip: "10.0.0.9",
  name: "Acme Amp",
  mac: null,
  hostname: null,
  manufacturer: "Acme",
  model: "Amp",
  device_name: null,
  firmware: null,
  serial_number: null,
  open_ports: [9443],
  ports: [{ port: 9443, label: null, in_use_by: null }],
  banners: {},
  protocols: [],
  category: "audio",
  alive: true,
  identification: {
    state: "identified",
    driver_id: "acme_amp",
    candidates: ["acme_amp"],
    source: "mdns",
    reason: "mDNS announcement",
    evidence: [],
  },
  evidence_log: [],
  mdns_services: [],
  ssdp_info: null,
  snmp_info: null,
};

beforeEach(() => {
  useDiscoveryStore.getState().clear();
  mocks.results = { devices: [SCANNED], status: "complete", warnings: [] };
  mocks.project = { devices: [], connections: {} };
  mocks.add.mockClear();
  mocks.load.mockClear();
});

describe("a device added with settings to set up", () => {
  it("is said to be added under the name the server gave it", async () => {
    render(<DiscoveryPanel />);
    fireEvent.click(await screen.findByText("Acme Amp"));
    fireEvent.click(await screen.findByRole("button", { name: /Add to Project/ }));
    // The driver's setup notes are shown once, before the add.
    fireEvent.click(await screen.findByRole("button", { name: /Confirm & Add/ }));
    await waitFor(() => expect(mocks.add).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: "Finish setup" }));
    expect(await screen.findByText('Added "Acme AX-9" to project.')).toBeTruthy();
    // The add was written by the server, so the project is read again.
    expect(mocks.load).toHaveBeenCalled();
  });
});
