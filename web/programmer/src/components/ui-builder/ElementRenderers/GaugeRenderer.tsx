import type { ReactNode } from "react";
import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/** Convert degrees to radians. */
function degToRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/**
 * Compute a point on a circle given center, radius, and angle in degrees.
 * 0 degrees = 3 o'clock; angles increase clockwise (SVG convention).
 */
function polarToCartesian(
  cx: number,
  cy: number,
  r: number,
  angleDeg: number,
): { x: number; y: number } {
  const rad = degToRad(angleDeg);
  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  };
}

/**
 * Build an SVG arc path from startAngle to endAngle (both in degrees,
 * measured clockwise from 3 o'clock in SVG coordinate space).
 */
function describeArc(
  cx: number,
  cy: number,
  r: number,
  startAngle: number,
  endAngle: number,
): string {
  const start = polarToCartesian(cx, cy, r, startAngle);
  const end = polarToCartesian(cx, cy, r, endAngle);
  let sweep = endAngle - startAngle;
  if (sweep < 0) sweep += 360;
  const largeArc = sweep > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`;
}

export function GaugeRenderer({ element, previewMode, liveState }: Props) {
  const min = element.min ?? 0;
  const max = element.max ?? 100;
  const arcAngle = element.arc_angle ?? 240;
  const unit = element.unit ?? "";
  const zones = element.zones;

  // Style properties with defaults
  const gaugeColor = (element.style.gauge_color as string) ?? "#4CAF50";
  const gaugeBgColor = (element.style.gauge_bg_color as string) ?? "#333333";
  const gaugeWidth = (element.style.gauge_width as number) ?? 8;
  const showValue = (element.style.show_value as boolean) ?? true;
  const showTicks = (element.style.show_ticks as boolean) ?? true;
  const tickCount = (element.style.tick_count as number) ?? 5;
  const textColor = (element.style.text_color as string) ?? "#ffffff";

  // Resolve value from binding or use demo
  let value: number;
  const valBinding = element.bindings.value as unknown as
    | ValueBinding
    | undefined;

  if (previewMode && valBinding?.key) {
    const stateValue = liveState[valBinding.key];
    if (stateValue !== undefined && stateValue !== null) {
      value = Number(stateValue);
    } else {
      value = min;
    }
  } else {
    // Demo value at 65% of range
    value = min + (max - min) * 0.65;
  }

  // Clamp value to [min, max]
  value = Math.max(min, Math.min(max, value));

  // Fraction of the arc that should be filled
  const fraction = max !== min ? (value - min) / (max - min) : 0;

  // SVG geometry
  const size = 100; // viewBox units
  const cx = size / 2;
  const cy = size / 2;
  const radius = (size - gaugeWidth * 2 - 8) / 2; // leave room for ticks

  // Arc angles: The arc is centered at the bottom of the circle.
  // For a 240-degree arc, the gap at the bottom is (360 - 240) = 120 degrees.
  // Start angle (SVG degrees, 0 = 3 o'clock, clockwise):
  //   Bottom of circle = 90 degrees (6 o'clock position).
  //   Start = 90 + gap/2, sweeping clockwise through top to 90 - gap/2.
  const gap = 360 - arcAngle;
  const startAngle = 90 + gap / 2;
  const endAngle = startAngle + arcAngle;

  // Background arc (full sweep)
  const bgPath = describeArc(cx, cy, radius, startAngle, endAngle);

  // Foreground arc (partial sweep based on value)
  const valueAngle = startAngle + arcAngle * fraction;

  // Determine foreground color — either from zones or solid gauge_color
  function getColorForValue(val: number): string {
    if (zones && zones.length > 0) {
      for (const zone of zones) {
        if (val >= zone.from && val <= zone.to) {
          return zone.color;
        }
      }
    }
    return gaugeColor;
  }

  // Build zone arc segments if zones are provided, otherwise a single foreground arc
  function renderForeground(): ReactNode[] {
    if (fraction <= 0) return [];

    if (zones && zones.length > 0) {
      // Render each zone as a separate arc segment, clipped to the current value
      const segments: ReactNode[] = [];
      for (let i = 0; i < zones.length; i++) {
        const zone = zones[i];
        // Map zone boundaries to fractions
        const zoneFracStart = Math.max(
          0,
          (zone.from - min) / (max - min),
        );
        const zoneFracEnd = Math.min(
          fraction,
          (zone.to - min) / (max - min),
        );
        if (zoneFracEnd <= zoneFracStart) continue;
        // Clamp to current value fraction
        const segStart = startAngle + arcAngle * zoneFracStart;
        const segEnd = startAngle + arcAngle * zoneFracEnd;
        segments.push(
          <path
            key={`zone-${i}`}
            d={describeArc(cx, cy, radius, segStart, segEnd)}
            fill="none"
            stroke={zone.color}
            strokeWidth={gaugeWidth}
            strokeLinecap="round"
          />,
        );
      }
      // If the value is in a range not covered by any zone, fill with default color
      // We also need to cover any gaps between zones up to the current value
      // For simplicity, render a base foreground arc underneath the zone arcs
      return [
        <path
          key="fg-base"
          d={describeArc(cx, cy, radius, startAngle, valueAngle)}
          fill="none"
          stroke={gaugeColor}
          strokeWidth={gaugeWidth}
          strokeLinecap="round"
        />,
        ...segments,
      ];
    }

    // Single-color foreground
    return [
      <path
        key="fg"
        d={describeArc(cx, cy, radius, startAngle, valueAngle)}
        fill="none"
        stroke={gaugeColor}
        strokeWidth={gaugeWidth}
        strokeLinecap="round"
      />,
    ];
  }

  // Tick marks
  function renderTicks(): ReactNode[] {
    if (!showTicks || tickCount < 2) return [];
    const ticks: ReactNode[] = [];
    const tickLength = 4;
    const outerR = radius + gaugeWidth / 2 + 2;
    const innerR = outerR + tickLength;
    for (let i = 0; i <= tickCount; i++) {
      const frac = i / tickCount;
      const angle = startAngle + arcAngle * frac;
      const outer = polarToCartesian(cx, cy, innerR, angle);
      const inner = polarToCartesian(cx, cy, outerR, angle);
      ticks.push(
        <line
          key={`tick-${i}`}
          x1={inner.x}
          y1={inner.y}
          x2={outer.x}
          y2={outer.y}
          stroke={textColor}
          strokeWidth={1}
          strokeOpacity={0.5}
        />,
      );
    }
    return ticks;
  }

  // Format the display value
  const displayValue =
    value === Math.floor(value) ? String(value) : value.toFixed(1);

  return (
    <div
      className="panel-element panel-gauge"
      style={{ width: "100%", height: "100%" }}
    >
      {element.label && (
        <div className="gauge-label">{element.label}</div>
      )}
      <svg
        viewBox={`0 0 ${size} ${size}`}
        style={{
          width: "100%",
          height: "100%",
          flex: "1 1 auto",
          minHeight: 0,
        }}
      >
        {/* Background arc */}
        <path
          d={bgPath}
          fill="none"
          stroke={gaugeBgColor}
          strokeWidth={gaugeWidth}
          strokeLinecap="round"
        />

        {/* Foreground arc(s) */}
        {renderForeground()}

        {/* Tick marks */}
        {renderTicks()}

        {/* Center value text */}
        {showValue && (
          <>
            <text
              x={cx}
              y={cy - 2}
              textAnchor="middle"
              dominantBaseline="central"
              fill={getColorForValue(value)}
              fontSize={radius * 0.45}
              fontWeight="bold"
              fontFamily="inherit"
            >
              {displayValue}
            </text>
            {unit && (
              <text
                x={cx}
                y={cy + radius * 0.3}
                textAnchor="middle"
                dominantBaseline="central"
                fill={textColor}
                fontSize={radius * 0.22}
                opacity={0.7}
                fontFamily="inherit"
              >
                {unit}
              </text>
            )}
          </>
        )}
      </svg>
    </div>
  );
}
