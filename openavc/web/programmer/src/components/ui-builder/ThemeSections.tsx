/**
 * The Theme Studio's Theme Info and Theme sections: the theme's name and
 * description, then its foundational tokens, the CSS variables many element
 * types read. Each token is the one place its concept is edited; nothing in
 * `ELEMENT_SECTIONS` (elementControls.ts) repeats one. A token's field is drawn
 * by its kind: a colour says what it inherits while unset, a length is shown in
 * pixels and stored in rem like every panel length, and the font is a list
 * that holds every stack Quick Adjust's Typography writes (`TYPOGRAPHY`, by
 * name, and a test holds the list to it), so a preset chosen there always
 * shows here as one of the choices.
 */
import type { ReactElement } from "react";
import { NumericInput } from "../shared/NumericInput";
import { ColorField } from "./ColorField";
import { CSS_VAR_FALLBACKS } from "./colorUtils";
import { DEFAULT_FONT, radiusPx, typographyFont } from "./quickAdjust";
import { ResetToSaved, StudioSection } from "./StudioParts";
import { differsFromSaved, variablesOf, withInfo, withVariable, type ThemeDraft } from "./themeEdits";
import { displayStyleValue, storeStyleValue } from "./uiBuilderHelpers";

interface ThemeToken {
  key: string;
  label: string;
  kind: "color" | "number" | "font";
  hint?: string;
}

const THEME_TOKENS: readonly ThemeToken[] = [
  { key: "panel_bg", label: "Page Background", kind: "color", hint: "Behind every page" },
  { key: "panel_text", label: "Text Color", kind: "color", hint: "Default text on labels, sliders, gauges, lists, inputs" },
  { key: "surface", label: "Surface", kind: "color", hint: "Slider tracks, dropdowns, text inputs, list rows, fader track, keypad display" },
  { key: "surface_border", label: "Surface Border", kind: "color", hint: "Thin border around surfaces above" },
  { key: "accent", label: "Accent", kind: "color", hint: "Slider fill, fader handle, focus, active button, page nav text" },
  { key: "danger", label: "Danger", kind: "color", hint: "Red zone of level meters, mute indicators, error states" },
  { key: "success", label: "Success", kind: "color", hint: "Green zone of level meters, OK states" },
  { key: "warning", label: "Warning", kind: "color", hint: "Yellow zone of level meters, lock indicators, caution states" },
  { key: "border_radius", label: "Border Radius (px)", kind: "number", hint: "Roundness of every element. 0 sharp, 16+ very round" },
  { key: "font_family", label: "Font Family", kind: "font", hint: "Typeface across the entire panel" },
];

/** The stack a Typography preset writes; a preset not there is a mistake in this list. */
function presetFont(preset: string): string {
  const font = typographyFont(preset);
  if (font === undefined) throw new Error(`No Typography preset "${preset}"`);
  return font;
}

/** The Font Family choices, in order: each stack and what the list calls it. */
const FONT_CHOICES: readonly (readonly [font: string, label: string])[] = [
  [DEFAULT_FONT, "Inter"],
  ["system-ui, sans-serif", "System UI"],
  ["'Roboto', sans-serif", "Roboto"],
  ["'Segoe UI', sans-serif", "Segoe UI"],
  [presetFont("serif"), "Serif"],
  [presetFont("mono"), "Monospace"],
];

const INFO_LABEL = { width: 70, fontSize: 11, color: "var(--text-secondary)" };

export function ThemeInfoSection({ draft }: { draft: ThemeDraft }) {
  const { theme, edit } = draft;
  return (
    <StudioSection title="Theme Info">
      <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 10 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={INFO_LABEL}>Name</label>
          <input
            value={theme.name}
            onChange={(e) => {
              const name = e.target.value;
              edit((t) => withInfo(t, { name }));
            }}
            style={{ flex: 1, fontSize: 12 }}
          />
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
          <label style={{ ...INFO_LABEL, paddingTop: 4 }}>
            Description
          </label>
          <input
            value={theme.description || ""}
            onChange={(e) => {
              const description = e.target.value;
              edit((t) => withInfo(t, { description }));
            }}
            style={{ flex: 1, fontSize: 12 }}
          />
        </div>
      </div>
    </StudioSection>
  );
}

interface TokenFieldProps {
  token: ThemeToken;
  value: unknown;
  /** The theme being edited, which the number field is told. */
  themeId: string;
  onChange: (next: unknown) => void;
}

/** A cleared colour token draws the panel's own default for it. */
function colorToken({ token, value, onChange }: TokenFieldProps) {
  return <ColorField value={value} inherits={CSS_VAR_FALLBACKS[token.key]} fontSize={11} onChange={onChange} />;
}

/** A numeric theme variable is a measurement, and the panel reads stored
 *  measurements as rem. Show px; an unset radius as the panel draws it. */
function numberToken({ token, value, themeId, onChange }: TokenFieldProps) {
  const px = token.key === "border_radius"
    ? radiusPx({ border_radius: value })
    : (displayStyleValue(token.key, Number(value || 0)) as number) ?? 0;
  return (
    <NumericInput
      owner={`${themeId}/${token.key}`}
      value={px}
      onCommit={(v) => {
        if (v !== undefined) onChange(storeStyleValue(token.key, v));
      }}
      min={0}
      max={64}
      style={{ flex: 1, fontSize: 11 }}
    />
  );
}

function fontToken({ value, onChange }: TokenFieldProps) {
  return (
    <select value={String(value ?? DEFAULT_FONT)} onChange={(e) => onChange(e.target.value)} style={{ flex: 1, fontSize: 11 }}>
      {FONT_CHOICES.map(([font, label]) => (
        <option key={font} value={font}>{label}</option>
      ))}
    </select>
  );
}

const TOKEN_FIELDS: Record<ThemeToken["kind"], (props: TokenFieldProps) => ReactElement> = {
  color: colorToken,
  number: numberToken,
  font: fontToken,
};

export function ThemeTokensSection({ draft }: { draft: ThemeDraft }) {
  const { theme, saved, edit } = draft;
  const vars = variablesOf(theme);
  const savedVars = variablesOf(saved);
  return (
    <StudioSection title="Theme">
      <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 10 }}>
        {THEME_TOKENS.map((token) => {
          const value = vars[token.key];
          const savedValue = savedVars[token.key];
          const isModified = differsFromSaved(value, savedValue);
          const set = (next: unknown) => edit((t) => withVariable(t, token.key, next));
          return (
            <div key={token.key}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <label
                  style={{
                    width: 130,
                    fontSize: 11,
                    color: isModified ? "var(--accent)" : "var(--text-secondary)",
                    fontWeight: isModified ? 600 : 400,
                  }}
                  title={token.hint}
                >
                  {token.label}
                </label>
                {TOKEN_FIELDS[token.kind]({ token, value, themeId: theme.id ?? "", onChange: set })}
                {isModified && <ResetToSaved saved={savedValue} size={11} onReset={() => set(savedValue)} />}
              </div>
            </div>
          );
        })}
      </div>
    </StudioSection>
  );
}
