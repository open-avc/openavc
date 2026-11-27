/**
 * How a control's id is spelled: a lowercase letter, then lowercase letters,
 * digits and underscores. The rule is the platform's
 * (`openavc/ui/page_structure.py`), served with its sentence in the generated
 * table (`ELEMENT_ID_RULE`), and never copied here: the AI's create doors
 * refuse an id that breaks it and Validate reports one a project already
 * holds, so the Builder judges it alike. A rename asks it
 * (`validateElementId` in projectRewrites.ts), and every id the Builder makes
 * is built on a stem that fits it (`elementIdStem`): a control off the palette
 * (`generateId` in newElement.ts, also a duplicate, a paste, a promote and a
 * demote under a taken id) and a page copy's controls (`duplicatePage`).
 */
import { ELEMENT_ID_RULE } from "../../api/projectValues.gen";

const ELEMENT_ID = new RegExp(ELEMENT_ID_RULE.pattern);

/** Whether `id` is spelled as a control's id may be. */
export function isElementId(id: string): boolean {
  return ELEMENT_ID.test(id);
}

/** The stem a made id is built on when what it is named after has nothing
 *  that fits. */
const FALLBACK_STEM = "control";

/**
 * `base` made to fit the rule, so `<stem>_<n>` does: lowercased, each run of
 * anything but a letter, a digit or an underscore one underscore, and what
 * comes before the first letter dropped. A plugin element's palette spelling
 * `plugin:acme_tiles:status_tile` is `plugin_acme_tiles_status_tile`; a base
 * that already fits is itself.
 */
export function elementIdStem(base: string): string {
  const stem = base.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^[^a-z]+/, "");
  return stem || FALLBACK_STEM;
}
