import { useState, useEffect } from "react";
import { Plus, X } from "lucide-react";
import type { UIElement, UIPage, UIElementOption, Placement, ProjectConfig } from "../../../api/types";
import { CopyButton } from "../../shared/CopyButton";
import { IconPicker } from "../IconPicker";
import { AssetPicker } from "../AssetPicker";
import { getAssetUrl } from "../../../api/systemClient";
import { pxToRem, remToPx } from "../uiBuilderHelpers";
import { KEYPAD_DIGITS_DEFAULT, KEYPAD_SEND_DELAY_DEFAULT_MS } from "../reviewProperties";
import { HONORED_PROPERTIES } from "../../../api/uiBindingReach.gen";
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { OptionSourcePicker } from "../../shared/OptionSourcePicker";
import { MacroRefPicker, DeviceRefPicker } from "../../shared/RefPickers";
import { SearchableSelect } from "../../shared/SearchableSelect";
import { pageGroups } from "../../shared/pickerOptions";
import { MatchDriverRangeRow } from "../BindingEditor/DeviceValuePicker";
import { CustomControlConfig } from "./CustomControlConfig";
import { FieldRow, SubSection } from "./FieldRow";
import { MatrixFields } from "./MatrixFields";
import { useRowKeys } from "./rowKeys";
import { GrantEditor } from "./GrantEditor";
import { panelElementFieldKind } from "./panelElementConfig";
import { numOrUndefined, intOrUndefined } from "./numericField";
import { usePluginStore } from "../../../store/pluginStore";
import { showInfo } from "../../../store/toastStore";

/**
 * Whether this element type's renderer draws `label`, per the panel's own
 * table. An unknown type (a plugin element the table has never seen) keeps the
 * field: silence there means "not recorded", not "not drawn".
 */
function drawsLabel(type: string): boolean {
  const honored = Object.prototype.hasOwnProperty.call(HONORED_PROPERTIES, type)
    ? HONORED_PROPERTIES[type]
    : null;
  return honored ? honored.includes("label") : type !== "label";
}

interface BasicPropertiesProps {
  element: UIElement;
  /** Read by the matrix setup picker, which needs the device list. */
  project: ProjectConfig;
  pages: UIPage[];
  macros?: { id: string; name: string }[];
  /** Where the element sits — geometry lives in the page's layout now, so the
   *  fader/meter orientation swap has to be handed both halves. */
  placement?: Placement;
  onChangePlacement?: (placement: Placement) => void;
  onChange: (patch: Partial<UIElement>) => void;
  onRename?: (newId: string) => void;
}

export function BasicProperties({
  element,
  project,
  pages,
  macros = [],
  placement,
  onChangePlacement,
  onChange,
  onRename,
}: BasicPropertiesProps) {
  useIntrinsicAspect(element, onChange);
  const TypeFields = typeFieldsFor(element.type);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {/* ID — inline rename, with reference rewriting in onRename handler */}
      <FieldRow label="ID">
        <ElementIdField id={element.id} onRename={onRename} />
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

      {/* Tag -- what this control calls itself when it runs a macro. Free
          text on purpose: "1", "hdmi2" and "row_3" are all the same to the
          platform, and the author is naming their own array. */}
      <FieldRow label="Tag">
        <input
          value={element.tag || ""}
          onChange={(e) => onChange({ tag: e.target.value || undefined })}
          placeholder="optional"
          style={{ flex: 1 }}
        />
      </FieldRow>
      <div
        style={{
          fontSize: "var(--font-size-xs, 11px)",
          color: "var(--text-muted)",
          margin: "-4px 0 4px 0",
        }}
      >
        A macro this control runs reads it as <code>$trigger.tag</code>, so a row
        of buttons can share one macro instead of needing one each.
      </div>

      {drawsLabel(element.type) && <LabelFields element={element} onChange={onChange} />}
      {TypeFields && (
        <TypeFields
          element={element}
          project={project}
          pages={pages}
          macros={macros}
          placement={placement}
          onChangePlacement={onChangePlacement}
          onChange={onChange}
        />
      )}
    </div>
  );
}

/** What a row needs: the element, and where its edits go. */
interface FieldProps {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
}

/** What a type's fields may read beyond the element itself. */
interface SectionProps extends FieldProps {
  project: ProjectConfig;
  pages: UIPage[];
  macros: { id: string; name: string }[];
  placement?: Placement;
  onChangePlacement?: (placement: Placement) => void;
}

/**
 * The fields each element type has beyond the ones every element has, in the
 * order they are drawn. A type with no row here (a plugin type the Builder has
 * never seen) gets only those.
 */
const TYPE_FIELDS: Record<string, (props: SectionProps) => React.JSX.Element> = {
  button: ButtonFields,
  label: LabelTextFields,
  page_nav: PageNavFields,
  slider: SliderFields,
  text_input: TextInputFields,
  image: ImageFields,
  camera_preset: CameraPresetFields,
  gauge: GaugeFields,
  level_meter: LevelMeterFields,
  fader: FaderFields,
  group: GroupFields,
  clock: ClockFields,
  list: ListFields,
  matrix: MatrixFields,
  keypad: KeypadFields,
  custom: CustomFields,
  plugin: PluginFields,
  select: SelectFields,
};

