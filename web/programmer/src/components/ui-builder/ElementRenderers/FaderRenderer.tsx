import { useRef } from "react";
import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/** Standard dB scale marks for audio faders. */
const DB_SCALE_MARKS = [-80, -60, -40, -20, -10, -5, 0, 5, 10];

/** Map a value from [min, max] to a 0..1 fraction. */
function valueToFraction(value: number, min: number, max: number): number {
  if (max === min) return 0;
  return Math.max(0, Math.min(1, (value - min) / (max - min)));
}

/** Map a 0..1 fraction back to a value in [min, max], snapped to step. */
function fractionToValue(
  fraction: number,
  min: number,
  max: number,
  step: number,
): number {
  const raw = min + fraction * (max - min);
  const snapped = Math.round(raw / step) * step;
  return Math.max(min, Math.min(max, parseFloat(snapped.toFixed(6))));
}

/** Pick a sensible default position when not in preview mode. */
function defaultValue(min: number, max: number): number {
  // Use 0 if it's within the range (common for dB faders)
  if (min <= 0 && max >= 0) return 0;
  // Otherwise use the midpoint
  return (min + max) / 2;
}

/** Darken a hex color by a factor (0 = black, 1 = original). Returns original if not valid hex. */
function darkenHex(hex: string, factor: number): string {
  if (!hex || hex.length < 7 || hex[0] !== "#") return hex;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  if (isNaN(r) || isNaN(g) || isNaN(b)) return hex;
  return `rgb(${Math.round(r * factor)}, ${Math.round(g * factor)}, ${Math.round(b * factor)})`;
}

