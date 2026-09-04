import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Pins the WIRING between the project store and the Save button, not the
// dirty rule itself.
//
// What shipped broken: the panel display and device settings live in the
// PROJECT, and handleSave was taught to flush the project store -- but
// `hasDirty`, which is the button's `disabled` prop, still counted only the
// system.json map. So changing a panel setting marked the project dirty,
// left the button greyed, saved nothing, and the edit silently went nowhere.
// A unit test of the dirty rule would have passed the whole time, because the
// rule was never the problem; the button not reading it was.

const projectState = {
  dirty: false,
  project: {
    openavc_version: "0.13.0",
    project: { id: "p1", name: "t" },
    settings: {
      display: {
        idle_dim_enabled: true,
        idle_dim_timeout_seconds: 300,
        idle_dim_level_percent: 20,
        idle_dim_wake_passes_touch: false,
        idle_dim_hold_state_key: "",
        brightness_percent: null as number | null,
      },
      devices: { reconnect_interval_seconds: null as number | null },
    },
  },
  update: vi.fn(() => { projectState.dirty = true; }),
  save: vi.fn(async () => { projectState.dirty = false; }),
};

vi.mock("../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: typeof projectState) => unknown) => selector(projectState),
    { getState: () => projectState },
  ),
}));

const CONFIG = {
  network: { http_port: 8080, bind_address: "0.0.0.0", control_interface: "", port80_redirect: false },
  auth: { programmer_username: "", programmer_password: "***", api_key: "", panel_lock_code: "" },
  isc: { enabled: true, discovery_enabled: true, auth_key: "" },
  logging: { level: "info", file_enabled: true, max_size_mb: 50, max_files: 5 },
  updates: { check_enabled: true, channel: "stable", auto_check_interval_hours: 24, auto_backup_before_update: true, notify_only: false },
  cloud: { enabled: false, endpoint: "", system_key: "", system_id: "" },
  kiosk: { enabled: false, target_url: "", cursor_visible: false },
  tls: { enabled: false, port: 8443, auto_generate: true, cert_file: "", key_file: "", redirect_http: true, cloud_cert: false },
};

vi.mock("../api/restClient", () => ({
  getSystemConfig: vi.fn(async () => structuredClone(CONFIG)),
  // panel_dim_available true so the Panel Display card renders at all.
  getSystemVersion: vi.fn(async () => ({ version: "0", channel: "stable", platform: "linux", kiosk_available: false, panel_dim_available: true })),
  getSshStatus: vi.fn(async () => null),
  getNetworkAdapters: vi.fn(async () => ({ adapters: [] })),
  getTlsStatus: vi.fn(async () => null),
  updateSystemConfig: vi.fn(async () => ({ status: "ok", updated_sections: [] })),
}));

vi.mock("../store/toastStore", () => ({ showError: vi.fn(), showSuccess: vi.fn() }));
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));

import { SystemSettingsView } from "./SystemSettingsView";

const saveButton = () =>
  screen.getAllByRole("button").find((b) => /save/i.test(b.textContent ?? "")) as HTMLButtonElement;

describe("Save button and the project store", () => {
  beforeEach(() => {
    projectState.dirty = false;
    vi.clearAllMocks();
  });

  it("is disabled when nothing has changed", async () => {
    render(<SystemSettingsView />);
    await waitFor(() => expect(saveButton()).toBeTruthy());
    expect(saveButton().disabled).toBe(true);
  });

  it("enables when only a PROJECT setting changed", async () => {
    const { rerender } = render(<SystemSettingsView />);
    await waitFor(() => expect(saveButton()).toBeTruthy());

    // What the user does: change a panel display value. It writes to the
    // project store, not to the system.json dirty map.
    projectState.dirty = true;
    rerender(<SystemSettingsView />);

    await waitFor(() =>
      expect(saveButton().disabled).toBe(false),
    );
  });

  it("saves the project when the button is pressed", async () => {
    const { rerender } = render(<SystemSettingsView />);
    await waitFor(() => expect(saveButton()).toBeTruthy());
    projectState.dirty = true;
    rerender(<SystemSettingsView />);

    fireEvent.click(saveButton());
    await waitFor(() => expect(projectState.save).toHaveBeenCalled());
  });
});
