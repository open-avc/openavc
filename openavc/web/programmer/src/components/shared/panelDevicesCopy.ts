import type { PanelDevice } from "../../api/restClient";

/** The sentences the notice and the Panels card say about a panel device.
 *
 *  Pure, so the wording can be tested without mounting either surface. Both
 *  render what these return and add nothing of their own.
 */

/** "iPad at 10.1.1.50": what a device is called before anyone names it, and
 *  the name Approve offers. The server uses the same rule for a blank name. */
export function defaultPanelName(device: Pick<PanelDevice, "platform" | "address">): string {
  const platform = device.platform || "Unknown device";
  return device.address ? `${platform} at ${device.address}` : platform;
}

/** The notice on every view while panels are waiting. One waiting panel is
 *  named in full so it can be approved from the notice; several are counted,
 *  and the Dashboard's Panels card tells them apart. */
export function panelRequestSentence(pending: PanelDevice[]): string {
  if (pending.length === 1) {
    const d = pending[0];
    return `A panel is asking to connect: code ${d.code ?? ""}, ${defaultPanelName(d)}.`;
  }
  return `${pending.length} panels are asking to connect.`;
}

export function revokeQuestion(name: string): string {
  return `Revoke ${name}? It shows the waiting screen until it is approved again.`;
}

/** The one-time notice on a system that was updated with its panels
 *  connected: it came up as Anyone on the network so nothing in the room
 *  changed, and this says what that means and where the switch is. */
export const PANEL_ACCESS_UPGRADE_NOTICE =
  "Panel access is Anyone on the network: any device that can reach this system can open the panel and control the space. "
  + "To have a new tablet or browser wait for your approval, choose Approved panels only under Settings > Access.";

/** "just now", "2 min", "3 h", "2 d"; null for a timestamp that cannot be
 *  read. Server timestamps are UTC ("2026-09-25T14:02:11Z"). */
export function elapsedText(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const seconds = Math.max(0, Math.round((now - t) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h`;
  return `${Math.floor(hours / 24)} d`;
}

/** How long a panel has been waiting: "just now", "since 2 min". */
export function sinceText(iso: string | null | undefined, now: number = Date.now()): string {
  const elapsed = elapsedText(iso, now);
  if (elapsed === null) return "";
  return elapsed === "just now" ? elapsed : `since ${elapsed}`;
}

/** When an approved panel was last seen: "last seen just now", "last seen 3 min ago". */
export function lastSeenText(iso: string | null | undefined, now: number = Date.now()): string {
  const elapsed = elapsedText(iso, now);
  if (elapsed === null) return "";
  return elapsed === "just now" ? "last seen just now" : `last seen ${elapsed} ago`;
}

/** When a panel was denied: "denied just now", "denied 5 min ago". */
export function deniedText(iso: string | null | undefined, now: number = Date.now()): string {
  const elapsed = elapsedText(iso, now);
  if (elapsed === null) return "";
  return elapsed === "just now" ? "denied just now" : `denied ${elapsed} ago`;
}