/** The row for this type, read as the table's own: `constructor` is not a type. */
function typeFieldsFor(type: string): ((props: SectionProps) => React.JSX.Element) | null {
  return Object.prototype.hasOwnProperty.call(TYPE_FIELDS, type) ? TYPE_FIELDS[type] : null;
}


/**
 * The label and its line breaks, for the types whose renderer draws one. Only
 * those: a field on a type that never draws it is how a project ends up
 * carrying a caption nobody can see, which the page review then warns about.
 */
function LabelFields({ element, onChange }: FieldProps) {
  return (
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
  );
}

/** A button: what it shows, its picture, and its icon. */
function ButtonFields({ element, onChange }: SectionProps) {
  return (
    <>
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
      {(element.display_mode === "image" || element.display_mode === "image_text") && (
        <>
          <FieldRow label="Image">
            <AssetPicker
              value={element.button_image || ""}
              onChange={(v) => onChange({ button_image: v || undefined })}
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
          {element.button_image && (
            <>
              <FieldRow label="Frameless">
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={!!element.frameless}
                    onChange={(e) => onChange({ frameless: e.target.checked || undefined })}
                  />
                  Hide background and border (image-as-button)
                </label>
              </FieldRow>
              <FieldRow label="Effect">
                <select
                  value={element.image_blend_mode || "none"}
                  onChange={(e) => onChange({ image_blend_mode: e.target.value === "none" ? undefined : e.target.value })}
                  style={{ flex: 1 }}
                  title="How the image reacts to the button's background color"
                >
                  <option value="none">None</option>
                  <option value="multiply">Tint (darker)</option>
                  <option value="screen">Tint (lighter)</option>
                  <option value="mask">Recolor shape</option>
                </select>
              </FieldRow>
              {element.image_blend_mode && element.image_blend_mode !== "none" && (
                <TintSourceStrip element={element} onChange={onChange} />
              )}
              <FieldRow label="Opacity">
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={element.image_opacity ?? 1}
                  onChange={(e) => {
                    const v = parseFloat(e.target.value);
                    onChange({ image_opacity: v >= 1 ? undefined : v });
                  }}
                  style={{ flex: 1 }}
                />
                <span style={{ fontSize: 11, color: "var(--text-muted)", minWidth: 32, textAlign: "right" }}>
                  {Math.round((element.image_opacity ?? 1) * 100)}%
                </span>
              </FieldRow>
            </>
          )}
        </>
      )}
      <IconFields element={element} onChange={onChange} />
    </>
  );
}

/** A label's own text, its line breaks and its rounding, and its icon. */
function LabelTextFields({ element, onChange }: SectionProps) {
  return (
    <>
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
        <ShownDecimalsRow element={element} onChange={onChange} placeholder="As reported" />
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: -2, marginBottom: 4 }}>
          Rounds a bound value when it is a number. Text is shown exactly as the device reports it.
        </div>
      </>
      <IconFields element={element} onChange={onChange} />
    </>
  );
}

/** A page nav: where it goes, whether it lights up there, and its icon. */
function PageNavFields({ element, pages, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Target Page">
        <SearchableSelect
          value={element.target_page || ""}
          onChange={(target_page) => onChange({ target_page })}
          groups={[
            { options: [{ value: "$back", label: "$back (dismiss overlay)", hint: "$back" }] },
            ...pageGroups(pages),
          ]}
          placeholder="Select page..."
          searchPlaceholder="Search pages..."
          style={{ flex: 1 }}
        />
      </FieldRow>
      <FieldRow label="Current page">
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={!!element.highlight_current}
            onChange={(e) => onChange({ highlight_current: e.target.checked || undefined })}
          />
          Light up while its page is showing
        </label>
      </FieldRow>
      <IconFields element={element} onChange={onChange} />
    </>
  );
}

