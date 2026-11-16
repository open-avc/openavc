/**
 * A project value as the panel draws it, read through the project schema's
 * own table (project_values.gen.js, generated from the models in
 * core/project_loader.py). The panel draws two kinds of project: a saved one,
 * which passed the loader, and the Builder's unsaved draft
 * (modules/builder_link.js), which never did. Both are read here, so a draft
 * draws what saving it would: a control's shown decimals, and the page
 * transition and entry stagger, are read only here.
 *
 * A number the table bounds is held to its range (the nearer end for one
 * outside it) and rounded where the schema takes whole numbers only. A blank
 * panel setting is the table's default.
 *
 * Nothing here reads a control's definition: callers hand in the value, so
 * the page review's renderer scan credits each read to the control that makes
 * it.
 */

import { PROJECT_VALUES } from './project_values.gen.js';

/**
 * `raw` as a number inside the range the schema declares for `field` on
 * `model`, or null when it is no number at all (absent, null, words). What
 * null draws is the caller's: each control has its own default.
 */
export function boundedNumber(model, field, raw) {
    if (raw == null) return null;
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    const { min, max, integer } = PROJECT_VALUES[model][field];
    return Math.max(min, Math.min(max, integer ? Math.round(n) : n));
}

/**
 * One panel setting (`ui.settings`) as the panel draws it. Blank is the
 * schema's default: absent, null or empty text; for a number, one that is no
 * number at all, or a 0 where the range starts above 0 (Panel Settings shows
 * that 0 as the default, and the format step that declared the range removed
 * a saved one).
 */
export function panelSetting(settings, field) {
    const spec = PROJECT_VALUES.UISettings[field];
    const raw = settings?.[field];
    if (raw == null || raw === '') return spec.default;
    if (spec.min === undefined) return raw;
    const n = boundedNumber('UISettings', field, raw);
    if (n === null || (Number(raw) === 0 && spec.min > 0)) return spec.default;
    return n;
}
