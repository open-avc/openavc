import type { LogEntryResponse } from "./types";
import { BASE, request } from "./base";

// --- Cloud Connection ---

export interface CloudStatus {
  enabled: boolean;
  connected: boolean;
  system_id: string;
  endpoint: string;
  session_id?: string;
  last_heartbeat?: string;
  uptime?: number;
}

export interface CloudPairResult {
  success: boolean;
  system_id: string;
  endpoint: string;
  agent_started?: boolean;
  warning?: string;
}

export async function getCloudStatus(): Promise<CloudStatus> {
  return request<CloudStatus>("/cloud/status");
}

export async function cloudPair(
  token: string,
  cloudApiUrl: string = "https://cloud.openavc.com"
): Promise<CloudPairResult> {
  return request<CloudPairResult>("/cloud/pair", {
    method: "POST",
    body: JSON.stringify({ token, cloud_api_url: cloudApiUrl }),
  });
}

export async function cloudUnpair(): Promise<{ success: boolean }> {
  return request<{ success: boolean }>("/cloud/unpair", { method: "POST" });
}

// --- Logs ---

export async function getRecentLogs(
  count = 100,
  category = ""
): Promise<LogEntryResponse[]> {
  const params = new URLSearchParams({ count: String(count) });
  if (category) params.set("category", category);
  return request(`/logs/recent?${params}`);
}

// --- System Updates ---

export interface UpdateStatus {
  current_version: string;
  deployment_type: string;
  can_self_update: boolean;
  update_available: string;
  update_channel: string;
  update_status: string;
  update_progress: number;
  update_error: string;
  /** Version of a cloud-staged update awaiting manual install ("" if none). */
  staged_version?: string;
  rollback_available: boolean;
  rollback_version: string;
}

export interface UpdateCheckResult {
  update_available: boolean;
  current_version: string;
  available_version?: string;
  channel: string;
  prerelease?: boolean;
  changelog?: string;
  published_at?: string;
  can_self_update?: boolean;
  deployment_type?: string;
  instructions?: string;
  error?: string;
}

export interface UpdateHistoryEntry {
  from_version: string;
  to_version: string;
  status: string;
  error?: string;
  /** True when this entry records a rollback rather than an update. Older
   *  entries instead carry the literal string "rollback" in to_version. */
  rollback?: boolean;
  timestamp: string;
}

export async function checkForUpdates(): Promise<UpdateCheckResult> {
  return request("/system/updates/check");
}

export async function applyUpdate(): Promise<{ success: boolean; message?: string; error?: string }> {
  return request("/system/updates/apply", { method: "POST" });
}

export async function rollbackUpdate(): Promise<{ success: boolean; message?: string; error?: string }> {
  return request("/system/updates/rollback", { method: "POST" });
}

export async function getUpdateStatus(): Promise<UpdateStatus> {
  return request("/system/updates/status");
}

export async function getUpdateHistory(): Promise<UpdateHistoryEntry[]> {
  return request("/system/updates/history");
}

// --- Assets ---

export type AssetType = "image" | "audio";

export interface AssetInfo {
  name: string;
  size: number;
  /** High-level category derived from extension */
  type: AssetType;
  /** File extension without the leading dot (e.g. "png", "mp3") */
  extension: string;
}

export async function listAssets(): Promise<{ assets: AssetInfo[]; total_size: number }> {
  return request("/projects/default/assets");
}

export async function uploadAsset(file: File): Promise<{ name: string; reference: string; size: number; type: AssetType }> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${BASE}/projects/default/assets`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Upload failed: ${body}`);
  }
  return res.json();
}