export function FaderRenderer({ element, liveState }: Props) {
  const min = element.min ?? -80;
  const max = element.max ?? 10;
  const step = element.step ?? 0.5;
  const unit = element.unit ?? "dB";
  const orientation = element.orientation ?? "vertical";
  const showValue = (element.style.show_value as boolean) ?? true;
  const showScale = (element.style.show_scale as boolean) ?? true;

  // Derive colors from element style (theme-aware)
  const textColor = String(element.style.text_color || "#cccccc");
  const bgColor = String(element.style.bg_color || "#2a2a2a");
  const trackBg = darkenHex(bgColor.startsWith("#") ? bgColor : "#2a2a2a", 0.6);
  const trackBorder = darkenHex(textColor.startsWith("#") ? textColor : "#cccccc", 0.4);
  const handleColor = darkenHex(textColor.startsWith("#") ? textColor : "#888888", 0.65);
  const fillColor = darkenHex(textColor.startsWith("#") ? textColor : "#4a6a8a", 0.5);
  const meterBg = trackBg;

  const valBinding = element.bindings.value as unknown as
    | ValueBinding
    | undefined;
  const meterBinding = element.bindings.meter as unknown as
    | ValueBinding
    | undefined;
  const stateKey = valBinding?.key;
  const meterKey = meterBinding?.key;

  const trackRef = useRef<HTMLDivElement>(null);

  // Determine the displayed value
  let displayValue = defaultValue(min, max);
  if (stateKey) {
    const sv = liveState[stateKey];
    if (sv !== undefined && sv !== null) {
      displayValue = Number(sv);
    }
  }

  // Meter value (optional VU-style indicator alongside the track)
  let meterFraction: number | null = null;
  if (meterKey) {
    const mv = liveState[meterKey];
    if (mv !== undefined && mv !== null) {
      meterFraction = valueToFraction(Number(mv), min, max);
    }
  }

  // --- Scale marks ---
  const scaleMarks = DB_SCALE_MARKS.filter((v) => v >= min && v <= max);

  // Format value for display
  const formatValue = (v: number): string => {
    const decimals = step < 1 ? 1 : 0;
    return `${v.toFixed(decimals)} ${unit}`;
  };

  const fraction = valueToFraction(displayValue, min, max);

  // --- Vertical layout ---
  if (orientation === "vertical") {
    return (
      <div
        className="panel-element panel-fader"
        style={{ width: "100%", height: "100%", boxSizing: "border-box", userSelect: "none" }}
      >
        {/* Label */}
        {element.label && (
          <div
            style={{
              fontSize: 11,
              color: textColor,
              marginBottom: 4,
              textAlign: "center",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              maxWidth: "100%",
            }}
          >
            {element.label}
          </div>
        )}

        {/* Track area: scale | track | meter */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "row",
            alignItems: "stretch",
            width: "100%",
            minHeight: 0,
            position: "relative",
          }}
        >
          {/* Scale marks */}
          {showScale && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                alignItems: "flex-end",
                paddingRight: 4,
                minWidth: 28,
                flexShrink: 0,
              }}
            >
              {scaleMarks
                .slice()
                .reverse()
                .map((mark) => {
                  const markFrac = valueToFraction(mark, min, max);
                  return (
                    <div
                      key={mark}
                      style={{
                        position: "absolute",
                        top: `${(1 - markFrac) * 100}%`,
                        right: "calc(100% - 28px)",
                        transform: "translateY(-50%)",
                        fontSize: 9,
                        color: trackBorder,
                        whiteSpace: "nowrap",
                        lineHeight: 1,
                      }}
                    >
                      {mark}
                    </div>
                  );
                })}
            </div>
          )}

          {/* Track + handle */}
          <div
            style={{
              flex: 1,
              display: "flex",
              justifyContent: "center",
              alignItems: "stretch",
              position: "relative",
              cursor: "default",
            }}
          >
            {/* Track groove */}
            <div
              ref={trackRef}

              style={{
                width: 6,
                backgroundColor: trackBg,
                borderRadius: 3,
                position: "relative",
                border: `1px solid ${trackBorder}`,
              }}
            >
              {/* Filled portion (below handle to min for vertical) */}
              <div
                style={{
                  position: "absolute",
                  bottom: 0,
                  left: 0,
                  right: 0,
                  height: `${fraction * 100}%`,
                  backgroundColor: fillColor,
                  borderRadius: 3,
                  opacity: 0.5,
                }}
              />

              {/* Handle */}
              <div
  
                style={{
                  position: "absolute",
                  bottom: `${fraction * 100}%`,
                  left: "50%",
                  transform: "translate(-50%, 50%)",
                  width: 40,
                  height: 24,
                  backgroundColor: handleColor,
                  borderRadius: 3,
                  border: `1px solid ${trackBorder}`,
                  cursor: "default",
                  boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
                  // Groove lines on the handle (mixing console style)
                  backgroundImage: `repeating-linear-gradient(
                    0deg,
                    transparent,
                    transparent 3px,
                    rgba(0,0,0,0.15) 3px,
                    rgba(0,0,0,0.15) 4px
                  )`,
                  zIndex: 2,
                }}
              />
            </div>
          </div>

          {/* Meter bar (optional) */}
          {meterFraction !== null && (
            <div
              style={{
                width: 4,
                marginLeft: 6,
                backgroundColor: meterBg,
                borderRadius: 2,
                position: "relative",
                flexShrink: 0,
              }}
            >
              <div
                style={{
                  position: "absolute",
                  bottom: 0,
                  left: 0,
                  right: 0,
                  height: `${meterFraction * 100}%`,
                  borderRadius: 2,
                  background:
                    "linear-gradient(to top, #4CAF50, #8BC34A 60%, #FFC107 80%, #F44336 95%)",
                }}
              />
            </div>
          )}
        </div>

        {/* Value display */}
        {showValue && (
          <div
            style={{
              fontSize: 11,
              color: textColor,
              marginTop: 4,
              textAlign: "center",
              whiteSpace: "nowrap",
              fontFamily: "monospace",
            }}
          >
            {formatValue(displayValue)}
          </div>
        )}
      </div>
    );
  }

  // --- Horizontal layout ---
  return (
    <div
      className="panel-element panel-fader"
      style={{ width: "100%", height: "100%", flexDirection: "column", justifyContent: "center", padding: "4px 8px", boxSizing: "border-box", userSelect: "none" }}
    >
      {/* Label */}
      {element.label && (
        <div
          style={{
            fontSize: 11,
            color: "#cccccc",
            marginBottom: 4,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {element.label}
        </div>
      )}

      {/* Track area: scale on top, track, meter below */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "stretch",
          position: "relative",
          minWidth: 0,
        }}
      >
        {/* Scale marks */}
        {showScale && (
          <div
            style={{
              position: "relative",
              height: 14,
              marginBottom: 2,
              flexShrink: 0,
            }}
          >
            {scaleMarks.map((mark) => {
              const markFrac = valueToFraction(mark, min, max);
              return (
                <div
                  key={mark}
                  style={{
                    position: "absolute",
                    left: `${markFrac * 100}%`,
                    transform: "translateX(-50%)",
                    fontSize: 9,
                    color: trackBorder,
                    whiteSpace: "nowrap",
                    lineHeight: 1,
                  }}
                >
                  {mark}
                </div>
              );
            })}
          </div>
        )}

        {/* Track + handle */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "stretch",
            position: "relative",
            cursor: "default",
            flex: 1,
          }}
        >
          {/* Track groove */}
          <div
            ref={trackRef}
            style={{
              height: 6,
              width: "100%",
              backgroundColor: trackBg,
              borderRadius: 3,
              position: "relative",
              border: `1px solid ${trackBorder}`,
            }}
          >
            {/* Filled portion */}
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                bottom: 0,
                width: `${fraction * 100}%`,
                backgroundColor: fillColor,
                borderRadius: 3,
                opacity: 0.5,
              }}
            />

            {/* Handle */}
            <div

              style={{
                position: "absolute",
                left: `${fraction * 100}%`,
                top: "50%",
                transform: "translate(-50%, -50%)",
                width: 24,
                height: 40,
                backgroundColor: handleColor,
                borderRadius: 3,
                border: `1px solid ${trackBorder}`,
                cursor: "default",
                boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
                backgroundImage: `repeating-linear-gradient(
                  90deg,
                  transparent,
                  transparent 3px,
                  rgba(0,0,0,0.15) 3px,
                  rgba(0,0,0,0.15) 4px
                )`,
                zIndex: 2,
              }}
            />
          </div>
        </div>

        {/* Meter bar (optional, horizontal) */}
        {meterFraction !== null && (
          <div
            style={{
              height: 4,
              marginTop: 4,
              backgroundColor: meterBg,
              borderRadius: 2,
              position: "relative",
              flexShrink: 0,
            }}
          >
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                bottom: 0,
                width: `${meterFraction * 100}%`,
                borderRadius: 2,
                background:
                  "linear-gradient(to right, #4CAF50, #8BC34A 60%, #FFC107 80%, #F44336 95%)",
              }}
            />
          </div>
        )}
      </div>

      {/* Value display */}
      {showValue && (
        <div
          style={{
            fontSize: 11,
            color: "#cccccc",
            marginTop: 4,
            textAlign: "center",
            whiteSpace: "nowrap",
            fontFamily: "monospace",
          }}
        >
          {formatValue(displayValue)}
        </div>
      )}
    </div>
  );
}
