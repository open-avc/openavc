import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Settings > Network: Accept connections from and Reach devices through.
// The server answers what a choice does (the preview); the screen says it,
// asks before a change that cuts off this browser, and saves the adapter by
// identity. The adapters and the server's answers are invented here.

const projectState = {
  dirty: false,
  project: {
    openavc_version: "0.13.0",
    project: { id: "p1", name: "t" },
    settings: {
      display: {
        idle_dim_enabled: true, idle_dim_timeout_seconds: 300, idle_dim_level_percent: 20,
        idle_dim_wake_passes_touch: false, idle_dim_hold_state_key: "",
        brightness_percent: null as number | null,
      },
      devices: { reconnect_interval_seconds: null as number | null },
    },
  },
  update: vi.fn(),
  save: vi.fn(async () => {}),
};

vi.mock("../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: typeof projectState) => unknown) => selector(projectState),
    { getState: () => projectState },
  ),
}));

const ETHERNET = {
  name: "Ethernet", mac: "50-EB-F6-D2-FC-29",
  addresses: [{ ip: "10.20.30.5", subnet: "10.20.30.0/24" }], link: true, has_address: true,
};
const WIFI = {
  name: "Wi-Fi", mac: "F0-57-A6-A8-D4-0F",
  addresses: [{ ip: "192.168.1.40", subnet: "192.168.1.0/24" }], link: true, has_address: true,
};
const UNPLUGGED = { name: "Ethernet 2", mac: "00-11-22-33-44-55", addresses: [], link: false, has_address: false };

const RUNNING_ANY = {
  mode: "any", adapter: {}, adapter_name: "", label: "Any network", addresses: ["0.0.0.0"],
  network_addresses: [], local_only: false, held_by: null, problem: null,
  restart_needed: false, safety_net: null,
};

function baseConfig(): Record<string, unknown> {
  return {
    network: {
      http_port: 8080, accept_connections_from: "any", accept_connections_adapter: {},
      bind_address: "0.0.0.0", reach_devices_through: {}, control_interface: "", port80_redirect: false,
    },
    auth: { programmer_username: "", programmer_password: "***", api_key: "" },
    isc: { enabled: true },
    logging: { level: "info", file_enabled: true, max_size_mb: 50, max_files: 5 },
    updates: { check_enabled: true, channel: "stable", auto_check_interval_hours: 24, notify_only: false },
    cloud: { enabled: false, endpoint: "", system_key: "", system_id: "" },
    kiosk: { enabled: false, target_url: "", cursor_visible: false },
    tls: { enabled: false, port: 8443, auto_generate: true, cert_file: "", key_file: "", redirect_http: true, cloud_cert: false },
    panels: { access: "approved", upgrade_notice: false },
    discovery: { advertise: true },
    _environment: { deployment_type: "linux_package", overrides: {} },
    _listen: RUNNING_ANY,
    _reach_devices: { mode: "every", adapter: {}, adapter_name: "", address: "", connected: true, label: "Every connected adapter" },
  };
}

type Choice = { accept_connections_from: string; accept_connections_adapter?: { name?: string; mac?: string } };

/** What the server would say about a choice, for a browser that reached it
 *  on Wi-Fi (192.168.1.40). */
function previewFor(choice: Choice, cutOffAsked = false) {
  const mode = choice.accept_connections_from;
  const onWifi = mode === "adapter" && choice.accept_connections_adapter?.name === "Wi-Fi";
  const onEthernet = mode === "adapter" && choice.accept_connections_adapter?.name === "Ethernet";
  const keeps = mode === "any" || onWifi;
  return {
    mode,
    adapter_name: onEthernet ? "Ethernet" : onWifi ? "Wi-Fi" : "",
    label: mode === "any" ? "Any network" : mode === "this_computer" ? "This computer only" : `${onEthernet ? "Ethernet" : "Wi-Fi"} only`,
    panel_addresses: mode === "any" ? ["10.20.30.5", "192.168.1.40"] : onEthernet ? ["10.20.30.5"] : onWifi ? ["192.168.1.40"] : [],
    connected_through: "192.168.1.40",
    serves_this_address: keeps,
    keeps_this_connection: keeps || !cutOffAsked,
    safety_net: !keeps && cutOffAsked,
    problem: null,
    consequences: mode === "this_computer"
      ? [{ code: "approved_panels", count: 3, message: "3 approved touch panels will stop connecting." }]
      : [],
    held_by: null,
  };
}

const served: { config: Record<string, unknown>; cutOff: boolean } = { config: baseConfig(), cutOff: true };

