/**
 * Commands that take a file or hand one back: the upload, and Send Command's
 * background runs of them (`api/routes/command_files.py`).
 *
 * A file goes up in chunks the server sizes (small through the cloud tunnel,
 * large on a direct connection), each a raw PUT at its offset. A 429 is waited
 * out and a chunk the server already has is skipped, so a long upload resumes
 * instead of starting over. The command then runs in the background on the
 * server; `command.run` WebSocket messages follow it.
 */
import { BASE, request } from "./base";
import { downloadFromApi } from "./downloadFile";
import { ApiError } from "./errors";

export interface CommandUpload {
  id: string;
  name: string;
  size: number;
  received: number;
  complete: boolean;
}

export interface UploadOpened {
  upload: CommandUpload;
  chunk_size: number;
}

export interface CommandRunFile {
  param: string;
  name: string;
  size: number;
  /** How much of it the driver has read so far. */
  read: number;
}

export interface CommandRun {
  run_id: string;
  device_id: string;
  command: string;
  label: string;
  status: "running" | "done" | "error" | "cancelled";
  started: number;
  ended: number | null;
  files: CommandRunFile[];
  sent: number;
  total: number;
  result: unknown;
  error: string | null;
  /** The HTTP status and code Send Command answers the same failure with. */
  error_status?: number | null;
  error_code?: string | null;
  /** The file the command handed back; `gone` once downloaded or expired. */
  returned: { name: string; size: number; gone?: boolean } | null;
  client_id: string | null;
}

/** Opens an upload for one file param, at whichever door will run it. */
export type UploadOpener = (param: string, file: File) => Promise<UploadOpened>;

const enc = encodeURIComponent;

export function deviceUploadOpener(deviceId: string, command: string): UploadOpener {
  return (param, file) =>
    request<UploadOpened>(`/devices/${enc(deviceId)}/command-files`, {
      method: "POST",
      body: JSON.stringify({ command, param, name: file.name, size: file.size }),
    });
}

export function auditUploadOpener(sessionId: string, command: string): UploadOpener {
  return (param, file) =>
    request<UploadOpened>(`/audit/sessions/${enc(sessionId)}/command-files`, {
      method: "POST",
      body: JSON.stringify({ command, param, name: file.name, size: file.size }),
    });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Send one opened upload's bytes; resolves with the upload's id. */
export async function sendFileChunks(
  opened: UploadOpened,
  file: File,
  onProgress: (sent: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  const { id } = opened.upload;
  let offset = opened.upload.received;
  while (offset < file.size) {
    if (signal?.aborted) throw new DOMException("The upload was stopped.", "AbortError");
    const piece = file.slice(offset, offset + opened.chunk_size);
    const res = await fetch(`${BASE}/command-files/${enc(id)}?offset=${offset}`, {
      method: "PUT",
      body: piece,
      headers: { "Content-Type": "application/octet-stream" },
      signal,
    });
    if (res.status === 429) {
      const wait = Number(res.headers.get("Retry-After") ?? "2");
      await sleep(Math.max(1, Number.isFinite(wait) ? wait : 2) * 1000);
      continue;
    }
    if (res.status === 409) {
      // The server already has more (an earlier try got through): resume there.
      const body = (await res.clone().json().catch(() => null)) as { received?: unknown } | null;
      if (body && typeof body.received === "number" && body.received !== offset) {
        offset = body.received;
        onProgress(offset);
        continue;
      }
    }
    if (!res.ok) throw new ApiError(res.status, await res.text());
    const body = (await res.json()) as { upload: CommandUpload };
    offset = body.upload.received;
    onProgress(offset);
  }
  return id;
}

/** Drop an upload that will not be used. Best effort: it expires anyway. */
export async function discardUpload(uploadId: string): Promise<void> {
  try {
    await fetch(`${BASE}/command-files/${enc(uploadId)}`, { method: "DELETE" });
  } catch {
    /* expires on the server */
  }
}

/**
 * Upload every chosen file of a command, one after another. `onProgress` is
 * the bytes sent of them all, and the file being sent. Resolves with the
 * upload that fills each file param; on a failure the ones already sent are
 * dropped.
 */
export async function uploadCommandFiles(
  open: UploadOpener,
  files: Record<string, File>,
  onProgress: (sent: number, total: number, file: File) => void,
  signal?: AbortSignal,
): Promise<Record<string, string>> {
  const total = Object.values(files).reduce((sum, f) => sum + f.size, 0);
  const ids: Record<string, string> = {};
  let done = 0;
  try {
    for (const [param, file] of Object.entries(files)) {
      const opened = await open(param, file);
      ids[param] = opened.upload.id;
      await sendFileChunks(opened, file, (sent) => onProgress(done + sent, total, file), signal);
      done += file.size;
    }
  } catch (e) {
    await Promise.all(Object.values(ids).map(discardUpload));
    throw e;
  }
  return ids;
}

export async function startCommandRun(
  deviceId: string,
  command: string,
  params: Record<string, unknown>,
  files: Record<string, string>,
): Promise<CommandRun> {
  const body = await request<{ run: CommandRun }>(`/devices/${enc(deviceId)}/command-runs`, {
    method: "POST",
    body: JSON.stringify({ command, params, files }),
  });
  return body.run;
}

export async function listCommandRuns(deviceId: string): Promise<CommandRun[]> {
  const body = await request<{ runs: CommandRun[] }>(`/devices/${enc(deviceId)}/command-runs`);
  return body.runs;
}

export async function cancelCommandRun(deviceId: string, runId: string): Promise<CommandRun> {
  const body = await request<{ run: CommandRun }>(
    `/devices/${enc(deviceId)}/command-runs/${enc(runId)}/cancel`,
    { method: "POST" },
  );
  return body.run;
}

/** Save the file a run handed back (it is deleted on the server once saved). */
export function downloadCommandRunFile(
  deviceId: string,
  runId: string,
  fallbackName: string,
): Promise<string> {
  return downloadFromApi(`/devices/${enc(deviceId)}/command-runs/${enc(runId)}/file`, fallbackName);
}

/** Save the file a command handed back during a device audit. */
export function downloadAuditCommandFile(
  sessionId: string,
  number: number,
  fallbackName: string,
): Promise<string> {
  return downloadFromApi(`/audit/sessions/${enc(sessionId)}/commands/${number}/file`, fallbackName);
}