/** A slider's travel, look, output range and sending. */
function SliderFields({ element, placement, onChangePlacement, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Min">
        <input
          type="number"
          value={element.min ?? ""}
          onChange={(e) => onChange({ min: numOrUndefined(e.target.value) })}
          placeholder="0"
          style={{ flex: 1 }}
        />
      </FieldRow>
      <FieldRow label="Max">
        <input
          type="number"
          value={element.max ?? ""}
          onChange={(e) => onChange({ max: numOrUndefined(e.target.value) })}
          placeholder="100"
          style={{ flex: 1 }}
        />
      </FieldRow>
      <FieldRow label="Step">
        <input
          type="number"
          value={element.step ?? ""}
          onChange={(e) => onChange({ step: numOrUndefined(e.target.value) })}
          placeholder="1"
          style={{ flex: 1 }}
          min={0.01}
          step={0.1}
        />
      </FieldRow>
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
      <FieldRow label="Orientation">
        <select
          value={element.orientation || "horizontal"}
          onChange={(e) => {
            const newOrientation = e.target.value;
            const oldOrientation = element.orientation || "horizontal";
            const patch: Partial<typeof element> = { orientation: newOrientation };
            onChange(patch);
            // A fader turned on its side wants its box turned with it.
            if (newOrientation !== oldOrientation && placement && onChangePlacement) {
              const swapped = { ...placement, w: placement.h, h: placement.w };
              onChangePlacement(swapped);
              showInfo(
                `Swapped the box to ${swapped.w.toFixed(1)}% x ${swapped.h.toFixed(1)}% for ${newOrientation} layout`,
              );
            }
          }}
          style={{ flex: 1 }}
        >
          <option value="horizontal">Horizontal</option>
          <option value="vertical">Vertical</option>
        </select>
      </FieldRow>
      <FieldRow label="Thumb Size">
        <input
          type="number"
          value={remToPx(element.thumb_size) ?? ""}
          onChange={(e) => onChange({ thumb_size: pxToRem(numOrUndefined(e.target.value)) ?? undefined })}
          placeholder="44"
          style={{ flex: 1 }}
          min={16}
          max={80}
          step={2}
        />
      </FieldRow>
      <FieldRow label="Show Value">
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={(element.style?.show_value as boolean) ?? false}
            onChange={(e) => onChange({ style: { ...element.style, show_value: e.target.checked } })}
          />
          Display current value
        </label>
      </FieldRow>
      <FieldRow label="Output Min">
        <input
          type="number"
          value={element.output_min ?? ""}
          onChange={(e) => onChange({ output_min: e.target.value === "" ? undefined : Number(e.target.value) })}
          placeholder="Same as Min"
          style={{ flex: 1 }}
        />
      </FieldRow>
      <FieldRow label="Output Max">
        <input
          type="number"
          value={element.output_max ?? ""}
          onChange={(e) => onChange({ output_max: e.target.value === "" ? undefined : Number(e.target.value) })}
          placeholder="Same as Max"
          style={{ flex: 1 }}
        />
      </FieldRow>
      <FieldRow label="Scale to Full Range">
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={element.scale_to_full !== false}
            onChange={(e) => onChange({ scale_to_full: e.target.checked })}
          />
          Hide output limit from end user
        </label>
      </FieldRow>
      <ResponseCurveFields element={element} onChange={onChange} />
      <ValueBehaviorFields element={element} onChange={onChange} includeUnit />
    </>
  );
}

/** A text input's placeholder. */
function TextInputFields({ element, onChange }: SectionProps) {
  return (
    <FieldRow label="Placeholder">
      <input
        value={element.placeholder || ""}
        onChange={(e) => onChange({ placeholder: e.target.value })}
        placeholder="Placeholder text..."
        style={{ flex: 1 }}
      />
    </FieldRow>
  );
}

/** An image's picture, its address, its description and how it fits. */
function ImageFields({ element, onChange }: SectionProps) {
  return (
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
      <FieldRow label="Alt Text">
        <input
          value={element.label || ""}
          onChange={(e) => onChange({ label: e.target.value || undefined })}
          placeholder="Describe the image"
          style={{ flex: 1, fontSize: 11 }}
        />
      </FieldRow>
      <FieldRow label="Fit">
        <select
          value={element.object_fit || "contain"}
          onChange={(e) => onChange({ object_fit: e.target.value })}
          style={{ flex: 1 }}
        >
          <option value="contain">Contain (fit, may letterbox)</option>
          <option value="cover">Cover (fill, may crop)</option>
          <option value="fill">Fill (stretch to fit)</option>
        </select>
      </FieldRow>
    </>
  );
}

/** A camera preset's number, and its icon. */
function CameraPresetFields({ element, onChange }: SectionProps) {
  return (
    <>
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
      <IconFields element={element} onChange={onChange} />
    </>
  );
}

/** A gauge's range, readout, arc and colour zones. */
function GaugeFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Min">
        <input type="number" value={element.min ?? ""} onChange={(e) => onChange({ min: numOrUndefined(e.target.value) })} placeholder="0" style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Max">
        <input type="number" value={element.max ?? ""} onChange={(e) => onChange({ max: numOrUndefined(e.target.value) })} placeholder="100" style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Unit">
        <input value={element.unit || ""} onChange={(e) => onChange({ unit: e.target.value })} placeholder="%, dB, etc." style={{ flex: 1 }} />
      </FieldRow>
      <ShownDecimalsRow element={element} onChange={onChange} placeholder="1" />
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
      <FieldRow label="Arc Angle">
        <input type="number" value={element.arc_angle ?? ""} onChange={(e) => onChange({ arc_angle: numOrUndefined(e.target.value) })} placeholder="240" min={90} max={360} style={{ flex: 1 }} />
      </FieldRow>

      <SubSection label="Gauge Appearance" />
      <FieldRow label="Gauge Color">
        <InlineColorPicker size="md" clearable
          value={String(element.style?.gauge_color || "")}
          onChange={(v) => onChange({ style: { ...element.style, gauge_color: v || undefined } })}
        />
      </FieldRow>
      <FieldRow label="Background Arc">
        <InlineColorPicker size="md" clearable
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
  );
}

/** A level meter's range, direction and segments. */
function LevelMeterFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Min">
        <input type="number" value={element.min ?? ""} onChange={(e) => onChange({ min: numOrUndefined(e.target.value) })} placeholder="-60" style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Max">
        <input type="number" value={element.max ?? ""} onChange={(e) => onChange({ max: numOrUndefined(e.target.value) })} placeholder="0" style={{ flex: 1 }} />
      </FieldRow>
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
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
      <FieldRow label="Green to">
        <input
          type="number"
          value={(element.style?.green_to as number) ?? ""}
          onChange={(e) => onChange({ style: { ...element.style, green_to: numOrUndefined(e.target.value) } })}
          placeholder="-12"
          style={{ flex: 1 }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>dB</span>
      </FieldRow>
      <FieldRow label="Yellow to">
        <input
          type="number"
          value={(element.style?.yellow_to as number) ?? ""}
          onChange={(e) => onChange({ style: { ...element.style, yellow_to: numOrUndefined(e.target.value) } })}
          placeholder="-3"
          style={{ flex: 1 }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>dB</span>
      </FieldRow>
    </>
  );
}

