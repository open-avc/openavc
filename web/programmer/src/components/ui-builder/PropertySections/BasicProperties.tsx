import { useState, useRef, useEffect } from "react";
import { HexColorPicker } from "react-colorful";
import { Plus, X } from "lucide-react";
import type { UIElement, UIPage, UIElementOption } from "../../../api/types";
import { CopyButton } from "../../shared/CopyButton";
import { IconPicker } from "../IconPicker";
import { AssetPicker } from "../AssetPicker";

interface BasicPropertiesProps {
  element: UIElement;
  pages: UIPage[];
  onChange: (patch: Partial<UIElement>) => void;
}

export function BasicProperties({
  element,
  pages,
  onChange,
}: BasicPropertiesProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {/* ID (read-only) */}
      <FieldRow label="ID">
        <input
          value={element.id}
          readOnly
          style={{
            flex: 1,
            opacity: 0.6,
            cursor: "default",
            background: "var(--bg-surface)",
          }}
        />
        <CopyButton value={element.id} title="Copy element ID" />
      </FieldRow>

      {/* Type (read-only) */}
      <FieldRow label="Type">
        <input
          value={element.type}
          readOnly
          style={{
            flex: 1,
            opacity: 0.6,
            cursor: "default",
            background: "var(--bg-surface)",
          }}
        />
      </FieldRow>

      {/* Label (for most elements except label and status_led and spacer) */}
      {element.type !== "label" &&
        element.type !== "status_led" &&
        element.type !== "spacer" && (
          <>
            <FieldRow label="Label">
              {element.style?.white_space === "pre-line" || element.style?.white_space === "pre-wrap" ? (
                <textarea
                  value={element.label || ""}
                  onChange={(e) => onChange({ label: e.target.value })}
                  rows={3}
                  style={{ flex: 1, resize: "vertical", fontSize: "var(--font-size-sm)" }}
                />
              ) : (
                <input
                  value={element.label || ""}
                  onChange={(e) => onChange({ label: e.target.value })}
                  style={{ flex: 1 }}
                />
              )}
            </FieldRow>
            {["button", "page_nav", "camera_preset"].includes(element.type) && (
              <FieldRow label="Multi-line">
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={element.style?.white_space === "pre-line" || element.style?.white_space === "pre-wrap"}
                    onChange={(e) => {
                      const updated = { ...(element.style || {}) };
                      if (e.target.checked) {
                        updated.white_space = "pre-line";
                      } else {
                        delete updated.white_space;
                      }
                      onChange({ style: updated });
                    }}
                  />
                  Use Enter for line breaks
                </label>
              </FieldRow>
            )}
          </>
        )}

      {/* Display mode (for buttons) */}
      {element.type === "button" && (
        <FieldRow label="Display">
          <select
            value={element.display_mode || "text"}
            onChange={(e) => onChange({ display_mode: e.target.value === "text" ? undefined : e.target.value })}
            style={{ flex: 1 }}
          >
            <option value="text">Text Only</option>
            <option value="icon_text">Icon + Text</option>
            <option value="icon_only">Icon Only</option>
            <option value="image">Image</option>
            <option value="image_text">Image + Text</option>
          </select>
        </FieldRow>
      )}

      {/* Image button properties */}
      {element.type === "button" &&
        (element.display_mode === "image" || element.display_mode === "image_text") && (
        <>
          <FieldRow label="Image">
            <AssetPicker
              value={element.button_image || ""}
              onChange={(v) => onChange({ button_image: v || undefined })}
            />
          </FieldRow>
          <FieldRow label="Active Img">
            <AssetPicker
              value={element.button_image_active || ""}
              onChange={(v) => onChange({ button_image_active: v || undefined })}
            />
          </FieldRow>
          <FieldRow label="Fit">
            <select
              value={element.image_fit || "cover"}
              onChange={(e) => onChange({ image_fit: e.target.value })}
              style={{ flex: 1 }}
            >
              <option value="cover">Cover</option>
              <option value="contain">Contain</option>
              <option value="fill">Fill</option>
            </select>
          </FieldRow>
        </>
      )}

      {/* Text (for label elements) */}
      {element.type === "label" && (
        <>
          <FieldRow label="Text">
            {element.style?.white_space === "pre-line" || element.style?.white_space === "pre-wrap" ? (
              <textarea
                value={element.text || ""}
                onChange={(e) => onChange({ text: e.target.value })}
                rows={3}
                style={{ flex: 1, resize: "vertical", fontSize: "var(--font-size-sm)" }}
              />
            ) : (
              <input
                value={element.text || ""}
                onChange={(e) => onChange({ text: e.target.value })}
                style={{ flex: 1 }}
              />
            )}
          </FieldRow>
          <FieldRow label="Multi-line">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={element.style?.white_space === "pre-line" || element.style?.white_space === "pre-wrap"}
                onChange={(e) => {
                  const updated = { ...(element.style || {}) };
                  if (e.target.checked) {
                    updated.white_space = "pre-line";
                  } else {
                    delete updated.white_space;
                  }
                  onChange({ style: updated });
                }}
              />
              Use Enter for line breaks
            </label>
          </FieldRow>
        </>
      )}

      {/* Target page (for page_nav) */}
      {element.type === "page_nav" && (
        <FieldRow label="Target Page">
          <select
            value={element.target_page || ""}
            onChange={(e) => onChange({ target_page: e.target.value })}
            style={{ flex: 1 }}
          >
            <option value="">Select page...</option>
            <option value="$back">$back (dismiss overlay)</option>
            {pages.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}{p.page_type === "overlay" ? " (overlay)" : p.page_type === "sidebar" ? " (sidebar)" : ""}
              </option>
            ))}
          </select>
        </FieldRow>
      )}

      {/* Slider min/max/step */}
      {element.type === "slider" && (
        <>
          <FieldRow label="Min">
            <input
              type="number"
              value={element.min ?? 0}
              onChange={(e) => onChange({ min: Number(e.target.value) })}
              style={{ flex: 1 }}
            />
          </FieldRow>
          <FieldRow label="Max">
            <input
              type="number"
              value={element.max ?? 100}
              onChange={(e) => onChange({ max: Number(e.target.value) })}
              style={{ flex: 1 }}
            />
          </FieldRow>
          <FieldRow label="Step">
            <input
              type="number"
              value={element.step ?? 1}
              onChange={(e) => onChange({ step: Number(e.target.value) })}
              style={{ flex: 1 }}
              min={0.01}
              step={0.1}
            />
          </FieldRow>
        </>
      )}

      {/* Placeholder (for text_input) */}
      {element.type === "text_input" && (
        <FieldRow label="Placeholder">
          <input
            value={element.placeholder || ""}
            onChange={(e) => onChange({ placeholder: e.target.value })}
            placeholder="Placeholder text..."
            style={{ flex: 1 }}
          />
        </FieldRow>
      )}

      {/* Image source */}
      {element.type === "image" && (
        <>
          <FieldRow label="Image">
            <AssetPicker
              value={element.src || ""}
              onChange={(v) => onChange({ src: v || undefined })}
            />
          </FieldRow>
          <FieldRow label="URL">
            <input
              value={element.src?.startsWith("assets://") ? "" : (element.src || "")}
              onChange={(e) => onChange({ src: e.target.value || undefined })}
              placeholder="Or enter external URL..."
              style={{ flex: 1, fontSize: 11 }}
            />
          </FieldRow>
        </>
      )}

      {/* Camera preset number */}
      {element.type === "camera_preset" && (
        <FieldRow label="Preset #">
          <input
            type="number"
            value={element.preset_number ?? ""}
            onChange={(e) =>
              onChange({
                preset_number: e.target.value
                  ? Number(e.target.value)
                  : undefined,
              })
            }
            min={1}
            style={{ flex: 1 }}
          />
        </FieldRow>
      )}

      {/* Gauge properties */}
      {element.type === "gauge" && (
        <>
          <FieldRow label="Min">
            <input type="number" value={element.min ?? 0} onChange={(e) => onChange({ min: Number(e.target.value) })} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Max">
            <input type="number" value={element.max ?? 100} onChange={(e) => onChange({ max: Number(e.target.value) })} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Unit">
            <input value={element.unit || ""} onChange={(e) => onChange({ unit: e.target.value })} placeholder="%, dB, etc." style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Arc Angle">
            <input type="number" value={element.arc_angle ?? 240} onChange={(e) => onChange({ arc_angle: Number(e.target.value) })} min={90} max={360} style={{ flex: 1 }} />
          </FieldRow>

          <SubSection label="Gauge Appearance" />
          <FieldRow label="Gauge Color">
            <ColorInput
              value={String(element.style?.gauge_color || "")}
              onChange={(v) => onChange({ style: { ...element.style, gauge_color: v || undefined } })}
            />
          </FieldRow>
          <FieldRow label="Background Arc">
            <ColorInput
              value={String(element.style?.gauge_bg_color || "")}
              onChange={(v) => onChange({ style: { ...element.style, gauge_bg_color: v || undefined } })}
            />
          </FieldRow>
          <FieldRow label="Arc Width">
            <input
              type="number"
              value={(element.style?.gauge_width as number) ?? 8}
              onChange={(e) => onChange({ style: { ...element.style, gauge_width: e.target.value ? Number(e.target.value) : undefined } })}
              min={2} max={20} style={{ flex: 1 }}
            />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
          </FieldRow>
          <FieldRow label="Show Value">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={(element.style?.show_value as boolean) ?? true}
                onChange={(e) => onChange({ style: { ...element.style, show_value: e.target.checked } })}
              />
              Display numeric value
            </label>
          </FieldRow>
          <FieldRow label="Show Ticks">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={(element.style?.show_ticks as boolean) ?? true}
                onChange={(e) => onChange({ style: { ...element.style, show_ticks: e.target.checked } })}
              />
              Show tick marks around the arc
            </label>
          </FieldRow>
          {(element.style?.show_ticks as boolean) !== false && (
            <FieldRow label="Tick Count">
              <input
                type="number"
                value={(element.style?.tick_count as number) ?? 5}
                onChange={(e) => onChange({ style: { ...element.style, tick_count: e.target.value ? Number(e.target.value) : undefined } })}
                min={2} max={20} style={{ flex: 1 }}
              />
            </FieldRow>
          )}

          <GaugeZonesEditor
            zones={element.zones ?? []}
            onChange={(zones) => onChange({ zones: zones.length > 0 ? zones : undefined })}
            elementMin={element.min ?? 0}
            elementMax={element.max ?? 100}
          />
        </>
      )}

      {/* Level Meter properties */}
      {element.type === "level_meter" && (
        <>
          <FieldRow label="Min">
            <input type="number" value={element.min ?? -60} onChange={(e) => onChange({ min: Number(e.target.value) })} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Max">
            <input type="number" value={element.max ?? 0} onChange={(e) => onChange({ max: Number(e.target.value) })} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Orientation">
            <select value={element.orientation || "vertical"} onChange={(e) => onChange({ orientation: e.target.value })} style={{ flex: 1 }}>
              <option value="vertical">Vertical</option>
              <option value="horizontal">Horizontal</option>
            </select>
          </FieldRow>

          <SubSection label="Meter Appearance" />
          <FieldRow label="Segments">
            <input
              type="number"
              value={(element.style?.meter_segments as number) ?? 20}
              onChange={(e) => onChange({ style: { ...element.style, meter_segments: e.target.value ? Number(e.target.value) : undefined } })}
              min={5} max={40} style={{ flex: 1 }}
            />
          </FieldRow>
          <FieldRow label="Show Peak">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={(element.style?.show_peak as boolean) ?? true}
                onChange={(e) => onChange({ style: { ...element.style, show_peak: e.target.checked } })}
              />
              Hold peak level indicator
            </label>
          </FieldRow>
          {(element.style?.show_peak as boolean) !== false && (
            <FieldRow label="Peak Hold">
              <input
                type="number"
                value={(element.style?.peak_hold_ms as number) ?? 1500}
                onChange={(e) => onChange({ style: { ...element.style, peak_hold_ms: e.target.value ? Number(e.target.value) : undefined } })}
                min={500} max={5000} step={100} style={{ flex: 1 }}
              />
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>ms</span>
            </FieldRow>
          )}
        </>
      )}

      {/* Fader properties */}
      {element.type === "fader" && (
        <>
          <FieldRow label="Min">
            <input type="number" value={element.min ?? -80} onChange={(e) => onChange({ min: Number(e.target.value) })} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Max">
            <input type="number" value={element.max ?? 10} onChange={(e) => onChange({ max: Number(e.target.value) })} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Step">
            <input type="number" value={element.step ?? 0.5} onChange={(e) => onChange({ step: Number(e.target.value) })} min={0.01} step={0.1} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Unit">
            <input value={element.unit || ""} onChange={(e) => onChange({ unit: e.target.value })} placeholder="dB" style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Orientation">
            <select value={element.orientation || "vertical"} onChange={(e) => onChange({ orientation: e.target.value })} style={{ flex: 1 }}>
              <option value="vertical">Vertical</option>
              <option value="horizontal">Horizontal</option>
            </select>
          </FieldRow>

          <SubSection label="Display Options" />
          <FieldRow label="Show Value">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={(element.style?.show_value as boolean) ?? true}
                onChange={(e) => onChange({ style: { ...element.style, show_value: e.target.checked } })}
              />
              Display current value
            </label>
          </FieldRow>
          <FieldRow label="Show Scale">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={(element.style?.show_scale as boolean) ?? true}
                onChange={(e) => onChange({ style: { ...element.style, show_scale: e.target.checked } })}
              />
              Show scale markings
            </label>
          </FieldRow>
        </>
      )}

      {/* Group properties */}
      {element.type === "group" && (
        <>
          <FieldRow label="Label Pos">
            <select value={element.label_position || "top-left"} onChange={(e) => onChange({ label_position: e.target.value })} style={{ flex: 1 }}>
              <option value="top-left">Top Left</option>
              <option value="top-center">Top Center</option>
              <option value="top-right">Top Right</option>
              <option value="bottom-left">Bottom Left</option>
              <option value="bottom-center">Bottom Center</option>
            </select>
          </FieldRow>
          <FieldRow label="Collapsible">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
              <input
                type="checkbox"
                checked={element.collapsible ?? false}
                onChange={(e) => onChange({ collapsible: e.target.checked })}
              />
              Allow user to collapse
            </label>
          </FieldRow>
        </>
      )}

      {/* Clock properties */}
      {element.type === "clock" && (
        <>
          <FieldRow label="Mode">
            <select value={element.clock_mode || "time"} onChange={(e) => onChange({ clock_mode: e.target.value })} style={{ flex: 1 }}>
              <option value="time">Time</option>
              <option value="date">Date</option>
              <option value="datetime">Date + Time</option>
              <option value="countdown">Countdown</option>
              <option value="elapsed">Elapsed</option>
              <option value="meeting">Meeting Timer</option>
            </select>
          </FieldRow>
          <FieldRow label="Format">
            <input value={element.format || ""} onChange={(e) => onChange({ format: e.target.value })} placeholder="h:mm A" style={{ flex: 1 }} />
          </FieldRow>
          {element.clock_mode === "meeting" && (
            <FieldRow label="Duration">
              <input type="number" value={element.duration_minutes ?? 60} onChange={(e) => onChange({ duration_minutes: Number(e.target.value) })} min={1} style={{ flex: 1 }} />
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>min</span>
            </FieldRow>
          )}
          <FieldRow label="Timezone">
            <input
              value={element.timezone || ""}
              onChange={(e) => onChange({ timezone: e.target.value || undefined })}
              placeholder="America/New_York"
              style={{ flex: 1, fontSize: 11 }}
            />
          </FieldRow>
          <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "0 0 0 76px" }}>
            Leave blank for local time. Uses IANA timezone names.
          </div>
        </>
      )}

      {/* List properties */}
      {element.type === "list" && (
        <>
          <FieldRow label="Style">
            <select value={element.list_style || "selectable"} onChange={(e) => onChange({ list_style: e.target.value })} style={{ flex: 1 }}>
              <option value="static">Static (read-only)</option>
              <option value="selectable">Selectable</option>
              <option value="multi_select">Multi-Select</option>
              <option value="action">Action</option>
            </select>
          </FieldRow>
          <FieldRow label="Item Height">
            <input type="number" value={element.item_height ?? 44} onChange={(e) => onChange({ item_height: Number(e.target.value) })} min={24} max={120} style={{ flex: 1 }} />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
          </FieldRow>
          <ListItemsEditor
            items={element.items ?? []}
            onChange={(items) => onChange({ items })}
          />
          <div style={{ fontSize: 10, color: "var(--text-muted)", fontStyle: "italic" }}>
            For dynamic items, use the Items binding to populate from state keys.
          </div>
        </>
      )}

      {/* Matrix properties */}
      {element.type === "matrix" && (
        <>
          <FieldRow label="Style">
            <select value={element.matrix_style || "crosspoint"} onChange={(e) => onChange({ matrix_style: e.target.value })} style={{ flex: 1 }}>
              <option value="crosspoint">Crosspoint Grid</option>
              <option value="list">List (Dropdowns)</option>
            </select>
          </FieldRow>
          <FieldRow label="Inputs">
            <input
              type="number"
              value={element.matrix_config?.input_count ?? 4}
              onChange={(e) => {
                const count = Math.max(1, Math.min(32, Number(e.target.value)));
                const cfg = { ...element.matrix_config, input_count: count };
                // Resize labels array
                const labels = [...(cfg.input_labels || [])];
                while (labels.length < count) labels.push(`Input ${labels.length + 1}`);
                cfg.input_labels = labels.slice(0, count);
                onChange({ matrix_config: cfg });
              }}
              min={1}
              max={32}
              style={{ flex: 1 }}
            />
          </FieldRow>
          <FieldRow label="Outputs">
            <input
              type="number"
              value={element.matrix_config?.output_count ?? 4}
              onChange={(e) => {
                const count = Math.max(1, Math.min(32, Number(e.target.value)));
                const cfg = { ...element.matrix_config, output_count: count };
                const labels = [...(cfg.output_labels || [])];
                while (labels.length < count) labels.push(`Output ${labels.length + 1}`);
                cfg.output_labels = labels.slice(0, count);
                onChange({ matrix_config: cfg });
              }}
              min={1}
              max={32}
              style={{ flex: 1 }}
            />
          </FieldRow>
          <FieldRow label="Route Key">
            <input
              value={element.matrix_config?.route_key_pattern || ""}
              onChange={(e) => onChange({ matrix_config: { ...element.matrix_config, route_key_pattern: e.target.value } })}
              placeholder="device.sw.output_*_source"
              style={{ flex: 1, fontSize: 11 }}
            />
          </FieldRow>
          <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "0 0 0 76px" }}>
            Use * for the output number (1-based)
          </div>
          <FieldRow label="Audio Follow">
            <input
              type="checkbox"
              checked={element.matrix_config?.audio_follow_video ?? false}
              onChange={(e) => onChange({ matrix_config: { ...element.matrix_config, audio_follow_video: e.target.checked } })}
            />
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Route audio with video</span>
          </FieldRow>
          <MatrixLabelEditor
            title="Input Labels"
            labels={element.matrix_config?.input_labels || []}
            onChange={(labels) => onChange({ matrix_config: { ...element.matrix_config, input_labels: labels } })}
          />
          <MatrixLabelEditor
            title="Output Labels"
            labels={element.matrix_config?.output_labels || []}
            onChange={(labels) => onChange({ matrix_config: { ...element.matrix_config, output_labels: labels } })}
          />

          <SubSection label="Matrix Appearance" />
          <FieldRow label="Active Color">
            <ColorInput
              value={String(element.style?.crosspoint_active_color || "")}
              onChange={(v) => onChange({ style: { ...element.style, crosspoint_active_color: v || undefined } })}
            />
          </FieldRow>
          <FieldRow label="Inactive Color">
            <ColorInput
              value={String(element.style?.crosspoint_inactive_color || "")}
              onChange={(v) => onChange({ style: { ...element.style, crosspoint_inactive_color: v || undefined } })}
            />
          </FieldRow>
          <FieldRow label="Cell Size">
            <input
              type="number"
              value={(element.style?.cell_size as number) ?? 44}
              onChange={(e) => onChange({ style: { ...element.style, cell_size: e.target.value ? Number(e.target.value) : undefined } })}
              min={24} max={80} style={{ flex: 1 }}
            />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
          </FieldRow>
        </>
      )}

      {/* Keypad properties */}
      {element.type === "keypad" && (
        <>
          <FieldRow label="Digits">
            <input type="number" value={element.digits ?? 4} onChange={(e) => onChange({ digits: Number(e.target.value) })} min={1} max={10} style={{ flex: 1 }} />
          </FieldRow>
          <FieldRow label="Style">
            <select value={element.keypad_style || "numeric"} onChange={(e) => onChange({ keypad_style: e.target.value })} style={{ flex: 1 }}>
              <option value="numeric">Numeric (0-9)</option>
              <option value="phone">Phone (* #)</option>
            </select>
          </FieldRow>
          <FieldRow label="Auto Send">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input type="checkbox" checked={element.auto_send ?? false} onChange={(e) => onChange({ auto_send: e.target.checked })} />
              Send after all digits entered
            </label>
          </FieldRow>
          {(element.auto_send ?? false) && (
            <FieldRow label="Send Delay">
              <input
                type="number"
                value={element.auto_send_delay_ms ?? 1500}
                onChange={(e) => onChange({ auto_send_delay_ms: e.target.value ? Number(e.target.value) : undefined })}
                min={0} max={5000} step={100} style={{ flex: 1 }}
              />
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>ms</span>
            </FieldRow>
          )}
          <FieldRow label="Show Display">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={element.show_display ?? true}
                onChange={(e) => onChange({ show_display: e.target.checked })}
              />
              Show digit display above keys
            </label>
          </FieldRow>
        </>
      )}

      {/* Plugin element config */}
      {element.type === "plugin" && (
        <>
          <FieldRow label="Plugin">
            <input value={element.plugin_id || ""} readOnly style={{ flex: 1, opacity: 0.6 }} />
          </FieldRow>
          <FieldRow label="Type">
            <input value={element.plugin_type || ""} readOnly style={{ flex: 1, opacity: 0.6 }} />
          </FieldRow>
          <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "2px 0" }}>
            Plugin configuration (JSON):
          </div>
          <textarea
            value={JSON.stringify(element.plugin_config || {}, null, 2)}
            onChange={(e) => {
              try {
                const cfg = JSON.parse(e.target.value);
                onChange({ plugin_config: cfg });
              } catch {
                // Invalid JSON — don't update
              }
            }}
            rows={4}
            style={{
              width: "100%",
              fontSize: 11,
              fontFamily: "monospace",
              resize: "vertical",
            }}
          />
        </>
      )}

      {/* Icon properties (for elements that display text) */}
      {["button", "label", "page_nav", "camera_preset"].includes(element.type) && (
        <>
          <FieldRow label="Icon">
            <IconPicker
              value={element.icon || ""}
              onChange={(v) => onChange({ icon: v || undefined })}
            />
          </FieldRow>
          {element.icon && (
            <>
              <FieldRow label="Position">
                <select
                  value={element.icon_position || "left"}
                  onChange={(e) => onChange({ icon_position: e.target.value })}
                  style={{ flex: 1 }}
                >
                  <option value="left">Left</option>
                  <option value="right">Right</option>
                  <option value="top">Top</option>
                  <option value="bottom">Bottom</option>
                  <option value="center">Icon Only</option>
                </select>
              </FieldRow>
              <FieldRow label="Icon Size">
                <input
                  type="number"
                  value={element.icon_size ?? 24}
                  onChange={(e) =>
                    onChange({
                      icon_size: e.target.value ? Number(e.target.value) : undefined,
                    })
                  }
                  min={12}
                  max={64}
                  style={{ width: 64 }}
                />
                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
              </FieldRow>
              <FieldRow label="Icon Color">
                <ColorInput
                  value={element.icon_color || ""}
                  onChange={(v) => onChange({ icon_color: v || undefined })}
                />
              </FieldRow>
            </>
          )}
        </>
      )}

      {/* Select options editor */}
      {element.type === "select" && (
        <OptionsEditor
          options={element.options ?? []}
          onChange={(options) => onChange({ options })}
        />
      )}
    </div>
  );
}

