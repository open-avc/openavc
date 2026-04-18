import type { CSSProperties } from "react";
import type { UIElement } from "../../../api/types";

import { getAssetUrl } from "../../../api/restClient";
import { buildElementStyle, applyImageEffectStyles } from "./styleHelpers";
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

  // Evaluate feedback binding (supports both legacy and multi-state).
  // In preview mode we use live state; in edit mode we show the default state so
  // the canvas reflects what the user configured in the TintSourceStrip.
  if (element.bindings.feedback) {
    const fb = element.bindings.feedback as unknown as MultiStateFeedback;
    const hasStates = !!fb.states;
    const hasLegacy = !!(fb.style_active || fb.style_inactive || fb.condition);
    if (hasStates || hasLegacy) {
      const stateValue = previewMode && fb.key ? liveState[fb.key] : undefined;

      if (hasStates) {
        const stateKey = stateValue != null ? String(stateValue) : fb.default_state || "";
        const stateAppearance = fb.states![stateKey] || fb.states![fb.default_state || ""] || {};
        if (stateAppearance.bg_color) activeStyle = { ...activeStyle, bg_color: stateAppearance.bg_color };
        if (stateAppearance.text_color) activeStyle = { ...activeStyle, text_color: stateAppearance.text_color };
        if (stateAppearance.box_shadow) activeStyle = { ...activeStyle, box_shadow: stateAppearance.box_shadow };
        if (stateAppearance.border_color) activeStyle = { ...activeStyle, border_color: stateAppearance.border_color };
        if (stateAppearance.label) activeLabel = String(stateAppearance.label);
        if (stateAppearance.icon) activeIcon = String(stateAppearance.icon);
        if (stateAppearance.icon_color) activeIconColor = String(stateAppearance.icon_color);
        if (stateAppearance.button_image) activeButtonImage = String(stateAppearance.button_image);
      } else if (hasLegacy) {
        // In edit mode, show inactive (base) state; in preview, evaluate condition.
        const isActive = previewMode && stateValue != null && stateValue == fb.condition?.equals;
        const appliedFb = isActive ? fb.style_active : fb.style_inactive;
        if (appliedFb) {
          activeStyle = { ...activeStyle, ...appliedFb };
          if (appliedFb.icon) activeIcon = String(appliedFb.icon);
          if (appliedFb.icon_color) activeIconColor = String(appliedFb.icon_color);
          if (appliedFb.button_image) activeButtonImage = String(appliedFb.button_image);
        }
      }
    }
  }

  let buttonStyle: CSSProperties = { ...buildElementStyle(activeStyle) };
  const displayMode = element.display_mode || "text";
  const imgSrc = resolveAssetRef(activeButtonImage);
  const showImage = (displayMode === "image" || displayMode === "image_text") && !!imgSrc;

  // Frameless must be applied BEFORE image effect so the subsequent backgroundImage
  // assignment wins over any chrome-clearing. Use longhands only (no `background`
  // shorthand) so later backgroundImage assignments aren't reset.
  if (element.frameless) {
    buttonStyle.backgroundColor = "transparent";
    buttonStyle.backgroundImage = "none";
    buttonStyle.borderWidth = 0;
    buttonStyle.borderStyle = "none";
    buttonStyle.borderColor = "transparent";
    buttonStyle.boxShadow = "none";
  }

  let layerStyle: CSSProperties | undefined;
  if (showImage) {
    const effect = applyImageEffectStyles(buttonStyle, imgSrc, {
      fit: element.image_fit,
      blend: element.image_blend_mode,
      opacity: element.image_opacity,
      tintColor: activeStyle.bg_color as string | undefined,
    });
    buttonStyle = effect.buttonStyle;
    layerStyle = effect.layerStyle;
  }

  if (showImage && displayMode === "image_text") {
    buttonStyle.textShadow = "0 1px 3px rgba(0,0,0,0.8)";
  }

  const showLabel = displayMode !== "image" && displayMode !== "icon_only";

  return (
    <div
      className="panel-element panel-button"
      style={{ width: "100%", height: "100%", ...buttonStyle }}
    >
      {layerStyle && <div className="panel-button-image-layer" style={layerStyle} />}
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