/** A fader's travel, readout, output range and sending. */
function FaderFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Min">
        <input type="number" value={element.min ?? ""} onChange={(e) => onChange({ min: numOrUndefined(e.target.value) })} placeholder="0" style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Max">
        <input type="number" value={element.max ?? ""} onChange={(e) => onChange({ max: numOrUndefined(e.target.value) })} placeholder="100" style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Step">
        <input type="number" value={element.step ?? ""} onChange={(e) => onChange({ step: numOrUndefined(e.target.value) })} placeholder="1" min={0.01} step={0.1} style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Unit">
        <input value={element.unit || ""} onChange={(e) => onChange({ unit: e.target.value })} placeholder="e.g. dB, %" style={{ flex: 1 }} />
      </FieldRow>
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
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

      <SubSection label="Output Range" />
      <FieldRow label="Output Min">
        <input
          type="number"
          value={element.output_min ?? ""}
          onChange={(e) => onChange({ output_min: e.target.value === "" ? undefined : Number(e.target.value) })}
          placeholder="Same as Min"
          style={{ flex: 1 }}
          step="any"
        />
      </FieldRow>
      <FieldRow label="Output Max">
        <input
          type="number"
          value={element.output_max ?? ""}
          onChange={(e) => onChange({ output_max: e.target.value === "" ? undefined : Number(e.target.value) })}
          placeholder="Same as Max"
          style={{ flex: 1 }}
          step="any"
        />
      </FieldRow>
      <FieldRow label="Scale to Full Range">
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={element.scale_to_full !== false}
            onChange={(e) => onChange({ scale_to_full: e.target.checked })}
          />
          Hide output limit from end user
        </label>
      </FieldRow>
      <ResponseCurveFields element={element} onChange={onChange} />
      <ValueBehaviorFields element={element} onChange={onChange} />
    </>
  );
}

/** Where a group draws its label. */
function GroupFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Label Pos">
        <select value={element.label_position || "top-left"} onChange={(e) => onChange({ label_position: e.target.value })} style={{ flex: 1 }}>
          <option value="top-left">Top Left</option>
          <option value="top-center">Top Center</option>
          <option value="top-right">Top Right</option>
          <option value="bottom-left">Bottom Left</option>
          <option value="bottom-center">Bottom Center</option>
          <option value="bottom-right">Bottom Right</option>
        </select>
      </FieldRow>
    </>
  );
}

/** A clock's mode, format, what it counts from, and its time zone. */
function ClockFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Mode">
        <select value={element.clock_mode || "time"} onChange={(e) => onChange({ clock_mode: e.target.value, format: "" })} style={{ flex: 1 }}>
          <option value="time">Time</option>
          <option value="date">Date</option>
          <option value="datetime">Date + Time</option>
          <option value="countdown">Countdown</option>
          <option value="elapsed">Elapsed</option>
          <option value="meeting">Meeting Timer</option>
        </select>
      </FieldRow>
      <FieldRow label="Format">
        <input value={element.format || ""} onChange={(e) => onChange({ format: e.target.value })} placeholder={{ time: "h:mm A", date: "MMM D, YYYY", datetime: "MMM D, YYYY h:mm A", countdown: "HH:mm:ss", elapsed: "HH:mm:ss", meeting: "mm:ss" }[element.clock_mode || "time"] || "h:mm A"} style={{ flex: 1 }} />
      </FieldRow>
      <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5, padding: "0 4px", marginTop: -4 }}>
        <strong>Tokens:</strong>{" "}
        h/hh (12hr) &middot; H/HH (24hr) &middot; mm (min) &middot; ss (sec) &middot; A/a (AM/pm)
        &middot; D/DD (day) &middot; M/MM (month #) &middot; MMM (Jan) &middot; MMMM (January)
        &middot; YYYY (year) &middot; ddd (Mon) &middot; dddd (Monday)
      </div>
      {element.clock_mode === "countdown" && (
        <>
          <FieldRow label="Target Time">
            <input
              type="datetime-local"
              value={element.target_time || ""}
              onChange={(e) => onChange({ target_time: e.target.value || undefined })}
              style={{ flex: 1, fontSize: 11 }}
            />
          </FieldRow>
          <FieldRow label="State Key">
            <VariableKeyPicker
              value={((element.bindings as { show?: { value?: { key?: string } } })?.show?.value?.key)
                || (element.start_key || "")}
              onChange={(key) => {
                const bindings = (element.bindings || {}) as Record<string, unknown>;
                const show = (bindings.show || {}) as Record<string, unknown>;
                if (key) {
                  onChange({ bindings: { ...bindings, show: { ...show, value: { source: "state", key } } }, target_time: undefined });
                } else {
                  const { value: _v, ...restShow } = show;
                  const nextBindings: Record<string, unknown> = { ...bindings };
                  if (Object.keys(restShow).length > 0) nextBindings.show = restShow;
                  else delete nextBindings.show;
                  onChange({ bindings: nextBindings, start_key: undefined });
                }
              }}
              showDeviceState
              placeholder="Select a state key (ISO datetime)..."
              style={{ flex: 1 }}
            />
          </FieldRow>
          <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "0 0 0 76px" }}>
            Set a fixed target time, or bind to a state key containing an ISO datetime. State key takes priority.
          </div>
        </>
      )}
      {element.clock_mode === "elapsed" && (
        <>
          <FieldRow label="Start Key">
            <VariableKeyPicker
              value={element.start_key || ""}
              onChange={(key) => onChange({ start_key: key || undefined })}
              showDeviceState
              placeholder="Select a state key (ISO datetime)..."
              style={{ flex: 1 }}
            />
          </FieldRow>
          <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "0 0 0 76px" }}>
            State key containing the start time (ISO datetime). Timer shows time elapsed since that value.
          </div>
        </>
      )}
      {element.clock_mode === "meeting" && (
        <FieldRow label="Duration">
          <input type="number" value={element.duration_minutes ?? ""} onChange={(e) => onChange({ duration_minutes: numOrUndefined(e.target.value) })} placeholder="60" min={1} style={{ flex: 1 }} />
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
  );
}

