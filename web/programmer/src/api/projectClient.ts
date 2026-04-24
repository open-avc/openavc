import type {
  ProjectConfig,
  LibraryProject,
  LibraryProjectDetail,
} from "./types";
import { BASE, request } from "./base";

// --- Project ---

export async function getProject(): Promise<ProjectConfig & { _etag?: string }> {
  const res = await fetch(`${BASE}/project`, {
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  const etag = res.headers.get("etag") ?? undefined;
  const text = await res.text();

  let data: ProjectConfig;
  if (text.length > 512_000 && typeof Worker !== "undefined") {
    data = await new Promise<ProjectConfig>((resolve, reject) => {
      const worker = new Worker(
        new URL("../workers/projectParser.ts", import.meta.url),
        { type: "module" }
      );
      worker.onmessage = (e) => {
        worker.terminate();
        if (e.data.ok) resolve(e.data.data);
        else reject(new Error(e.data.error));
      };
      worker.onerror = () => {
        worker.terminate();
        resolve(JSON.parse(text));
      };
      worker.postMessage(text);
    });
  } else {
    data = JSON.parse(text);
  }

  if (etag) (data as any)._etag = etag;
  return data;
}

export async function getSystemStatus(): Promise<Record<string, unknown>> {
  return request("/status");
}

export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
  }
}

export async function saveProject(
  project: ProjectConfig,
  etag?: string
): Promise<{ status: string; etag?: string }> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (etag) headers["If-Match"] = etag;

  const res = await fetch(`${BASE}/project`, {
    method: "PUT",
    headers,
    body: JSON.stringify(project),
  });

  if (res.status === 409) {
    throw new ConflictError("Project was modified by another session. Reload to see the latest changes.");
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }

  const result = await res.json();
  const newEtag = res.headers.get("etag") ?? undefined;
  return { status: result.status, etag: newEtag };
}

export async function reloadProject(): Promise<{ status: string }> {
  return request("/project/reload", { method: "POST" });
}

// --- Backups ---

export interface BackupInfo {
  filename: string;
  reason: string;
  timestamp: string;
  project_name: string;
  size: number;
  format: "zip" | "legacy";
}

export async function listBackups(): Promise<BackupInfo[]> {
  return request<BackupInfo[]>("/backups");
}

export async function createBackup(
  reason?: string
): Promise<{ status: string; filename: string }> {
  return request("/backups/create", {
    method: "POST",
    body: JSON.stringify({ reason: reason || "Manual backup" }),
  });
}

export async function restoreBackup(
  filename: string
): Promise<{ status: string; filename: string }> {
  return request(`/backups/${encodeURIComponent(filename)}/restore`, { method: "POST" });
}

// --- Project Library ---

export async function listLibrary(): Promise<LibraryProject[]> {
  return request("/library");
}

export async function getLibraryProject(id: string): Promise<LibraryProjectDetail> {
  return request(`/library/${id}`);
}

export async function saveToLibrary(data: {
  id: string;
  name: string;
  description?: string;
}): Promise<{ status: string; id: string }> {
  return request("/library", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteLibraryProject(
  id: string
): Promise<{ status: string; id: string }> {
  return request(`/library/${id}`, { method: "DELETE" });
}

export async function updateLibraryProject(
  id: string,
  data: { name?: string; description?: string }
): Promise<{ status: string; id: string }> {
  return request(`/library/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function duplicateLibraryProject(
  id: string,
  newId: string,
  newName: string
): Promise<{ status: string; id: string }> {
  return request(`/library/${id}/duplicate`, {
    method: "POST",
    body: JSON.stringify({ new_id: newId, new_name: newName }),
  });
}

export async function exportLibraryProject(id: string): Promise<void> {
  const res = await fetch(`${BASE}/library/${id}/export`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const disposition = res.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `${id}.avc`;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function importToLibrary(file: File, id?: string): Promise<{
  status: string; id: string;
  installed_drivers?: string[];
  missing_drivers?: { driver_id: string; driver_name: string; affected_devices: string[] }[];
  warnings?: string[];
}> {
  const formData = new FormData();
  formData.append("file", file);
  if (id) formData.append("id", id);
  const res = await fetch(`${BASE}/library/import`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

export async function openFromLibrary(
  libraryId: string,
  projectName: string,
  projectId?: string
): Promise<{ status: string; project_name: string }> {
  return request("/project/open-from-library", {
    method: "POST",
    body: JSON.stringify({
      library_id: libraryId,
      project_name: projectName,
      project_id: projectId,
    }),
  });
}

export async function createBlankProject(
  projectName: string,
  projectId?: string
): Promise<{ status: string; project_name: string }> {
  return request("/project/create-blank", {
    method: "POST",
    body: JSON.stringify({
      project_name: projectName,
      project_id: projectId,
    }),
  });
}

// --- Driver Validation ---

export async function validateDrivers(): Promise<{
  available: string[];
  missing: { driver_id: string; affected_devices: string[] }[];
}> {
  return request("/project/validate-drivers");
}

// --- Plugin Validation ---

export async function validatePlugins(): Promise<{
  available: { plugin_id: string; plugin_name: string; version: string; status: string }[];
  missing: { plugin_id: string; affected_config: boolean }[];
  platform_warnings: { plugin_id: string; current_platform: string; supported_platforms: string[]; message: string }[];
}> {
  return request("/project/validate-plugins");
}
