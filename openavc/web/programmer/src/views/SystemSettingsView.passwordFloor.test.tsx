import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The Programmer password field meets the same length floor as first-run
// setup. The server refuses a short one with the same sentence; the field says
// so before the request, and Save waits until it is fixed.

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

import { PASSWORD_TOO_SHORT, passwordTooShortToSave } from "../api/auth";
import { ApiError } from "../api/errors";
import { showError, showSuccess } from "../store/toastStore";

const passwordField = () => screen.getByPlaceholderText("Set (hidden)");
const saveButton = () => screen.getByRole("button", { name: "Save" });

async function renderSettings() {
  render(<SystemSettingsView />);
  await waitFor(() => expect(screen.queryByText("Programmer login")).toBeTruthy());
}

describe("passwordTooShortToSave", () => {
  it("measures what the server stores, and lets an empty field clear", () => {
    expect(passwordTooShortToSave("a")).toBe(true);
    expect(passwordTooShortToSave("abc1234")).toBe(true);
    expect(passwordTooShortToSave("  abc1234  ")).toBe(true);
    expect(passwordTooShortToSave("abc12345")).toBe(false);
    expect(passwordTooShortToSave("")).toBe(false);
    expect(passwordTooShortToSave("   ")).toBe(false);
  });
});

describe("Programmer password length floor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    served.config = CONFIG;
  });

  it("says nothing about a stored password nobody touched", async () => {
    await renderSettings();
    expect(screen.queryByText(PASSWORD_TOO_SHORT)).toBeNull();
  });

  it("refuses a short password before it is sent", async () => {
    await renderSettings();
    await userEvent.type(passwordField(), "abc1234");
    expect(screen.getByText(PASSWORD_TOO_SHORT)).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    await userEvent.click(saveButton());
    expect(api.updateSystemConfig).not.toHaveBeenCalled();
  });

  it("saves a password of eight characters", async () => {
    await renderSettings();
    await userEvent.type(passwordField(), "abc12345");
    expect(screen.queryByText(PASSWORD_TOO_SHORT)).toBeNull();
    expect(saveButton()).toBeEnabled();
    await userEvent.click(saveButton());
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.updateSystemConfig).mock.calls[0][0]).toMatchObject({
      auth: { programmer_password: "abc12345" },
    });
  });

  it("lets an emptied field through, because empty removes the password", async () => {
    await renderSettings();
    await userEvent.type(passwordField(), "a");
    expect(screen.getByText(PASSWORD_TOO_SHORT)).toBeInTheDocument();
    await userEvent.clear(screen.getByPlaceholderText("No password set"));
    expect(screen.queryByText(PASSWORD_TOO_SHORT)).toBeNull();
  });
});

describe("Saving a new password", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    served.config = CONFIG;
  });

  it("does not call a save that worked a failure when the session ends with it", async () => {
    await renderSettings();
    // A new password ends every session, this tab's included, so the
    // read-back after the save answers 401.
    vi.mocked(api.getSystemConfig).mockRejectedValueOnce(
      new ApiError(401, '{"detail":"Authentication required"}'),
    );
    await userEvent.type(passwordField(), "abc12345");
    await userEvent.click(saveButton());
    await waitFor(() => expect(api.getSystemConfig).toHaveBeenCalledTimes(2));
    expect(showSuccess).toHaveBeenCalledWith("Settings saved.");
    expect(showError).not.toHaveBeenCalled();
  });

  it("still says so when the save itself is refused", async () => {
    await renderSettings();
    vi.mocked(api.updateSystemConfig).mockRejectedValueOnce(
      new ApiError(400, JSON.stringify({ detail: PASSWORD_TOO_SHORT })),
    );
    await userEvent.type(passwordField(), "abc12345");
    await userEvent.click(saveButton());
    await waitFor(() => expect(showError).toHaveBeenCalledWith(`Failed to save: ${PASSWORD_TOO_SHORT}`));
    expect(showSuccess).not.toHaveBeenCalled();
  });

  it("says the save went through when only the refresh after it failed", async () => {
    await renderSettings();
    vi.mocked(api.getSystemConfig).mockRejectedValueOnce(
      new ApiError(500, '{"detail":"Server error"}'),
    );
    await userEvent.type(passwordField(), "abc12345");
    await userEvent.click(saveButton());
    await waitFor(() => expect(showError).toHaveBeenCalledTimes(1));
    expect(vi.mocked(showError).mock.calls[0][0]).toMatch(/^Settings saved, but this page could not refresh/);
  });
});
