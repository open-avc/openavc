/**
 * How a control's id is spelled: a lowercase letter, then lowercase letters,
 * digits and underscores. The rule is the platform's
 * (`openavc/ui/page_structure.py`), served with its sentence in the generated
 * table (`ELEMENT_ID_RULE`), and never copied here: the AI's create doors
 * refuse an id that breaks it and Validate reports one a project already
 * holds, so the Builder judges it alike. A rename asks it
 * (`validateElementId` in projectRewrites.ts).
 */
import { ELEMENT_ID_RULE } from "../../api/projectValues.gen";

const ELEMENT_ID = new RegExp(ELEMENT_ID_RULE.pattern);

/** Whether `id` is spelled as a control's id may be. */
export function isElementId(id: string): boolean {
  return ELEMENT_ID.test(id);
}
