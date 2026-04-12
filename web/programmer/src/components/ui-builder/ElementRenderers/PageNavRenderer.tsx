import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * PageNavRenderer — mirrors panel.js renderPageNav().
 * Uses .panel-page-nav from panel-elements.css.
 */
export function PageNavRenderer({ element }: Props) {
  // Per-element style overrides
  const overrides = buildElementStyle(element.style);

  return (
    <div
      className="panel-element panel-page-nav"
      style={{ width: "100%", height: "100%", ...overrides }}
    >
      <IconTextLayout
        icon={element.icon}
        iconPosition={element.icon_position}
        iconSize={element.icon_size}
        iconColor={element.icon_color}
      >
        {element.label || element.target_page || "\u2192"}
      </IconTextLayout>
    </div>
  );
}
