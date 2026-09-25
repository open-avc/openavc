import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The two Settings cards panel approval adds: Panel access (Access) and
// Advertise on the network (Network). Both save through the ordinary config
// PATCH and apply at once, so neither may raise the restart notice.

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

// No `panels` and no `discovery` section: what a server before this setting
// existed answered, and what the other Settings tests use. The cards have to
// read that as the defaults, approved panels only and advertising on.
const CONFIG: Record<string, unknown> = {
  network: { http_port: 8080, bind_address: "0.0.0.0", control_interface: "", port80_redirect: false },
  auth: { programmer_username: "", programmer_password: "***", api_key: "" },
  isc: { enabled: true },
  logging: { level: "info", file_enabled: true, max_size_mb: 50, max_files: 5 },
  updates: { check_enabled: true, channel: "stable", auto_check_interval_hours: 24, notify_only: false },
  cloud: { enabled: false, endpoint: "", system_key: "", system_id: "" },
  kiosk: { enabled: false, target_url: "", cursor_visible: false },
  tls: { enabled: false, port: 8443, auto_generate: true, cert_file: "", key_file: "", redirect_http: true, cloud_cert: false },
};
const served = { config: CONFIG };

vi.mock("../api/restClient", () => ({
  getSystemConfig: vi.fn(async () => structuredClone(served.config)),
  getSystemVersion: vi.fn(async () => ({
    version: "0", channel: "stable", platform: "linux", kiosk_available: false, panel_dim_available: false,
  })),
  getSshStatus: vi.fn(async () => null),
  getNetworkAdapters: vi.fn(async () => ({ adapters: [] })),
  getTlsStatus: vi.fn(async () => null),
  updateSystemConfig: vi.fn(async () => ({ status: "ok", updated_sections: [] })),
}));

vi.mock("../store/toastStore", () => ({ showError: vi.fn(), showSuccess: vi.fn() }));
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));

import * as api from "../api/restClient";
import { SystemSettingsView } from "./SystemSettingsView";

const approvedOnly = () => screen.getByRole("radio", { name: /^Approved panels only/ });
const anyone = () => screen.getByRole("radio", { name: /^Anyone on the network/ });
const advertise = () => screen.getByRole("switch", { name: "Advertise on the network" });

async function renderSettings() {
  render(<SystemSettingsView />);
  await waitFor(() => expect(screen.queryByText("Panel access")).toBeTruthy());
}

describe("Panel access card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    served.config = CONFIG;
  });

  it("offers the two modes with their sentences, approved panels only by default", async () => {
    await renderSettings();
    expect(approvedOnly()).toBeChecked();
    expect(anyone()).not.toBeChecked();
    expect(screen.getByText(
      "A new tablet or browser waits until you approve it here. Approved panels stay approved.",
    )).toBeInTheDocument();
    expect(screen.getByText(
      "Any device that can reach this system can open the panel and control the space.",
    )).toBeInTheDocument();
  });

  it("shows the saved mode", async () => {
    served.config = { ...CONFIG, panels: { access: "open" } };
    await renderSettings();
    expect(anyone()).toBeChecked();
    expect(approvedOnly()).not.toBeChecked();
  });

  it("no longer says the panel is never protected, and points at the card instead", async () => {
    await renderSettings();
    expect(screen.queryByText(/never protected/)).toBeNull();
    expect(screen.queryByText(/always reach it/)).toBeNull();
    expect(screen.getAllByText(/Who can open the panel is set below under Panel access\./)).toHaveLength(2);
  });

  it("saves the mode through the config and asks for no restart", async () => {
    await renderSettings();
    await userEvent.click(anyone());
    expect(anyone()).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    expect(api.updateSystemConfig).toHaveBeenCalledWith({ panels: { access: "open" } });
    expect(screen.queryByText(/need a restart/)).toBeNull();
    expect(screen.queryByText(/Restart the server/)).toBeNull();
  });
});

describe("Advertise on the network", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    served.config = CONFIG;
  });

  it("is on when the server has no discovery section", async () => {
    await renderSettings();
    expect(advertise()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(
      /Lets the OpenAVC Panel app find this system in its list\. Turning this off hides the system from the list; devices can still connect by address\./,
    )).toBeInTheDocument();
  });

  it("shows the saved value", async () => {
    served.config = { ...CONFIG, discovery: { advertise: false } };
    await renderSettings();
    expect(advertise()).toHaveAttribute("aria-checked", "false");
  });

  it("saves through the config and asks for no restart", async () => {
    await renderSettings();
    await userEvent.click(advertise());
    expect(advertise()).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByText(/need a restart/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    expect(api.updateSystemConfig).toHaveBeenCalledWith({ discovery: { advertise: false } });
    expect(screen.queryByText(/Restart the server/)).toBeNull();
  });
});
