/**
 * Serial line settings as the Programmer reads them. THE table is the driver
 * contract's (`SERIAL_LINE_SETTINGS` in openavc/drivers/serial_line.py,
 * generated into types.gen.ts), and `readLineSetting` is its reading
 * (`read_line_setting`), held to the same answers over one corpus by
 * tests/test_serial_line_settings.py. The Builder's Connection tab and the
 * device form ask `lineOptions` what a picker offers and which option a
 * written value is, so `Even`, `even` and `E` show as one parity there too.
 */
import {
  SERIAL_LINE_SETTINGS,
  type SerialLineChoice,
  type SerialLineKey,
} from "../../api/types.gen";

export type { SerialLineKey };
export type LineValue = string | number;

// Decimal text, digits 0-9 only, and the spaces trimmed before text is read:
// the same two rules as serial_line.py's _DECIMAL_TEXT and _TRIMMED.
const DECIMAL_TEXT = /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/;
const SURROUNDING_SPACE = /^[ \t\r\n]+|[ \t\r\n]+$/g;

function trimmed(text: string): string {
  return text.replace(SURROUNDING_SPACE, "");
}

/** A number, or decimal text, as a number; null for anything else, true and
 *  false included. */
function lineNumber(value: unknown): number | null {
  let number: number;
  if (typeof value === "number") {
    number = value;
  } else if (typeof value === "string") {
    const text = trimmed(value);
    if (!DECIMAL_TEXT.test(text)) return null;
    number = Number(text);
  } else {
    return null;
  }
  return Number.isFinite(number) ? number : null;
}

function spellingsOf(choice: SerialLineChoice): string[] {
  const words = [String(choice.value), ...(choice.spellings ?? [])];
  if (choice.label) words.push(choice.label);
  return words.map((word) => word.toLowerCase());
}

function choiceOf(choices: readonly SerialLineChoice[], value: unknown): LineValue | null {
  if (typeof choices[0].value === "string") {
    if (typeof value !== "string") return null;
    const word = trimmed(value).toLowerCase();
    return choices.find((choice) => spellingsOf(choice).includes(word))?.value ?? null;
  }
  const number = lineNumber(value);
  return choices.find((choice) => choice.value === number)?.value ?? null;
}

/** THE reading of a serial line setting: the value the port opens with, or
 *  null when `value` has none. Null and blank have none; an absent setting
 *  is the table's `default`. */
export function readLineSetting(key: SerialLineKey, value: unknown): LineValue | null {
  const setting = SERIAL_LINE_SETTINGS[key];
  if (setting.choices) return choiceOf(setting.choices, value);
  return baudRate(value);
}

/** A baud rate: any whole number above zero. */
function baudRate(value: unknown): number | null {
  const number = lineNumber(value);
  const whole = number !== null && Number.isInteger(number);
  return whole && number >= 1 ? number : null;
}

/** The value a setting has when nothing is written. */
export function lineDefault(key: SerialLineKey): LineValue {
  return SERIAL_LINE_SETTINGS[key].default;
}

export interface LineOption {
  value: string;
  label: string;
}

/** What a picker for `key` offers, and which option `written` is.
 *
 *  The options are the table's choices, or for the baud rate the rates it
 *  offers plus the one written when that is another whole number. A written
 *  value with no reading is kept as an option of its own, marked, so the form
 *  shows what is saved and choosing another replaces it. Nothing written
 *  selects the table's default. */
export function lineOptions(
  key: SerialLineKey,
  written: unknown,
): { selected: string; options: LineOption[] } {
  const setting = SERIAL_LINE_SETTINGS[key];
  const options: LineOption[] = setting.choices
    ? setting.choices.map((c) => ({ value: String(c.value), label: c.label ?? String(c.value) }))
    : (setting.offered ?? []).map((rate) => ({ value: String(rate), label: String(rate) }));
  if (written == null || written === "") {
    return { selected: String(setting.default), options };
  }
  const read = readLineSetting(key, written);
  if (read === null) {
    const shown = String(written);
    return { selected: shown, options: [...options, { value: shown, label: `${shown} (choose another)` }] };
  }
  const selected = String(read);
  if (!options.some((option) => option.value === selected)) {
    options.push({ value: selected, label: selected });
    options.sort((a, b) => Number(a.value) - Number(b.value));
  }
  return { selected, options };
}
