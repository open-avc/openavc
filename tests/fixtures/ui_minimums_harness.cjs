/*
 * Loads the generated control-minimum table as the Programmer IDE would.
 *
 * The table is generated from Python and byte-compared by a Python test, which
 * proves the CONTENT is current. It does not prove the file is valid, loadable
 * TypeScript -- nothing imports it until the Builder does, so a broken emit
 * would sit undetected until then. This bundles it with esbuild and reads the
 * real exported values back out.
 *
 * Invoked as: node ui_minimums_harness.cjs <abs path to uiMinimums.gen.ts>
 * with cwd set so `require('esbuild')` resolves via NODE_PATH.
 */
const path = require('path');
const esbuild = require('esbuild');

const target = process.argv[2];

const built = esbuild.buildSync({
    entryPoints: [target],
    bundle: true,
    format: 'cjs',
    write: false,
    platform: 'node',
    logLevel: 'silent',
});

const code = built.outputFiles[0].text;
const module_ = { exports: {} };
new Function('module', 'exports', 'require', code)(module_, module_.exports, require);
const api = module_.exports;

const results = {};
const record = (name, fn) => {
    try { results[name] = { ok: true, value: fn() }; }
    catch (e) { results[name] = { ok: false, error: String(e) }; }
};

record('exports_the_table', () => Object.keys(api.CONTROL_MINIMUMS).sort());
record('exports_the_type_list', () => api.TYPES_WITH_MINIMUMS.slice().sort());
record('reference_screen', () => api.UI_REFERENCE);
record('rem_base', () => api.REM_BASE_PX);

// A representative row of each shape: a plain constant, one that scales with an
// authored internal, and the one that grows when it draws a caption.
record('fader_row', () => api.CONTROL_MINIMUMS.fader);
record('slider_scaling', () => api.CONTROL_MINIMUMS.slider.scalesWith);
record('status_led_caption_bonus', () => api.CONTROL_MINIMUMS.status_led.captionWidthBonusPx);
record('matrix_does_not_scale', () => api.CONTROL_MINIMUMS.matrix.scalesWith);

// Every row must carry provenance -- a number nobody can trace is one nobody
// can safely change.
record('every_internal_has_a_source', () => {
    const missing = [];
    for (const [type, rule] of Object.entries(api.CONTROL_MINIMUMS)) {
        for (const i of rule.internals) if (!i.source) missing.push(`${type}:${i.part}`);
        if (rule.scalesWith && !rule.scalesWith.source) missing.push(`${type}:scalesWith`);
    }
    return missing;
});

process.stdout.write(JSON.stringify(results));
