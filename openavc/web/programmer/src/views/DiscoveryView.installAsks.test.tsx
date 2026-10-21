import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Install & Add for a community driver that needs a setting the scan cannot
// supply (an API key): the server installs the driver and refuses the add by
// name, and the card becomes the installed driver's, asking for the setting
// before Confirm & Add, instead of leaving a device saved with it blank.

const DRIVER = {
  id: "acme_vault",
  name: "Acme Vault",
  manufacturer: "Acme",
  category: "utility",
  commands: {},
  default_config: { host: "", port: 8443, api_key: "" },
  config_schema: {
    host: { type: "string", label: "Host", required: true },
    port: { type: "integer", label: "Port", default: 8443 },
    api_key: { type: "string", label: "API Key", required: true, secret: true },
  },
};

const mocks = vi.hoisted(() => ({
  results: {} as Record<string, unknown>,
  installed: [] as unknown[],
  installAndMatch: vi.fn(),
  add: vi.fn(async () => ({ status: "ok", device_id: "acme_vault_10_0_0_9", name: "Acme Vault" })),
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
  listDrivers: vi.fn(async () => mocks.installed),
  fetchCommunityDrivers: vi.fn(async () => [
    {
      id: "acme_vault",
      name: "Acme Vault",
      manufacturer: "Acme",
      category: "utility",
      version: "1.0.0",
      file: "utility/acme_vault.py",
    },
  ]),
  discoveryStartScan: vi.fn(async () => ({})),
  discoveryCancelScan: vi.fn(async () => ({})),
  discoverySetConfig: vi.fn(async () => ({})),
  discoveryInstallAndMatch: mocks.installAndMatch,
  discoveryAddDevice: mocks.add,
  getDriverHelp: vi.fn(async () => ({ setup: "Create an API key on the vault's admin page." })),
}));

vi.mock("../store/projectStore", () => ({
  useProjectStore: (selector: (s: unknown) => unknown) =>
    selector({ project: { devices: [], connections: {} } }),
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
import { useDriverBuilderStore } from "../store/driverBuilderStore";

const SCANNED = {
  ip: "10.0.0.9",
  mac: null,
  hostname: null,
  manufacturer: "Acme",
  model: "Vault",
  device_name: null,
  firmware: null,
  serial_number: null,
  open_ports: [8443],
  banners: {},
  protocols: [],
  category: "utility",
  alive: true,
  identification: {
    state: "identified",
    driver_id: "acme_vault",
    candidates: ["acme_vault"],
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
  useDriverBuilderStore.setState({ registeredDrivers: [] });
  mocks.results = { devices: [SCANNED], status: "complete", port_labels: {}, port_drivers: {}, warnings: [] };
  mocks.installed = [];
  mocks.add.mockClear();
  mocks.installAndMatch.mockReset();
  mocks.installAndMatch.mockImplementation(async () => {
    mocks.installed = [DRIVER];
    return {
      status: "installed_not_added",
      error: "API Key is required.",
      missing: ["api_key"],
      device: SCANNED,
    };
  });
});

async function installFromTheCard() {
  render(<DiscoveryPanel />);
  fireEvent.click(await screen.findByText("Acme Vault"));
  fireEvent.click(await screen.findByRole("button", { name: /Install & Add/ }));
  await screen.findByRole("button", { name: /Confirm & Add/ });
}

describe("Install & Add for a driver that needs a setting the scan cannot supply", () => {
  it("asks for it once the driver is installed, instead of an error", async () => {
    await installFromTheCard();
    expect(screen.getByText(/Driver installed. Fill in the settings below/)).toBeTruthy();
    expect(screen.getByLabelText(/^API Key/)).toHaveAttribute("type", "password");
    expect(screen.queryByLabelText(/^Host/)).toBeNull();
    expect(screen.getByText("Create an API key on the vault's admin page.")).toBeTruthy();
    expect(screen.queryByText(/could not add device/)).toBeNull();
    expect(mocks.add).not.toHaveBeenCalled();
  });

  it("refuses Confirm & Add with it blank, and adds the device with it entered", async () => {
    await installFromTheCard();
    fireEvent.click(screen.getByRole("button", { name: /Confirm & Add/ }));
    expect(await screen.findByText("API Key is required.")).toBeTruthy();
    expect(mocks.add).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/^API Key/), { target: { value: "k-123" } });
    fireEvent.click(screen.getByRole("button", { name: /Confirm & Add/ }));
    await waitFor(() => expect(mocks.add).toHaveBeenCalledTimes(1));
    expect(mocks.add).toHaveBeenCalledWith({
      ip: "10.0.0.9",
      driver_id: "acme_vault",
      config: { api_key: "k-123" },
    });
    expect(mocks.installAndMatch).toHaveBeenCalledTimes(1);
  });

  it("still says why when the add failed for another reason", async () => {
    mocks.installAndMatch.mockImplementation(async () => ({
      status: "installed_not_added",
      error: "Device 'acme_vault_10_0_0_9' is already in the project",
      device: SCANNED,
    }));
    render(<DiscoveryPanel />);
    fireEvent.click(await screen.findByText("Acme Vault"));
    fireEvent.click(await screen.findByRole("button", { name: /Install & Add/ }));
    expect(
      await screen.findByText(/Driver installed but could not add device: Device 'acme_vault_10_0_0_9' is already in the project/),
    ).toBeTruthy();
  });
});
