import type { CSSProperties } from "react";

/**
 * Build a React CSSProperties object from an element's style dict.
 * Shared by all element renderers so new style properties are handled consistently.
 */
export function buildElementStyle(
  style: Record<string, unknown>,
  defaults?: Partial<CSSProperties>,
  themeDefaults?: Record<string, unknown>,
): CSSProperties {
  const css: CSSProperties = { ...defaults };

  // Merge: theme defaults provide base, element style overrides
  const mergedStyle = themeDefaults ? { ...themeDefaults, ...style } : style;

  // Background: gradient takes priority over solid color
  const gradient = mergedStyle.background_gradient as
    | { from?: string; to?: string; angle?: number }
    | undefined;
  if (gradient?.from && gradient?.to) {
    const angle = gradient.angle ?? 180;
    css.background = `linear-gradient(${angle}deg, ${gradient.from}, ${gradient.to})`;
  } else if (mergedStyle.bg_color) {
    css.backgroundColor = String(mergedStyle.bg_color);
  }

  // Text
  if (mergedStyle.text_color) css.color = String(mergedStyle.text_color);
  if (mergedStyle.font_size) css.fontSize = `${mergedStyle.font_size}px`;
  if (mergedStyle.font_weight)
    css.fontWeight = mergedStyle.font_weight as CSSProperties["fontWeight"];

  // Text alignment → justify-content mapping (fixes flexbox override)
  if (mergedStyle.text_align) {
    const alignMap: Record<string, string> = {
      left: "flex-start",
      center: "center",
      right: "flex-end",
    };
    css.justifyContent =
      alignMap[mergedStyle.text_align as string] || "center";
    css.textAlign = mergedStyle.text_align as CSSProperties["textAlign"];
  }

  // Vertical alignment
  if (mergedStyle.vertical_align) {
    const vMap: Record<string, string> = {
      top: "flex-start",
      center: "center",
      bottom: "flex-end",
    };
    css.alignItems = vMap[mergedStyle.vertical_align as string] || "center";
  }

  // Border
  if (mergedStyle.border_width) {
    css.borderWidth = `${mergedStyle.border_width}px`;
    css.borderStyle = (mergedStyle.border_style as string) || "solid";
    css.borderColor = (mergedStyle.border_color as string) || "#666666";
  }
  if (mergedStyle.border_radius != null) {
    css.borderRadius = `${mergedStyle.border_radius}px`;
  }

  // Box shadow with presets
  if (mergedStyle.box_shadow && mergedStyle.box_shadow !== "none") {
    const presets: Record<string, string> = {
      sm: "0 2px 4px rgba(0,0,0,0.2)",
      md: "0 4px 8px rgba(0,0,0,0.3)",
      lg: "0 8px 16px rgba(0,0,0,0.4)",
      glow: `0 0 12px ${(mergedStyle.text_color as string) || "rgba(33,150,243,0.5)"}`,
      inset: "inset 0 2px 4px rgba(0,0,0,0.3)",
    };
    css.boxShadow =
      presets[mergedStyle.box_shadow as string] || String(mergedStyle.box_shadow);
  }

  // Margin
  if (mergedStyle.margin != null) {
    const mv =
      mergedStyle.margin_vertical != null
        ? Number(mergedStyle.margin_vertical)
        : Number(mergedStyle.margin);
    const mh =
      mergedStyle.margin_horizontal != null
        ? Number(mergedStyle.margin_horizontal)
        : Number(mergedStyle.margin);
    css.margin = `${mv}px ${mh}px`;
  } else {
    if (mergedStyle.margin_vertical != null) {
      css.marginTop = `${mergedStyle.margin_vertical}px`;
      css.marginBottom = `${mergedStyle.margin_vertical}px`;
    }
    if (mergedStyle.margin_horizontal != null) {
      css.marginLeft = `${mergedStyle.margin_horizontal}px`;
      css.marginRight = `${mergedStyle.margin_horizontal}px`;
    }
  }

  // Padding
  if (mergedStyle.padding != null) {
    const pv =
      mergedStyle.padding_vertical != null
        ? Number(mergedStyle.padding_vertical)
        : Number(mergedStyle.padding);
    const ph =
      mergedStyle.padding_horizontal != null
        ? Number(mergedStyle.padding_horizontal)
        : Number(mergedStyle.padding);
    css.padding = `${pv}px ${ph}px`;
  } else {
    if (mergedStyle.padding_vertical != null) {
      css.paddingTop = `${mergedStyle.padding_vertical}px`;
      css.paddingBottom = `${mergedStyle.padding_vertical}px`;
    }
    if (mergedStyle.padding_horizontal != null) {
      css.paddingLeft = `${mergedStyle.padding_horizontal}px`;
      css.paddingRight = `${mergedStyle.padding_horizontal}px`;
    }
  }

  // Typography extras
  if (mergedStyle.text_transform)
    css.textTransform = mergedStyle.text_transform as CSSProperties["textTransform"];
  if (mergedStyle.letter_spacing)
    css.letterSpacing = `${mergedStyle.letter_spacing}px`;
  if (mergedStyle.line_height) css.lineHeight = String(mergedStyle.line_height);

  // White space (multi-line labels)
  if (mergedStyle.white_space)
    css.whiteSpace = mergedStyle.white_space as CSSProperties["whiteSpace"];

  // Overflow
  if (mergedStyle.overflow)
    css.overflow = mergedStyle.overflow as CSSProperties["overflow"];

  // Opacity
  if (mergedStyle.opacity != null) css.opacity = Number(mergedStyle.opacity);

  return css;
}

