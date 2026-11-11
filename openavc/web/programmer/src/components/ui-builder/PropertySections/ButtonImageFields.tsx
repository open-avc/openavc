/**
 * A button's picture: which one, how it fits, and how it takes the button's
 * colours.
 *
 * A tinted picture reacts to the button's background colour, so the strip
 * under the effect shows the colours it will take: one per state of the
 * button's look binding, or the button's own background when it has none.
 */
import type { UIElement } from "../../../api/types";
import { AssetPicker } from "../AssetPicker";
import { CheckboxRow, FieldRow, type FieldProps } from "./FieldRow";

export function ButtonImageFields({ element, onChange }: FieldProps) {
  return (
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
      {element.button_image && <PictureTreatment element={element} onChange={onChange} />}
    </>
  );
}

/** What is drawn around the picture, how it blends with the colour, and how strongly. */
function PictureTreatment({ element, onChange }: FieldProps) {
  return (
    <>
      <CheckboxRow
        label="Frameless"
        checked={!!element.frameless}
        onToggle={(on) => onChange({ frameless: on || undefined })}
      >
        Hide background and border (image-as-button)
      </CheckboxRow>
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
  );
}

interface Look {
  key?: string;
  states?: Record<string, { bg_color?: string }>;
  style_active?: { bg_color?: string };
  style_inactive?: { bg_color?: string };
}

interface Swatch {
  name: string;
  color: string;
}

const BUTTON_BACKGROUND = "#424242";
const ACTIVE_BACKGROUND = "#2196F3";

/** The button's look binding, when it has one. */
function lookOf(element: UIElement): Look | null {
  return ((element.bindings as { show?: { look?: unknown } })?.show?.look ?? null) as Look | null;
}

/**
 * The colours a tinted picture takes: one per state the look names, else the
 * look's off and on colours, else the button's own background. `bound` says
 * whether a look decides them.
 */
function tintSwatches(look: Look | null, fallback: string): { swatches: Swatch[]; bound: boolean } {
  if (look?.states) return { bound: true, swatches: stateSwatches(look.states, fallback) };
  if (look?.style_active || look?.style_inactive) return { bound: true, swatches: onOffSwatches(look, fallback) };
  return { bound: false, swatches: [{ name: "base", color: fallback }] };
}

function stateSwatches(states: NonNullable<Look["states"]>, fallback: string): Swatch[] {
  return Object.entries(states).map(([name, appearance]) => ({
    name, color: appearance?.bg_color || fallback,
  }));
}

function onOffSwatches(look: Look, fallback: string): Swatch[] {
  return [
    { name: "off", color: look.style_inactive?.bg_color || fallback },
    { name: "on", color: look.style_active?.bg_color || ACTIVE_BACKGROUND },
  ];
}

/** A look with on and off colours and no key yet, for the author to wire up. */
function withStateColors(element: UIElement, fallback: string): Partial<UIElement> {
  const bindings = (element.bindings || {}) as Record<string, unknown>;
  const show = (bindings.show || {}) as Record<string, unknown>;
  return {
    bindings: {
      ...bindings,
      show: {
        ...show,
        look: {
          source: "state",
          key: "",
          states: {
            off: { bg_color: fallback },
            on: { bg_color: ACTIVE_BACKGROUND },
          },
          default_state: "off",
        },
      },
    },
  };
}

function TintSourceStrip({ element, onChange }: FieldProps) {
  const look = lookOf(element);
  const fallback = (element.style?.bg_color as string | undefined) || BUTTON_BACKGROUND;
  const { swatches, bound } = tintSwatches(look, fallback);

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
        {!bound && (
          <button
            onClick={() => onChange(withStateColors(element, fallback))}
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
      <TintNotes element={element} look={look} bound={bound} />
    </div>
  );
}

/** What the tint still needs from the author: Frameless for a recolor, state colours, a state key. */
function TintNotes({ element, look, bound }: { element: UIElement; look: Look | null; bound: boolean }) {
  return (
    <>
      {element.image_blend_mode === "mask" && !element.frameless && (
        <div style={{ fontSize: 10, color: "var(--warning, #ff9800)", lineHeight: 1.4 }}>
          Recolor shape reuses the button's background color to fill the image, so the logo blends into the button rectangle. Turn on Frameless to see the shape clearly.
        </div>
      )}
      {!bound && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.4 }}>
          Image tints with the button's background. Add state colors so the image reacts to a state change.
        </div>
      )}
      {/* The look names states, but no state key decides between them. */}
      {bound && look?.states && Object.keys(look.states).length > 0 && !look.key && (
        <div style={{ fontSize: 10, color: "var(--warning, #ff9800)", lineHeight: 1.4 }}>
          Pick a state key in the Bindings panel to wire this up.
        </div>
      )}
    </>
  );
}
