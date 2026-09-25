import { BASE } from "./base";
import { ApiError } from "./errors";

/**
 * Hand a file to the browser to save.
 *
 * The one copy of the Blob-and-anchor download. A plain link cannot fetch an
 * API file, because the session token rides on a request header the fetch
 * interceptor adds (api/auth.ts), so the file is fetched first and then saved.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked on the next turn: some browsers start the save after click()
  // returns, and a URL revoked first saves nothing.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** The file name a response's Content-Disposition gives, or null. */
export function dispositionFilename(disposition: string | null): string | null {
  if (!disposition) return null;
  const star = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (star) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      /* fall through to the plain form */
    }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : null;
}

/**
 * GET an API path and save the response, named as the server names it.
 * Returns the name used. Throws ApiError with the server's message on failure.
 */
export async function downloadFromApi(path: string, fallbackName: string): Promise<string> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  const name = dispositionFilename(res.headers.get("content-disposition")) ?? fallbackName;
  downloadBlob(await res.blob(), name);
  return name;
}
