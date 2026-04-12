import { useRef, useEffect, useState } from "react";
import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

const DEFAULT_GREEN_TO = -12;
const DEFAULT_YELLOW_TO = -3;

const ACTIVE_COLORS: Record<string, string> = { green: "#4CAF50", yellow: "#FF9800", red: "#F44336" };
const DIM_COLORS: Record<string, string> = { green: "#0f2410", yellow: "#332701", red: "#310d0b" };

function getSegmentZone(
  segIndex: number,
  totalSegments: number,
  min: number,
  max: number,
  greenTo: number,
  yellowTo: number,
): string {
  const range = max - min;
  if (range <= 0) return "green";
  const dbValue = min + (segIndex / (totalSegments - 1)) * range;
  if (dbValue >= yellowTo) return "red";
  if (dbValue >= greenTo) return "yellow";
  return "green";
}

/**
 * LevelMeterRenderer — mirrors panel.js renderLevelMeter().
 * Uses .panel-level-meter, .meter-label, .meter-bar, .meter-segment from panel-elements.css.
 */
export function LevelMeterRenderer({ element, previewMode, liveState }: Props) {
  const min = element.min ?? -60;
  const max = element.max ?? 0;
  const orientation = element.orientation ?? "vertical";
  const totalSegments = (element.style.meter_segments as number) ?? 20;
  const showPeak = (element.style.show_peak as boolean) ?? true;
  const peakHoldMs = (element.style.peak_hold_ms as number) ?? 1500;
  const greenTo = (element.style.green_to as number) ?? DEFAULT_GREEN_TO;
  const yellowTo = (element.style.yellow_to as number) ?? DEFAULT_YELLOW_TO;
  const isVertical = orientation === "vertical";

  const valBinding = element.bindings.value as unknown as ValueBinding | undefined;
  const stateKey = valBinding?.key;

  let currentValue: number;
  if (previewMode && stateKey) {
    const raw = liveState[stateKey];
    currentValue = raw !== undefined && raw !== null ? Number(raw) : min;
  } else {
    currentValue = DEFAULT_GREEN_TO;
  }

  const clampedValue = Math.max(min, Math.min(max, currentValue));
  const range = max - min;
  const fraction = range > 0 ? (clampedValue - min) / range : 0;
  const activeCount = Math.round(fraction * totalSegments);

  const [peakSegment, setPeakSegment] = useState(-1);
  const peakTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevActiveCountRef = useRef(0);

  useEffect(() => {
    if (!showPeak) {
      setPeakSegment(-1);
      return;
    }
    if (activeCount > prevActiveCountRef.current || activeCount > peakSegment) {
      setPeakSegment(activeCount > 0 ? activeCount - 1 : -1);
      if (peakTimerRef.current) clearTimeout(peakTimerRef.current);
      peakTimerRef.current = setTimeout(() => setPeakSegment(-1), peakHoldMs);
    }
    prevActiveCountRef.current = activeCount;
    return () => {
      if (peakTimerRef.current) clearTimeout(peakTimerRef.current);
    };
  }, [activeCount, showPeak, peakHoldMs, peakSegment]);

  const overrides = buildElementStyle(element.style);

  // Build segments matching panel.js DOM (div.meter-segment with data-zone)
  const segments: React.ReactNode[] = [];
  for (let i = 0; i < totalSegments; i++) {
    const zone = getSegmentZone(i, totalSegments, min, max, greenTo, yellowTo);
    const isActive = i < activeCount;
    const isPeak = showPeak && i === peakSegment && !isActive;
    const bgColor = isActive ? ACTIVE_COLORS[zone] : isPeak ? ACTIVE_COLORS[zone] : DIM_COLORS[zone];

    segments.push(
      <div
        key={i}
        className="meter-segment"
        data-zone={zone}
        style={{
          backgroundColor: bgColor,
          opacity: isPeak && !isActive ? 0.7 : 1,
        }}
      />,
    );
  }

  return (
    <div
      className={`panel-element panel-level-meter ${isVertical ? "vertical" : "horizontal"}`}
      style={{ width: "100%", height: "100%", ...overrides }}
    >
      {element.label && <div className="meter-label">{element.label}</div>}
      <div className="meter-bar">{segments}</div>
    </div>
  );
}
