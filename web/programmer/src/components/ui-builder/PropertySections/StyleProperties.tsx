import type { UIElement } from "../../../api/types";
import { AssetPicker } from "../AssetPicker";
import { InlineColorPicker } from "../../shared/InlineColorPicker";

interface StylePropertiesProps {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
  themeDefaults?: Record<string, unknown>;
}

// Shadow presets (must match panel.js applyStyle box-shadow handling)
const SHADOW_PRESETS: Record<string, string> = {
  sm: "0 2px 4px rgba(0,0,0,0.2)",
  md: "0 4px 8px rgba(0,0,0,0.3)",
  lg: "0 8px 16px rgba(0,0,0,0.4)",
  glow: "0 0 12px rgba(33,150,243,0.5)",
  inset: "inset 0 2px 4px rgba(0,0,0,0.3)",
};

export function StyleProperties({ element, onChange, themeDefaults }: StylePropertiesProps) {
  const style = element.style || {};

  const handleStyleChange = (key: string, value: unknown) => {
    onChange({ style: { ...style, [key]: value === undefined || value === "" ? undefined : value } });
  };

  // Check if a style property is explicitly set on this element (vs inherited from theme)
  const isOverridden = (key: string): boolean => {
    return style[key] !== undefined && style[key] !== null && style[key] !== "";
  };

  // Get the effective value: element style if set, otherwise theme default
  const getEffective = (key: string): unknown => {
    if (isOverridden(key)) return style[key];
    return themeDefaults?.[key];
  };

  // Reset a style property to inherit from theme
  const handleReset = (key: string) => {
    const updated = { ...style };
    delete updated[key];
    onChange({ style: updated });
  };

  const handleGradientChange = (field: string, value: unknown) => {
    const grad = (style.background_gradient as Record<string, unknown>) || {
      type: "linear",
      angle: 180,
      from: "",
      to: "",
    };
    const updated = { ...grad, [field]: value };
    // Clear gradient if both colors are empty
    if (!updated.from && !updated.to) {
      onChange({ style: { ...style, background_gradient: undefined } });
    } else {
      onChange({ style: { ...style, background_gradient: updated } });
    }
  };

  const gradient = (style.background_gradient as Record<string, unknown>) || null;
  const gradientEnabled = !!gradient?.from;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      {/* --- Colors --- */}
      <SectionLabel>Colors</SectionLabel>

      <StyleRow
        label="Background"
        tooltip="Element background color"
        isOverridden={isOverridden("bg_color")}
        onReset={() => handleReset("bg_color")}
      >
        <InlineColorPicker size="md" clearable
          value={String(style.bg_color || "")}
          onChange={(v) => handleStyleChange("bg_color", v)}
          placeholder={String(themeDefaults?.bg_color || "")}
        />
      </StyleRow>

      <StyleRow
        label="Text Color"
        tooltip="Color of text and icons in this element"
        isOverridden={isOverridden("text_color")}
        onReset={() => handleReset("text_color")}
      >
        <InlineColorPicker size="md" clearable
          value={String(style.text_color || "")}
          onChange={(v) => handleStyleChange("text_color", v)}
          placeholder={String(themeDefaults?.text_color || "")}
        />
      </StyleRow>

      {/* Accent color — overrides the theme accent for interactive sub-elements */}
      {["slider", "fader", "select", "text_input", "page_nav", "keypad"].includes(element.type) && (
        <StyleRow
          label="Accent Color"
          tooltip="Color for interactive parts (thumb, handle, fill). Overrides theme accent for this element."
          isOverridden={isOverridden("accent_color")}
          onReset={() => handleReset("accent_color")}
        >
          <InlineColorPicker size="md" clearable
            value={String(style.accent_color || "")}
            onChange={(v) => handleStyleChange("accent_color", v)}
            placeholder={String(themeDefaults?.accent_color || "")}
          />
        </StyleRow>
      )}

      {/* Track/surface color — overrides the theme surface for track backgrounds */}
      {["slider", "fader", "select", "text_input", "keypad"].includes(element.type) && (
        <StyleRow
          label="Track Color"
          tooltip="Background color for tracks, inputs, and surface areas. Overrides theme surface for this element."
          isOverridden={isOverridden("track_color")}
          onReset={() => handleReset("track_color")}
        >
          <InlineColorPicker size="md" clearable
            value={String(style.track_color || "")}
            onChange={(v) => handleStyleChange("track_color", v)}
            placeholder={String(themeDefaults?.track_color || "")}
          />
        </StyleRow>
      )}

      {/* Element-specific colors */}
      {element.type === "list" && (
        <>
          <StyleRow
            label="Item Background"
            tooltip="Background color for each list item"
            isOverridden={isOverridden("item_bg")}
            onReset={() => handleReset("item_bg")}
          >
            <InlineColorPicker size="md" clearable
              value={String(style.item_bg || "")}
              onChange={(v) => handleStyleChange("item_bg", v)}
              placeholder={String(themeDefaults?.item_bg || "")}
            />
          </StyleRow>
          <StyleRow
            label="Selected Item"
            tooltip="Background color for the currently selected list item"
            isOverridden={isOverridden("item_active_bg")}
            onReset={() => handleReset("item_active_bg")}
          >
            <InlineColorPicker size="md" clearable
              value={String(style.item_active_bg || "")}
              onChange={(v) => handleStyleChange("item_active_bg", v)}
              placeholder={String(themeDefaults?.item_active_bg || "")}
            />
          </StyleRow>
        </>
      )}

      {/* --- Typography --- */}
      <SectionLabel>Typography</SectionLabel>

      <StyleRow label="Font Size" tooltip="Text size in pixels">
        <input
          type="number"
          value={Number(style.font_size) || ""}
          onChange={(e) =>
            handleStyleChange(
              "font_size",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder={themeDefaults?.font_size != null ? String(themeDefaults.font_size) : "14"}
          min={8}
          max={72}
          style={{ width: 64, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <div style={{ display: "flex", gap: 2 }}>
          {[12, 14, 16, 18, 24, 28].map((size) => (
            <button
              key={size}
              onClick={() => handleStyleChange("font_size", size)}
              style={{
                padding: "2px 4px",
                borderRadius: 3,
                fontSize: 10,
                color:
                  Number(style.font_size) === size
                    ? "var(--accent)"
                    : "var(--text-muted)",
                background:
                  Number(style.font_size) === size
                    ? "var(--accent-dim)"
                    : "transparent",
              }}
            >
              {size}
            </button>
          ))}
        </div>
      </StyleRow>

      <StyleRow label="Weight" tooltip="How bold or light the text appears">
        <select
          value={String(style.font_weight || "400")}
          onChange={(e) => handleStyleChange("font_weight", e.target.value === "400" ? undefined : e.target.value)}
          style={{
            flex: 1,
            padding: "4px 6px",
            fontSize: "var(--font-size-sm)",
          }}
        >
          <option value="400">Normal</option>
          <option value="300">Light</option>
          <option value="500">Medium</option>
          <option value="bold">Bold</option>
          <option value="800">Extra Bold</option>
        </select>
      </StyleRow>

      <StyleRow label="Align" tooltip="Horizontal text alignment">
        <div style={{ display: "flex", gap: 2 }}>
          {(["left", "center", "right"] as const).map((align) => (
            <button
              key={align}
              onClick={() => handleStyleChange("text_align", align)}
              style={{
                padding: "3px 8px",
                borderRadius: 3,
                fontSize: "var(--font-size-sm)",
                color:
                  style.text_align === align
                    ? "var(--accent)"
                    : "var(--text-muted)",
                background:
                  style.text_align === align
                    ? "var(--accent-dim)"
                    : "var(--bg-base)",
                border: "1px solid var(--border-color)",
              }}
            >
              {align.charAt(0).toUpperCase() + align.slice(1)}
            </button>
          ))}
        </div>
      </StyleRow>

      <StyleRow label="Vertical Align" tooltip="Vertical text position within the element">
        <div style={{ display: "flex", gap: 2 }}>
          {(["top", "center", "bottom"] as const).map((v) => (
            <button
              key={v}
              onClick={() => handleStyleChange("vertical_align", v)}
              style={{
                padding: "3px 8px",
                borderRadius: 3,
                fontSize: "var(--font-size-sm)",
                color:
                  style.vertical_align === v
                    ? "var(--accent)"
                    : "var(--text-muted)",
                background:
                  style.vertical_align === v
                    ? "var(--accent-dim)"
                    : "var(--bg-base)",
                border: "1px solid var(--border-color)",
              }}
            >
              {v.charAt(0).toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
      </StyleRow>

      <StyleRow label="Text Case" tooltip="Change text to uppercase, lowercase, or capitalized">
        <select
          value={String(style.text_transform || "")}
          onChange={(e) => handleStyleChange("text_transform", e.target.value)}
          style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Normal</option>
          <option value="uppercase">UPPERCASE</option>
          <option value="lowercase">lowercase</option>
          <option value="capitalize">Capitalize</option>
        </select>
      </StyleRow>

      <StyleRow label="Letter Spacing" tooltip="Space between individual letters (pixels)">
        <input
          type="number"
          value={style.letter_spacing != null ? Number(style.letter_spacing) : ""}
          onChange={(e) =>
            handleStyleChange(
              "letter_spacing",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="0"
          min={-2}
          max={20}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      <StyleRow label="Line Height" tooltip="Space between lines of text (multiplier of font size, e.g. 1.5 = 50% extra space)">
        <input
          type="number"
          value={style.line_height != null ? Number(style.line_height) : ""}
          onChange={(e) =>
            handleStyleChange(
              "line_height",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="1.2"
          min={0.5}
          max={3}
          step={0.1}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>&times; font size</span>
      </StyleRow>

      <StyleRow label="Text Wrapping" tooltip="How the element handles line breaks and extra spaces">
        <select
          value={String(style.white_space || "")}
          onChange={(e) => handleStyleChange("white_space", e.target.value || undefined)}
          style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Normal</option>
          <option value="pre-line">Preserve Line Breaks</option>
          <option value="pre-wrap">Preserve All Whitespace</option>
        </select>
      </StyleRow>

      {/* --- Border --- */}
      <SectionLabel>Border</SectionLabel>

      <StyleRow
        label="Width"
        tooltip="Border thickness in pixels"
        isOverridden={isOverridden("border_width")}
        onReset={() => handleReset("border_width")}
      >
        <input
          type="number"
          value={style.border_width != null ? Number(style.border_width) : ""}
          onChange={(e) =>
            handleStyleChange(
              "border_width",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder={themeDefaults?.border_width != null ? String(themeDefaults.border_width) : "0"}
          min={0}
          max={20}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      {Number(getEffective("border_width")) ? (
        <>
          <StyleRow
            label="Border Color"
            tooltip="Color of the border"
            isOverridden={isOverridden("border_color")}
            onReset={() => handleReset("border_color")}
          >
            <InlineColorPicker size="md" clearable
              value={String(style.border_color || "")}
              onChange={(v) => handleStyleChange("border_color", v)}
              placeholder={String(themeDefaults?.border_color || "")}
            />
          </StyleRow>

          <StyleRow label="Border Style" tooltip="Visual style of the border line">
            <select
              value={String(style.border_style || "solid")}
              onChange={(e) => handleStyleChange("border_style", e.target.value)}
              style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
            >
              <option value="solid">Solid</option>
              <option value="dashed">Dashed</option>
              <option value="dotted">Dotted</option>
              <option value="none">None</option>
            </select>
          </StyleRow>
        </>
      ) : (
        <div style={{ fontSize: 10, color: "var(--text-muted)", fontStyle: "italic" }}>
          Set border width above 0 to configure color and style
        </div>
      )}

      <StyleRow
        label="Corner Radius"
        tooltip="How rounded the corners are (pixels). Higher values = more rounded."
        isOverridden={isOverridden("border_radius")}
        onReset={() => handleReset("border_radius")}
      >
        <input
          type="number"
          value={Number(style.border_radius) || ""}
          onChange={(e) =>
            handleStyleChange(
              "border_radius",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder={themeDefaults?.border_radius != null ? String(themeDefaults.border_radius) : "8"}
          min={0}
          max={50}
          style={{ width: 64, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
      </StyleRow>

      {/* --- Shadow --- */}
      <SectionLabel>Shadow</SectionLabel>

      <StyleRow
        label="Shadow"
        tooltip="Drop shadow behind the element"
        isOverridden={isOverridden("box_shadow")}
        onReset={() => handleReset("box_shadow")}
      >
        <select
          value={String(style.box_shadow || "")}
          onChange={(e) => handleStyleChange("box_shadow", e.target.value)}
          style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">{themeDefaults?.box_shadow ? `Theme (${themeDefaults.box_shadow})` : "None"}</option>
          <option value="sm">Small</option>
          <option value="md">Medium</option>
          <option value="lg">Large</option>
          <option value="glow">Glow (uses text color)</option>
          <option value="inset">Inset</option>
          <option value="none">None</option>
        </select>
      </StyleRow>

      {/* Shadow preview */}
      {!!(style.box_shadow && style.box_shadow !== "none" && style.box_shadow !== "") && (
        <div
          style={{
            height: 28,
            borderRadius: 6,
            background: "var(--bg-surface)",
            boxShadow: SHADOW_PRESETS[style.box_shadow as string] || String(style.box_shadow),
            border: "1px solid var(--border-color)",
          }}
        />
      )}

      {/* --- Margin --- */}
      <SectionLabel>Margin</SectionLabel>

      <StyleRow label="All Sides" tooltip="Space between the element edges and its grid cell (pixels)">
        <input
          type="number"
          value={style.margin != null ? Number(style.margin) : ""}
          onChange={(e) =>
            handleStyleChange(
              "margin",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="0"
          min={0}
          max={50}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      <StyleRow label="Horizontal" tooltip="Left and right margin (overrides All Sides for left/right)">
        <input
          type="number"
          value={style.margin_horizontal != null ? Number(style.margin_horizontal) : ""}
          onChange={(e) =>
            handleStyleChange(
              "margin_horizontal",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="—"
          min={0}
          max={50}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      <StyleRow label="Vertical" tooltip="Top and bottom margin (overrides All Sides for top/bottom)">
        <input
          type="number"
          value={style.margin_vertical != null ? Number(style.margin_vertical) : ""}
          onChange={(e) =>
            handleStyleChange(
              "margin_vertical",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="—"
          min={0}
          max={50}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      {/* --- Padding --- */}
      <SectionLabel>Padding</SectionLabel>

      <StyleRow label="All Sides" tooltip="Equal padding on all four sides of the element (pixels)">
        <input
          type="number"
          value={style.padding != null ? Number(style.padding) : ""}
          onChange={(e) =>
            handleStyleChange(
              "padding",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="0"
          min={0}
          max={100}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      <StyleRow label="Horizontal" tooltip="Left and right padding (overrides All Sides for left/right)">
        <input
          type="number"
          value={style.padding_horizontal != null ? Number(style.padding_horizontal) : ""}
          onChange={(e) =>
            handleStyleChange(
              "padding_horizontal",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="—"
          min={0}
          max={100}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      <StyleRow label="Vertical" tooltip="Top and bottom padding (overrides All Sides for top/bottom)">
        <input
          type="number"
          value={style.padding_vertical != null ? Number(style.padding_vertical) : ""}
          onChange={(e) =>
            handleStyleChange(
              "padding_vertical",
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          placeholder="—"
          min={0}
          max={100}
          style={{ width: 56, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </StyleRow>

      {/* --- Gradient --- */}
      <SectionLabel>Gradient</SectionLabel>

      <StyleRow label="Gradient" tooltip="Apply a color gradient over the background">
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={gradientEnabled}
            onChange={(e) => {
              if (e.target.checked) {
                // Enable with a sensible default start color
                const startColor = String(style.bg_color || themeDefaults?.bg_color || "#333333");
                handleGradientChange("from", startColor);
              } else {
                onChange({ style: { ...style, background_gradient: undefined } });
              }
            }}
          />
          Enable
        </label>
      </StyleRow>

      {gradientEnabled && (
        <>
          <StyleRow label="Gradient Start" tooltip="Color at the beginning of the gradient">
            <InlineColorPicker size="md" clearable
              value={String(gradient?.from || "")}
              onChange={(v) => handleGradientChange("from", v)}
            />
          </StyleRow>

          <StyleRow label="Gradient End" tooltip="Color at the end of the gradient">
            <InlineColorPicker size="md" clearable
              value={String(gradient?.to || "")}
              onChange={(v) => handleGradientChange("to", v)}
            />
          </StyleRow>

          <StyleRow label="Angle" tooltip="Direction of the gradient in degrees (0 = up, 90 = right, 180 = down)">
            <input
              type="range"
              min={0}
              max={360}
              step={1}
              value={Number(gradient?.angle ?? 180)}
              onChange={(e) =>
                handleGradientChange("angle", Number(e.target.value))
              }
              style={{ flex: 1 }}
            />
            <span
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-muted)",
                minWidth: 32,
                textAlign: "right",
              }}
            >
              {Number(gradient?.angle ?? 180)}°
            </span>
          </StyleRow>

          {/* Gradient preview */}
          {!!(gradient?.from && gradient?.to) && (
            <div
              style={{
                height: 20,
                borderRadius: 4,
                background: `linear-gradient(${Number(gradient.angle ?? 180)}deg, ${gradient.from}, ${gradient.to})`,
                border: "1px solid var(--border-color)",
              }}
            />
          )}
        </>
      )}

      {/* --- Background Image --- */}
      {element.type === "button" && (element.display_mode === "image" || element.display_mode === "image_text") ? (
        <>
          <SectionLabel>Background Image</SectionLabel>
          <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "4px 0" }}>
            Image buttons use the Image properties in the Basic tab.
          </div>
        </>
      ) : (
        <>
          <SectionLabel>Background Image</SectionLabel>

          <StyleRow label="Image" tooltip="Upload or select a background image for this element">
            <AssetPicker
              value={String(style.background_image || "")}
              onChange={(v) => handleStyleChange("background_image", v)}
            />
          </StyleRow>

          {String(style.background_image || "") !== "" && (
            <>
              <StyleRow label="Size" tooltip="How the background image fills the element">
                <select
                  value={String(style.background_size || "cover")}
                  onChange={(e) => handleStyleChange("background_size", e.target.value)}
                  style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
                >
                  <option value="cover">Cover (fill, may crop)</option>
                  <option value="contain">Contain (fit, may letterbox)</option>
                  <option value="stretch">Stretch (distort to fill)</option>
                </select>
              </StyleRow>

              <StyleRow label="Position" tooltip="Which part of the image stays visible when cropped">
                <select
                  value={String(style.background_position || "center")}
                  onChange={(e) => handleStyleChange("background_position", e.target.value)}
                  style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
                >
                  <option value="center">Center</option>
                  <option value="top">Top</option>
                  <option value="bottom">Bottom</option>
                  <option value="left">Left</option>
                  <option value="right">Right</option>
                  <option value="top left">Top Left</option>
                  <option value="top right">Top Right</option>
                  <option value="bottom left">Bottom Left</option>
                  <option value="bottom right">Bottom Right</option>
                </select>
              </StyleRow>

              <StyleRow label="Image Opacity" tooltip="Transparency of the background image only (does not affect text or other content)">
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={style.background_opacity != null ? Number(style.background_opacity) : 1}
                  onChange={(e) =>
                    handleStyleChange("background_opacity", parseFloat(e.target.value))
                  }
                  style={{ flex: 1 }}
                />
                <span
                  style={{
                    fontSize: "var(--font-size-sm)",
                    color: "var(--text-muted)",
                    minWidth: 32,
                    textAlign: "right",
                  }}
                >
                  {Math.round((style.background_opacity != null ? Number(style.background_opacity) : 1) * 100)}%
                </span>
              </StyleRow>
            </>
          )}
        </>
      )}

      {/* --- Appearance --- */}
      <SectionLabel>Appearance</SectionLabel>

      <StyleRow label="Element Opacity" tooltip="Transparency of the entire element including all content (100% = fully visible, 0% = invisible)">
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={style.opacity != null ? Number(style.opacity) : 1}
          onChange={(e) =>
            handleStyleChange("opacity", parseFloat(e.target.value))
          }
          style={{ flex: 1 }}
        />
        <span
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            minWidth: 32,
            textAlign: "right",
          }}
        >
          {Math.round((style.opacity != null ? Number(style.opacity) : 1) * 100)}%
        </span>
      </StyleRow>

      <StyleRow label="Transition Speed" tooltip="How fast this element's style changes animate (in milliseconds). Affects feedback state transitions.">
        <input
          type="number"
          value={style.transition_duration != null ? Number(style.transition_duration) : ""}
          onChange={(e) => handleStyleChange("transition_duration", e.target.value ? Number(e.target.value) : undefined)}
          placeholder="200"
          min={0}
          max={5000}
          step={50}
          style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        />
        <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>ms</span>
      </StyleRow>

      <StyleRow label="Content Overflow" tooltip="What happens when content is larger than the element — hide it, show scrollbars, or let it overflow">
        <select
          value={String(style.overflow || "")}
          onChange={(e) => handleStyleChange("overflow", e.target.value)}
          style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Default</option>
          <option value="hidden">Hidden (clip content)</option>
          <option value="visible">Visible (show all)</option>
          <option value="scroll">Scrollable</option>
        </select>
      </StyleRow>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 600,
        color: "var(--text-muted)",
        textTransform: "uppercase",
        letterSpacing: 1,
        marginTop: 4,
        paddingBottom: 2,
        borderBottom: "1px solid var(--border-color)",
      }}
    >
      {children}
    </div>
  );
}

function HelpTip({ text }: { text: string }) {
  return (
    <span
      title={text}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 14,
        height: 14,
        borderRadius: "50%",
        fontSize: 9,
        color: "var(--text-muted)",
        border: "1px solid var(--border-color)",
        cursor: "help",
        flexShrink: 0,
        opacity: 0.7,
      }}
    >
      ?
    </span>
  );
}

function StyleRow({
  label,
  children,
  isOverridden,
  onReset,
  tooltip,
}: {
  label: string;
  children: React.ReactNode;
  isOverridden?: boolean;
  onReset?: () => void;
  tooltip?: string;
}) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
        <label
          style={{
            fontSize: 11,
            color: isOverridden ? "var(--text-primary)" : "var(--text-muted)",
            fontWeight: isOverridden ? 500 : 400,
          }}
        >
          {label}
        </label>
        {tooltip && <HelpTip text={tooltip} />}
        {isOverridden && onReset && (
          <button
            onClick={onReset}
            title="Reset to theme default"
            style={{
              padding: "0 3px",
              fontSize: 9,
              color: "var(--text-muted)",
              borderRadius: 3,
              cursor: "pointer",
              lineHeight: "14px",
              opacity: 0.6,
            }}
          >
            ↩
          </button>
        )}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          flexWrap: "wrap",
        }}
      >
        {children}
      </div>
    </div>
  );
}

