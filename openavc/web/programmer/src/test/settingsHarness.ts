// The invented system the Settings screen's pins render against: what the
// server answers, the project it has open, and the calls the screen makes.
// Each test file mocks the modules itself (vi.mock is hoisted per file) and
// points the factories here, so every file draws the same screen.
import { vi, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";

export const RUNNING_ANY = {
  mode: "any", adapter: {}, adapter_name: "", label: "Any network", addresses: ["0.0.0.0"],
  network_addresses: [], local_only: false, held_by: null, problem: null,
  restart_needed: false, safety_net: null,
};

export function baseConfig(): Record<string, any> {
  return {
    network: {
      http_port: 8080, accept_connections_from: "any", accept_connections_adapter: {},
      bind_address: "0.0.0.0", reach_devices_through: {}, control_interface: "", port80_redirect: false,
    },
    auth: { programmer_username: "admin", programmer_password: "***", api_key: "" },
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

export const CERT = {
  subject: "CN=OpenAVC Server",
  issuer: "CN=OpenAVC Root CA",
  expires_at: "2027-06-01T00:00:00Z",
  days_until_expiry: 200,
  fingerprint: "AA:BB:CC:DD",
  sans: ["192.168.1.40", "localhost"],
  warnings: [] as string[],
};

export function cloudCert(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    enabled: false, paired: true, available: true, active: false,
    hostname_suffix: "abc123.certs.example.net", expires_at: null, renews_at: null,
    phase: "idle", last_error: "", last_error_detail: "", last_attempt_at: "", retry_pending: false,
    ...over,
  };
}

export const served: {
  config: Record<string, any>;
  version: Record<string, unknown>;
  tls: Record<string, any> | null;
  ssh: Record<string, unknown> | null;
  adapters: Array<{ name: string; ip: string }>;
} = {
  config: baseConfig(),
  version: { version: "0", channel: "stable", platform: "linux", kiosk_available: false, panel_dim_available: false, update_guidance: "" },
  tls: null,
  ssh: null,
  adapters: [],
};

const answers = {
  getSystemConfig: async () => structuredClone(served.config),
  getSystemVersion: async () => structuredClone(served.version),
  getSshStatus: async () => (served.ssh ? structuredClone(served.ssh) : null),
  getNetworkAdapters: async () => ({ adapters: served.adapters, interfaces: [] }),
  getTlsStatus: async () => (served.tls ? structuredClone(served.tls) : null),
  previewListen: async () => null,
  updateSystemConfig: async (_patch?: unknown) => ({ status: "ok", updated_sections: [] }),
  restartSystem: async () => ({ status: "restarting", mode: "graceful", delay_seconds: 2 }),
  rebootSystem: async () => ({ status: "rebooting" }),
  setSsh: async (enabled: boolean) => ({ success: true, enabled }),
  enableCloudCert: async () => ({ status: "ok", enabled: true, started: true }),
  disableCloudCert: async () => ({ status: "ok", enabled: false }),
  downloadCertificate: async () => new Blob(["CA"]),
  uploadTlsCert: async (_cert: File, _key: File) => ({
    cert_path: "/data/tls/cert.pem", key_path: "/data/tls/key.pem",
    fingerprint: "11:22:33", subject: "CN=room.example", issuer: "CN=Example CA",
    expires_at: "2027-01-15T00:00:00Z", days_until_expiry: 20,
    sans: ["room.example"], warnings: [] as string[],
  }),
};

/** Every call the screen makes to the server, answered from `served`. */
export const api = Object.fromEntries(
  Object.entries(answers).map(([name, answer]) => [name, vi.fn(answer)]),
) as unknown as { [K in keyof typeof answers]: ReturnType<typeof vi.fn<(typeof answers)[K]>> };

export const toast = { showError: vi.fn(), showSuccess: vi.fn() };
export const clipboard = { copyToClipboard: vi.fn(async (_text: string) => true) };

export const projectState = {
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
  } as Record<string, any> | null,
  update: vi.fn(),
  save: vi.fn(async () => {}),
};

export const projectStoreModule = {
  useProjectStore: Object.assign(
    (selector: (s: typeof projectState) => unknown) => selector(projectState),
    { getState: () => projectState },
  ),
};

/** Put every answer back the way a fresh file starts. */
export function resetHarness(): void {
  vi.clearAllMocks();
  // A test that changed an answer (mockRejectedValue) changes it back here.
  for (const [name, answer] of Object.entries(answers)) {
    (api as Record<string, ReturnType<typeof vi.fn>>)[name].mockReset().mockImplementation(answer);
  }
  clipboard.copyToClipboard.mockReset().mockImplementation(async () => true);
  served.config = baseConfig();
  served.version = { version: "0", channel: "stable", platform: "linux", kiosk_available: false, panel_dim_available: false, update_guidance: "" };
  served.tls = null;
  served.ssh = null;
  served.adapters = [];
  projectState.dirty = false;
  projectState.project = {
    openavc_version: "0.13.0",
    project: { id: "p1", name: "t" },
    settings: {
      display: {
        idle_dim_enabled: true, idle_dim_timeout_seconds: 300, idle_dim_level_percent: 20,
        idle_dim_wake_passes_touch: false, idle_dim_hold_state_key: "",
        brightness_percent: null,
      },
      devices: { reconnect_interval_seconds: null },
    },
  };
}

/** Render the page and wait for it to finish loading its config. */
export async function renderLoaded(view: ReactElement) {
  const result = render(view);
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  await waitFor(() => expect(api.getTlsStatus).toHaveBeenCalled());
  return result;
}

/** What a File holds, as text (jsdom's File has no text()). */
export function fileText(file: File): Promise<string> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(file);
  });
}

export const saveButton = () =>
  screen.getAllByRole("button").find((b) => /^(save|saving\.\.\.)$/i.test((b.textContent ?? "").trim())) as HTMLButtonElement;
