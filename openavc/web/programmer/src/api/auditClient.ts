import { request } from "./base";
import type { DiscoveredDevice, DiscoveryEvidence, IdentificationMatch } from "./discoveryClient";
import { downloadFromApi } from "./downloadFile";
import type { ChildEntityEntry, DriverParamDef } from "./types";

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

/** How a session stands. "gone" is the page's own: the server it ran on
 *  restarted and knows nothing of it. */
export type AuditSessionStatus = "active" | "finished" | "cancelled" | "expired" | "shutdown" | "gone";

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
  /** The device said it: written at or after its first reply. */
  reported: boolean;
  /** It has a value the driver wrote before the device replied. */
  set_by_driver?: boolean;
  first_reported_at: number | null;
  /** Why the value is not of its declared type, or "". */
  problem: string;
  /** The response rules that would set it (YAML drivers). */
  sources: string[];
}

export interface AuditListen {
  status: AuditListenStatus;
  /** The driver is running against the device, so commands can be sent. */
  active: boolean;
  /** The driver is connected to the device right now. */
  connected: boolean;
  error: string;
  started_at: number;
  connected_at: number | null;
  first_tx_at: number | null;
  first_rx_at: number | null;
  ends_at: number | null;
  finished_at: number | null;
  max_ends_at: number;
  poll_interval: number;
  /** While listening; the later_ pair after the window closed. */
  reconnects: number;
  drops: number;
  later_reconnects?: number;
  later_drops?: number;
  offline: { code: string; detail: string; next_step: string } | null;
  declared: number;
  reported: number;
  set_by_driver?: number;
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
    /** Each child type's label, singular and plural. */
    child_labels?: Record<string, { one: string; many: string }>;
    settings: { key: string; label: string; state_key: string; value: unknown; populated: boolean }[];
  };
  front_panel: {
    answer: "showed" | "did_not";
    note: string;
    at: number;
    changes: { t: number; key: string; old: unknown; new: unknown }[];
  } | null;
}

/** One of the driver's commands, as the live driver declares it. */
export interface AuditCommandInfo {
  name: string;
  label: string;
  help: string;
  params: Record<string, Partial<DriverParamDef>>;
  /** A status query: it declares query_for, or the driver polls it by name. */
  query: boolean;
  query_for: string;
  polled: boolean;
  /** What the driver says the command changes, e.g. {power: true}. */
  sets: Record<string, unknown>;
  available_offline: boolean;
  /** Seconds the command takes the device off the network (0: it does not). */
  restarts_device_for: number;
  /** A required parameter has no value yet, so it cannot run in the batch. */
  needs_input: boolean;
  /** The driver's own confirmation for this command ("" when it asks none). */
  confirm: string;
  /** One to try first: the driver puts it on a device page, and it neither
   *  asks for a confirmation nor restarts the device. */
  suggested: boolean;
}

/** A declared effect (``sets``) checked against what the device reports. */
export interface AuditCommandEffect {
  state: string;
  state_key: string;
  expected: unknown;
  has_value: boolean;
  value: unknown;
  outcome: "confirmed" | "already" | "different" | "unchanged" | "not_reported" | "no_value";
}

export type AuditTrialStatus = "sending" | "watching" | "done";

/** One command sent, and what came of it. */
export interface AuditCommandTrial {
  number: number;
  command: string;
  label: string;
  params: Record<string, unknown>;
  /** The how-manyth time this command was sent (1 = the first). */
  attempt: number;
  /** Sent with the status queries rather than on its own. */
  batch: boolean;
  connect_attempt: number;
  sent_at: number;
  returned_at: number | null;
  ends_at: number | null;
  finished_at: number | null;
  status: AuditTrialStatus;
  result: unknown;
  /** Why it was not accepted, in words ("" when it was). */
  error: string;
  error_type: string;
  traffic: { sent: number; received: number; entries: AuditTrafficEntry[] };
  /** The command sent before this one, and how long before. */
  since_previous: { number: number; command: string; label: string; seconds: number } | null;
  /** Seconds "Wait longer" added. */
  extended: number;
  stopped_early: boolean;
  /** Status values that changed while it was watched (the newest 20). */
  changes: { t: number; key: string; old: unknown; new: unknown }[];
  /** The status values already changing on their own when it was sent. */
  already_moving: string[];
  /** Each value its window saw change, once (from every change, not the newest 20). */
  moved: AuditMovedValue[];
  device_errors: { t: number; error: string }[];
  /** Every last_error the driver wrote while it was watched. */
  error_writes: { t: number; error: unknown }[];
  effects: AuditCommandEffect[];
  query: {
    state: string;
    state_key: string;
    value: unknown;
    changed: boolean;
    outcome: "reported" | "not_reported" | "no_reply";
  } | null;
  refusals: {
    device_errors?: number;
    last_error?: string | null;
    last_error_writes?: number;
    /** Refusals of requests the driver sent on its own after the command, once per text. */
    later?: { error: unknown; after: number; count: number; request: string }[];
    unmatched?: number;
    unmatched_examples?: string[];
  };
  /** True: the driver said it succeeded and nothing was sent. Null: not known. */
  sent_nothing: boolean | null;
  restart: {
    declared_seconds: number;
    went_away_after: number | null;
    back_after: number | null;
    away_for: number | null;
    within_declared: boolean | null;
  } | null;
  /** Each time the connection dropped inside the window of a command that
   *  declares no restart, in order: seconds after the send, and after the
   *  drop until it was back. Empty when it stayed up. */
  drops: { after: number; back_after: number | null }[];
  /** What it did, in a sentence, once its window closed ("" before). */
  summary: string;
  /** The person's answer to "Did it happen?", or null. */
  answer: { answer: AuditCommandAnswer; note: string; at: number } | null;
}

