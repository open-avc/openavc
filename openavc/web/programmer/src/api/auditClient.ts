import { request } from "./base";
import type { DiscoveredDevice, DiscoveryEvidence, IdentificationMatch } from "./discoveryClient";
import { downloadFromApi } from "./downloadFile";

// --- Device Audit (/api/audit) ---

/** A project device that connects to the audited address. */
export interface AuditConflictDevice {
  device_id: string;
  device_name: string;
  driver: string;
  transport: string;
  host: string;
  port: number | string | null;
  bridge: string;
  connected: boolean;
  paused: boolean;
}

export interface AuditConflicts {
  address: string;
  /** "" when the address resolves to nothing. */
  ip: string;
  resolved: boolean;
  devices: AuditConflictDevice[];
}

export type AuditActivityKey =
  | "address"
  | "ports"
  | "greetings"
  | "web"
  | "announcements"
  | "snmp"
  | "probes";

export type AuditActivityStatus = "pending" | "running" | "done" | "skipped" | "failed";

export interface AuditActivity {
  key: AuditActivityKey;
  status: AuditActivityStatus;
  message: string;
  started_at: number | null;
  finished_at: number | null;
}

export type AuditVerdictState = "identified" | "possible" | "unknown" | "nothing";

export interface AuditSignalHits {
  source: string;
  tier: string;
  strong: boolean;
  drivers: string[];
  evidence: DiscoveryEvidence;
}

export interface AuditSignalCheck {
  kind: string;
  declared: string;
  strong: boolean;
  cross_vendor: boolean;
  status: "matched" | "not_matched" | "not_observed";
  observed: string[];
  detail: string;
}

export interface AuditDriverName {
  name: string;
  manufacturer: string;
  installed: boolean;
}

export interface AuditCatalog {
  source?: string;
  fetched_at?: number;
  sha256?: string;
  driver_count?: number;
  error?: string;
  reachable?: boolean;
  used?: "fresh" | "cached" | "none";
}

export interface AuditVerdict {
  state: AuditVerdictState;
  sentence: string;
  identification: IdentificationMatch;
  explanation: {
    signals: AuditSignalHits[];
    drivers: Record<string, string[]>;
    strong_drivers: string[];
  };
  drivers: Record<string, AuditDriverName>;
  checks: Record<string, AuditSignalCheck[]>;
  catalog: AuditCatalog;
}

export interface AuditLimit {
  id: string;
  text: string;
}

export interface AuditCheckResult {
  verdict: AuditVerdict;
  evidence: DiscoveryEvidence[];
  device: DiscoveredDevice | null;
  limits: AuditLimit[];
  open_ports: number[];
}

export type AuditCheckStatus = "idle" | "running" | "done" | "failed" | "cancelled";

export interface AuditCheckState {
  status: AuditCheckStatus;
  error: string;
  activities: AuditActivity[];
  result: AuditCheckResult | null;
}

export type AuditSessionStatus = "active" | "finished" | "cancelled" | "expired" | "shutdown";

export interface AuditTester {
  name?: string;
  company?: string;
  email?: string;
  notes?: string;
  leave_out_serial?: boolean;
}

/** One file of the driver that ran, against the catalog's published hash. */
export interface AuditDriverFile {
  name: string;
  sha256: string | null;
  catalog_sha256: string | null;
  /** null when the catalog publishes no hash for this file. */
  matches_catalog: boolean | null;
}

/** Exactly which driver ran: what the report says about it. */
export interface AuditDriverIdentity {
  id: string;
  name: string;
  manufacturer: string;
  version: string;
  format: "avcdriver" | "python";
  transport: string;
  source: "catalog" | "imported" | "built_in";
  files: AuditDriverFile[];
  catalog_files_missing: string[];
  /** True when a file differs from the one the catalog publishes. */
  modified: boolean;
  catalog: { listed: boolean; version: string; verified: boolean | null; checked: boolean };
}

export type AuditVerdictAgreement = "agrees" | "candidate" | "differs" | "no_verdict";

/** The answer on "Which driver?". */
export interface AuditDriverChoice {
  driver_id: string;
  manufacturer: string;
  model: string;
  firmware: string;
  identity: AuditDriverIdentity;
  /** listed is null when no model was entered. */
  model_listing: { listed: boolean | null; confidence: string | null };
  verdict_agreement: AuditVerdictAgreement;
}

/** One step of what connecting sends, from the driver's own code run
 *  against a transport that records. */
export type AuditPreviewStep =
  | { stage: AuditPreviewStage; kind: "wait"; pattern: string }
  | { stage: AuditPreviewStage; kind: "send"; hex: string; text: string }
  | {
      stage: AuditPreviewStage;
      kind: "request";
      method: string;
      target: string;
      headers: Record<string, string>;
      body: string;
    };