/** A list's style, row height and items. */
function ListFields({ element, onChange }: SectionProps) {
  return (
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
        <input type="number" value={remToPx(element.item_height) ?? ""} onChange={(e) => onChange({ item_height: pxToRem(numOrUndefined(e.target.value)) ?? undefined })} placeholder="44" min={24} max={120} style={{ flex: 1 }} />
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
  );
}

/** A keypad's digits, layout, sending and display. */
function KeypadFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Digits">
        <input type="number" value={element.digits ?? ""} onChange={(e) => onChange({ digits: numOrUndefined(e.target.value) })} placeholder={String(KEYPAD_DIGITS_DEFAULT)} min={1} max={10} style={{ flex: 1 }} />
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
          Send without Enter
        </label>
      </FieldRow>
      <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "0 0 0 76px" }}>
        {(element.keypad_style || "numeric") === "phone"
          ? "The phone layout has no Enter key, so it sends only this way: as soon as Digits keys are pressed, or after the Send Delay with no key press."
          : "Sends as soon as Digits keys are pressed, or after the Send Delay with no key press. With this off, Enter sends."}
      </div>
      {(element.auto_send ?? false) && (
        <>
          <FieldRow label="Send Delay">
            <input
              type="number"
              value={element.auto_send_delay_ms ?? KEYPAD_SEND_DELAY_DEFAULT_MS}
              onChange={(e) => onChange({ auto_send_delay_ms: e.target.value ? Number(e.target.value) : undefined })}
              min={0} max={5000} step={100} style={{ flex: 1 }}
            />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>ms</span>
          </FieldRow>
          <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "0 0 0 76px" }}>
            How long after the last key press a shorter entry is sent. Under {KEYPAD_SEND_DELAY_DEFAULT_MS} ms, a code can be sent before it is finished.
          </div>
        </>
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
      {(element.show_display ?? true) && (
        <FieldRow label="Mask Display">
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={element.mask_display ?? false}
              onChange={(e) => onChange({ mask_display: e.target.checked })}
            />
            Show a dot for each digit typed
          </label>
        </FieldRow>
      )}
    </>
  );
}

/** A custom control: the page it runs, and what it may touch. */
function CustomFields({ element, onChange }: SectionProps) {
  return (
    <>
      <CustomControlConfig
        file={element.custom_file || ""}
        config={element.custom_config || {}}
        onChange={onChange}
      />
      <GrantEditor grant={element.grant} onChange={(grant) => onChange({ grant })} />
    </>
  );
}

/** A plugin element: its settings, and what it may touch. */
function PluginFields({ element, onChange }: SectionProps) {
  return (
    <>
      <PluginElementConfig
        pluginId={element.plugin_id || ""}
        pluginType={element.plugin_type || ""}
        config={element.plugin_config || {}}
        onChange={(cfg) => onChange({ plugin_config: cfg })}
      />
      <GrantEditor grant={element.grant} onChange={(grant) => onChange({ grant })} />
    </>
  );
}

/** The icon a text-drawing control shows beside its words. */
function IconFields({ element, onChange }: FieldProps) {
  return (
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
              value={remToPx(element.icon_size) ?? 24}
              onChange={(e) =>
                onChange({
                  icon_size: e.target.value ? pxToRem(Number(e.target.value)) ?? undefined : undefined,
                })
              }
              min={12}
              max={64}
              style={{ width: 64 }}
            />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
          </FieldRow>
          <FieldRow label="Icon Color">
            <InlineColorPicker size="md" clearable
              value={element.icon_color || ""}
              onChange={(v) => onChange({ icon_color: v || undefined })}
            />
          </FieldRow>
        </>
      )}
    </>
  );
}

/** A select's options. */
function SelectFields({ element, onChange }: SectionProps) {
  return (
    <OptionsEditor
      options={element.options ?? []}
      onChange={(options) => onChange({ options })}
    />
  );
}

/**
 * Give a picture element the shape of the picture in it.
 *
 * This is the "intrinsic" aspect lock. It can't be written when the element is
 * created — a fresh image element has no picture and therefore no shape — so it
 * is written the moment a source is chosen, and only if the author has not
 * already set a lock of their own. The renderer invents nothing on its own, so
 * if the authoring surface doesn't do this, nothing does.
 *
 * It probes the committed source with this render's patch callback: a callback
 * retained by the picker would still hold the element from before selection.
 * Another edit, a new selection or an unmount makes that render stale, and
 * cancels the probe.
 */