export type AuditCommandAnswer = "yes" | "no" | "partly" | "cant_tell";

/** A status value that reads differently from before the first command or
 *  setting, and the command whose window saw it move (null: none was). */
export interface AuditChangedValue {
  key: string;
  label: string;
  before: unknown;
  now: unknown;
  by: { number: number; label: string } | null;
  /** It changed while nothing was being watched after that command (a meter,
   *  a clock, a change at the device): the audit did not leave it this way. */
  on_its_own: boolean;
}

/** A status value a command's window saw change: before the first change,
 *  after the last, how many times, and whether it was already changing. */
export interface AuditMovedValue {
  key: string;
  label: string;
  first: unknown;
  last: unknown;
  times: number;
  already_moving: boolean;
  /** It ended where it began (a reconnect re-reading it, a pulse). */
  went_back?: boolean;
}

/** The commands step for one driver. */
export interface AuditCommands {
  catalog: AuditCommandInfo[];
  /** The status values the parameter pickers read (`options_state`), by key. */
  picker_state: Record<string, unknown>;
  batch: { status: "running" | "done"; total: number; sent: number; skipped: string[] } | null;
  /** The number of the command being sent or watched, if any. */
  current: number | null;
  trials: AuditCommandTrial[];
  /** What changed since the first command or setting. */
  changed: AuditChangedValue[];
}

/** One device setting the driver declares, as the audit can test it. */
export interface AuditSettingInfo {
  key: string;
  label: string;
  help: string;
  /** Its type and allowed values, the way a parameter declares them. */
  definition: Partial<DriverParamDef> & { regex?: string };
  state_key: string;
  /** The value it has now, as the device last reported it. */
  value: unknown;
  can_write: boolean;
  /** Why it cannot be tested ("" when it can). */
  reason: string;
}

/** One half of a setting's round trip: the write, or putting it back. */
export interface AuditSettingHalf {
  at?: number;
  error: string;
  confirmed: boolean;
  /** What the device reported afterwards. */
  value: unknown;
  /** Seconds from the write to the read-back. */
  after?: number;
  automatic?: boolean;
  /** The call failed after bytes had already gone to the device. */
  sent?: boolean;
  /** The audit ended before the read-back did. */
  interrupted?: boolean;
  /** It already showed this value and the device reported nothing after the write. */
  unchanged?: boolean;
}

/** One setting written, read back and put back. */
export interface AuditSettingTrial {
  number: number;
  key: string;
  label: string;
  original: unknown;
  value: unknown;
  started_at: number;
  status: "writing" | "restoring" | "done";
  write: AuditSettingHalf | Record<string, never>;
  restore: AuditSettingHalf | null;
  summary: string;
}

/** The device settings of one driver. */
export interface AuditSettings {
  catalog: AuditSettingInfo[];
  current: number | null;
  trials: AuditSettingTrial[];
}

export type AuditOutageKind = "power_cycle" | "cable_pull";

/** One power cycle or cable pull: the clocks, what they measured, what came back. */
export interface AuditOutage {
  number: number;
  kind: AuditOutageKind;
  status: "running" | "done" | "stopped";
  end_reason: string;
  /** Why it stopped early: the person's Stop, the time limit, or "" (it ended on its own). */
  end_code: "" | "person" | "ceiling";
  connect_attempt: number;
  started_at: number;
  finished_at: number | null;
  /** The person's marks. */
  off_at: number | null;
  on_at: number | null;
  /** The device's own answers to ping (null when ping is not used). */
  unreachable_at: number | null;
  reachable_at: number | null;
  /** The driver: when OpenAVC noticed, and when it was connected again. */
  noticed_at: number | null;
  reconnected_at: number | null;
  /** The connection dropping again after the reconnect, and coming back. */
  dropped_again_at: number | null;
  reconnected_again_at: number | null;
  /** When the ceiling passed with OpenAVC not having noticed. */
  not_noticed_at: number | null;
  ends_at: number | null;
  notice_ceiling_seconds: number;
  ping: { used: boolean; why: string };
  /** How the driver notices a device that went: a liveness probe, how often
   *  it probes, and the longest it takes to give up (seconds). */
  watch: { liveness_probe: boolean; probe_every: number; notice_within: number; poll_interval: number };
  reason: { code: string; detail: string } | null;
  reasons: { t: number; code: string; detail: string }[];
  measured: {
    noticed_after: number | null;
    away_for: number | null;
    answered_after_on: number | null;
    reconnected_after_back: number | null;
    dropped_again_after: number | null;
    reconnected_again_after: number | null;
  };
  before: string[];
  repopulated: { reported_again: string[]; not_reported_again: string[] };
  announcements: { t: number; protocol: string; detail: Record<string, unknown> }[];
  /** What it showed, in a sentence, once it has ended ("" before). */
  summary: string;
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
  /**
   * The credential fields whose typed value is the driver's published
   * default (or its name), by label. Shown before the download; never in
   * the report.
   */
  published_secrets?: string[];
  listen?: AuditListen;
  commands?: AuditCommands;
  settings?: AuditSettings;
  outages?: AuditOutage[];
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
  /** The number of the last message the server sent about this audit
   *  before this state was taken. */
  seq?: number;
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
  set_by_driver?: number;
  offline: { code: string; detail: string; next_step: string } | null;
  contract: { counts: Record<string, number> };
  unprompted_replies: { count: number };
  /** How often the link dropped while listening, and how often the driver got it back. */
  drops: number;
  reconnects: number;
  /** The same after the listening window closed. */
  later_drops?: number;
  later_reconnects?: number;
  traffic: { count: number; sent: number; received: number; not_captured: boolean };
}

