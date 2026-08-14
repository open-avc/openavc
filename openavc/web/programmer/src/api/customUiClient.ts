/**
 * The project's ui/ folder — custom controls the integrator writes themselves.
 *
 * One control is one folder (`room_map/index.html` plus its CSS, JS and
 * images). These calls are the IDE's half of that folder: list what is there,
 * drop files in, take them out. The panel fetches the files themselves from the
 * same paths without a credential, because a wall panel has none to present.
 */
import { BASE, request } from "./base";

export interface CustomUiFile {
  /** Path relative to the ui/ folder, e.g. "room_map/index.html". */
  path: string;
  size: number;
  /** Epoch seconds. */
  modified: number;
}

export interface CustomUiListing {
  files: CustomUiFile[];
  total_size: number;
  max_total_size: number;
  max_file_size: number;
}

export async function listCustomUiFiles(): Promise<CustomUiListing> {
  return request("/projects/default/ui");
}

/** Encode a relative path for a URL without turning its slashes into %2F. */
function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export interface CustomUiSaveResult {
  status: string;
  path: string;
  size: number;
  /**
   * What the saved file will get wrong in a real space, one sentence each.
   *
   * The review runs on the server and only there: a save is already a round
   * trip, so a second copy of these checks in the browser would be two
   * implementations to keep saying the same thing. Absent when there is
   * nothing to report.
   */
  warnings?: string[];
}

export async function writeCustomUiFile(
  path: string,
  content: string,
): Promise<CustomUiSaveResult> {
  return request(`/projects/default/ui/${encodePath(path)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export async function deleteCustomUiFile(path: string): Promise<{ status: string }> {
  return request(`/projects/default/ui/${encodePath(path)}`, { method: "DELETE" });
}

/**
 * The text of one file, for the editor.
 *
 * Read back through the same open route the panel fetches it from, because
 * that is the file that actually runs — a second read path could disagree
 * with what a panel is being served. The route sends `no-cache`, so this is
 * the version on disk rather than one the browser kept.
 */
export async function readCustomUiFile(path: string): Promise<string> {
  const res = await fetch(customUiFileUrl(path));
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.text();
}

/**
 * Upload one file, or a .zip that unpacks keeping its own folders.
 *
 * `folder` is where it lands (empty for the top of the tree); the file keeps
 * its own name. A browser dropping a folder reports each entry's
 * `webkitRelativePath`, so pass the folder half of that here and the control's
 * structure survives the trip.
 */
export async function uploadCustomUiFile(
  file: File,
  folder = "",
): Promise<{ status: string; written: string[]; skipped: string[] }> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("path", folder);
  const res = await fetch(`${BASE}/projects/default/ui`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

/**
 * Upload a batch, each file with the folder it belongs in.
 *
 * The two doors that take a drop — the element's properties panel and the
 * Code view's file tree — share this so a folder lands the same way through
 * both. `baseFolder` is where the whole batch goes (the tree can drop into a
 * control's folder); each entry's own `folder` is appended under it.
 */
export async function uploadCustomUiFiles(
  entries: { file: File; folder: string }[],
  baseFolder = "",
): Promise<{ written: string[]; skipped: string[] }> {
  const written: string[] = [];
  const skipped: string[] = [];
  for (const { file, folder } of entries) {
    const dest = [baseFolder, folder].filter(Boolean).join("/");
    const result = await uploadCustomUiFile(file, dest);
    written.push(...result.written);
    skipped.push(...result.skipped);
  }
  return { written, skipped };
}

/** The URL the panel serves a custom UI file from. */
export function customUiFileUrl(path: string): string {
  return `${BASE}/projects/default/ui/${encodePath(path)}`;
}
