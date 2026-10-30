import { BASE, request } from "./base";
import { ApiError } from "./errors";
import { downloadFromApi } from "./downloadFile";

// --- Problem reports (/api/devices/{id}/problem-report, /api/problem-reports) ---

/** What a report about one device would include, read from the same records
 *  the report is built from. */
export interface ProblemReportPreview {
  device: { id: string; name: string };
  driver: {
    id: string;
    name: string;
    version: string;
    /** "built_in", "catalog" or "imported". */
    source: string | null;
    modified: boolean | null;
    listed: boolean | null;
    /** False when the driver library could not be reached. */
    checked: boolean | null;
    files: string[];
    loaded: boolean;
  } | null;
  address: { transport?: string; host?: string; port?: number | string | null; bridge?: string };
  values: number;
  changes: number;
  messages: number;
  /** The oldest recorded message's time, seconds since 1970; null with none. */
  window_start: number | null;
  log_lines: number;
  serial_found: boolean;
  simulated: boolean;
  openavc_version: string;
  /** `platform.platform()` as the server reports it. */
  os: string;
  /** A deployment type code (`windows_installer`, `docker`, ...). */
  deployment_type: string;
  /** Where Send delivers; "" when sending is off. */
  report_url: string;
  report_host: string;
  max_bytes: number;
}

/** Where Report a problem was pressed, and what the page showed then. */
export interface ProblemReportContext {
  source: "driver_line" | "offline_banner" | "command_failure";
  error?: string;
  command?: string;
  params?: Record<string, unknown>;
  status?: number | null;
  code?: string;
}

export interface ProblemReportBody {
  message: string;
  name: string;
  email: string;
  leave_out_serial: boolean;
  context: ProblemReportContext | null;
}

export interface SavedProblemReport {
  name: string;
  report_id: string;
  size_bytes: number;
}

/** Why a send did not arrive: every kind but "network" is a final answer. */
export type SendFailure =
  | "network"
  | "too_large"
  | "rate_limited"
  | "refused"
  | "storage_full"
  | "not_configured";

/** What one press of Send came to on the server (either report kind). */
export interface ServerSendResult {
  name: string;
  report_id?: string;
  sent: boolean;
  reference: string | null;
  failure: SendFailure | null;
  status: number | null;
  /** Where the browser sends the same file when this server could not. */
  report_url?: string;
}

const enc = encodeURIComponent;

export function getProblemReportPreview(deviceId: string): Promise<ProblemReportPreview> {
  return request(`/devices/${enc(deviceId)}/problem-report`);
}

export function createProblemReport(
  deviceId: string,
  body: ProblemReportBody,
): Promise<SavedProblemReport> {
  return request(`/devices/${enc(deviceId)}/problem-report`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function sendProblemReport(name: string): Promise<ServerSendResult> {
  return request(`/problem-reports/${enc(name)}/send`, { method: "POST" });
}

export function downloadProblemReport(name: string): Promise<string> {
  return downloadFromApi(`/problem-reports/${enc(name)}`, name);
}

/** The kept zip's bytes, for the browser's own send. */
export function problemReportFile(name: string): Promise<Blob> {
  return fileAt(`/problem-reports/${enc(name)}`);
}

/** Send to OpenAVC on the audit's Report step: the server saves the report as
 *  it stands, then sends that file. */
export function sendAuditSessionReport(sessionId: string): Promise<ServerSendResult> {
  return request(`/audit/sessions/${enc(sessionId)}/report/send`, { method: "POST" });
}

/** Send a kept audit report (the ended notice). */
export function sendSavedAuditReport(name: string): Promise<ServerSendResult> {
  return request(`/audit/reports/${enc(name)}/send`, { method: "POST" });
}

/** A kept audit report's bytes, by the name a send answered with. Never the
 *  session's own report route: that builds a new file with a new id. */
export function auditReportFile(name: string): Promise<Blob> {
  return fileAt(`/audit/reports/${enc(name)}`);
}

async function fileAt(path: string): Promise<Blob> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.blob();
}

/**
 * The browser's own send of a report zip to the report service, for when this
 * server could not reach it. A "simple" request: a FormData body, no headers
 * of its own (the browser writes the multipart boundary) and no credentials,
 * so no preflight is sent and nothing of this session's rides along.
 */
export function postReportFromBrowser(
  url: string,
  file: Blob,
  filename: string,
  fields: Record<string, string>,
): Promise<Response> {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  form.append("file", file, filename);
  return fetch(url, { method: "POST", body: form, credentials: "omit" });
}