export type AuditPreviewStage = "sign_in" | "start_up" | "poll" | "keep_alive";

export interface AuditConnectPreview {
  /** false for a driver whose steps are code; `reason` says so. */
  available: boolean;
  reason: string;
  steps: AuditPreviewStep[];
  poll_interval: number;
  keep_alive_interval: number;
}

/** The connection settings as recorded, secrets masked. */
export interface AuditConnection {
  config: Record<string, unknown>;
  transport: string;
  /** The project device whose saved settings were used, or "". */
  saved_from: string;
  preview: AuditConnectPreview;
}

/** One payload that crossed the device's wire, as the report writes it. */
export interface AuditTrafficEntry {
  seq: number;
  t: number;
  direction: "tx" | "rx";
  channel: string;
  hex: string;
  text: string;
  /** A raw receive chunk, before the frame parser cut it. */
  chunk?: boolean;
  meta?: Record<string, unknown>;
}

export type AuditListenStatus =
  | "connecting"
  | "listening"
  | "not_connected"
  | "done"
  | "failed"
  | "stopped";

export interface AuditStatusVariable {
  name: string;
  label: string;
  type: string;
  value: unknown;
  reported: boolean;
  first_reported_at: number | null;
  /** Why the value is not of its declared type, or "". */
  problem: string;
  /** The response rules that would set it (YAML drivers). */
  sources: string[];
}

export interface AuditListen {
  status: AuditListenStatus;
  error: string;
  started_at: number;
  connected_at: number | null;
  first_tx_at: number | null;
  first_rx_at: number | null;
  ends_at: number | null;
  finished_at: number | null;
  max_ends_at: number;
  poll_interval: number;
  reconnects: number;
  drops: number;
  offline: { code: string; detail: string; next_step: string } | null;
  declared: number;
  reported: number;
  traffic: {
    entries: number;
    sent: number;
    received: number;
    bytes: number;
    truncated: boolean;
    /** State changed but no traffic was recorded: the driver owns its connection. */
    not_captured: boolean;
  };
  contract: {
    counts: Record<string, number>;
    recent: { t: number; kind: string; detail: Record<string, unknown> }[];
  };
  status_table: {
    variables: AuditStatusVariable[];
    children: Record<string, Record<string, Record<string, unknown>>>;
    settings: { key: string; label: string; state_key: string; value: unknown; populated: boolean }[];
  };
  front_panel: {
    answer: "showed" | "did_not";
    note: string;
    at: number;
    changes: { t: number; key: string; old: unknown; new: unknown }[];
  } | null;
}

/** One driver tested against the device. */
export interface AuditDriverRun {
  index: number;
  choice: AuditDriverChoice;
  started_at: number | null;
  finished_at: number | null;
  /** True while its driver is connected to the device. */
  active: boolean;
  connection: AuditConnection | null;
  listen?: AuditListen;
}

/** A paused project device whose saved settings the chosen driver can use. */
export interface AuditSavedSettings {
  device_id: string;
  name: string;
  /** Every setting but the secrets. */
  config: Record<string, unknown>;
  /** The secret fields that have a saved value (never the values). */
  secrets_set: string[];
}

export interface AuditDriverBody {
  /** null: there is no driver for this device yet. */
  driver_id: string | null;
  manufacturer: string;
  model: string;
  firmware: string;
}

/** The project device whose page started the audit. */
export interface AuditOrigin {
  device_id: string;
  name: string;
  driver: string;
}

/** What "Audit this device" on a device page starts from. */
export interface AuditDeviceTarget {
  device_id: string;
  name: string;
  driver: string;
  /** Where the device really connects ("" when it cannot be audited). */
  address: string;
  transport: string;
  auditable: boolean;
  /** Why it cannot be audited, in words. */
  reason: string;
}

export interface AuditSessionState {
  session_id: string;
  status: AuditSessionStatus;
  target: { address: string; ip: string };
  options: { extended: boolean; snmp_communities: number };
  started_at: number;
  ended_at: number | null;
  steps: string[];
  paused: { device_id: string; name: string; owned: boolean }[];
  origin?: AuditOrigin | null;
  report_name: string | null;
  tester: AuditTester;
  check: AuditCheckState | null;
  /** What the person says the device is. */
  device?: { manufacturer: string; model: string; firmware: string };
  /** True when the person said there is no driver for this device yet. */
  no_driver?: boolean;
  runs?: AuditDriverRun[];
}

export interface AuditTimelineEntry {
  t: number;
  kind: string;
  text: string;
  data?: Record<string, unknown>;
}

export interface AuditStartBody {
  address: string;
  pause: string[];
  extended: boolean;
  snmp_communities: string[];
  /** The project device whose page started the audit. */
  from_device?: string | null;
}

export interface AuditReportFile {
  name: string;
  size: number;
  modified: number;
}