interface ImageEffectOptions {
  fit?: string;
  blend?: string;
  opacity?: number;
  tintColor?: string;
}

/**
 * Apply button image effect (plain, blend, or mask) to a base style.
 * Mirrors panel.js applyImageEffect. The tint color lives on the image layer
 * (via background-blend-mode or mask fill) so frameless buttons can still tint
 * without a visible button background.
 */
export function applyImageEffectStyles(
  baseStyle: CSSProperties,
  imageUrl: string,
  options: ImageEffectOptions = {},
): { buttonStyle: CSSProperties; layerStyle?: CSSProperties } {
  const buttonStyle: CSSProperties = { ...baseStyle };
  const fit = options.fit || "cover";
  const blend = options.blend || "none";
  const opacity = options.opacity != null ? Number(options.opacity) : 1;
  // Use explicit tintColor; fall back to currentColor rather than reading baseStyle.backgroundColor
  // (which may have been just cleared by frameless).
  const tintColor = options.tintColor || "currentColor";
  const sizeCss = fit === "fill" ? "100% 100%" : fit;

  const isMask = blend === "mask";
  const needsBlend = blend && blend !== "none" && blend !== "normal" && !isMask;
  const needsLayer = needsBlend || isMask || opacity < 1;

  if (!needsLayer) {
    buttonStyle.backgroundImage = `url(${imageUrl})`;
    buttonStyle.backgroundSize = sizeCss;
    buttonStyle.backgroundPosition = "center";
    buttonStyle.backgroundRepeat = "no-repeat";
    return { buttonStyle };
  }

  buttonStyle.backgroundImage = "none";
  buttonStyle.position = "relative";
  // Isolation + z-index: -1 on the layer places it above the button's own background
  // but below text/icons (which paint in document order above negative-z stacking contexts).
  buttonStyle.isolation = "isolate";

  const layerStyle: CSSProperties = {
    position: "absolute",
    inset: 0,
    pointerEvents: "none",
    zIndex: -1,
    backgroundSize: sizeCss,
    backgroundPosition: "center",
    backgroundRepeat: "no-repeat",
  };
  if (opacity < 1) layerStyle.opacity = opacity;

  if (isMask) {
    const maskUrl = `url(${imageUrl})`;
    layerStyle.backgroundColor = tintColor;
    layerStyle.WebkitMaskImage = maskUrl;
    layerStyle.maskImage = maskUrl;
    layerStyle.WebkitMaskSize = sizeCss;
    layerStyle.maskSize = sizeCss;
    layerStyle.WebkitMaskPosition = "center";
    layerStyle.maskPosition = "center";
    layerStyle.WebkitMaskRepeat = "no-repeat";
    layerStyle.maskRepeat = "no-repeat";
  } else if (needsBlend) {
    layerStyle.backgroundImage = `url(${imageUrl})`;
    layerStyle.backgroundColor = tintColor;
    layerStyle.backgroundBlendMode = blend as CSSProperties["backgroundBlendMode"];
  } else {
    layerStyle.backgroundImage = `url(${imageUrl})`;
  }

  return { buttonStyle, layerStyle };
}
