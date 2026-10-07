import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A field the service's environment holds runs from the environment at every
// start, so Settings shows it locked and says which variable holds it and
// where that is set on this kind of install.

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

const BASE: Record<string, unknown> = {
  network: { http_port: 8080, bind_address: "0.0.0.0", control_interface: "", port80_redirect: false },
  auth: { programmer_username: "", programmer_password: "***", api_key: "" },
  isc: { enabled: true },
  logging: { level: "info", file_enabled: true, max_size_mb: 50, max_files: 5 },
  updates: { check_enabled: true, channel: "stable", auto_check_interval_hours: 24, notify_only: false },
  cloud: { enabled: false, endpoint: "", system_key: "", system_id: "" },
  kiosk: { enabled: false, target_url: "", cursor_visible: false },
  tls: { enabled: false, port: 8443, auto_generate: true, cert_file: "", key_file: "", redirect_http: true, cloud_cert: false },
  panels: { access: "approved", upgrade_notice: false },
  discovery: { advertise: false },
};

function withEnvironment(deployment_type: string, overrides: Record<string, string>) {
  return { ...BASE, _environment: { deployment_type, overrides } };
}

const served: { config: Record<string, unknown> } = { config: BASE };

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

import { SystemSettingsView } from "./SystemSettingsView";

const bindField = () => screen.getByLabelText("Bind address") as HTMLInputElement;
const advertise = () => screen.getByRole("switch", { name: "Advertise on the network" });

async function renderSettings() {
  render(<SystemSettingsView />);
  await waitFor(() => expect(screen.queryByLabelText("Bind address")).toBeTruthy());
}

/** The note in the same row as `el`. */
function noteBeside(el: HTMLElement): HTMLElement | null {
  let row: HTMLElement | null = el;
  for (let i = 0; i < 4 && row; i++) {
    row = row.parentElement;
    const note = row && within(row).queryByTestId("env-held-note");
    if (note) return note;
  }
  return null;
}

describe("A field the environment holds", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    served.config = BASE;
  });

  it("is editable when nothing holds it", async () => {
    await renderSettings();
    expect(bindField()).not.toBeDisabled();
    expect(screen.queryAllByTestId("env-held-note")).toHaveLength(0);
  });

  it("is locked and names its variable", async () => {
    served.config = withEnvironment("linux_package", { "network.bind_address": "OPENAVC_BIND" });
    await renderSettings();
    expect(bindField()).toBeDisabled();
    const note = noteBeside(bindField());
    expect(note?.textContent).toContain("Set by OPENAVC_BIND in this server's environment");
    expect(note?.textContent).toContain("sudo systemctl edit openavc");
    expect(note?.querySelector("a")?.getAttribute("href")).toContain(
      "hardened-deployment/#setting-values-in-the-environment-instead",
    );
    // Only the held field is locked.
    expect(screen.getByLabelText("HTTP port")).not.toBeDisabled();
  });

  it("says the installer resets it on Windows", async () => {
    served.config = withEnvironment("windows_installer", { "network.bind_address": "OPENAVC_BIND" });
    await renderSettings();
    expect(noteBeside(bindField())?.textContent).toContain("sets it again on every update");
  });

  it("locks a switch, so a click changes nothing and nothing is saved", async () => {
    served.config = withEnvironment("docker", { "discovery.advertise": "OPENAVC_MDNS_ADVERTISE" });
    await renderSettings();
    const sw = advertise();
    expect(sw).toBeDisabled();
    await userEvent.click(sw);
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/OPENAVC_MDNS_ADVERTISE/).closest("[data-testid='env-held-note']")?.textContent)
      .toContain("container's environment");
  });

  it("locks both panel access choices", async () => {
    served.config = withEnvironment("linux_package", { "panels.access": "OPENAVC_PANEL_ACCESS" });
    await renderSettings();
    // A disabled fieldset disables what is inside it; the controls' own
    // `disabled` property stays false, which is why these ask jest-dom.
    expect(screen.getByRole("radio", { name: /^Approved panels only/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^Anyone on the network/ })).toBeDisabled();
    const notes = screen.getAllByTestId("env-held-note");
    expect(notes).toHaveLength(1);
    expect(notes[0].textContent).toContain("Set by OPENAVC_PANEL_ACCESS");
  });
});

describe("The Access section", () => {
  it("does not tie the need for a password to the bind address", async () => {
    served.config = BASE;
    await renderSettings();
    const access = screen.getByRole("heading", { name: "Access" });
    const lead = access.nextElementSibling as HTMLElement;
    expect(lead.textContent).not.toContain("127.0.0.1");
    expect(lead.textContent).toContain("always asks for the admin password");
  });
});