function useIntrinsicAspect(element: UIElement, onChange: (patch: Partial<UIElement>) => void) {
  useEffect(() => {
    const src = pictureToMeasure(element);
    if (!src) return;
    const probe = new Image();
    let cancelled = false;
    probe.onload = () => {
      if (cancelled) return;
      const ratio = aspectOf(probe);
      if (ratio !== null) onChange({ aspect_lock: ratio });
    };
    probe.src = src.startsWith("assets://") ? getAssetUrl(src.slice("assets://".length)) : src;
    return () => { cancelled = true; };
  }, [element, onChange]);
}

/** The picture whose shape this element still needs, if it needs one. */
function pictureToMeasure(element: UIElement): string | null {
  if (element.type !== "image" || element.aspect_lock != null) return null;
  return element.src || null;
}

/** A loaded picture's width over its height, to four places; none for a zero side. */
function aspectOf(picture: HTMLImageElement): number | null {
  if (!picture.naturalWidth || !picture.naturalHeight) return null;
  return Math.round((picture.naturalWidth / picture.naturalHeight) * 10000) / 10000;
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

  const rowKeys = useRowKeys(options.length);

  const removeOption = (index: number) => {
    rowKeys.removeAt(index);
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
            key={rowKeys.keys[i]}
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

  const rowKeys = useRowKeys(items.length);

  const removeItem = (index: number) => {
    rowKeys.removeAt(index);
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
          <div key={rowKeys.keys[i]} style={{ display: "flex", alignItems: "center", gap: 4 }}>
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

function ElementIdField({
  id,
  onRename,
}: {
  id: string;
  onRename?: (newId: string) => void;
}) {
  const [draft, setDraft] = useState(id);
  const [focused, setFocused] = useState(false);

  // Reset draft when the underlying id changes (selection switch, undo, etc.)
  // — only when the field isn't being actively edited.
  useEffect(() => {
    if (!focused) setDraft(id);
  }, [id, focused]);

  const commit = () => {
    if (!onRename) return;
    const trimmed = draft.trim();
    if (trimmed === id) return;
    onRename(trimmed);
    // The handler shows an error toast if invalid; reset the draft if the
    // parent didn't actually rename.
    setDraft(id);
  };

  return (
    <input
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false);
        commit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          (e.target as HTMLInputElement).blur();
        } else if (e.key === "Escape") {
          e.preventDefault();
          setDraft(id);
          (e.target as HTMLInputElement).blur();
        }
      }}
      readOnly={!onRename}
      title={onRename ? "Rename element (Enter to commit, Esc to cancel)" : "Element ID"}
      style={{
        flex: 1,
        background: onRename ? "var(--bg-primary)" : "var(--bg-surface)",
        cursor: onRename ? "text" : "default",
      }}
    />
  );
}

/**
 * Response curve fields shared by the slider and fader property panels. Linear
 * moves the value proportionally with the travel; Logarithmic tapers the travel
 * so it feels like a real audio fader (equal travel = equal loudness step). The
 * dB-range field is the one "advanced" number — how many decibels the throw
 * spans — and only shows once Logarithmic is chosen, so the common case stays a
 * single dropdown. This is a panel feel setting only; the value sent to the
 * device is unchanged.
 */
function ResponseCurveFields({
  element,
  onChange,
}: {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
}) {
  const isLog = element.response === "logarithmic";
  return (
    <>
      <SubSection label="Response" />
      <FieldRow label="Response">
        <select
          value={element.response || "linear"}
          onChange={(e) => onChange({ response: e.target.value })}
          style={{ flex: 1 }}
        >
          <option value="linear">Linear</option>
          <option value="logarithmic">Logarithmic (audio)</option>
        </select>
      </FieldRow>
      {isLog && (
        <>
          <FieldRow label="Curve (dB)">
            <input
              type="number"
              value={element.response_db_range ?? 60}
              onChange={(e) =>
                onChange({ response_db_range: e.target.value === "" ? undefined : Number(e.target.value) })
              }
              min={6}
              max={120}
              step={1}
              style={{ flex: 1 }}
            />
          </FieldRow>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: -2, marginBottom: 4 }}>
            Decibels the throw spans. Larger gives finer control near the bottom. Leave at 60 for a typical audio taper.
          </div>
        </>
      )}
    </>
  );
}

/**
 * How many decimal places a readout shows. Every element that DRAWS a number
 * offers this, because a device float arrives at full float64 width: an amp
 * reporting 0.06 A puts 0.06000000238418579 on the panel unless something
 * rounds it. `placeholder` is what that element type does when the field is
 * left empty, so the author can see the default without reading a doc.
 *
 * Display only — the value sent to a device is formatted by the driver's
 * command parameter, never by this.
 */
function ShownDecimalsRow({
  element,
  onChange,
  placeholder,
}: {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
  placeholder: string;
}) {
  return (
    <FieldRow label="Shown decimals">
      <input
        type="number"
        value={element.display_decimals ?? ""}
        onChange={(e) =>
          onChange({ display_decimals: e.target.value === "" ? undefined : Number(e.target.value) })
        }
        placeholder={placeholder}
        min={0}
        max={6}
        step={1}
        style={{ flex: 1 }}
      />
    </FieldRow>
  );
}