function OptionsEditor({
  options,
  onChange,
}: {
  options: UIElementOption[];
  onChange: (options: UIElementOption[]) => void;
}) {
  const addOption = () => {
    onChange([...options, { label: `Option ${options.length + 1}`, value: `option_${options.length + 1}` }]);
  };

  const removeOption = (index: number) => {
    onChange(options.filter((_, i) => i !== index));
  };

  const updateOption = (index: number, patch: Partial<UIElementOption>) => {
    onChange(
      options.map((opt, i) => (i === index ? { ...opt, ...patch } : opt)),
    );
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-xs)",
        }}
      >
        <label
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
          }}
        >
          Options
        </label>
        <button
          onClick={addOption}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            padding: "2px 6px",
            borderRadius: "var(--border-radius)",
            fontSize: 11,
            color: "var(--accent)",
          }}
        >
          <Plus size={12} /> Add
        </button>
      </div>

      {options.length === 0 && (
        <div
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
            padding: "var(--space-xs)",
          }}
        >
          No options. Click Add to create one.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {options.map((opt, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <input
              value={opt.label}
              onChange={(e) => updateOption(i, { label: e.target.value })}
              placeholder="Label"
              style={{
                flex: 1,
                padding: "3px 6px",
                fontSize: 11,
              }}
            />
            <input
              value={opt.value}
              onChange={(e) => updateOption(i, { value: e.target.value })}
              placeholder="Value"
              style={{
                flex: 1,
                padding: "3px 6px",
                fontSize: 11,
              }}
            />
            <button
              onClick={() => removeOption(i)}
              style={{
                display: "flex",
                padding: 2,
                color: "var(--text-muted)",
              }}
            >
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ListItemsEditor({
  items,
  onChange,
}: {
  items: Array<{ label: string; value: string }>;
  onChange: (items: Array<{ label: string; value: string }>) => void;
}) {
  const addItem = () => {
    onChange([...items, { label: `Item ${items.length + 1}`, value: `item_${items.length + 1}` }]);
  };

  const removeItem = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
  };

  const updateItem = (index: number, patch: Partial<{ label: string; value: string }>) => {
    onChange(items.map((it, i) => (i === index ? { ...it, ...patch } : it)));
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-xs)" }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Items</label>
        <button
          onClick={addItem}
          style={{ display: "flex", alignItems: "center", gap: 2, padding: "2px 6px", borderRadius: "var(--border-radius)", fontSize: 11, color: "var(--accent)" }}
        >
          <Plus size={12} /> Add
        </button>
      </div>
      {items.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "var(--space-xs)" }}>
          No items. Click Add to create one, or use an Items binding for dynamic content.
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {items.map((item, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input
              value={item.label}
              onChange={(e) => updateItem(i, { label: e.target.value })}
              placeholder="Label"
              style={{ flex: 1, padding: "3px 6px", fontSize: 11 }}
            />
            <input
              value={item.value}
              onChange={(e) => updateItem(i, { value: e.target.value })}
              placeholder="Value"
              style={{ flex: 1, padding: "3px 6px", fontSize: 11 }}
            />
            <button onClick={() => removeItem(i)} style={{ display: "flex", padding: 2, color: "var(--text-muted)" }}>
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function MatrixLabelEditor({
  title,
  labels,
  onChange,
}: {
  title: string;
  labels: string[];
  onChange: (labels: string[]) => void;
}) {
  if (labels.length === 0) return null;

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {labels.map((label, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ fontSize: 10, color: "var(--text-muted)", width: 16, textAlign: "right", flexShrink: 0 }}>{i + 1}</span>
            <input
              value={label}
              onChange={(e) => {
                const updated = [...labels];
                updated[i] = e.target.value;
                onChange(updated);
              }}
              style={{ flex: 1, padding: "2px 6px", fontSize: 11 }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
      }}
    >
      <label
        style={{
          width: 72,
          flexShrink: 0,
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

function SubSection({ label }: { label: string }) {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 600,
        color: "var(--text-muted)",
        textTransform: "uppercase",
        letterSpacing: 1,
        marginTop: 6,
        paddingBottom: 2,
        borderBottom: "1px solid var(--border-color)",
      }}
    >
      {label}
    </div>
  );
}

function ColorInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", display: "flex", alignItems: "center", gap: 4 }}>
      <div
        onClick={() => setOpen(!open)}
        style={{
          width: 24,
          height: 24,
          borderRadius: 4,
          backgroundColor: value || "transparent",
          border: "1px solid var(--border-color)",
          cursor: "pointer",
          flexShrink: 0,
        }}
      />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="#000000"
        style={{ width: 80, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
      />
      {value && (
        <button
          onClick={() => onChange("")}
          style={{ padding: "2px 4px", fontSize: 10, color: "var(--text-muted)", borderRadius: 3 }}
        >
          Clear
        </button>
      )}
      {open && (
        <div
          style={{
            position: "absolute",
            zIndex: 100,
            top: 30,
            left: 0,
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            padding: "var(--space-sm)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          <HexColorPicker
            color={value || "#000000"}
            onChange={onChange}
            style={{ width: 180, height: 150 }}
          />
        </div>
      )}
    </div>
  );
}

function GaugeZonesEditor({
  zones,
  onChange,
  elementMin,
  elementMax,
}: {
  zones: Array<{ from: number; to: number; color: string }>;
  onChange: (zones: Array<{ from: number; to: number; color: string }>) => void;
  elementMin: number;
  elementMax: number;
}) {
  const addZone = () => {
    // Auto-calculate next zone range
    const lastTo = zones.length > 0 ? zones[zones.length - 1].to : elementMin;
    const remaining = elementMax - lastTo;
    const newFrom = lastTo;
    const newTo = Math.min(elementMax, lastTo + Math.max(1, Math.round(remaining / 2)));
    const colors = ["#4CAF50", "#FFC107", "#F44336", "#2196F3", "#9C27B0"];
    const color = colors[zones.length % colors.length];
    onChange([...zones, { from: newFrom, to: newTo, color }]);
  };

  const removeZone = (index: number) => {
    onChange(zones.filter((_, i) => i !== index));
  };

  const updateZone = (index: number, patch: Partial<{ from: number; to: number; color: string }>) => {
    onChange(zones.map((z, i) => (i === index ? { ...z, ...patch } : z)));
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 6, marginBottom: 4 }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Color Zones</label>
        <button
          onClick={addZone}
          style={{ display: "flex", alignItems: "center", gap: 2, padding: "2px 6px", borderRadius: "var(--border-radius)", fontSize: 11, color: "var(--accent)" }}
        >
          <Plus size={12} /> Add Zone
        </button>
      </div>
      {zones.length === 0 && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", fontStyle: "italic" }}>
          No color zones. Add zones to color-code value ranges (e.g., green 0-50, yellow 50-80, red 80-100).
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {zones.map((zone, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div
              style={{
                width: 18,
                height: 18,
                borderRadius: 3,
                backgroundColor: zone.color,
                border: "1px solid var(--border-color)",
                cursor: "pointer",
                flexShrink: 0,
              }}
              title="Zone color"
            />
            <input
              type="number"
              value={zone.from}
              onChange={(e) => updateZone(i, { from: Number(e.target.value) })}
              style={{ width: 48, padding: "2px 4px", fontSize: 11 }}
              title="Zone start"
            />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>to</span>
            <input
              type="number"
              value={zone.to}
              onChange={(e) => updateZone(i, { to: Number(e.target.value) })}
              style={{ width: 48, padding: "2px 4px", fontSize: 11 }}
              title="Zone end"
            />
            <input
              value={zone.color}
              onChange={(e) => updateZone(i, { color: e.target.value })}
              style={{ width: 68, padding: "2px 4px", fontSize: 10, fontFamily: "monospace" }}
              title="Color hex"
            />
            <button onClick={() => removeZone(i)} style={{ display: "flex", padding: 2, color: "var(--text-muted)" }}>
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
