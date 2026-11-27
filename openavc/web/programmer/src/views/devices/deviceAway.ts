/**
 * How a device's page explains a device that is away: what the live state says
 * about it, which one banner explains it, and what the Offline banner adds to
 * the server's sentence (the driver's hint, its last line, whether it offers
 * Report a problem).
 *
 * Which banner is the device's presence word (components/shared/
 * devicePresence.ts), the one the status dot, the device list and the
 * Dashboard read: its driver is not installed; a command is restarting it; a
 * command put it to sleep; Live Test or a Device Audit holds it; it is
 * offline, and then it waits for a login this system does not have, or the
 * server says why. A device online, connecting, or turned off in the project
 * has none. A new way of being away is one more entry in `BANNER_FOR`, its
 * banner, and its entry in DeviceBanners.tsx, which the compiler asks for.
 *
 * The rest is what the chosen banner says: who holds the pause, the fault's
 * code and sentence, whether the server stopped retrying.
 */
import { isSecretConfigField } from "./deviceConfigCoerce";
import { presenceWord, type PresenceWord } from "../../components/shared/devicePresence";

/** What the live state says about a device being away (device.<id>.*). */
export interface DeviceAway {
  /** What its state adds up to, the word that chooses the banner. */
  presence: PresenceWord;
  connected: boolean;
  /** Who holds a pause (`paused_for`), named in the banner. */
  pausedFor: unknown;
  /** The stable code from the server's classifier (core/connection_fault.py). */
  offlineReason: string;
  /** The server's sentence: the fault's, or a restart's or a sleep's, which
   *  leave `offlineReason` empty. This page only shows it. */
  offlineDetail: string;
  /** The server stopped retrying. */
  reconnectFailed: boolean;
}

export function readDeviceAway(liveState: Record<string, unknown>, deviceId: string): DeviceAway {
  const read = (prop: string) => liveState[`device.${deviceId}.${prop}`];
  return {
    presence: presenceWord(read("presence")),
    connected: Boolean(read("connected")),
    pausedFor: read("paused_for"),
    offlineReason: String(read("offline_reason") ?? ""),
    offlineDetail: String(read("offline_detail") ?? ""),
    reconnectFailed: Boolean(read("reconnect_failed")),
  };
}

/** The device waits for a login this system does not have. */
export function waitsForLogin(away: Pick<DeviceAway, "offlineReason">): boolean {
  return away.offlineReason === "login_missing";
}

/** The banners that explain an away device. */
export type AwayBanner = "orphaned" | "paused" | "restarting" | "asleep" | "needs_login" | "offline";

/** The words that are a banner of their own, whatever else the state says. */
const BANNER_FOR: Partial<Record<PresenceWord, AwayBanner>> = {
  waiting_for_driver: "orphaned",
  restarting: "restarting",
  asleep: "asleep",
  paused: "paused",
};

/** What deciding which way a device is away reads. */
export type AwayFacts = Pick<DeviceAway, "presence" | "offlineReason" | "offlineDetail">;

/**
 * Which way the device is away, before asking which settings wait for a
 * login: the banner the page shows, and what the device list's Needs login
 * chip asks (the list does not read which settings wait).
 */
export function awayState(away: AwayFacts): AwayBanner | null {
  const banner = BANNER_FOR[away.presence];
  if (banner) return banner;
  if (away.presence !== "offline") return null;
  if (waitsForLogin(away)) return "needs_login";
  return away.offlineDetail ? "offline" : null;
}

/**
 * The one banner that explains the device, or null when none does.
 * `loginFields` counts the settings that wait for a login: with none named
 * there is nothing to ask for, and the Offline banner does not stand in.
 */
export function awayBanner(away: AwayFacts, loginFields: number): AwayBanner | null {
  const state = awayState(away);
  return state === "needs_login" && loginFields === 0 ? null : state;
}