/**
 * Value/send behavior shared by slider and fader. `Send` chooses whether the
 * control streams commands while dragging or fires a single command on release
 * — the latter for gear that can't absorb a burst. `includeUnit` adds the unit
 * field for the slider (the fader already has its own).
 */
function ValueBehaviorFields({
  element,
  onChange,
  includeUnit,
}: {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
  includeUnit?: boolean;
}) {
  const onRelease = element.send_on_release === true;
  return (
    <>
      <SubSection label="Value & Send" />
      {includeUnit && (
        <FieldRow label="Unit">
          <input
            value={element.unit || ""}
            onChange={(e) => onChange({ unit: e.target.value })}
            placeholder="e.g. dB, %"
            style={{ flex: 1 }}
          />
        </FieldRow>
      )}
      <ShownDecimalsRow element={element} onChange={onChange} placeholder="Auto" />
      <FieldRow label="Send">
        <select
          value={onRelease ? "release" : "live"}
          onChange={(e) => onChange({ send_on_release: e.target.value === "release" })}
          style={{ flex: 1 }}
        >
          <option value="live">While dragging</option>
          <option value="release">On release only</option>
        </select>
      </FieldRow>
      {!onRelease && (
        <FieldRow label="Rate (ms)">
          <input
            type="number"
            value={element.send_throttle_ms ?? ""}
            onChange={(e) =>
              onChange({ send_throttle_ms: e.target.value === "" ? undefined : Number(e.target.value) })
            }
            placeholder={element.type === "fader" ? "50" : "100"}
            min={0}
            step={10}
            style={{ flex: 1 }}
          />
        </FieldRow>
      )}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: -2, marginBottom: 4 }}>
        {!onRelease && "Rate is the shortest time between commands while dragging; 0 sends every movement. "}
        "On release only" sends one command when you let go. Use it for devices that can't keep up with a stream of commands. Shown decimals affect the readout, not the value sent (a driver formats that).
      </div>
    </>
  );
}

type PanelConfigField = {
  key: string;
  label: string;
  type: string;
  default?: unknown;
  options?: string[];
  options_source?: string;
};

/**
 * Renders one panel-element config_schema field. Routes ref types to the same
 * pickers the plugin CONFIG_SCHEMA form uses (see panelElementFieldKind), so an
 * author declaring `state_key`/`device_ref`/`macro_ref` gets a picker instead
 * of a raw text box, and `text` is a textarea while `string` is single-line.
 */
function PanelFieldControl({
  field,
  config,
  onChange,
}: {
  field: PanelConfigField;
  config: Record<string, unknown>;
  onChange: (cfg: Record<string, unknown>) => void;
}) {
  const set = (v: unknown) => onChange({ ...config, [field.key]: v });
  const strVal = String(config[field.key] ?? field.default ?? "");

  switch (panelElementFieldKind(field)) {
    case "boolean":
      return (
        <input
          type="checkbox"
          checked={config[field.key] != null ? !!config[field.key] : !!field.default}
          onChange={(e) => set(e.target.checked)}
        />
      );
    case "select":
      return (
        <OptionSourcePicker
          value={strVal}
          staticOptions={field.options}
          optionsSource={field.options_source}
          onChange={set}
          emptyHint={
            field.options_source
              ? "Nothing has offered a source yet. A device whose driver publishes a preview shows up here on its own; anything else is added by hand under Video Streams."
              : undefined
          }
        />
      );
    case "number":
      return (
        <input
          type="number"
          value={config[field.key] != null ? Number(config[field.key]) : ""}
          placeholder={field.default != null ? String(field.default) : undefined}
          onChange={(e) => set(field.type === "integer" ? intOrUndefined(e.target.value) : numOrUndefined(e.target.value))}
          step={field.type === "float" ? 0.1 : 1}
          style={{ flex: 1 }}
        />
      );
    case "state_key":
      return <VariableKeyPicker value={strVal} onChange={set} style={{ flex: 1 }} />;
    case "macro_ref":
      return <MacroRefPicker value={strVal} onChange={set} style={{ flex: 1 }} />;
    case "device_ref":
      return <DeviceRefPicker value={strVal} onChange={set} style={{ flex: 1 }} />;
    case "textarea":
      return (
        <textarea
          value={strVal}
          onChange={(e) => set(e.target.value)}
          placeholder={field.default != null ? String(field.default) : ""}
          rows={4}
          style={{ flex: 1, resize: "vertical", fontFamily: "var(--font-mono, monospace)", fontSize: "var(--font-size-sm)" }}
        />
      );
    default:
      return (
        <input
          value={strVal}
          onChange={(e) => set(e.target.value)}
          placeholder={field.default != null ? String(field.default) : ""}
          style={{ flex: 1 }}
        />
      );
  }
}

