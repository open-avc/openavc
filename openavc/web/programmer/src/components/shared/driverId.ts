// What a driver may be called, as the IDE's id inputs apply it: lowercase
// letters, digits and underscores. The characters come from the driver
// contract (DRIVER_ID_CHARS, generated from openavc/drivers/spec.py); the
// server holds an id to the same pattern where it is first given
// (openavc/drivers/driver_id.py), and a driver already installed under an id
// outside it keeps that id.

import { DRIVER_ID_CHARS } from "../../api/types.gen";

const DRIVER_ID = new RegExp(`^[${DRIVER_ID_CHARS}]+$`);
const NOT_AN_ID_CHARACTER = new RegExp(`[^${DRIVER_ID_CHARS}]`, "g");
const RUN_OF_OTHERS = new RegExp(`[^${DRIVER_ID_CHARS}]+`, "g");

/** True when `text` is an id a new driver may have. */
export function isDriverId(text: unknown): text is string {
  return typeof text === "string" && DRIVER_ID.test(text);
}

/** What an id field keeps of what is typed into it: lowercase, with each
 *  character an id cannot hold left out. */
export function typedDriverId(text: string): string {
  return text.toLowerCase().replace(NOT_AN_ID_CHARACTER, "");
}

/** An id made from other text (what New Driver's ID box holds, an imported
 *  file's id): lowercase, each run of characters an id cannot hold one
 *  underscore, and none at either end. Empty when nothing usable is left. */
export function cleanedDriverId(text: string): string {
  return text.toLowerCase().replace(RUN_OF_OTHERS, "_").replace(/^_+|_+$/g, "");
}
