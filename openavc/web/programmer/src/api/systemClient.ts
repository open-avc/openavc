import type { LogEntryResponse } from "./types";
import type { PanelAccessMode } from "./panelDevicesClient";
import { BASE, request } from "./base";
import { ApiError } from "./errors";

// --- Cloud Connection ---

export interface CloudStatus {
  enabled: boolean;
  connected: boolean;
  system_id: string;
  endpoint: string;
  session_id?: string;
  last_heartbeat?: string;
  uptime?: number;
  /** Set when the agent stopped trying: "auth_failed" | "version_mismatch". */
  stop_reason?: string;
  /** Plain-language cause and fix for stop_reason, safe to show as-is. */
  stop_detail?: string;
}

export interface CloudPairResult {
  status: string;
  system_id: string;
  endpoint: string;
  /** Host (and port) of the cloud that answered, so the success line can name it. */
  cloud?: string;
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

export async function cloudUnpair(): Promise<{ status: string }> {
  return request<{ status: string }>("/cloud/unpair", { method: "POST" });
}

// --- Logs ---

export async function getRecentLogs(
  count = 100,
  category = ""
): Promise<LogEntryResponse[]> {
  const params = new URLSearchParams({ count: String(count) });
  if (category) params.set("category", category);
  return (await request<{ logs: LogEntryResponse[] }>(`/logs/recent?${params}`)).logs;
}

// --- System Updates ---

export interface UpdateStatus {
  update_owner?: "firmware";
  firmware_version?: string;
  update_guidance?: string;
  update_check_available?: boolean;
  host_configuration_valid?: boolean;
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
  /** Version of an update the pre-start helper could not apply ("" if none). */
  deferred_version?: string;
  /** How many times that update has been attempted (0 if none). */
  deferred_attempts?: number;
  /** Why the last attempt stopped ("" if none). */
  deferred_reason?: string;
  /** True once the helper has stopped retrying that update. */
  deferred_final?: boolean;
  rollback_available: boolean;
  rollback_version: string;
}

export interface UpdateCheckResult {
  update_owner?: "firmware";
  firmware_version?: string;
  update_guidance?: string;
  update_check_available?: boolean;
  host_configuration_valid?: boolean;
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
  /** How to update this deployment without internet access. Set only when
   *  the check itself failed, which is all an isolated system ever sees. */
  offline_instructions?: string;
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
  return (
    await request<{ history: UpdateHistoryEntry[] }>("/system/updates/history")
  ).history;
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
  /**
   * Who still shows this asset, in the words the delete refuses in --
   * "element 'hero' on page 'main'", "theme 'midnight'". Present only for the
   * active project, because nothing else is loaded to be asked; absent is "no
   * answer", which is not the same claim as an empty list.
   */
  used_by?: string[];
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
    throw new ApiError(res.status, await res.text());
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
  return (await request<{ themes: ThemeSummary[] }>("/themes")).themes;
}

export async function getTheme(themeId: string): Promise<ThemeDefinition> {
  return request<ThemeDefinition>(`/themes/${themeId}`);
}

export async function createTheme(data: ThemeDefinition): Promise<{ status: string; theme_id: string }> {
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
): Promise<{ status: string; theme_id: string; name: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const url = `${BASE}/themes/import${overwrite ? "?overwrite=true" : ""}`;
  const res = await fetch(url, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    if (res.status === 409) {
      // The collision 409 carries `code`/`theme_id`/`name` beside the readable
      // `detail` string, so the overwrite prompt has the ids it needs.
      const body = await res.json().catch(() => null);
      if (body?.code === "theme_exists") {
        throw new ThemeExistsError(body.theme_id, body.name || body.theme_id);
      }
      throw new Error(typeof body?.detail === "string" ? body.detail : "Import failed");
    }
    throw new ApiError(res.status, await res.text());
  }
  return res.json();
}

// --- System Config ---

/** An adapter as a choice names it: by identity, never by address. A literal
 *  `address` appears only when the environment set one. */
export interface AdapterIdentity {
  name?: string;
  mac?: string;
  address?: string;
}

export type ListenMode = "any" | "adapter" | "this_computer";

/** Why the network is not being listened on: the one sentence every surface
 *  shows ("Not accepting connections from the network: the Ethernet adapter
 *  isn't connected."). */
export interface ListenProblem {
  code: "adapter_not_connected" | "adapter_no_address" | "address_not_found";
  message: string;
}

/** Who can connect as this server is running it (`/api/status.listen`, and
 *  `_listen` on the config). */
export interface ListenRunning {
  mode: ListenMode;
  adapter: AdapterIdentity;
  adapter_name: string;
  /** "Any network", "Ethernet only", "This computer only". */
  label: string;
  /** Every address a web listener bound. */
  addresses: string[];
  /** The adapter's own addresses listened on (one adapter only). */
  network_addresses: string[];
  /** Nothing on the network can connect. */
  local_only: boolean;
  /** The environment variable holding the choice, or null. */
  held_by: string | null;
  problem: ListenProblem | null;
}

/** While a change that cut off the browser making it waits to be confirmed. */
export interface ListenSafetyNet {
  previous: string;
  choice: string;
  /** The server is listening on the new choice, so the clock is running. */
  running: boolean;
  deadline: string | null;
}

export interface ListenBlock extends ListenRunning {
  /** The saved choice differs from the running one. */
  restart_needed: boolean;
  /** The ports this server is listening on now; a saved change moves them at
   *  the restart. `https_port` is null while HTTPS is off. */
  http_port?: number;
  https_port?: number | null;
  safety_net: ListenSafetyNet | null;
}

/** Reach devices through, resolved now. */
export interface ReachDevicesBlock {
  mode: "every" | "adapter";
  adapter: AdapterIdentity;
  adapter_name: string;
  /** The address device traffic binds to ("" for every adapter). */
  address: string;
  connected: boolean;
  label: string;
}

export interface SystemConfig {
  network: {
    http_port: number;
    /** Settings > Network > Accept connections from. */
    accept_connections_from: ListenMode;
    /** The adapter for "adapter": `{name, mac}`. */
    accept_connections_adapter: AdapterIdentity;
    /** Written for a rollback only; the server refuses a change to it. */
    bind_address: string;
    /** Settings > Network > Reach devices through: `{}` for every connected
     *  adapter, or `{name, mac}`. Null until the server first starts. */
    reach_devices_through: AdapterIdentity | null;
    /** Written for a rollback only; the server refuses a change to it. */
    control_interface: string;
    port80_redirect: boolean;
  };
  auth: { programmer_username: string; programmer_password: string; api_key: string };
  isc: { enabled: boolean };
  logging: { level: string; file_enabled: boolean; max_size_mb: number; max_files: number };
  updates: { check_enabled: boolean; channel: string; auto_check_interval_hours: number; notify_only: boolean };
  cloud: { enabled: boolean; endpoint: string; system_key: string; system_id: string };
  kiosk: { enabled: boolean; target_url: string; cursor_visible: boolean };
  tls: { enabled: boolean; port: number; auto_generate: boolean; cert_file: string; key_file: string; redirect_http: boolean; cloud_cert: boolean };
  /** Who may open the panel. Applies as soon as it is saved. `upgrade_notice`
   *  is true on a system that was updated with its panels connected and is
   *  still open; the Programmer's notice clears it. */
  panels: { access: PanelAccessMode; upgrade_notice: boolean };
  /** The mDNS advertisement the Panel app's list is built from. Applies as
   *  soon as it is saved. */
  discovery: { advertise: boolean };
  /** Not a system.json section: how this server was installed, and which
   *  fields its environment holds (`"network.bind_address": "OPENAVC_BIND"`).
   *  The server runs with a held value whatever system.json says and refuses
   *  a change to one. */
  _environment?: { deployment_type: string; overrides: Record<string, string>; configuration_owner?: "firmware" };
  /** Not a system.json section: who can connect as this server is running it. */
  _listen?: ListenBlock;
  /** Not a system.json section: Reach devices through, resolved now. */
  _reach_devices?: ReachDevicesBlock | null;
}

export async function getSystemVersion(): Promise<{
  update_owner?: "firmware";
  firmware_version?: string;
  update_guidance?: string;
  update_check_available?: boolean;
  host_configuration_valid?: boolean;
  version: string;
  channel: string;
  platform: string;
  kiosk_available: boolean;
  panel_dim_available: boolean;
}> {
  return request("/system/version");
}

export async function getSystemConfig(): Promise<SystemConfig> {
  return request("/system/config");
}

export async function updateSystemConfig(
  data: Partial<SystemConfig>
): Promise<{ status: string; updated_sections: string[]; safety_net?: ListenSafetyNet | null }> {
  return request("/system/config", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

/** What the server says saving a choice of who can connect would do. */
export interface ListenPreview {
  mode: ListenMode;
  adapter_name: string;
  label: string;
  /** The addresses a panel could open under the choice, best first. */
  panel_addresses: string[];
  /** This computer's address the asking browser reached. */
  connected_through: string;
  /** That address still answers under the choice. */
  serves_this_address: boolean;
  /** The person can keep working without walking anywhere (the address
   *  answers, or they are on this computer or the cloud tunnel). */
  keeps_this_connection: boolean;
  /** Saving it arms the safety net (the opposite of the above). */
  safety_net: boolean;
  problem: ListenProblem | null;
  /** What stops working: approved panels, Inter-System. */
  consequences: { code: string; count: number; message: string }[];
  held_by: string | null;
  /** Remote access through OpenAVC Cloud is set up on this system, so it
   *  keeps working whatever the choice (from the cloud agent, never
   *  system.json's cloud section). */
  cloud_remote_access: boolean;
}

export async function previewListen(choice: {
  accept_connections_from: ListenMode;
  accept_connections_adapter?: AdapterIdentity;
}): Promise<ListenPreview> {
  return request("/system/listen/preview", {
    method: "POST",
    body: JSON.stringify(choice),
  });
}

/** Called once on every Programmer load: keeps a change the safety net was
 *  watching, and hands over the "Went back" sentence once. */
export async function confirmListen(): Promise<{ kept: string | null; went_back: string | null }> {
  return request("/system/listen/confirm", { method: "POST" });
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
): Promise<{ success: boolean; enabled: boolean; pending?: boolean; error?: string }> {
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
  status: string;
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
export async function disableCloudCert(): Promise<{ status: string; enabled: boolean }> {
  return request<{ status: string; enabled: boolean }>("/system/tls/cloud-cert/disable", {
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
  /** False when the adapter has no link (cable out, radio off): a scan skips it. */
  link: boolean;
}

/** One physical adapter, connected or not: what the adapter pickers list. */
export interface NetworkInterface {
  name: string;
  mac: string;
  addresses: { ip: string; subnet: string }[];
  /** False when the adapter is not connected (cable out, radio off). */
  link: boolean;
  has_address: boolean;
  /** The adapter's 169.254 addresses, apart from `addresses`. The pickers
   *  list and show an adapter by `addresses` only; the server binds one of
   *  these to reach a device at a 169.254 address. */
  link_local?: { ip: string; subnet: string }[];
}

/** `adapters` is one entry per IPv4 address (Discovery); `interfaces` is one
 *  entry per adapter, including one with no address (Settings' pickers). */
export async function getNetworkAdapters(): Promise<{ adapters: NetworkAdapter[]; interfaces?: NetworkInterface[] }> {
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
// from the adapter list above, which only chooses which adapter to use.
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
 *  only validates (`success: true, applied: false`, warnings populated); with
 *  `confirmed: true` it applies (`applied: true`) — live backends roll back
 *  automatically if activation fails, reboot backends save and restart the
 *  device (`reboot: true`). `success: false` is a refused change, not an
 *  HTTP error. */
export interface HostIpv4Result {
  success?: boolean;
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
): Promise<{ success: boolean; error?: string }> {
  return request("/system/network/wifi/connect", {
    method: "POST",
    body: JSON.stringify({ ssid, psk }),
  });
}

export async function setHostWifiRadio(
  enabled: boolean
): Promise<{ success: boolean; enabled?: boolean; error?: string }> {
  return request("/system/network/wifi/radio", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function setHostHostname(
  hostname: string
): Promise<{ success: boolean; error?: string }> {
  return request("/system/network/hostname", {
    method: "POST",
    body: JSON.stringify({ hostname }),
  });
}
