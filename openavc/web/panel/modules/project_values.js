/**
 * A project value as the panel draws it, read through the project schema's
 * own table (project_values.gen.js, generated from the models in
 * core/project_loader.py). The panel draws two kinds of project: a saved one,
 * which passed the loader, and the Builder's unsaved draft
 * (modules/builder_link.js), which never did. Both are read here, so a draft
 * draws what saving it would.
 *
 * A number the table bounds is held to its range (the nearer end for one
 * outside it) and rounded where the schema takes whole numbers only.
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
