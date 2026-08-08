/**
 * Maps a panel-element `config_schema` field to the form control it renders in
 * the UI Builder Properties panel.
 *
 * Mirrors plugin CONFIG_SCHEMA semantics so a `state_key` / `device_ref` /
 * `macro_ref` field gets a real picker (not a bare text box), and `text` is a
 * multi-line textarea while `string` is a single-line input — matching how
 * PluginConfigForm renders the same types.
 *
 * Pure + exported so the rendering decision is unit-testable.
 */
export type PanelFieldKind =
  | "boolean"
  | "select"
  | "number"
  | "state_key"
  | "macro_ref"
  | "device_ref"
  | "textarea"
  | "text";

export function panelElementFieldKind(field: {
  type?: string;
  options?: unknown;
  options_source?: unknown;
}): PanelFieldKind {
  switch (field.type) {
    case "boolean":
      return "boolean";
    case "select":
      // A select with no options can't render a dropdown — fall back to text.
      return field.options || field.options_source ? "select" : "text";
    case "integer":
    case "float":
      return "number";
    case "state_key":
      return "state_key";
    case "macro_ref":
      return "macro_ref";
    case "device_ref":
      return "device_ref";
    case "text":
      return "textarea";
    case "string":
      return "text";
    default:
      return "text";
  }
}
