/**
 * What the Code view says about a Python driver after Save, Save & Reload and
 * Reload: why the file will not load, or what the driver check found in it
 * (the rules `python -m openavc.drivers.check` holds it to).
 */
import type { SaveDriverSourceResult } from "../../api/driverClient";
import { showError, showLastingWarning, showSuccess } from "../../store/toastStore";

/** The driver check's problems: how many, then each on its own line. */
export function problemsToFix(issues: string[]): string {
  const count = issues.length;
  const heading = `The driver check found ${count} problem${count === 1 ? "" : "s"} to fix:`;
  return [heading, ...issues.map((issue) => `• ${issue}`)].join("\n");
}

/** What a plain Save says about the file it kept, or null when nothing is wrong. */
export function keptMessage(res: SaveDriverSourceResult): string | null {
  if (res.syntax_error) return `Saved, but this driver will not load: ${res.syntax_error}`;
  if (res.issues?.length) return `Saved.\n${problemsToFix(res.issues)}`;
  return null;
}

/** Why Save & Reload wrote nothing. */
export function refusedMessage(res: SaveDriverSourceResult): string {
  if (res.issues?.length) {
    return `Not saved. The file on disk is unchanged and still loads.\n${problemsToFix(res.issues)}`;
  }
  return `Not saved. ${res.error}. The file on disk is unchanged and still loads.`;
}

/** What a reload that loaded the driver says, and whether it names problems. */
export function reloadedMessage(
  devicesReconnected: number,
  notices: string[] | undefined
): { text: string; problems: boolean } {
  const reloaded = devicesReconnected > 0
    ? `Driver reloaded, ${devicesReconnected} device(s) reconnected`
    : "Driver reloaded";
  if (!notices?.length) return { text: reloaded, problems: false };
  return { text: `${reloaded}.\n${problemsToFix(notices)}`, problems: true };
}

/** Say what a plain Save kept: an error for a file that will not load, a
 *  lasting warning listing what the driver check found, else nothing. */
export function announceKept(res: SaveDriverSourceResult): void {
  const kept = keptMessage(res);
  if (kept) (res.syntax_error ? showError : showLastingWarning)(kept);
}

/** Say a reload loaded the driver: a passing success, or a lasting warning
 *  listing what the driver check found in it. */
export function announceReloaded(devicesReconnected: number, notices: string[] | undefined): void {
  const reloaded = reloadedMessage(devicesReconnected, notices);
  (reloaded.problems ? showLastingWarning : showSuccess)(reloaded.text);
}
