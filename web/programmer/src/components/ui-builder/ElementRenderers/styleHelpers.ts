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
