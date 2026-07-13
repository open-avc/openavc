// Derive API base path so tunneled remote access works.
// /tunnel/{id}/programmer/ → /tunnel/{id}/api
// /programmer/ → /api
function getBasePath(): string {
  const pathParts = window.location.pathname.split("/programmer");
  const prefix = pathParts[0] || "";
  return `${prefix}/api`;
}
export const BASE = getBasePath();

/** Tunnel-aware prefix (e.g. "/tunnel/{id}" or ""). */
export function getTunnelPrefix(): string {
  const pathParts = window.location.pathname.split("/programmer");
  return pathParts[0] || "";
}

export async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  // Handle 204 No Content (e.g., DELETE responses) — res.json() on an empty
  // body rejects with "Unexpected end of JSON input". Mirrors cloudClient.ts.
  if (res.status === 204) {
    return undefined as T;
  }
  // Handle other non-JSON / empty responses.
  const contentType = res.headers.get("content-type");
  if (!contentType || !contentType.includes("application/json")) {
    return undefined as T;
  }
  return res.json();
}
