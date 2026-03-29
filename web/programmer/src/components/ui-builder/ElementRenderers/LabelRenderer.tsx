import type { UIElement } from "../../../api/types";
import type { TextBinding } from "../uiBuilderHelpers";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function LabelRenderer({ element, previewMode, liveState }: Props) {
  let displayText = element.text || "";

  if (previewMode && element.bindings.text) {
    const tb = element.bindings.text as Record<string, unknown>;
    if (tb.key) {
      const value = liveState[tb.key as string];
      if (tb.condition) {
        const condition = tb.condition as { equals?: unknown };
        const isMatch = value === condition.equals;
        displayText = String(isMatch ? tb.text_true || "" : tb.text_false || "");
      } else if (value !== undefined && value !== null) {
        displayText = tb.format
          ? String(tb.format).replace("{value}", String(value))
          : String(value);
      }
    }
  }

  const css = buildElementStyle(element.style, {
    display: "flex",
    alignItems: "center",
    width: "100%",
    height: "100%",
  });

  // Defaults
  if (!element.style.text_color) css.color = "#ffffff";
  if (!element.style.font_weight) css.fontWeight = "normal";
  if (!element.style.text_align) css.textAlign = "left";
  if (!element.style.padding && !element.style.padding_horizontal && !element.style.padding_vertical) {
    css.padding = "4px 8px";
  }

  // Rich text: convert **bold** and *italic* to HTML
  const useRich = !!element.style.white_space;
  let richHtml = "";
  if (useRich) {
    richHtml = displayText
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>");
  }

  return (
    <div style={css}>
      {useRich ? (
        <span dangerouslySetInnerHTML={{ __html: richHtml }} />
      ) : (
        <IconTextLayout
          icon={element.icon}
          iconPosition={element.icon_position}
          iconSize={element.icon_size}
          iconColor={element.icon_color}
        >
          {displayText}
        </IconTextLayout>
      )}
    </div>
  );
}
