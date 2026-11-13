/**
 * Which panel method draws each kind of control, and which draws each kind of
 * binding again when the state it reads changes.
 *
 * RENDERERS is asked once for every control a page draws, by the control's
 * `type`; EVALUATORS once for every binding the binding pass draws, by the
 * binding's `type`, which the renderer that registered the binding gave it. A
 * type with no row draws nothing: the panel logs an unknown control and leaves
 * it out, and a binding drawn some other way has no row on purpose (a macro's
 * progress line, `macro_progress`, is drawn by modules/macro_runs.js).
 *
 * A new control type is a row in RENDERERS naming its `render<Type>` method on
 * the panel. A new kind of binding is three things: the renderer that
 * registers it, a row in EVALUATORS naming its `evaluate<Kind>` method, and
 * that method.
 *
 * Each row calls the method on the panel it is handed, when it is asked, so a
 * method replaced on one panel (as tests do) is the one that runs. The tables
 * are Maps, so a type spelled like a name every object has (`constructor`,
 * `__proto__`) finds no row.
 *
 * The page review's mirror test (tests/test_ui_page_review_mirrors.py) runs
 * both tables to learn which method each type reaches, and from that which
 * properties and show slots each control honours. A row calls one method on
 * the panel with what it was handed, and does nothing else. That test is why
 * the tables are a module of their own: it imports this file under Node,
 * where panel.js itself cannot be imported. The server frames' table (FRAMES)
 * has no such reader and stays in panel.js.
 */

export const RENDERERS = new Map([
    ['button', (app, element) => app.renderButton(element)],
    ['label', (app, element) => app.renderLabel(element)],
    ['status_led', (app, element) => app.renderStatusLed(element)],
    ['slider', (app, element) => app.renderSlider(element)],
    ['page_nav', (app, element) => app.renderPageNav(element)],
    ['select', (app, element) => app.renderSelect(element)],
    ['text_input', (app, element) => app.renderTextInput(element)],
    ['image', (app, element) => app.renderImage(element)],
    ['camera_preset', (app, element) => app.renderCameraPreset(element)],
    ['list', (app, element) => app.renderList(element)],
    ['matrix', (app, element) => app.renderMatrix(element)],
    ['gauge', (app, element) => app.renderGauge(element)],
    ['level_meter', (app, element) => app.renderLevelMeter(element)],
    ['fader', (app, element) => app.renderFader(element)],
    ['group', (app, element) => app.renderGroup(element)],
    ['clock', (app, element) => app.renderClock(element)],
    ['keypad', (app, element) => app.renderKeypad(element)],
    ['plugin', (app, element) => app.renderPluginElement(element)],
    ['custom', (app, element) => app.renderCustomElement(element)],
]);

export const EVALUATORS = new Map([
    ['visible_when', (app, b) => app.evaluateVisibleWhen(b)],
    ['feedback', (app, b) => app.evaluateFeedback(b)],
    ['toggle_look', (app, b) => app.evaluateToggleLook(b)],
    ['label_look', (app, b) => app.evaluateLabelLook(b)],
    ['image_look', (app, b) => app.evaluateImageLook(b)],
    ['nav_look', (app, b) => app.evaluateNavLook(b)],
    ['group_look', (app, b) => app.evaluateGroupLook(b)],
    ['text', (app, b) => app.evaluateText(b)],
    ['color', (app, b) => app.evaluateColor(b)],
    ['slider_value', (app, b) => app.evaluateSliderValue(b)],
    ['select_value', (app, b) => app.evaluateSelectValue(b)],
    ['select_look', (app, b) => app.evaluateSelectLook(b)],
    ['text_input_value', (app, b) => app.evaluateTextInputValue(b)],
    ['gauge_value', (app, b) => app.evaluateGaugeValue(b)],
    ['level_meter_value', (app, b) => app.evaluateLevelMeterValue(b)],
    ['fader_value', (app, b) => app.evaluateFaderValue(b)],
    ['matrix_routes', (app, b) => app.evaluateMatrixRoutes(b)],
    ['list_items', (app, b) => app.evaluateListItems(b)],
    ['list_selected', (app, b) => app.evaluateListSelected(b)],
]);