function PluginElementConfig({
  pluginId,
  pluginType,
  config,
  onChange,
}: {
  pluginId: string;
  pluginType: string;
  config: Record<string, unknown>;
  onChange: (cfg: Record<string, unknown>) => void;
}) {
  const panelElements = usePluginStore((s) => s.extensions.panel_elements);
  const ext = panelElements.find(
    (e) => e.plugin_id === pluginId && e.type === pluginType,
  );
  const schema = ext?.config_schema;

  const [jsonMode, setJsonMode] = useState(false);

  return (
    <>
      <FieldRow label="Plugin">
        <input value={pluginId} readOnly style={{ flex: 1, opacity: 0.6 }} />
      </FieldRow>
      <FieldRow label="Type">
        <input value={pluginType} readOnly style={{ flex: 1, opacity: 0.6 }} />
      </FieldRow>

      {schema && schema.length > 0 && !jsonMode ? (
        <>
          {schema.map((field) => (
            <FieldRow key={field.key} label={field.label}>
              <PanelFieldControl field={field} config={config} onChange={onChange} />
            </FieldRow>
          ))}
          <button
            onClick={() => setJsonMode(true)}
            style={{ fontSize: 10, color: "var(--text-muted)", background: "transparent", border: "none", cursor: "pointer", alignSelf: "flex-start", textDecoration: "underline" }}
          >
            Edit as JSON
          </button>
        </>
      ) : (
        <>
          <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "2px 0" }}>
            {schema && schema.length > 0 ? "Raw JSON:" : "Plugin configuration (JSON):"}
          </div>
          <textarea
            value={JSON.stringify(config, null, 2)}
            onChange={(e) => {
              try { onChange(JSON.parse(e.target.value)); } catch { /* invalid JSON */ }
            }}
            rows={4}
            style={{ width: "100%", fontSize: 11, fontFamily: "monospace", resize: "vertical" }}
          />
          {schema && schema.length > 0 && (
            <button
              onClick={() => setJsonMode(false)}
              style={{ fontSize: 10, color: "var(--text-muted)", background: "transparent", border: "none", cursor: "pointer", alignSelf: "flex-start", textDecoration: "underline" }}
            >
              Edit as form
            </button>
          )}
        </>
      )}
    </>
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

  const rowKeys = useRowKeys(zones.length);

  const removeZone = (index: number) => {
    rowKeys.removeAt(index);
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
          <div key={rowKeys.keys[i]} style={{ display: "flex", alignItems: "center", gap: 4 }}>
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

interface TintSourceStripProps {
  element: UIElement;
  onChange: (patch: Partial<UIElement>) => void;
}

function TintSourceStrip({ element, onChange }: TintSourceStripProps) {
  const fb = ((element.bindings as { show?: { look?: unknown } })?.show?.look ?? null) as
    | {
        states?: Record<string, { bg_color?: string }>;
        style_active?: { bg_color?: string };
        style_inactive?: { bg_color?: string };
      }
    | null;

  const baseBg = (element.style?.bg_color as string | undefined);
  const fallback = baseBg || "#424242";

  type Swatch = { name: string; color: string };
  let swatches: Swatch[] = [];
  let hasBinding = false;

  if (fb?.states) {
    hasBinding = true;
    for (const [name, appearance] of Object.entries(fb.states)) {
      swatches.push({ name, color: appearance?.bg_color || fallback });
    }
  } else if (fb?.style_active || fb?.style_inactive) {
    hasBinding = true;
    swatches.push({ name: "off", color: fb.style_inactive?.bg_color || fallback });
    swatches.push({ name: "on", color: fb.style_active?.bg_color || "#2196F3" });
  } else {
    swatches.push({ name: "base", color: fallback });
  }

  const createDefaultBinding = () => {
    const bindings = (element.bindings || {}) as Record<string, unknown>;
    const show = (bindings.show || {}) as Record<string, unknown>;
    onChange({
      bindings: {
        ...bindings,
        show: {
          ...show,
          look: {
            source: "state",
            key: "",
            states: {
              off: { bg_color: fallback },
              on: { bg_color: "#2196F3" },
            },
            default_state: "off",
          },
        },
      },
    });
  };

  return (
    <div style={{ padding: "0 0 6px 90px", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Tints using:</span>
        {swatches.map((s) => (
          <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div
              style={{
                width: 16,
                height: 16,
                background: s.color,
                border: "1px solid var(--border-color)",
                borderRadius: 3,
              }}
              title={s.color}
            />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{s.name}</span>
          </div>
        ))}
        {!hasBinding && (
          <button
            onClick={createDefaultBinding}
            style={{
              padding: "2px 8px",
              fontSize: 11,
              color: "var(--accent)",
              background: "transparent",
              border: "1px solid var(--accent)",
              borderRadius: 3,
              cursor: "pointer",
            }}
            title="Create a feedback binding with on/off state colors"
          >
            + Add state colors
          </button>
        )}
      </div>
      {element.image_blend_mode === "mask" && !element.frameless && (
        <div style={{ fontSize: 10, color: "var(--warning, #ff9800)", lineHeight: 1.4 }}>
          Recolor shape reuses the button's background color to fill the image, so the logo blends into the button rectangle. Turn on Frameless to see the shape clearly.
        </div>
      )}
      {!hasBinding && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.4 }}>
          Image tints with the button's background. Add state colors so the image reacts to a state change.
        </div>
      )}
      {hasBinding && fb?.states && Object.keys(fb.states).length > 0 && !((element.bindings as { show?: { look?: { key?: string } } })?.show?.look)?.key && (
        <div style={{ fontSize: 10, color: "var(--warning, #ff9800)", lineHeight: 1.4 }}>
          Pick a state key in the Bindings panel to wire this up.
        </div>
      )}
    </div>
  );
}
