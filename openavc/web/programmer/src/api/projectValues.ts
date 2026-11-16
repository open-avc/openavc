/**
 * A project value as the Builder shows it, read through the project schema's
 * own table (projectValues.gen.ts, generated from the models in
 * core/project_loader.py), the way the panel reads it
 * (web/panel/modules/project_values.js).
 */
import { PROJECT_VALUES, type ProjectValue } from "./projectValues.gen";

/** A blank another setting decides: that setting's value -> what it draws. */
interface BlankRule<T> {
  readonly blank_from: Readonly<Record<string, Readonly<Record<string, T>>>>;
  readonly otherwise: string;
}

/**
 * What a blank value draws when the setting the table's `blank_from` says
 * decides it holds `word`: the value named for that word, else for the word
 * `otherwise` names (a blank one included). Null is a value that draws none.
 */
export function blankDrawn<T>(rule: BlankRule<T>, word: unknown): T {
  const [byValue] = Object.values(rule.blank_from);
  const named = typeof word === "string" && Object.prototype.hasOwnProperty.call(byValue, word);
  return byValue[named ? word : rule.otherwise];
}

const ELEMENT_VALUES: Readonly<Record<string, ProjectValue>> = PROJECT_VALUES.UIElement;

/**
 * Whether a value in a control's `style` is blank, the control drawing its own
 * size: none written, or a 0 the table reads as blank (`zero_is_blank`: a
 * crosspoint cell of 0 is the cell that fits the box). The panel reads the
 * same rule (`styleSize`), and the AI's floor the schema's (`style_is_blank`).
 */
export function styleIsBlank(key: string, value: unknown): boolean {
  if (value == null) return true;
  const path = `style.${key}`;
  const declared = Object.prototype.hasOwnProperty.call(ELEMENT_VALUES, path);
  return value === 0 && declared && ELEMENT_VALUES[path].zero_is_blank === true;
}
