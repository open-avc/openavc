/**
 * The Theme Studio's Quick Adjust: the brand accent and the three presets
 * (`quickAdjust.ts`), each marked "modified" while it differs from the saved
 * theme. A preset choice goes back through the Studio's edits like any other,
 * so the radius drag-along applies to Roundness too.
 */
import type { CSSProperties, ReactNode } from "react";
import type { ThemeDefinition } from "../../api/restClient";
import {
  ROUNDNESS, SURFACE_STYLES, TYPOGRAPHY, DEFAULT_FONT, radiusPx, roundnessOf, roundnessRadius,
  surfaceStyleOf, typographyOf, typographyFont, type Preset, type SurfaceStyle,
} from "./quickAdjust";
import { StudioSection } from "./StudioParts";
import { differsFromSaved } from "./themeEdits";

const HEX_RE = /^#[0-9a-f]{6}$/i;
const DEFAULT_ACCENT = "#2196F3";

const labelStyle: CSSProperties = { fontSize: 12, fontWeight: 600, color: "var(--text-primary)", marginBottom: 2 };
const hintStyle: CSSProperties = { fontSize: 10, color: "var(--text-muted)", lineHeight: 1.3, marginBottom: 4 };
const noteStyle: CSSProperties = { fontSize: 10, color: "var(--text-muted)", marginTop: 3 };

function SegmentedControl({ options, value, onChange, ariaLabel }: {
  options: readonly Preset[];
  value: string | null;
  onChange: (value: string) => void;
  ariaLabel?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      style={{ display: "inline-flex", borderRadius: 6, overflow: "hidden", border: "1px solid var(--border-color)" }}
    >
      {options.map((opt, i) => {
        const selected = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(opt.value)}
            style={{
              padding: "5px 14px",
              fontSize: 11,
              fontWeight: selected ? 600 : 400,
              background: selected ? "var(--accent-bg)" : "var(--bg-hover)",
              color: selected ? "#fff" : "var(--text-secondary)",
              border: "none",
              borderRight: i < options.length - 1 ? "1px solid var(--border-color)" : "none",
              cursor: "pointer",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/** One adjustment: its name (marked while it differs from the saved theme), what it changes, its control. */
function Adjustment({ label, modified, hint, children }: {
  label: string;
  modified: boolean;
  hint: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <div style={labelStyle}>
        {label}
        {modified && (
          <span style={{ fontSize: 9, color: "var(--accent)", fontWeight: 600, marginLeft: 6 }}>modified</span>
        )}
      </div>
      <div style={hintStyle}>{hint}</div>
      {children}
    </div>
  );
}

function AccentPicker({ accent, onSet }: { accent: string; onSet: (value: string) => void }) {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <input
        type="color"
        aria-label="Brand accent color"
        value={HEX_RE.test(accent) ? accent : DEFAULT_ACCENT}
        onChange={(e) => onSet(e.target.value)}
        style={{
          width: 36,
          height: 28,
          padding: 0,
          border: "1px solid var(--border-color)",
          borderRadius: 4,
          cursor: "pointer",
          flexShrink: 0,
        }}
      />
      <input
        type="text"
        aria-label="Brand accent hex value"
        value={accent}
        onChange={(e) => onSet(e.target.value)}
        style={{ flex: 1, fontSize: 11, fontFamily: "monospace" }}
      />
    </div>
  );
}

interface QuickAdjustProps {
  vars: Record<string, unknown>;
  defaults: ThemeDefinition["element_defaults"];
  savedVars: Record<string, unknown>;
  savedDefaults: ThemeDefinition["element_defaults"];
  onSetVar: (key: string, value: unknown) => void;
  onApplySurfaceStyle: (style: SurfaceStyle) => void;
}

export function QuickAdjustSection({ vars, defaults, savedVars, savedDefaults, onSetVar, onApplySurfaceStyle }: QuickAdjustProps) {
  const accent = String(vars.accent ?? DEFAULT_ACCENT);
  const radius = radiusPx(vars);
  const roundness = roundnessOf(radius);
  const surfaceStyle = surfaceStyleOf(defaults);
  const fontFamily = String(vars.font_family ?? DEFAULT_FONT);
  const typography = typographyOf(fontFamily);
  const changed = (key: string) => differsFromSaved(vars[key], savedVars[key]);

  return (
    <StudioSection title="Quick Adjust">
      <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 14 }}>
        <Adjustment
          label="Brand Accent"
          modified={changed("accent")}
          hint="Active buttons, slider fills, fader handles, focus rings, and highlights"
        >
          <AccentPicker accent={accent} onSet={(v) => onSetVar("accent", v)} />
        </Adjustment>

        <Adjustment
          label="Roundness"
          modified={changed("border_radius")}
          hint="Corner radius for buttons, sliders, and all elements"
        >
          <SegmentedControl
            ariaLabel="Roundness"
            options={ROUNDNESS}
            value={roundness}
            onChange={(v) => onSetVar("border_radius", roundnessRadius(v))}
          />
          {roundness === null && (
            <div style={noteStyle}>Custom ({radius}px), adjust in the Theme section below</div>
          )}
        </Adjustment>

        <Adjustment
          label="Surface Style"
          modified={surfaceStyle !== surfaceStyleOf(savedDefaults)}
          hint="How elements sit on the page: flat, with depth shadows, or with outlines"
        >
          <SegmentedControl
            ariaLabel="Surface style"
            options={SURFACE_STYLES}
            value={surfaceStyle}
            onChange={(v) => onApplySurfaceStyle(v as SurfaceStyle)}
          />
        </Adjustment>

        <Adjustment
          label="Typography"
          modified={changed("font_family")}
          hint="Font family across the entire panel"
        >
          <SegmentedControl
            ariaLabel="Typography"
            options={TYPOGRAPHY}
            value={typography}
            onChange={(v) => onSetVar("font_family", typographyFont(v))}
          />
          {typography === null && <div style={noteStyle}>Custom: {fontFamily}</div>}
        </Adjustment>
      </div>
    </StudioSection>
  );
}
