/** How long a command's `restarts_device_for` takes the device away, read
 *  the way the platform reads it (openavc/drivers/restart_window.py): a
 *  number above 0, rounded up to the whole second and held to the most the
 *  contract allows; anything else is no restart. The range is generated from
 *  the contract (types.gen.ts). `restartWindow.test.ts` runs the platform's
 *  own case table (tests/fixtures/restart_seconds_cases.json).
 *
 *  The Builder holds the field as a number, so text is not read here; the
 *  platform's checker names a value written as text when the driver is
 *  validated or saved. */

import { RESTART_SECONDS_MAX, RESTART_SECONDS_MIN } from "../../api/types";

export { RESTART_SECONDS_MAX, RESTART_SECONDS_MIN };

export function restartWindowSeconds(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return undefined;
  return Math.min(RESTART_SECONDS_MAX, Math.ceil(value));
}
