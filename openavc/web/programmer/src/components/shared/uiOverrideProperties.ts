/**
 * What a macro or script can override on a panel element at runtime.
 *
 * Writing `ui.<element id>.<property>` changes that control on every panel
 * showing it, live, and clearing the key puts the element back the way it was
 * authored. The panel reads exactly these six and nothing else.
 *
 * MIRRORS `OVERRIDES` in `openavc/web/panel/modules/overrides.js`. Pinned by
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
  /** The element types this override applies to, when it is not every
   *  element. A picker only offers the key on those types.
   *  适用元素类型（非全元素时）。选择器只对这些类型提供该键。 */
  appliesTo?: readonly string[];
}

export const UI_OVERRIDE_PROPERTIES: readonly UiOverrideProperty[] = [
  {
    name: "label",
    label: "Label",
    type: "string",
    hint: "The words on the control, or its caption, replacing the ones it was given.",
  },
  {
    name: "visible",
    label: "Visible",
    type: "boolean",
    hint: "Show or hide the control. No hides it.",
  },
  {
    name: "bg_color",
    label: "Background color",
    type: "string",
    hint: "A CSS color, e.g. #e67e22.",
  },
  {
    name: "text_color",
    label: "Text color",
    type: "string",
    hint: "A CSS color, e.g. #ffffff.",
  },
  {
    name: "opacity",
    label: "Opacity",
    type: "number",
    hint: "0 to 1. Below 1 fades the control.",
  },
  {
    name: "url",
    label: "URL",
    type: "string",
    hint: "Web Page elements: switches the page the frame shows while the key is set. Clearing it puts the element's own URL back.",
    appliesTo: ["web"],
  },
] as const;

/** The value type a `ui.<elementId>.<property>` key takes, or undefined for a
 *  key that is not a control override. A picker gives a boolean one Yes / No:
 *  the panel hides a control only on `false`, never on the word "No". */
export function uiOverrideType(key: unknown): UiOverrideProperty["type"] | undefined {
  if (typeof key !== "string" || !key.startsWith("ui.")) return undefined;
  const name = key.slice(key.lastIndexOf(".") + 1);
  return UI_OVERRIDE_PROPERTIES.find((p) => p.name === name)?.type;
}

/** `ui.<elementId>.<property>` for every property, for one element. */
export function uiOverrideKeys(elementId: string): string[] {
  return UI_OVERRIDE_PROPERTIES.map((p) => `ui.${elementId}.${p.name}`);
}
