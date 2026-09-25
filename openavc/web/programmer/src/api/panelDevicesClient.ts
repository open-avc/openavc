import { request } from "./base";

// --- Panel devices ---
// The tablets, phones and browsers that have asked to open the panel. A new
// device waits until it is approved once; the Programmer approves, denies,
// renames and revokes them here. The server never sends a secret or a hash.

/** Who may open the panel: `approved` (approved panels only) or `open`
 *  (anyone on the network). Anything else the server treats as approved. */
export type PanelAccessMode = "approved" | "open";

export type PanelDeviceStatus = "pending" | "approved" | "denied";

export interface PanelDevice {
  id: string;
  status: PanelDeviceStatus;
  /** Blank while the device waits; set at approval (the platform and address
   *  when the approver gave none). */
  name: string;
  /** What kind of device, read from its user agent: "iPad", "Android tablet",
   *  "Windows PC", and so on. */
  platform: string;
  address: string;
  first_seen: string;
  last_seen: string;
  /** Six digits shown as "482-915"; only on a pending or denied record. */
  code?: string;
  approved_at?: string | null;
  approved_by?: string;
  denied_at?: string | null;
}

export interface PanelDeviceList {
  access: PanelAccessMode;
  pending: PanelDevice[];
  approved: PanelDevice[];
  denied: PanelDevice[];
}

export async function listPanelDevices(): Promise<PanelDeviceList> {
  return request<PanelDeviceList>("/panel/devices");
}

/** A blank name means the platform and address; the server fills it in. */
export async function approvePanelDevice(id: string, name: string): Promise<PanelDevice> {
  return request<PanelDevice>(`/panel/devices/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function denyPanelDevice(id: string): Promise<PanelDevice> {
  return request<PanelDevice>(`/panel/devices/${encodeURIComponent(id)}/deny`, {
    method: "POST",
  });
}

export async function revokePanelDevice(id: string): Promise<{ status: string; panel_id: string }> {
  return request(`/panel/devices/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function renamePanelDevice(id: string, name: string): Promise<PanelDevice> {
  return request<PanelDevice>(`/panel/devices/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}
