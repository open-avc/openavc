/**
 * The Basic fields of the controls that show or set a number: slider, fader,
 * gauge and level meter.
 *
 * They share their rows: the two ends of the travel (each type's placeholders
 * are the panel's defaults for it), the output range a slider or fader sends
 * across, the readout's rounding, the response curve and how a drag sends.
 * The rounding and the curve are the panel's own; the output range and the
 * sending decide what a device is sent, and its driver formats the value.
 */
import { Plus, X } from "lucide-react";
import type { Placement } from "../../../api/types";
import { showInfo } from "../../../store/toastStore";
import { MatchDriverRangeRow } from "../BindingEditor/DeviceValuePicker";
import { pxToRem, remToPx } from "../uiBuilderHelpers";
import { CheckboxRow, FieldRow, SubSection, type FieldProps } from "./FieldRow";
import { numOrUndefined } from "./numericField";
import { useRowKeys } from "./rowKeys";
import { StyleColorRow, StyleNumberRow, StyleToggle } from "./StyleRows";

export function SliderFields({
  element,
  placement,
  onChangePlacement,
  onChange,
}: FieldProps & { placement?: Placement; onChangePlacement?: (placement: Placement) => void }) {
  return (
    <>
      <RangeFields element={element} onChange={onChange} placeholders={["0", "100"]} />
      <StepField element={element} onChange={onChange} />
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
      <FieldRow label="Orientation">
        <select
          value={element.orientation || "horizontal"}
          onChange={(e) => {
            const newOrientation = e.target.value;
            onChange({ orientation: newOrientation });
            if (newOrientation !== (element.orientation || "horizontal")) {
              turnBox(newOrientation, placement, onChangePlacement);
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
      <StyleToggle element={element} onChange={onChange} field="show_value" label="Show Value" fallback={false}>
        Display current value
      </StyleToggle>
      <OutputRangeFields element={element} onChange={onChange} />
      <ResponseCurveFields element={element} onChange={onChange} />
      <ValueBehaviorFields element={element} onChange={onChange} includeUnit />
    </>
  );
}

/** A slider turned on its side wants its box turned with it, and says so. */
function turnBox(
  orientation: string,
  placement: Placement | undefined,
  onChangePlacement: ((placement: Placement) => void) | undefined,
) {
  if (!placement || !onChangePlacement) return;
  const swapped = { ...placement, w: placement.h, h: placement.w };
  onChangePlacement(swapped);
  showInfo(
    `Swapped the box to ${swapped.w.toFixed(1)}% x ${swapped.h.toFixed(1)}% for ${orientation} layout`,
  );
}

export function FaderFields({ element, onChange }: FieldProps) {
  return (
    <>
      <RangeFields element={element} onChange={onChange} placeholders={["0", "100"]} />
      <StepField element={element} onChange={onChange} />
      <UnitField element={element} onChange={onChange} placeholder="e.g. dB, %" />
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
      <UprightOrientationField element={element} onChange={onChange} />

      <SubSection label="Display Options" />
      <StyleToggle element={element} onChange={onChange} field="show_value" label="Show Value" fallback>
        Display current value
      </StyleToggle>
      <StyleToggle element={element} onChange={onChange} field="show_scale" label="Show Scale" fallback>
        Show scale markings
      </StyleToggle>

      <SubSection label="Output Range" />
      <OutputRangeFields element={element} onChange={onChange} step="any" />
      <ResponseCurveFields element={element} onChange={onChange} />
      <ValueBehaviorFields element={element} onChange={onChange} />
    </>
  );
}

export function GaugeFields({ element, onChange }: FieldProps) {
  return (
    <>
      <RangeFields element={element} onChange={onChange} placeholders={["0", "100"]} />
      <UnitField element={element} onChange={onChange} placeholder="%, dB, etc." />
      <ShownDecimalsRow element={element} onChange={onChange} placeholder="1" />
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
      <FieldRow label="Arc Angle">
        <input type="number" value={element.arc_angle ?? ""} onChange={(e) => onChange({ arc_angle: numOrUndefined(e.target.value) })} placeholder="240" min={90} max={360} style={{ flex: 1 }} />
      </FieldRow>
      <GaugeAppearance element={element} onChange={onChange} />
    </>
  );
}

function GaugeAppearance({ element, onChange }: FieldProps) {
  return (
    <>
      <SubSection label="Gauge Appearance" />
      <StyleColorRow element={element} onChange={onChange} field="gauge_color" label="Gauge Color" />
      <StyleColorRow element={element} onChange={onChange} field="gauge_bg_color" label="Background Arc" />
      <StyleNumberRow
        element={element} onChange={onChange} field="gauge_width" label="Arc Width"
        fallback={8} limits={{ min: 2, max: 20 }} unit="px"
      />
      <StyleToggle element={element} onChange={onChange} field="show_value" label="Show Value" fallback>
        Display numeric value
      </StyleToggle>
      <StyleToggle element={element} onChange={onChange} field="show_ticks" label="Show Ticks" fallback>
        Show tick marks around the arc
      </StyleToggle>
      {(element.style?.show_ticks as boolean) !== false && (
        <StyleNumberRow
          element={element} onChange={onChange} field="tick_count" label="Tick Count"
          fallback={5} limits={{ min: 2, max: 20 }}
        />
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

export function LevelMeterFields({ element, onChange }: FieldProps) {
  return (
    <>
      <RangeFields element={element} onChange={onChange} placeholders={["-60", "0"]} />
      <MatchDriverRangeRow element={element} onElementPatch={onChange} />
      <UprightOrientationField element={element} onChange={onChange} />
      <MeterAppearance element={element} onChange={onChange} />
    </>
  );
}

function MeterAppearance({ element, onChange }: FieldProps) {
  return (
    <>
      <SubSection label="Meter Appearance" />
      <StyleNumberRow
        element={element} onChange={onChange} field="meter_segments" label="Segments"
        fallback={20} limits={{ min: 5, max: 40 }}
      />
      <StyleToggle element={element} onChange={onChange} field="show_peak" label="Show Peak" fallback>
        Hold peak level indicator
      </StyleToggle>
      {(element.style?.show_peak as boolean) !== false && (
        <StyleNumberRow
          element={element} onChange={onChange} field="peak_hold_ms" label="Peak Hold"
          fallback={1500} limits={{ min: 500, max: 5000, step: 100 }} unit="ms"
        />
      )}
      <MeterThresholdRow element={element} onChange={onChange} field="green_to" label="Green to" placeholder="-12" />
      <MeterThresholdRow element={element} onChange={onChange} field="yellow_to" label="Yellow to" placeholder="-3" />
    </>
  );
}

/** Where one colour band of the meter ends, in dB; blank is the panel's default. */
function MeterThresholdRow({
  element,
  onChange,
  field,
  label,
  placeholder,
}: FieldProps & { field: string; label: string; placeholder: string }) {
  return (
    <FieldRow label={label}>
      <input
        type="number"
        value={(element.style?.[field] as number) ?? ""}
        onChange={(e) => onChange({ style: { ...element.style, [field]: numOrUndefined(e.target.value) } })}
        placeholder={placeholder}
        style={{ flex: 1 }}
      />
      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>dB</span>
    </FieldRow>
  );
}

/** The value at each end of the travel; the placeholders are the panel's defaults for this type. */
function RangeFields({
  element,
  onChange,
  placeholders,
}: FieldProps & { placeholders: [min: string, max: string] }) {
  return (
    <>
      <FieldRow label="Min">
        <input type="number" value={element.min ?? ""} onChange={(e) => onChange({ min: numOrUndefined(e.target.value) })} placeholder={placeholders[0]} style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Max">
        <input type="number" value={element.max ?? ""} onChange={(e) => onChange({ max: numOrUndefined(e.target.value) })} placeholder={placeholders[1]} style={{ flex: 1 }} />
      </FieldRow>
    </>
  );
}

function StepField({ element, onChange }: FieldProps) {
  return (
    <FieldRow label="Step">
      <input type="number" value={element.step ?? ""} onChange={(e) => onChange({ step: numOrUndefined(e.target.value) })} placeholder="1" min={0.01} step={0.1} style={{ flex: 1 }} />
    </FieldRow>
  );
}

function UnitField({ element, onChange, placeholder }: FieldProps & { placeholder: string }) {
  return (
    <FieldRow label="Unit">
      <input value={element.unit || ""} onChange={(e) => onChange({ unit: e.target.value })} placeholder={placeholder} style={{ flex: 1 }} />
    </FieldRow>
  );
}

/** A fader's or meter's direction: upright unless set. Its box stays as it is. */
function UprightOrientationField({ element, onChange }: FieldProps) {
  return (
    <FieldRow label="Orientation">
      <select value={element.orientation || "vertical"} onChange={(e) => onChange({ orientation: e.target.value })} style={{ flex: 1 }}>
        <option value="vertical">Vertical</option>
        <option value="horizontal">Horizontal</option>
      </select>
    </FieldRow>
  );
}

/**
 * What a slider or fader sends at each end of its travel, when that is not its
 * Min and Max, and whether the end user sees the full travel or the limit.
 * `step` is the output fields' own (the fader's takes any decimal).
 */
function OutputRangeFields({ element, onChange, step }: FieldProps & { step?: string }) {
  return (
    <>
      <FieldRow label="Output Min">
        <input
          type="number"
          value={element.output_min ?? ""}
          onChange={(e) => onChange({ output_min: e.target.value === "" ? undefined : Number(e.target.value) })}
          placeholder="Same as Min"
          style={{ flex: 1 }}
          step={step}
        />
      </FieldRow>
      <FieldRow label="Output Max">
        <input
          type="number"
          value={element.output_max ?? ""}
          onChange={(e) => onChange({ output_max: e.target.value === "" ? undefined : Number(e.target.value) })}
          placeholder="Same as Max"
          style={{ flex: 1 }}
          step={step}
        />
      </FieldRow>
      <CheckboxRow
        label="Scale to Full Range"
        checked={element.scale_to_full !== false}
        onToggle={(on) => onChange({ scale_to_full: on })}
      >
        Hide output limit from end user
      </CheckboxRow>
    </>
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
function ResponseCurveFields({ element, onChange }: FieldProps) {
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
export function ShownDecimalsRow({
  element,
  onChange,
  placeholder,
}: FieldProps & { placeholder: string }) {
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
}: FieldProps & { includeUnit?: boolean }) {
  const onRelease = element.send_on_release === true;
  return (
    <>
      <SubSection label="Value & Send" />
      {includeUnit && <UnitField element={element} onChange={onChange} placeholder="e.g. dB, %" />}
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
