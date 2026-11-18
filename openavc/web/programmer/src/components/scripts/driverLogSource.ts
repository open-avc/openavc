/**
 * Which log lines are one Python driver's own in the Code view's console.
 */
import type { PythonDriverInfo } from "../../api/types";

/** The logger a Python driver's own lines arrive under (one that logs through
 *  `getLogger(__name__)`): its module, which the server names after the
 *  driver's file, not its id, so a driver kept under another file name is
 *  matched by that name. A driver not in the list is matched by its id. */
export function driverLogSource(drivers: PythonDriverInfo[], driverId: string): string {
  const filename = drivers.find((driver) => driver.id === driverId)?.filename;
  const stem = filename ? filename.replace(/\.py$/, "") : driverId;
  return `openavc_driver_${stem}`;
}