export async function deleteAsset(filename: string): Promise<{ status: string }> {
  return request(`/projects/default/assets/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
}

export function getAssetUrl(filename: string): string {
  return `${BASE}/projects/default/assets/${encodeURIComponent(filename)}`;
}

// --- Themes ---

export interface ThemeSummary {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  preview_colors: string[];
  /** Full theme variables — used by the Theme Studio picker to render rich
   *  per-card previews (font + button colors + surface) without per-card fetches. */
  variables: Record<string, unknown>;
  source: string;
}

export interface ThemeDefinition {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  preview_colors: string[];
  variables: Record<string, unknown>;
  element_defaults: Record<string, Record<string, unknown>>;
  page_defaults?: Record<string, unknown>;
  _source?: string;
}

export async function listThemes(): Promise<ThemeSummary[]> {
  return request<ThemeSummary[]>("/themes");
}

export async function getTheme(themeId: string): Promise<ThemeDefinition> {
  return request<ThemeDefinition>(`/themes/${themeId}`);
}

export async function createTheme(data: ThemeDefinition): Promise<{ status: string; id: string }> {
  return request("/themes", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateTheme(themeId: string, data: ThemeDefinition): Promise<{ status: string }> {
  return request(`/themes/${themeId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteTheme(themeId: string): Promise<{ status: string }> {
  return request(`/themes/${themeId}`, { method: "DELETE" });
}

/** Thrown when importing a theme whose id collides with an existing custom
 *  theme. The import dialog catches this to offer Overwrite / Keep both. */
export class ThemeExistsError extends Error {
  constructor(public themeId: string, public themeName: string) {
    super(`A custom theme named "${themeName}" already exists`);
    this.name = "ThemeExistsError";
  }
}

export async function importTheme(
  file: File,
  overwrite = false,
): Promise<{ status: string; id: string; name: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const url = `${BASE}/themes/import${overwrite ? "?overwrite=true" : ""}`;
  const res = await fetch(url, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    if (res.status === 409) {
      const body = await res.json().catch(() => null);
      const detail = body?.detail;
      if (detail && typeof detail === "object" && detail.code === "theme_exists") {
        throw new ThemeExistsError(detail.id, detail.name || detail.id);
      }
      throw new Error(typeof detail === "string" ? detail : "Import failed");
    }
    const body = await res.text();
    throw new Error(`Import failed: ${body}`);
  }
  return res.json();
}

// --- System Config ---

export interface SystemConfig {
  network: { http_port: number; bind_address: string; control_interface: string; port80_redirect: boolean };
  auth: { programmer_username: string; programmer_password: string; api_key: string; panel_lock_code: string };
  isc: { enabled: boolean; discovery_enabled: boolean; auth_key: string };
  logging: { level: string; file_enabled: boolean; max_size_mb: number; max_files: number };
  updates: { check_enabled: boolean; channel: string; auto_check_interval_hours: number; auto_backup_before_update: boolean; notify_only: boolean };
  cloud: { enabled: boolean; endpoint: string; system_key: string; system_id: string };
  kiosk: { enabled: boolean; target_url: string; cursor_visible: boolean };
  tls: { enabled: boolean; port: number; auto_generate: boolean; cert_file: string; key_file: string; redirect_http: boolean; cloud_cert: boolean };
}

export async function getSystemVersion(): Promise<{
  version: string;
  channel: string;
  platform: string;
  kiosk_available: boolean;
}> {
  return request("/system/version");
}

export async function getSystemConfig(): Promise<SystemConfig> {
  return request("/system/config");
}

export async function updateSystemConfig(
  data: Partial<SystemConfig>
): Promise<{ success: boolean; updated_sections: string[] }> {
  return request("/system/config", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function rebootSystem(): Promise<{ status: string }> {
  return request("/system/reboot", { method: "POST" });
}

/** SSH availability + state. `supported` is true only on a Pi appliance (the
 *  privileged helper is installed); the toggle is hidden otherwise. `enabled`
 *  reflects whether sshd is running now (null if undeterminable). */
export interface SshStatus {
  supported: boolean;
  enabled: boolean | null;
}

export async function getSshStatus(): Promise<SshStatus> {
  return request("/system/ssh");
}

/** Enable/disable SSH on a Pi appliance. `pending` means the change was
 *  submitted but unconfirmed before the server's short wait elapsed. */
export async function setSsh(
  enabled: boolean
): Promise<{ ok: boolean; enabled: boolean; pending?: boolean; error?: string }> {
  return request("/system/ssh", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export interface RestartResult {
  status: string;
  mode: "graceful" | "hard";
  delay_seconds: number;
}

/** Trigger an OpenAVC process restart.
 *
 *  The server emits `system.restart_requested` and exits ~2s later (graceful)
 *  or immediately (hard). Service managers (NSSM / systemd / Docker) bring
 *  the process back; dev mode spawns a detached replacement. The browser
 *  should poll `/api/health` on the post-restart URL until it returns 2xx. */
export async function restartSystem(
  mode: "graceful" | "hard" = "graceful"
): Promise<RestartResult> {
  return request<RestartResult>("/system/restart", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

// --- HTTPS / TLS ---

export interface TlsCertInfo {
  subject: string;
  issuer: string;
  expires_at: string;
  days_until_expiry: number;
  fingerprint: string;
  sans: string[];
  warnings: string[];
}

/** State of the cloud-issued trusted certificate (browser-trusted, no
 *  warnings). Present even when TLS is off so the Settings callout can
 *  offer the enable flow before HTTPS exists. */
export interface CloudCertStatus {
  /** The user opted in (tls.cloud_cert flag). */
  enabled: boolean;
  /** A cloud pairing exists on this system. */
  paired: boolean;
  /** Connected to the cloud and the session offers trusted certificates. */
  available: boolean;
  /** A valid cloud certificate is being served (via SNI) right now. */
  active: boolean;
  /** "<label>.<zone>" — the wildcard base of the certified hostname. */
  hostname_suffix: string;
  expires_at: string | null;
  renews_at: string | null;
  phase: "idle" | "enrolling" | "issuing";
  /** Typed error code from the last failed issuance ("" when none). */
  last_error: string;
  last_error_detail: string;
  last_attempt_at: string;
  retry_pending: boolean;
}

export interface TlsStatus {
  enabled: boolean;
  port?: number;
  redirect_http?: boolean;
  mode?: "auto" | "provided";
  cert?: TlsCertInfo | null;
  cloud_cert?: CloudCertStatus;
  error?: string;
}

export async function getTlsStatus(): Promise<TlsStatus> {
  return request<TlsStatus>("/system/tls-status");
}

export interface CloudCertEnableResult {
  enabled: boolean;
  started: boolean;
  reason?: string;
  message?: string;
}

/** Enroll for a cloud-issued trusted certificate, or retry after a failed
 *  issuance (bypasses the daily retry backoff). Issuance is asynchronous —
 *  poll getTlsStatus() until cloud_cert.phase returns to "idle". */
export async function enableCloudCert(): Promise<CloudCertEnableResult> {
  return request<CloudCertEnableResult>("/system/tls/cloud-cert/enable", {
    method: "POST",
  });
}

/** Turn the trusted certificate off: stops serving it, deletes it, and
 *  notifies the cloud best-effort. Never blocked by cloud reachability. */
export async function disableCloudCert(): Promise<{ enabled: boolean }> {
  return request<{ enabled: boolean }>("/system/tls/cloud-cert/disable", {
    method: "POST",
  });
}

/** Fetch the auto-generated CA cert so it can be installed on panel devices.
 *  Returns the PEM bytes (caller can `URL.createObjectURL(blob)` and trigger a download).
 *  Throws on 404 (TLS off or no CA in provided mode). */
export async function downloadCertificate(): Promise<Blob> {
  const res = await fetch(`${BASE}/certificate`);
  if (!res.ok) {
    throw new Error(`No certificate available (HTTP ${res.status})`);
  }
  return res.blob();
}

export interface TlsUploadResult {
  cert_path: string;
  key_path: string;
  fingerprint: string;
  subject: string;
  issuer: string;
  expires_at: string;
  days_until_expiry: number;
  sans: string[];
  warnings: string[];
}

/** Upload a user-provided cert + matching private key as PEM files.
 *  On success the server writes them to data_dir/tls/user-{cert,key}.pem and
 *  returns the absolute paths plus parsed metadata. The caller is responsible
 *  for following this with a config patch that points tls.cert_file /
 *  tls.key_file at the returned paths.
 *  Throws Error with the server's user-friendly message on 400 (cert/key
 *  mismatch, passphrase-protected key, garbage input, ...). */
export async function uploadTlsCert(cert: File, key: File): Promise<TlsUploadResult> {
  const form = new FormData();
  form.append("cert", cert);
  form.append("key", key);
  const res = await fetch(`${BASE}/system/tls/upload-cert`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    let detail = `Upload failed (HTTP ${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* fall through with generic message */
    }
    throw new Error(detail);
  }
  return res.json();
}

// --- Network Adapters ---

export interface NetworkAdapter {
  name: string;
  ip: string;
  subnet: string;
  mac: string;
}

export async function getNetworkAdapters(): Promise<{ adapters: NetworkAdapter[] }> {
  return request("/network/adapters");
}

// --- Serial Ports ---
// Serial ports on the OpenAVC server host, for the device connection picker.
// USB-to-serial adapters present to the OS as a plain serial port; the adapter
// must be plugged into the machine running the server (not the browser).

export interface SerialPortInfo {
  /** OS device path (COM3 / /dev/ttyUSB0 / /dev/cu.usbserial-XXXX). */
  device: string;
  /** Human description from the OS (falls back to the device path). */
  description: string;
  manufacturer: string;
  vid: number | null;
  pid: number | null;
  /** USB serial number — the stable identity the connection binds to ("" if the adapter exposes none). */
  serial_number: string;
  hwid: string;
  /** True when the port looks like a USB device (has a USB VID). */
  usb: boolean;
  /** Friendly composed label for the picker. */
  label: string;
}

export async function getSerialPorts(): Promise<{ ports: SerialPortInfo[] }> {
  return request("/system/serial-ports");
}

// --- Host Network Configuration ---
// The machine's own network (IP, gateway, DNS, WiFi, hostname) — distinct
// from the adapter list above, which only selects the control interface.
// Available only on deployments where OpenAVC owns the OS (Pi appliance,
// Linux with NetworkManager); elsewhere the GET throws "API 404" and the
// settings card hides itself.

export interface HostNetworkConfig {
  method: string; // "auto" (DHCP) | "manual" (static)
  addresses: string[];
  gateway: string | null;
  dns: string[];
}

export interface HostNetworkInterface {
  device: string;
  type: "ethernet" | "wifi";
  state: string;
  connection: string | null;
  mac: string | null;
  ip4: { addresses: string[]; gateway: string | null; dns: string[] };
  config: HostNetworkConfig | null;
}

export interface HostNetworkStatus {
  backend: string;
  hostname: string | null;
  /** WiFi radio state when the backend has a WiFi device; null if unknown. */
  wifi_enabled?: boolean | null;
  capabilities: {
    ipv4: boolean;
    wifi: boolean;
    hostname: boolean;
    /** How an IPv4 change takes effect: applied immediately ("live",
     *  rolled back on failure) or by restarting the device ("reboot"). */
    ipv4_apply?: "live" | "reboot";
  };
  interfaces: HostNetworkInterface[];
}

/** Result of POST /system/network/ipv4. With `confirmed: false` the server
 *  only validates (`applied: false`, warnings populated); with `confirmed:
 *  true` it applies — live backends roll back automatically if activation
 *  fails, reboot backends save and restart the device (`reboot: true`). */
export interface HostIpv4Result {
  ok?: boolean;
  valid?: boolean;
  applied?: boolean;
  rolled_back?: boolean;
  reboot?: boolean;
  warnings?: string[];
  error?: string;
}

export interface WifiNetwork {
  ssid: string;
  signal: number;
  secured: boolean;
  in_use: boolean;
}

export async function getHostNetwork(): Promise<HostNetworkStatus> {
  return request("/system/network");
}

export async function setHostIpv4(body: {
  connection: string;
  method: "auto" | "manual";
  address?: string | null;
  gateway?: string | null;
  dns?: string[];
  confirmed: boolean;
}): Promise<HostIpv4Result> {
  return request("/system/network/ipv4", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function scanHostWifi(): Promise<{ networks: WifiNetwork[] }> {
  return request("/system/network/wifi/scan", { method: "POST" });
}

export async function connectHostWifi(
  ssid: string,
  psk: string | null
): Promise<{ ok: boolean; error?: string }> {
  return request("/system/network/wifi/connect", {
    method: "POST",
    body: JSON.stringify({ ssid, psk }),
  });
}

export async function setHostWifiRadio(
  enabled: boolean
): Promise<{ ok: boolean; enabled?: boolean; error?: string }> {
  return request("/system/network/wifi/radio", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function setHostHostname(
  hostname: string
): Promise<{ ok: boolean; error?: string }> {
  return request("/system/network/hostname", {
    method: "POST",
    body: JSON.stringify({ hostname }),
  });
}
