/**
 * The Theme Studio's Page Background section: the theme's own colour, a gradient
 * over it and an image over that, each drawn from and written through
 * `pageBackground.ts` and written to the theme whole (`withPageDefaults`). Its
 * title says "modified" while the page background differs from the saved
 * theme. The two number fields are told which theme they edit (`owner`):
 * switching theme draws this section again in place, and a half-typed number
 * would otherwise land on the theme switched to.
 */
import type { CSSProperties } from "react";
import { ColorPickerCell } from "./ColorPickerCell";
import { NumericInput } from "../shared/NumericInput";
import { StudioSection } from "./StudioParts";
import { withPageDefaults, type ThemeDraft } from "./themeEdits";
import {
  readBackground, patched, withCustomColor, withGradient, withGradientPart, withImage, backgroundDiffers,
  DEFAULT_PAGE_COLOR, DEFAULT_GRADIENT_END, DEFAULT_GRADIENT_ANGLE,
  type PageDefaults, type PageBackground, type Gradient,
} from "./pageBackground";

const IMAGE_SIZE_OPTIONS = [
  { value: "cover", label: "Cover" },
  { value: "contain", label: "Contain" },
  { value: "auto", label: "Actual size" },
  { value: "100% 100%", label: "Stretch" },
];

const ROW: CSSProperties = { display: "flex", gap: 8, alignItems: "center" };
const NESTED: CSSProperties = { display: "flex", flexDirection: "column", gap: 4, marginTop: 6 };
const ROW_LABEL: CSSProperties = { width: 90, fontSize: 11, color: "var(--text-secondary)", flexShrink: 0 };
const HINT: CSSProperties = { fontSize: 9, color: "var(--text-muted)", lineHeight: 1.3, marginTop: 2 };
const FIELD: CSSProperties = { flex: 1, fontSize: 11 };
const TEXT_FIELD: CSSProperties = { flex: 1, fontSize: 11, fontFamily: "monospace" };

export function PageBackgroundSection({ draft }: { draft: ThemeDraft }) {
  const { theme, saved, edit } = draft;
  const pageDefaults = (theme.page_defaults as PageDefaults) || {};
  const savedPageDefaults = (saved?.page_defaults as PageDefaults) || {};
  const owner = theme.id ?? "";
  const onChange = (next: PageDefaults) => edit((t) => withPageDefaults(t, next));
  const pd = pageDefaults;
  const background = readBackground(pd);
  const parts = { pd, background, owner, onChange };
  return (
    <StudioSection title="Page Background" modified={backgroundDiffers(pageDefaults, savedPageDefaults)}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 10 }}>
        <div style={HINT}>
          The background painted behind every page that doesn&apos;t set its own. Color, image,
          and gradient layer in that order.
        </div>
        <ColorRows {...parts} />
        <GradientRows {...parts} />
        <ImageRows {...parts} />
      </div>
    </StudioSection>
  );
}

interface PartProps {
  pd: PageDefaults;
  background: PageBackground;
  owner: string;
  onChange: (next: PageDefaults) => void;
}

/** A colour of the page background: its swatch (on `seed` while empty) and its text. */
function colorPair(value: string, seed: string, write: (next: string) => void) {
  return (
    <>
      <ColorPickerCell value={value} fallback={seed} onChange={write} />
      <input type="text" value={value} onChange={(e) => write(e.target.value)} style={TEXT_FIELD} />
    </>
  );
}

function ColorRows({ pd, background, onChange }: PartProps) {
  const color = background.customColor;
  return (
    <div>
      <div style={ROW}>
        <label style={ROW_LABEL}>Color</label>
        <select
          value={color ? "custom" : "inherit"}
          onChange={(e) => onChange(withCustomColor(pd, e.target.value !== "inherit"))}
          style={FIELD}
        >
          <option value="inherit">Theme page background (default)</option>
          <option value="custom">Custom color</option>
        </select>
      </div>
      {color !== "" && (
        <div style={{ ...ROW, marginTop: 4 }}>
          <span style={ROW_LABEL} />
          {colorPair(color, DEFAULT_PAGE_COLOR, (next) => onChange(patched(pd, { background_color: next })))}
        </div>
      )}
    </div>
  );
}

function GradientRows({ pd, background, owner, onChange }: PartProps) {
  const gradient = background.gradient;
  const write = (part: Gradient) => onChange(withGradientPart(pd, part));
  return (
    <div>
      <label style={{ ...ROW, fontSize: 11, color: "var(--text-secondary)", cursor: "pointer" }}>
        <input type="checkbox" checked={!!gradient} onChange={(e) => onChange(withGradient(pd, e.target.checked))} />
        Gradient overlay
      </label>
      {gradient && (
        <div style={NESTED}>
          <div style={ROW}>
            <label style={ROW_LABEL}>From</label>
            {colorPair(String(gradient.from ?? ""), DEFAULT_PAGE_COLOR, (from) => write({ from }))}
          </div>
          <div style={ROW}>
            <label style={ROW_LABEL}>To</label>
            {colorPair(String(gradient.to ?? ""), DEFAULT_GRADIENT_END, (to) => write({ to }))}
          </div>
          <div style={ROW}>
            <label style={ROW_LABEL}>Angle (deg)</label>
            <NumericInput
              owner={`${owner}/page_defaults.gradient.angle`}
              value={gradient.angle ?? DEFAULT_GRADIENT_ANGLE}
              min={0}
              max={360}
              onCommit={(v) => {
                if (v !== undefined) write({ angle: v });
              }}
              style={FIELD}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ImageRows({ pd, background, owner, onChange }: PartProps) {
  const set = (patch: PageDefaults) => onChange(patched(pd, patch));
  return (
    <div>
      <div style={ROW}>
        <label style={ROW_LABEL}>Image</label>
        <input
          type="text"
          value={background.image}
          placeholder="assets://bg.jpg or https://…"
          onChange={(e) => onChange(withImage(pd, e.target.value))}
          style={TEXT_FIELD}
        />
      </div>
      <div style={HINT}>Upload images in the Assets panel, then reference them as assets://name.</div>
      {background.image && (
        <div style={NESTED}>
          <div style={ROW}>
            <label style={ROW_LABEL}>Fit</label>
            <select
              value={background.imageSize}
              onChange={(e) => set({ background_image_size: e.target.value })}
              style={FIELD}
            >
              {IMAGE_SIZE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div style={ROW}>
            <label style={ROW_LABEL}>Position</label>
            <input
              type="text"
              value={background.imagePosition}
              placeholder="center"
              onChange={(e) => set({ background_image_position: e.target.value })}
              style={TEXT_FIELD}
            />
          </div>
          <div style={ROW}>
            <label style={ROW_LABEL}>Opacity</label>
            <NumericInput
              owner={`${owner}/page_defaults.background_image_opacity`}
              value={background.imageOpacity}
              min={0}
              max={1}
              step={0.05}
              onCommit={(v) => {
                if (v !== undefined) set({ background_image_opacity: v });
              }}
              style={FIELD}
            />
          </div>
        </div>
      )}
    </div>
  );
}
