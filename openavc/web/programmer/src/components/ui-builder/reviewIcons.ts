/**
 * An icon name the panel draws nothing for.
 *
 * The panel draws a control's icon by its name from its sprite (icons.svg),
 * draws an old Lucide name as the sprite id it has now, and draws an uploaded
 * picture (`assets://`) as it is (web/panel/modules/words.js). A name in
 * neither list draws nothing, while this Builder may still preview it: Lucide's
 * React icons answer to more names than the sprite has. Both lists are written
 * by scripts/build-icons.mjs (spriteIconNames.ts).
 *
 * What is read is what `drawIcon` draws: the style's icon over the control's
 * own, and each state's icon in a `show.look`, in both of the look's forms,
 * where the type draws each. A value that is not text names no icon.
 *
 * Mirrors openavc/ui/review_icons.py and `icon_findings` in page_review.py.
 */

import type { UIElement } from "../../api/types";
import { HONORED_PROPERTIES, STATE_ICON_TYPES } from "../../api/uiBindingReach.gen";
import { isPlainObject, own } from "./authoredValues";
import { showOf, type ReviewFinding } from "./reviewFinding";
import { OLD_ICON_NAMES, SPRITE_ICON_NAMES } from "./spriteIconNames";

const UPLOADED_ICON = "assets://";

const PANEL_ICON_NAMES = new Set([...SPRITE_ICON_NAMES, ...OLD_ICON_NAMES]);

const REMEDY = "Use one the icon picker offers.";

/** Whether the panel draws nothing for an icon written as `icon`: text that
 *  names neither a sprite icon nor an old name for one, nor an uploaded picture. */
export function drawsNothing(icon: unknown): boolean {
  return (
    typeof icon === "string" &&
    icon !== "" &&
    !icon.startsWith(UPLOADED_ICON) &&
    !PANEL_ICON_NAMES.has(icon)
  );
}

/** The icon `drawIcon` draws for the control itself: the style's, else its own. */
function drawnIcon(el: UIElement): unknown {
  const style: unknown = el.style;
  const styleIcon = isPlainObject(style) ? style.icon : undefined;
  return styleIcon || el.icon;
}

/** Each state of a look that names an icon, with it: the `states` map's, else
 *  the on/off form's two halves. */
function stateIcons(look: Record<string, unknown>): [string, unknown][] {
  const states = look.states;
  const sides: [string, unknown][] = isPlainObject(states)
    ? Object.entries(states).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    : [["active", look.style_active], ["inactive", look.style_inactive]];
  return sides
    .filter(([, spec]) => isPlainObject(spec) && !!spec.icon)
    .map(([name, spec]) => [name, (spec as Record<string, unknown>).icon]);
}

/** An icon name the panel draws nothing for, where this type draws it: its own
 *  icon, and a look's per-state icons. */
export function iconFindings(el: UIElement): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  const ownIcon = (own(HONORED_PROPERTIES, el.type) ?? []).includes("icon") ? drawnIcon(el) : null;
  if (drawsNothing(ownIcon)) {
    findings.push({
      elementId: el.id,
      kind: "unknown_icon",
      message: `${el.id} (${el.type}): the panel has no icon named '${ownIcon}', so it draws none. ${REMEDY}`,
      key: `unknown_icon|${el.id}|icon`,
    });
  }
  const look = STATE_ICON_TYPES.includes(el.type) ? showOf(el)?.look : null;
  const unknown = (isPlainObject(look) ? stateIcons(look) : [])
    .filter(([, icon]) => drawsNothing(icon))
    .map(([name, icon]) => `${name} ('${icon}')`);
  if (unknown.length) {
    findings.push({
      elementId: el.id,
      kind: "unknown_icon",
      message:
        `${el.id} (${el.type}): show.look names an icon the panel does not have for ` +
        `${unknown.join(", ")}, and draws none there. ${REMEDY}`,
      key: `unknown_icon|${el.id}|look.icon`,
    });
  }
  return findings;
}
