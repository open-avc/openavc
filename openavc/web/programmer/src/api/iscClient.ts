// The Inter-System mesh key. The project holds the "saved on this system"
// marker in its place, so the key reaches the browser only when somebody asks
// for it (Show), and goes to the system on its own save, never in the project.
import { request } from "./base";

/** Where the mesh key stands on this system, as `/api/isc/status` says. */
export type IscKeyState = "saved" | "needs_login" | "none";

/** The mesh key saved on this system, "" when there is none. */
export async function getIscAuthKey(): Promise<string> {
  const answer = await request<{ auth_key?: string }>("/isc/auth-key");
  return typeof answer?.auth_key === "string" ? answer.auth_key : "";
}

/** Save a new mesh key on this system. */
export async function saveIscAuthKey(authKey: string): Promise<void> {
  await request("/isc/auth-key", {
    method: "PUT",
    body: JSON.stringify({ auth_key: authKey }),
  });
}