/** One driver run in the report. */
export interface AuditReportDriver {
  run: number;
  driver: { id: string; name: string; version: string; modified: boolean };
  attempts: AuditReportAttempt[];
  /** What the run suggests for the catalog's confidence in this model, and why. */
  suggested_confidence?: {
    level: "full" | "partial" | null;
    reasons: { held: boolean; text: string }[];
  };
  /** The catalog's Driver test report form, filled in from this run (catalog drivers only). */
  test_report?: { url: string; fields: Record<string, string> } | null;
  commands?: { trials: AuditCommandTrial[]; changed?: AuditChangedValue[] } | null;
  settings?: { trials: AuditSettingTrial[] } | null;
  outages?: AuditOutage[];
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

/** Send one of the driver's commands; what it does arrives over the WebSocket.
 *  `again` is "Try again": the number of an earlier send of this command,
 *  whose values the server still holds. */
export function sendAuditCommand(
  sessionId: string,
  name: string,
  params: Record<string, unknown>,
  again: number | null = null,
): Promise<{ session: AuditSessionState }> {
  return request(
    `/audit/sessions/${encodeURIComponent(sessionId)}/commands/${encodeURIComponent(name)}`,
    { method: "POST", body: JSON.stringify(again ? { again } : { params }) },
  );
}

/** The children the audited driver has registered, of one type (a command's
 *  child picker). */
export function listAuditChildren(
  sessionId: string,
  childType: string,
): Promise<{ child_type: string; children: ChildEntityEntry[] }> {
  return request(
    `/audit/sessions/${encodeURIComponent(sessionId)}/children/${encodeURIComponent(childType)}`,
  );
}

/** Write a device setting, read it back, put the old value back. */
export function writeAuditSetting(
  sessionId: string,
  key: string,
  value: unknown,
): Promise<{ session: AuditSessionState }> {
  return request(
    `/audit/sessions/${encodeURIComponent(sessionId)}/settings/${encodeURIComponent(key)}`,
    { method: "POST", body: JSON.stringify({ value }) },
  );
}

/** "Put it back": retry a setting's restore that did not read back. */
export function putAuditSettingBack(
  sessionId: string,
  key: string,
): Promise<{ session: AuditSessionState }> {
  return request(
    `/audit/sessions/${encodeURIComponent(sessionId)}/settings/${encodeURIComponent(key)}/restore`,
    { method: "POST" },
  );
}

/** Start the power cycle test; what it measures arrives over the WebSocket. */
export function startPowerCycle(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/power-cycle`, {
    method: "POST",
  });
}

/** Start the cable pull test; what it measures arrives over the WebSocket. */
export function startCablePull(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/cable-pull`, {
    method: "POST",
  });
}

/** The person says the device went off, or is back on. */
export function markOutage(
  sessionId: string,
  mark: "off" | "on",
): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/outage/mark`, {
    method: "POST",
    body: JSON.stringify({ mark }),
  });
}

/** End the power or cable test that is running. */
export function stopOutage(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/outage/stop`, {
    method: "POST",
  });
}

/** "Did it happen?" for command number `trial`. */
export function answerAuditCommand(
  sessionId: string,
  trial: number,
  answer: AuditCommandAnswer,
  note: string,
): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/answers`, {
    method: "POST",
    body: JSON.stringify({ trial, answer, note }),
  });
}

/** "Wait longer": keep watching the command just sent. */
export function waitLonger(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/watch/extend`, {
    method: "POST",
  });
}

/** "Stop watching": close the command's window now. */
export function stopWatching(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/watch/stop`, {
    method: "POST",
  });
}

/** Send every status query the driver declares, one after another. */
export function runAuditQueries(sessionId: string): Promise<{ session: AuditSessionState }> {
  return request(`/audit/sessions/${encodeURIComponent(sessionId)}/queries`, { method: "POST" });
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
