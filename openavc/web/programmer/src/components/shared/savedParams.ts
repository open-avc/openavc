// A secret command parameter saved on this system (a stream key, a pairing
// PIN in a macro step or a control's action). The project holds a marker
// naming the saved value in its place, and the browser never receives the
// value: the parameter's box (SavedParamField) sends a typed value on its own
// and puts the marker it gets back in the step. Mirrors
// openavc/core/saved_params.py (tests/test_saved_login_marker_parity.py).

import { isSecretSetting } from "./secretSetting";

/** How a saved parameter's marker starts; 16 hex digits and ">" follow. */
export const SAVED_PARAM_PREFIX = "<saved on this system #";

const MARKER = /^<saved on this system #([0-9a-f]{16})>$/;

/** The name a saved parameter's marker carries, or null for any other value. */
export function savedParamName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const found = MARKER.exec(value);
  return found ? found[1] : null;
}

/** Whether `value` is a saved parameter's marker. */
export function isSavedParam(value: unknown): boolean {
  return savedParamName(value) !== null;
}

/** Whether a param on a surface that saves its params (`savedKey` given: a
 *  macro step, a control's action) is drawn as a value saved on this system:
 *  a secret, empty or already saved. One already in the clear keeps its box. */
export function drawsAsSaved(savedKey: string | undefined, def: object, value: string): savedKey is string {
  return !!savedKey && isSecretSetting(savedKey, def) && (value === "" || isSavedParam(value));
}