vi.mock("../api/restClient", () => ({
  getSystemConfig: vi.fn(async () => structuredClone(served.config)),
  getSystemVersion: vi.fn(async () => ({
    version: "0", channel: "stable", platform: "linux", kiosk_available: false, panel_dim_available: false,
  })),
  getSshStatus: vi.fn(async () => null),
  getNetworkAdapters: vi.fn(async () => ({ adapters: [], interfaces: [ETHERNET, WIFI, UNPLUGGED] })),
  getTlsStatus: vi.fn(async () => null),
  previewListen: vi.fn(async (choice: Choice) => previewFor(choice, served.cutOff)),
  updateSystemConfig: vi.fn(async () => ({ status: "ok", updated_sections: ["network"] })),
  restartSystem: vi.fn(async () => ({ status: "restarting", mode: "graceful", delay_seconds: 2 })),
  downloadCertificate: vi.fn(),
}));

vi.mock("../store/toastStore", () => ({ showError: vi.fn(), showSuccess: vi.fn() }));
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));

import * as api from "../api/restClient";
import { SystemSettingsView } from "./SystemSettingsView";

const listenField = () => screen.getByLabelText("Accept connections from") as HTMLSelectElement;
const reachField = () => screen.getByLabelText("Reach devices through") as HTMLSelectElement;
const optionValue = (select: HTMLSelectElement, label: string) =>
  [...select.options].find((o) => o.textContent === label)!.value;

async function renderSettings() {
  render(<SystemSettingsView />);
  await waitFor(() => expect(screen.queryByLabelText("Accept connections from")).toBeTruthy());
  await waitFor(() => expect(listenField().options.length).toBeGreaterThan(2));
}

async function choose(label: string) {
  await userEvent.selectOptions(listenField(), optionValue(listenField(), label));
}

describe("Accept connections from", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    served.config = baseConfig();
    served.cutOff = true;
  });

  it("lists Any network, each adapter by name and address, and This computer only", async () => {
    await renderSettings();
    expect([...listenField().options].map((o) => o.textContent)).toEqual([
      "Any network",
      "Ethernet · 10.20.30.5 · 10.20.30.0/24",
      "Wi-Fi · 192.168.1.40 · 192.168.1.0/24",
      "Ethernet 2 · not connected",
      "This computer only",
    ]);
    expect(listenField().value).toBe("any");
  });

  it("says who can connect, and where panels open, for the choice on screen", async () => {
    await renderSettings();
    await waitFor(() => expect(screen.getByTestId("panels-open-at").textContent).toContain("http://10.20.30.5:8080/panel"));
    expect(screen.getByTestId("panels-open-at").textContent).toContain("http://192.168.1.40:8080/panel");

    await choose("Ethernet · 10.20.30.5 · 10.20.30.0/24");
    expect(screen.getByTestId("accept-connections-help").textContent).toBe(
      "Only devices on the Ethernet network, and this computer itself, can open the panel and the Programmer.",
    );
    await waitFor(() => expect(screen.getByTestId("panels-open-at").textContent).not.toContain("192.168.1.40"));
    expect(api.previewListen).toHaveBeenLastCalledWith({
      accept_connections_from: "adapter",
      accept_connections_adapter: { name: "Ethernet", mac: "50-EB-F6-D2-FC-29" },
    });
  });

  it("this computer only lists no panel address, says what stops, and turns Advertise off", async () => {
    await renderSettings();
    await choose("This computer only");
    expect(screen.getByTestId("accept-connections-help").textContent).toBe(
      "Only this computer's own screen and browser can open the panel and the Programmer.",
    );
    expect((await screen.findByTestId("accept-connections-consequences")).textContent).toContain(
      "3 approved touch panels will stop connecting.",
    );
    expect(screen.queryByTestId("panels-open-at")).toBeNull();
    const advertise = screen.getByRole("switch", { name: "Advertise on the network" });
    expect(advertise).toBeDisabled();
    expect(advertise.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("advertise-help").textContent).toBe(
      "Nothing on the network can connect, so there is nothing to announce.",
    );
  });

  it("asks for a restart, and stops asking when the running choice is chosen again", async () => {
    await renderSettings();
    expect(screen.queryByTestId("restart-banner")).toBeNull();
    await choose("This computer only");
    expect(screen.getByTestId("restart-banner")).toBeTruthy();
    await choose("Any network");
    expect(screen.queryByTestId("restart-banner")).toBeNull();
  });

  it("says why the network is not listened on, as the server does", async () => {
    const problem = {
      code: "adapter_not_connected",
      message: "Not accepting connections from the network: the Ethernet adapter isn't connected.",
    };
    served.config = {
      ...baseConfig(),
      network: { ...(baseConfig().network as object), accept_connections_from: "adapter", accept_connections_adapter: { name: "Ethernet", mac: ETHERNET.mac } },
      _listen: { ...RUNNING_ANY, mode: "adapter", adapter: { name: "Ethernet", mac: ETHERNET.mac }, adapter_name: "Ethernet", local_only: true, problem },
    };
    await renderSettings();
    expect(screen.getByTestId("accept-connections-problem").textContent).toBe(problem.message);
  });

  it("warns about a missing password for every choice that reaches a network", async () => {
    served.config = { ...baseConfig(), auth: { programmer_username: "", programmer_password: "", api_key: "" } };
    await renderSettings();
    const warning = () => screen.queryByText(/accessible on the network with no authentication/);
    expect(warning()).toBeTruthy();
    await choose("Ethernet · 10.20.30.5 · 10.20.30.0/24");
    expect(warning()).toBeTruthy();
    await choose("This computer only");
    expect(warning()).toBeNull();
  });

  it("asks before a change that cuts off this browser, and saves nothing until agreed", async () => {
    await renderSettings();
    await choose("Ethernet · 10.20.30.5 · 10.20.30.0/24");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Change who can connect?")).toBeTruthy();
    const warning = within(dialog).getByTestId("listen-cut-off-warning").textContent;
    expect(warning).toContain(
      "You're connected through 192.168.1.40. After the restart, OpenAVC won't accept connections from that network, so this page will stop loading here.",
    );
    expect(warning).toContain("http://10.20.30.5:8080/programmer");
    expect(warning).toContain("from a device on the Ethernet network, or use this computer's own screen");
    expect(warning).toContain(
      "If nobody opens the Programmer that way within 10 minutes, OpenAVC goes back to Any network.",
    );
    // Not paired with the cloud: the cloud line would not help.
    expect(warning).not.toContain("OpenAVC Cloud");
    expect(api.updateSystemConfig).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api.updateSystemConfig).not.toHaveBeenCalled();
  });

  it("restarts and hands over the new address once agreed", async () => {
    vi.mocked(api.updateSystemConfig).mockImplementation(async () => {
      // What the server holds after the save: a choice waiting for a restart.
      served.config = {
        ...baseConfig(),
        network: {
          ...(baseConfig().network as object),
          accept_connections_from: "adapter",
          accept_connections_adapter: { name: "Ethernet", mac: ETHERNET.mac },
        },
        _listen: { ...RUNNING_ANY, restart_needed: true },
      };
      return { status: "ok", updated_sections: ["network"], safety_net: { previous: "Any network", choice: "Ethernet only", running: false, deadline: null } };
    });
    await renderSettings();
    await choose("Ethernet · 10.20.30.5 · 10.20.30.0/24");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Restart and apply" }));

    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.updateSystemConfig).mock.calls[0][0]).toMatchObject({
      network: {
        accept_connections_from: "adapter",
        accept_connections_adapter: { name: "Ethernet", mac: "50-EB-F6-D2-FC-29" },
      },
    });
    // Straight to the restart, which says where to open the Programmer.
    const lines = await screen.findByTestId("restart-handoff-lines");
    expect(lines.textContent).toContain("This page will stop loading here.");
    // The new choice's own address, on the port this page reaches the
    // server through (jsdom's here).
    expect(lines.textContent).toMatch(/http:\/\/10\.20\.30\.5:\d+\/programmer/);
    expect(screen.queryByText("Restart to apply changes?")).toBeNull();
  });

  it("saves a change that keeps this browser and offers the usual restart", async () => {
    served.cutOff = false;
    await renderSettings();
    await choose("Wi-Fi · 192.168.1.40 · 192.168.1.0/24");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Restart to apply changes?")).toBeTruthy();
    expect(screen.queryByText("Change who can connect?")).toBeNull();
  });
});

