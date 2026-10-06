/**
 * THE words a true / false value reads as in the Programmer: Yes and No.
 *
 * Every place that shows or picks a boolean asks here: a device setting and
 * its default, a state value, a variable, a command parameter, a condition, a
 * trigger, a macro step's value, a Driver Builder default. The stored value
 * never changes: a picker's options keep the values "true" / "false", and code
 * (YAML, scripts, the API) keeps true / false. Words an author wrote for a
 * value, such as a monitor's state labels or a binding's value map, are shown
 * instead of these.
 *
 * Yes / No rather than On / Off because it reads right on both kinds of
 * boolean: a feature that is switched (Auto Attenuation: Yes) and a condition
 * that holds (Connected: Yes), where On / Off does not (Connected: On). It is
 * also what the Dashboard tiles and the cloud alerts already call a bare
 * boolean (`openavc/core/monitors.py`, mirrored in `api/monitorHelpers.ts`).
 */

export const YES = "Yes";
export const NO = "No";

/** One row of a boolean picker: the stored value and its word. */
export interface BooleanOption {
  value: "true" | "false";
  label: string;
}

/** A boolean picker's rows, in the order every picker lists them. */
export const BOOLEAN_OPTIONS: readonly BooleanOption[] = [
  { value: "true", label: YES },
  { value: "false", label: NO },
];

/** The word for a boolean. */
export function booleanWord(value: boolean): string {
  return value ? YES : NO;
}

/**
 * The word for a value that spells a boolean: a real true / false, or the
 * text "true" / "false" in any case (how a form, a project file or a map key
 * holds one). Null for anything else, so a caller that knows the field is a
 * boolean can fall back to the value as it is.
 */
export function booleanWordFor(value: unknown): string | null {
  if (typeof value === "boolean") return booleanWord(value);
  if (typeof value === "string") {
    const lowered = value.trim().toLowerCase();
    if (lowered === "true") return YES;
    if (lowered === "false") return NO;
  }
  return null;
}

/**
 * A value as a readout shows it: a boolean as Yes / No, no value as `empty`,
 * anything else as its text. Only a real boolean becomes a word: the text
 * "true" from a device that reports text is shown as it arrived.
 */
export function valueText(value: unknown, empty = ""): string {
  if (value === null || value === undefined) return empty;
  if (typeof value === "boolean") return booleanWord(value);
  return String(value);
}

/**
 * A value the way a macro step or trigger summary writes it: a boolean as
 * Yes / No, text in quotes so a space or an empty value can be seen, a number
 * as it is.
 */
export function quotedValueText(value: unknown): string {
  if (typeof value === "boolean") return booleanWord(value);
  return JSON.stringify(value) ?? String(value);
}
