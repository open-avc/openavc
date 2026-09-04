import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// What the Panel Display card is, asserted by name.
//
// This card is gated on `panel_dim_available`, which the server answers false
// on every deployment that is not the all-in-one appliance -- so on a dev box
// it is never drawn, and until this file nothing rendered it. The e2e
// affordance inventory did not either, and passed clean while a button was
// deleted from it. Every bug found in this card on 2026-09-04 was found by
// hand, on hardware, by Aaron.
//
// So this names the controls rather than counting them: the inventory can tell
// you a button went missing, and this tells you WHICH one and what it was for.
// Two of the assertions pin a decision rather than a control -- the brightness
// floor, and the "Clear" button that is deliberately gone -- because both were
// reached by stranding a real panel and neither is recoverable by reading the
// component.

const displaySettings = {
  idle_dim_enabled: true,
  idle_dim_timeout_seconds: 300,
  idle_dim_level_percent: 20,
  idle_dim_wake_passes_touch: false,
  idle_dim_hold_state_key: "",
  brightness_percent: null as number | null,
};

const projectState = {
  dirty: false,
  project: {
    openavc_version: "0.13.0",
    project: { id: "p1", name: "t" },
    settings: {
      display: displaySettings,
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

// Flipped per test: this ONE boolean is the whole difference between a screen
// that has the card and one that does not.
const capability = { panel_dim_available: true };

vi.mock("../api/restClient", () => ({
  getSystemConfig: vi.fn(async () => structuredClone(CONFIG)),
  getSystemVersion: vi.fn(async () => ({
    version: "0", channel: "stable", platform: "linux",
    kiosk_available: false, panel_dim_available: capability.panel_dim_available,
  })),
  getSshStatus: vi.fn(async () => null),
  getNetworkAdapters: vi.fn(async () => ({ adapters: [] })),
  getTlsStatus: vi.fn(async () => null),
  updateSystemConfig: vi.fn(async () => ({ status: "ok", updated_sections: [] })),
}));

vi.mock("../store/toastStore", () => ({ showError: vi.fn(), showSuccess: vi.fn() }));
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
// Left real would pull the whole state store in; the card only has to show it.
vi.mock("../components/shared/VariableKeyPicker", () => ({
  VariableKeyPicker: () => <div data-testid="variable-key-picker" />,
}));

import { SystemSettingsView } from "./SystemSettingsView";

const heading = () => screen.queryByText("Panel Display");
const slider = () =>
  document.querySelector('input[type="range"]') as HTMLInputElement | null;

async function renderSettings() {
  render(<SystemSettingsView />);
  await waitFor(() => expect(screen.getAllByRole("button").length).toBeGreaterThan(0));
}

describe("Panel Display card", () => {
  beforeEach(() => {
    capability.panel_dim_available = true;
    Object.assign(displaySettings, {
      idle_dim_enabled: true,
      idle_dim_timeout_seconds: 300,
      idle_dim_level_percent: 20,
      idle_dim_wake_passes_touch: false,
      idle_dim_hold_state_key: "",
      brightness_percent: null,
    });
    vi.clearAllMocks();
  });

  it("is drawn when the instance drives a screen it can dim", async () => {
    await renderSettings();
    await waitFor(() => expect(heading()).toBeTruthy());
  });

  it("is absent everywhere else, which is why nothing had ever rendered it", async () => {
    capability.panel_dim_available = false;
    await renderSettings();
    // Give the same window the positive case gets, so this cannot pass by
    // reading the screen before the capability call has landed.
    await waitFor(() => expect(screen.queryByText("Devices")).toBeTruthy());
    expect(heading()).toBeNull();
  });

  it("has every control the dim policy is made of", async () => {
    await renderSettings();
    await waitFor(() => expect(heading()).toBeTruthy());
    for (const label of [
      "Brightness",
      "Dim the panel when idle",
      "Dim after",
      "Dim to",
      "Stay bright while",
      "Waking touch also presses the button",
    ]) {
      expect(screen.queryByText(label), `${label} is gone from the card`).toBeTruthy();
    }
    expect(slider()).toBeTruthy();
    expect(screen.queryByTestId("variable-key-picker")).toBeTruthy();
  });

  it("collapses the timer controls when the dim is switched off", async () => {
    displaySettings.idle_dim_enabled = false;
    await renderSettings();
    await waitFor(() => expect(heading()).toBeTruthy());
    // The brightness slider is NOT part of the dim policy and stays.
    expect(screen.queryByText("Brightness")).toBeTruthy();
    expect(screen.queryByText("Dim after")).toBeNull();
    expect(screen.queryByText("Dim to")).toBeNull();
  });

  it("cannot set a brightness too low to read the panel by", async () => {
    // Enforced on the server too, because the value can arrive from a cloud
    // template. The slider floor is what stops somebody reaching it here: at
    // 1% the panel is dark, and the control that would undo it is on the panel.
    await renderSettings();
    await waitFor(() => expect(slider()).toBeTruthy());
    expect(Number(slider()!.min)).toBe(10);
  });

  it("offers no way to stop managing the brightness", async () => {
    // There was a "Clear" button here. It meant "stop managing" and restored
    // nothing, so pressing it at 1% left the panel dark with the only control
    // that would undo it reading "not set". Removed rather than repaired --
    // the local-tuning case it existed for is covered by the write being
    // edge-triggered. This is what stops it coming back as a kindness.
    await renderSettings();
    await waitFor(() => expect(heading()).toBeTruthy());
    const clear = screen.getAllByRole("button")
      .filter((b) => /^\s*clear\s*$/i.test(b.textContent ?? ""));
    expect(clear, "the brightness Clear button is back").toHaveLength(0);
  });
});
