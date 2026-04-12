import type { UIElement } from "../../../api/types";

import { getAssetUrl } from "../../../api/restClient";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface MultiStateFeedback {
  key?: string;
  states?: Record<string, Record<string, unknown>>;
  default_state?: string;
  condition?: { equals?: unknown };
  style_active?: Record<string, string>;
  style_inactive?: Record<string, string>;
}

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

function resolveAssetRef(ref: string | undefined): string {
  if (!ref) return "";
  if (ref.startsWith("assets://")) return getAssetUrl(ref.slice("assets://".length));
  return ref;
}

/**
 * ButtonRenderer — mirrors panel.js renderButton().
 * Uses .panel-button from panel-elements.css.
 */
export function ButtonRenderer({ element, previewMode, liveState }: Props) {
  let activeStyle = { ...element.style };
  let activeLabel = element.label || "";
  let activeIcon = element.icon;
  let activeIconColor = element.icon_color;
  let activeButtonImage = element.button_image;

  // Evaluate feedback binding (supports both legacy and multi-state)
  if (previewMode && element.bindings.feedback) {
    const fb = element.bindings.feedback as unknown as MultiStateFeedback;
    if (fb.key) {
      const stateValue = liveState[fb.key];

      if (fb.states) {
        const stateKey = stateValue != null ? String(stateValue) : fb.default_state || "";
        const stateAppearance = fb.states[stateKey] || fb.states[fb.default_state || ""] || {};
        if (stateAppearance.bg_color) activeStyle = { ...activeStyle, bg_color: stateAppearance.bg_color };
        if (stateAppearance.text_color) activeStyle = { ...activeStyle, text_color: stateAppearance.text_color };
        if (stateAppearance.box_shadow) activeStyle = { ...activeStyle, box_shadow: stateAppearance.box_shadow };
        if (stateAppearance.border_color) activeStyle = { ...activeStyle, border_color: stateAppearance.border_color };
        if (stateAppearance.label) activeLabel = String(stateAppearance.label);
        if (stateAppearance.icon) activeIcon = String(stateAppearance.icon);
        if (stateAppearance.icon_color) activeIconColor = String(stateAppearance.icon_color);
        if (stateAppearance.button_image) activeButtonImage = String(stateAppearance.button_image);
      } else {
        const isActive = stateValue == fb.condition?.equals;
        if (isActive && fb.style_active) {
          activeStyle = { ...activeStyle, ...fb.style_active };
        } else if (!isActive && fb.style_inactive) {
          activeStyle = { ...activeStyle, ...fb.style_inactive };
        }
      }
    }
  }

  // Per-element style overrides
  const overrides = buildElementStyle(activeStyle);

  // Display mode: image buttons
  const displayMode = element.display_mode || "text";
  const imgSrc = resolveAssetRef(activeButtonImage);
  if ((displayMode === "image" || displayMode === "image_text") && imgSrc) {
    overrides.backgroundImage = `url(${imgSrc})`;
    overrides.backgroundSize = element.image_fit || "cover";
    overrides.backgroundPosition = "center";
    overrides.backgroundRepeat = "no-repeat";
    if (displayMode === "image_text") {
      overrides.textShadow = "0 1px 3px rgba(0,0,0,0.8)";
    }
  }

  const showLabel = displayMode !== "image" && displayMode !== "icon_only";

  return (
    <div
      className="panel-element panel-button"
      style={{ width: "100%", height: "100%", ...overrides }}
    >
      <IconTextLayout
        icon={activeIcon}
        iconPosition={displayMode === "icon_only" ? "center" : element.icon_position}
        iconSize={element.icon_size}
        iconColor={activeIconColor}
      >
        {showLabel ? activeLabel : ""}
      </IconTextLayout>
    </div>
  );
}
