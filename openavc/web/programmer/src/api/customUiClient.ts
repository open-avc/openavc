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

export async function writeCustomUiFile(
  path: string,
  content: string,
): Promise<{ status: string; path: string; size: number }> {
  return request(`/projects/default/ui/${encodePath(path)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export async function deleteCustomUiFile(path: string): Promise<{ status: string }> {
  return request(`/projects/default/ui/${encodePath(path)}`, { method: "DELETE" });
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

/** The URL the panel serves a custom UI file from. */
export function customUiFileUrl(path: string): string {
  return `${BASE}/projects/default/ui/${encodePath(path)}`;
}