// Offline reasons the server stops retrying on, because only a human can
// clear them. Mirrors _PERMANENT_FAULT_CODES in openavc/core/connection_fault.py
// (auth_failed is in that set too, but gets its own lockout-specific wording
// below); tests/test_connection_fault_completeness.py holds the two in step.
const PERMANENT_OFFLINE_REASONS = new Set([
  "host_key_rejected",
  "tls_cert_untrusted",
  "invalid_config",
  "client_missing",
  "login_missing",
  "driver_failed",
]);

/** The driver's own troubleshooting line for a device that will not connect
 *  (DRIVER_INFO.help.connection): what the server's classifier cannot know,
 *  such as a setting to turn on at the device first. Empty when it has none. */
export function driverConnectionHint(driverInfo: Record<string, unknown> | undefined): string {
  return String((driverInfo as { help?: { connection?: string } } | undefined)?.help?.connection ?? "");
}

/** True when the driver has a password, key or other sign-in field, so a
 *  refused sign-in may be fixed under Edit. A device that pairs (a TV
 *  accepting a prompt, a bridge's link button) or has no credentials does not. */
export function hasSignInFields(schema: Record<string, unknown> | undefined): boolean {
  return Object.keys(schema ?? {}).some((key) => isSecretConfigField(schema, key));
}

/** The Offline banner's last line: what happens next, and what to do. */
export function offlineNextStep(
  reason: string | undefined,
  failed: boolean,
  signInFields: boolean,
): string {
  if (reason === "no_simulator") {
    // Refused on purpose, and retried by nothing: it is the simulation that
    // keeps it off, so stopping it is what brings the device back.
    return "Not connecting while simulation runs. Stopping simulation connects it to the real device again.";
  }
  if (!failed) {
    // Deliberately no attempt count. There's no ceiling to count toward, and
    // a device left off over a weekend would show a number in the thousands,
    // which tells the integrator nothing except that something looks broken.
    return "Reconnecting automatically, and will keep trying until the device comes back.";
  }
  if (reason === "auth_failed") {
    // Retrying a rejected login would only trip the device's brute-force
    // lockout, so the server holds off on purpose.
    // The line above is the driver's sentence and names what was refused,
    // which is not always a password: a declined pairing prompt, a bridge to
    // pair with its link button, a setting on the device itself.
    return signInFields
      ? "Not retrying, so repeated sign-ins cannot lock OpenAVC out of the device. Fix the cause above (the sign-in details are under Edit), then press Reconnect."
      : "Not retrying, so repeated sign-ins cannot lock OpenAVC out of the device. Fix the cause above, then press Reconnect.";
  }
  if (PERMANENT_OFFLINE_REASONS.has(reason ?? "")) {
    // Stopped early on purpose, not out of attempts: the detail line above
    // already names what to change. Saying "gave up" here would read as "we
    // tried everything", sending the integrator hunting a network problem
    // that isn't there.
    return "Not retrying: this won't clear on its own. Fix the cause above, then press Reconnect.";
  }
  // Only a fault someone has to clear reaches `failed` now, and both branches
  // above name one. A network fault never lands here: it retries for as long
  // as the device is in the project.
  return "Not retrying. Fix the cause above, then press Reconnect.";
}

/** Faults no driver change fixes: the address does not answer, a setting or a
 *  program on this computer, the device's own key or settings, the bridge it
 *  goes through, a driver with no simulator while simulation runs, or a login
 *  not saved on this computer. */
const OFFLINE_NOT_THE_DRIVER = new Set([
  "unreachable",
  "tls_cert_untrusted",
  "host_key_rejected",
  "client_missing",
  "invalid_config",
  "bridge_offline",
  "no_simulator",
  "login_missing",
]);

/** Report a problem on the Offline banner: a fault the driver could have
 *  caused. An unknown code, or none, might be the driver too. */
export function reportableOffline(reason: string | undefined): boolean {
  return !OFFLINE_NOT_THE_DRIVER.has(reason ?? "");
}
