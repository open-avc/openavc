/**
 * What a macro or script can override on a panel element at runtime.
 *
 * Writing `ui.<element id>.<property>` changes that control on every panel
 * showing it, live, and clearing the key puts the element back the way it was
 * authored. The panel reads exactly these five and nothing else.
 *
 * MIRRORS `evaluateUiOverrides` in `openavc/web/panel/panel.js`. Pinned by
 * `tests/test_ui_override_properties_mirror.py`, because a property offered
 * here that the panel does not read is a control that silently does nothing.
 */
export interface UiOverrideProperty {
  /** The key suffix: `ui.<element id>.<name>`. */
  name: string;
  /** What the author picks it by. */
  label: string;
  /** The shape of the value, shown as the row's type badge. */
  type: "string" | "boolean" | "number";
  hint: string;
}

export const UI_OVERRIDE_PROPERTIES: readonly UiOverrideProperty[] = [
  {
    name: "label",
    label: "Label",
    type: "string",
    hint: "The words on the control, replacing the one it was given.",
  },
  {
    name: "visible",
    label: "Visible",
    type: "boolean",
    hint: "Show or hide the control. false hides it.",
  },
  {
    name: "bg_color",
    label: "Background colour",
    type: "string",
    hint: "A CSS colour, e.g. #e67e22.",
  },
  {
    name: "text_color",
    label: "Text colour",
    type: "string",
    hint: "A CSS colour, e.g. #ffffff.",
  },
  {
    name: "opacity",
    label: "Opacity",
    type: "number",
    hint: "0 to 1. Below 1 fades the control.",
  },
] as const;

/** `ui.<elementId>.<property>` for every property, for one element. */
export function uiOverrideKeys(elementId: string): string[] {
  return UI_OVERRIDE_PROPERTIES.map((p) => `ui.${elementId}.${p.name}`);
}
