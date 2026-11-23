import { openDevice } from "../../components/shared/driverInUse";

/**
 * The setup run this tab has open, kept while it runs so the wizard comes
 * back when the page does. A restart on a system with a Programmer password
 * signs the tab out and takes the page, wizard and all, with it; after the
 * next sign-in (or a reload) the device is opened again and its wizard asks
 * the server how the run ended (`GET /api/devices/{id}/setup-runs/{run_id}`,
 * which answers a run from before a restart as interrupted). sessionStorage,
 * so it is this tab's only and ends with the tab.
 */

const KEY = "openavc.setup.open_run";

export interface OpenSetupRun {
  deviceId: string;
  actionId: string;
  runId: string;
}

function read(): OpenSetupRun | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (!value || typeof value !== "object") return null;
    const { deviceId, actionId, runId } = value as Record<string, unknown>;
    if (typeof deviceId !== "string" || typeof actionId !== "string" || typeof runId !== "string") {
      return null;
    }
    return { deviceId, actionId, runId };
  } catch {
    return null;
  }
}

function forget(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // A storage the browser refuses: nothing was kept.
  }
}

/** Keep the run the wizard started, until it ends or the wizard is closed. */
export function rememberOpenSetupRun(run: OpenSetupRun): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(run));
  } catch {
    // A storage the browser refuses: the wizard still follows the run while
    // the page lasts.
  }
}

/** The run ended or its wizard was closed: stop keeping it, if it is the one kept. */
export function forgetOpenSetupRun(runId: string): void {
  if (read()?.runId === runId) forget();
}

let reopened: OpenSetupRun | null = null;

/**
 * Once the IDE is signed in: open the device of the run this tab kept, for
 * its wizard to take (`takeReopenedRun`). Nothing kept, nothing opened.
 */
export function reopenSetupRun(): void {
  const run = read();
  forget();
  if (!run) return;
  reopened = run;
  openDevice(run.deviceId);
}

/**
 * The reopened run, for the device page of `deviceId`, handed over once:
 * kept until that page has the run's action (`hasAction`), which arrives
 * with the device's driver details.
 */
export function takeReopenedRun(
  deviceId: string,
  hasAction: (actionId: string) => boolean = () => true,
): OpenSetupRun | null {
  if (!reopened || reopened.deviceId !== deviceId || !hasAction(reopened.actionId)) return null;
  const run = reopened;
  reopened = null;
  return run;
}
