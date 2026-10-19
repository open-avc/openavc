import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A discovered device whose driver needs a setting the scan cannot supply (a
// product's username and password) is asked for it on the card, before it is
// added, the way Add Device asks. The address is the scan's.

const mocks = vi.hoisted(() => ({
  results: {} as Record<string, unknown>,
  project: { devices: [] as unknown[], connections: {} as Record<string, unknown> },
  add: vi.fn(async () => ({ status: "ok", device_id: "acme_amp_10_0_0_9", name: "Acme Amp" })),
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
      default_config: { host: "", port: 9443, username: "", password: "" },
      config_schema: {
        host: { type: "string", label: "Host", required: true },
        port: { type: "integer", label: "Port", default: 9443 },
        username: { type: "string", label: "Username", required: true },
        password: { type: "string", label: "Password", required: true, secret: true },
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
  useProjectStore: (selector: (s: unknown) => unknown) => selector({ project: mocks.project }),
}));

vi.mock("../store/navigationStore", () => ({
  useNavigationStore: (selector: (s: unknown) => unknown) =>
    selector({ navigate: vi.fn(), setPendingDeviceId: vi.fn() }),
}));

vi.mock("../store/toastStore", () => ({ showError: vi.fn() }));

vi.mock("../components/shared/DeviceSettingsSetupDialog", () => ({
  DeviceSettingsSetupDialog: () => null,
  hasDriverSetupSettings: () => false,
}));

import { DiscoveryPanel } from "./DiscoveryView";
import { useDiscoveryStore } from "../store/discoveryStore";

const SCANNED = {
  ip: "10.0.0.9",
  mac: null,
  hostname: null,
  manufacturer: "Acme",
  model: "Amp",
  device_name: null,
  firmware: null,
  serial_number: null,
  open_ports: [9443],
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
  mocks.results = { devices: [SCANNED], status: "complete", port_labels: {}, port_drivers: {}, warnings: [] };
  mocks.project = { devices: [], connections: {} };
  mocks.add.mockClear();
});

async function openCardAndAdd() {
  render(<DiscoveryPanel />);
  fireEvent.click(await screen.findByText("Acme Amp"));
  fireEvent.click(await screen.findByRole("button", { name: /Add to Project/ }));
  await screen.findByRole("button", { name: /Confirm & Add/ });
}

describe("adding a discovered device that needs a sign-in", () => {
  it("asks for the username and password before adding, never the address", async () => {
    await openCardAndAdd();
    expect(screen.getByLabelText(/^Username/)).toBeTruthy();
    expect(screen.getByLabelText(/^Password/)).toHaveAttribute("type", "password");
    expect(screen.queryByLabelText(/^Host/)).toBeNull();
    expect(screen.queryByLabelText(/^Port/)).toBeNull();
  });

  it("refuses to add with them blank, by name", async () => {
    await openCardAndAdd();
    fireEvent.click(screen.getByRole("button", { name: /Confirm & Add/ }));
    expect(await screen.findByText("Username and Password are required.")).toBeTruthy();
    expect(mocks.add).not.toHaveBeenCalled();
  });

  it("adds the device with what was entered", async () => {
    await openCardAndAdd();
    fireEvent.change(screen.getByLabelText(/^Username/), { target: { value: "engineer" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: /Confirm & Add/ }));
    await waitFor(() => expect(mocks.add).toHaveBeenCalledTimes(1));
    expect(mocks.add).toHaveBeenCalledWith({
      ip: "10.0.0.9",
      driver_id: "acme_amp",
      config: { username: "engineer", password: "s3cret" },
    });
  });
});
