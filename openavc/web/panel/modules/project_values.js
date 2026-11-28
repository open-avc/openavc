/**
 * A project value as the panel draws it, read through the project schema's
 * own table (project_values.gen.js, generated from the models in
 * core/project_loader.py). The panel draws two kinds of project: a saved one,
 * which passed the loader, and the Builder's unsaved draft
 * (modules/builder_link.js), which never did. Both are read here, so a draft
 * draws what saving it would: a control's shown decimals, the page transition
 * and entry stagger, the idle page, the failure message switch, the theme a
 * project with no theme id draws, the format a clock with none draws and
 * whether a 0 crosspoint cell is one at all are read only here, and so are
 * what the lock screen's PIN box takes and a press's hold and repeat timings.
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

/**
 * The word a fixed-choice `field` of `model` reads as: `raw` where the table
 * lists it, else the one its `otherwise` names (null: as nothing). A word a
 * later version saved stays in the project as written.
 */
export function chosenWord(model, field, raw) {
    const { words, otherwise } = PROJECT_VALUES[model][field];
    return words.includes(raw) ? raw : otherwise;
}

/** The most characters text in `field` of `model` takes; undefined for no limit. */
export function longestText(model, field) {
    return PROJECT_VALUES[model][field].max_length;
}

/**
 * `text` as `field` of `model` takes it while it is typed: only the digits 0
 * to 9 where the table says the field takes digits, then no more than its
 * longest.
 */
export function typedText(model, field, text) {
    const { max_length: longest, digits } = PROJECT_VALUES[model][field];
    const kept = digits ? String(text).replace(/[^0-9]/g, '') : String(text);
    return longest === undefined ? kept : kept.slice(0, longest);
}

/**
 * A size in a control's `style` as the panel draws it: the value as written,
 * or null where the control sizes itself. A 0 is null where the table reads a
 * 0 as blank (`zero_is_blank`: a crosspoint cell of 0 is the cell that fits
 * the box), and so is a value that is no size at all (absent, empty, false,
 * NaN), as the panel has always drawn one.
 */
export function styleSize(field, raw) {
    if (raw === 0) return PROJECT_VALUES.UIElement[`style.${field}`]?.zero_is_blank ? null : raw;
    return raw || null;
}

/**
 * THE theme a panel's settings draw: its `theme_id`, else the theme the
 * table's `blank_from` names for its light or dark `theme`, a word it does not
 * name reading as the default `theme`'s. The project keeps a blank id blank;
 * this is only what it draws.
 */
export function themeIdOf(settings) {
    return panelSetting(settings, 'theme_id') || blankDrawn('UISettings', 'theme_id', settings?.theme);
}

/**
 * What a blank `field` of `model` draws when the setting the table's
 * `blank_from` says decides it holds `word`: the value named for that word,
 * else for the word `otherwise` names (a blank one included). Null is a value
 * that draws none.
 */
export function blankDrawn(model, field, word) {
    const { blank_from: blankFrom, otherwise } = PROJECT_VALUES[model][field];
    const [byValue] = Object.values(blankFrom);
    const named = typeof word === 'string' && Object.prototype.hasOwnProperty.call(byValue, word);
    return byValue[named ? word : otherwise];
}

/**
 * A press's timing in milliseconds (`hold_repeat_ms`, `hold_threshold_ms`,
 * on the press's first action): the one written, or the table's default for
 * a blank one, none written or 0.
 */
export function pressTiming(raw, field) {
    return raw || PROJECT_VALUES.UIElement[`bindings.do.press.${field}`].default;
}