describe("Reach devices through", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    served.config = baseConfig();
    served.cutOff = true;
  });

  it("saves the adapter by identity, with no restart", async () => {
    await renderSettings();
    expect(reachField().value).toBe("");
    expect(reachField().options[0].textContent).toBe("Every connected adapter");
    await userEvent.selectOptions(reachField(), optionValue(reachField(), "Wi-Fi · 192.168.1.40 · 192.168.1.0/24"));
    expect(screen.getByTestId("reach-devices-help").textContent).toContain("Device control and discovery scans use this adapter.");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.updateSystemConfig).mock.calls[0][0]).toEqual({
      network: { reach_devices_through: { name: "Wi-Fi", mac: "F0-57-A6-A8-D4-0F" } },
    });
    expect(screen.queryByText("Restart to apply changes?")).toBeNull();
  });

  it("keeps a chosen adapter that is gone visible, and says so", async () => {
    served.config = {
      ...baseConfig(),
      network: { ...(baseConfig().network as object), reach_devices_through: { name: "Ethernet 3", mac: "AA-BB-CC-DD-EE-FF" } },
      _reach_devices: {
        mode: "adapter", adapter: { name: "Ethernet 3", mac: "AA-BB-CC-DD-EE-FF" }, adapter_name: "Ethernet 3",
        address: "10.9.9.9", connected: false, label: "Ethernet 3",
      },
    };
    await renderSettings();
    const selected = reachField().options[reachField().selectedIndex];
    expect(selected.textContent).toBe("Ethernet 3 · not connected");
    expect(screen.getByTestId("reach-devices-problem").textContent).toContain("Ethernet 3 isn't connected");
  });
});
