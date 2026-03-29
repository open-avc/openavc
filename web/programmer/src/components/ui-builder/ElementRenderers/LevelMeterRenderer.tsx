import { useRef, useEffect, useState } from "react";
import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/** Segment color thresholds (in dB by convention). */
const DEFAULT_GREEN_TO = -12;
const DEFAULT_YELLOW_TO = -3;

/** Segment colors for each zone. */
const COLOR_GREEN = "#4CAF50";
const COLOR_YELLOW = "#FFC107";
const COLOR_RED = "#F44336";

/** Dim versions of zone colors (solid, no opacity — prevents background bleed-through). */
const DIM_GREEN = "#0f2410";
const DIM_YELLOW = "#332701";
const DIM_RED = "#310d0b";

function getDimColor(color: string): string {
  if (color === COLOR_GREEN) return DIM_GREEN;
  if (color === COLOR_YELLOW) return DIM_YELLOW;
  if (color === COLOR_RED) return DIM_RED;
  // Fallback: darken by 80% (only for valid hex colors)
  if (!color || color.length < 7 || color[0] !== "#") return DIM_GREEN;
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  if (isNaN(r) || isNaN(g) || isNaN(b)) return DIM_GREEN;
  return `rgb(${Math.round(r * 0.2)}, ${Math.round(g * 0.2)}, ${Math.round(b * 0.2)})`;
}

function getSegmentColor(
  segIndex: number,
  totalSegments: number,
  min: number,
  max: number,
): string {
  const range = max - min;
  if (range <= 0) return COLOR_GREEN;
  // Map segment index to dB value
  const dbValue = min + (segIndex / (totalSegments - 1)) * range;
  if (dbValue <= DEFAULT_GREEN_TO) return COLOR_GREEN;
  if (dbValue <= DEFAULT_YELLOW_TO) return COLOR_YELLOW;
  return COLOR_RED;
}

export function LevelMeterRenderer({
  element,
  previewMode,
  liveState,
}: Props) {
  const min = element.min ?? -60;
  const max = element.max ?? 0;
  const orientation = element.orientation ?? "vertical";
  const totalSegments = (element.style.meter_segments as number) ?? 20;
  const showPeak = (element.style.show_peak as boolean) ?? true;
  const peakHoldMs = (element.style.peak_hold_ms as number) ?? 1500;

  // Resolve current value
  const valBinding = element.bindings.value as unknown as
    | ValueBinding
    | undefined;
  const stateKey = valBinding?.key;

  let currentValue: number;
  if (previewMode && stateKey) {
    const raw = liveState[stateKey];
    currentValue =
      raw !== undefined && raw !== null ? Number(raw) : min;
  } else {
    // Demo value: about -12 dB (green zone mostly lit)
    currentValue = DEFAULT_GREEN_TO;
  }

  // Clamp value to range
  const clampedValue = Math.max(min, Math.min(max, currentValue));

  // Peak hold tracking
  const [peakSegment, setPeakSegment] = useState(-1);
  const peakTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevActiveCountRef = useRef(0);

  // Calculate how many segments are active
  const range = max - min;
  const fraction = range > 0 ? (clampedValue - min) / range : 0;
  const activeCount = Math.round(fraction * totalSegments);

  useEffect(() => {
    if (!showPeak) {
      setPeakSegment(-1);
      return;
    }
    // Update peak if current exceeds it
    if (activeCount > prevActiveCountRef.current || activeCount > peakSegment) {
      setPeakSegment(activeCount > 0 ? activeCount - 1 : -1);
      // Reset the decay timer
      if (peakTimerRef.current) clearTimeout(peakTimerRef.current);
      peakTimerRef.current = setTimeout(() => {
        setPeakSegment(-1);
      }, peakHoldMs);
    }
    prevActiveCountRef.current = activeCount;
    return () => {
      if (peakTimerRef.current) clearTimeout(peakTimerRef.current);
    };
  }, [activeCount, showPeak, peakHoldMs, peakSegment]);

  const isVertical = orientation === "vertical";

  // Build segment array — index 0 is the lowest value segment
  const segments: React.ReactNode[] = [];
  for (let i = 0; i < totalSegments; i++) {
    const isActive = i < activeCount;
    const isPeak = showPeak && i === peakSegment && !isActive;
    const color = getSegmentColor(i, totalSegments, min, max);

    segments.push(
      <div
        key={i}
        style={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          borderRadius: 2,
          backgroundColor: isActive ? color : isPeak ? color : getDimColor(color),
          opacity: isPeak && !isActive ? 0.7 : 1,
          transition: "background-color 0.08s ease-out, opacity 0.08s ease-out",
        }}
      />,
    );
  }

  // For vertical orientation, reverse so highest value is at top
  if (isVertical) {
    segments.reverse();
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: isVertical ? "column" : "row",
        alignItems: "stretch",
        width: "100%",
        height: "100%",
        padding: 6,
        gap: 4,
        boxSizing: "border-box",
      }}
    >
      {/* Label area */}
      {element.label && !isVertical && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            fontSize: 11,
            color: "#aaaaaa",
            whiteSpace: "nowrap",
            paddingRight: 4,
            userSelect: "none",
          }}
        >
          {element.label}
        </div>
      )}

      {/* Segment bar */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: isVertical ? "column" : "row",
          gap: 2,
          minWidth: 0,
          minHeight: 0,
        }}
      >
        {segments}
      </div>

      {/* Label at bottom for vertical */}
      {element.label && isVertical && (
        <div
          style={{
            textAlign: "center",
            fontSize: 11,
            color: "#aaaaaa",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            paddingTop: 2,
            userSelect: "none",
          }}
        >
          {element.label}
        </div>
      )}
    </div>
  );
}
