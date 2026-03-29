import type { UIElement } from "../../../api/types";
import type { FeedbackBinding } from "../uiBuilderHelpers";
import * as wsClient from "../../../api/wsClient";
import { getAssetUrl } from "../../../api/restClient";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface MultiStateFeedback {
  key?: string;
  states?: Record<string, Record<string, unknown>>;
  default_state?: string;
  // Legacy fields
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
        // Multi-state feedback
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
        // Legacy binary feedback
        const isActive = stateValue == fb.condition?.equals;
        if (isActive && fb.style_active) {
          activeStyle = { ...activeStyle, ...fb.style_active };
        } else if (!isActive && fb.style_inactive) {
          activeStyle = { ...activeStyle, ...fb.style_inactive };
        }
      }
    }
  }

  const css = buildElementStyle(activeStyle, {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    cursor: previewMode ? "pointer" : "default",
    userSelect: "none",
    width: "100%",
    height: "100%",
    fontWeight: "500",
    wordBreak: "break-word",
  });

  // Defaults if not set by style
  if (!activeStyle.bg_color && !activeStyle.background_gradient) {
    css.backgroundColor = "#424242";
  }
  if (!activeStyle.text_color) css.color = "#CCCCCC";
  if (!activeStyle.border_radius) css.borderRadius = "8px";
  if (!activeStyle.padding && !activeStyle.padding_horizontal && !activeStyle.padding_vertical) {
    css.padding = "8px";
  }

  // Display mode: image buttons
  const displayMode = element.display_mode || "text";
  const imgSrc = resolveAssetRef(activeButtonImage);
  if ((displayMode === "image" || displayMode === "image_text") && imgSrc) {
    css.backgroundImage = `url(${imgSrc})`;
    css.backgroundSize = element.image_fit || "cover";
    css.backgroundPosition = "center";
    css.backgroundRepeat = "no-repeat";
    if (displayMode === "image_text") {
      css.textShadow = "0 1px 3px rgba(0,0,0,0.8)";
    }
  }

  const handleMouseDown = () => {
    if (!previewMode) return;
    const press = element.bindings?.press as Record<string, unknown> | undefined;
    const mode = press?.mode as string || "tap";

    if (mode === "toggle" && press?.toggle_key) {
      const stateValue = liveState[press.toggle_key as string];
      const toggleValue = press.toggle_value;
      const isActive = stateValue !== undefined && toggleValue !== undefined &&
        String(stateValue).toLowerCase() === String(toggleValue).toLowerCase();
      wsClient.send({ type: isActive ? "ui.toggle_off" : "ui.press", element_id: element.id });
    } else {
      wsClient.send({ type: "ui.press", element_id: element.id });
    }
  };

  const handleMouseUp = () => {
    if (!previewMode) return;
    wsClient.send({ type: "ui.release", element_id: element.id });
  };

  const showLabel = displayMode !== "image" && displayMode !== "icon_only";

  return (
    <div
      onMouseDown={handleMouseDown}
      onMouseUp={handleMouseUp}
      style={css}
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