/** One connect-and-listen attempt as the report keeps it (the parts the wizard reads). */
export interface AuditReportAttempt {
  status: AuditListenStatus;
  error: string;
  started_at: number;
  connected_at: number | null;
  declared: number;
  reported: number;
  offline: { code: string; detail: string; next_step: string } | null;
  contract: { counts: Record<string, number> };
  unprompted_replies: { count: number };
  traffic: { count: number; not_captured: boolean };
}

/** One driver run in the report. */
export interface AuditReportDriver {
  run: number;
  driver: { id: string; name: string; version: string; modified: boolean };
  attempts: AuditReportAttempt[];
}

/** The report record (report.json). Only the parts the wizard reads are typed. */
export interface AuditReport {
  report_version: number;
  complete: boolean;
  target: { address: string; ip: string; hostname: string | null; same_subnet: boolean | null };
  device: {
    entered?: { manufacturer: string | null; model: string | null; firmware: string | null };
    reported: {
      manufacturer: string | null;
      model: string | null;
      firmware: string | null;
      serial_number: string | null;
      device_name: string | null;
      hostname: string | null;
      mac: string | null;
    };
  };
  catalog: AuditCatalog;
  footprint: {
    ping?: { result?: string };
    mac?: { address: string | null; source: string };
    ports?: { checked: number; range: string; open: number[]; refused: number[]; filtered: number[] };
    web?: Record<string, { status_line: string; title: string | null; www_authenticate: string | null; error: string }>;
    mdns?: { services: { service_type: string | null }[] } | null;
    ssdp?: { device_types: string[] } | null;
    amx_ddp?: { make: string | null; model: string | null } | null;
    snmp?: { answered: boolean; values?: Record<string, string> };
  };
  verdict: Omit<AuditVerdict, "catalog">;
  drivers?: AuditReportDriver[];
  limits: AuditLimit[];
}

export function getAuditConflicts(address: string): Promise<AuditConflicts> {
  return request(`/audit/conflicts?${new URLSearchParams({ address }).toString()}`);
}

export function getAuditDeviceTarget(deviceId: string): Promise<AuditDeviceTarget> {
  return request(`/audit/devices/${encodeURIComponent(deviceId)}`);
}

export function startAudit(body: AuditStartBody): Promise<{ session: AuditSessionState }> {
  return request("/audit/sessions", { method: "POST", body: JSON.stringify(body) });
}

export function getCurrentAudit(): Promise<{ session: AuditSessionState | null }> {
  return request("/audit/sessions/current");
}

export function endAudit(
  sessionId: string,
  cancel = false,
): Promise<{ session: AuditSessionState }> {
  const query = cancel ? "?cancel=true" : "";
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}${query}`, { method: "DELETE" });
}

export function startNetworkCheck(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/network-check`, {
    method: "POST",
  });
}

export function setAuditDriver(
  sessionId: string,
  body: AuditDriverBody,
): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/driver`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Finish with the current driver (its results stay) to choose another. */
export function nextAuditDriver(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/next-driver`, {
    method: "POST",
  });
}

export function getAuditSavedSettings(
  sessionId: string,
): Promise<{ devices: AuditSavedSettings[] }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/saved-settings`);
}

export function setAuditConnection(
  sessionId: string,
  config: Record<string, unknown>,
  useSaved: string | null,
): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/connection`, {
    method: "POST",
    body: JSON.stringify({ config, use_saved: useSaved }),
  });
}

/** Connect the chosen driver and listen; progress arrives over the WebSocket. */
export function connectAudit(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/connect`, { method: "POST" });
}

export function keepListening(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/listen/extend`, {
    method: "POST",
  });
}

export function answerFrontPanel(
  sessionId: string,
  answer: "showed" | "did_not",
  note = "",
): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/front-panel`, {
    method: "POST",
    body: JSON.stringify({ answer, note }),
  });
}

export function setAuditTester(
  sessionId: string,
  tester: AuditTester,
): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/tester`, {
    method: "PATCH",
    body: JSON.stringify(tester),
  });
}

export function getAuditReport(sessionId: string): Promise<AuditReport> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/report?format=json`);
}

/** Save the session's report zip; returns the file name. */
export function downloadAuditReport(sessionId: string): Promise<string> {
  return downloadFromApi(
    `/audit/sessions/${encodeURIComponent(sessionId)}/report`,
    "openavc-device-audit.zip",
  );
}

export function listAuditReports(): Promise<{ reports: AuditReportFile[] }> {
  return request("/audit/reports");
}

export function downloadSavedAuditReport(name: string): Promise<string> {
  return downloadFromApi(`/audit/reports/${encodeURIComponent(name)}`, name);
}

export function deleteAuditReport(name: string): Promise<{ deleted: string }> {
  return request(`/audit/reports/${encodeURIComponent(name)}`, { method: "DELETE" });
}
