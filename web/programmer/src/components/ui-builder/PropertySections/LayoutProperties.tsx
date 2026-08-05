import type { UIElement, Placement } from "../../../api/types";
import { touchTargetWarning, TOUCH_MIN_MM } from "../uiBuilderHelpers";
import { NumericInput } from "../../shared/NumericInput";

interface LayoutPropertiesProps {
  element: UIElement;
  /** Where the element sits, as a percentage of its parent box. */
  placement: Placement;
  /** Containers on this page it could be parented to — never itself, and never
   *  something already inside it. */
  containers: { id: string; label: string }[];
  /** The box the percentages are OF, in reference pixels. An aspect lock is a
   *  pixel ratio, so it only means anything against the real parent. */
  parentPx: { width: number; height: number };
  onChangePlacement: (placement: Placement) => void;
  onChange: (patch: Partial<UIElement>) => void;
  /** Changing the container is NOT a plain patch: the percentages are of
   *  whatever holds the element, so they have to be re-expressed or it jumps. */
  onChangeParent: (parentId: string | null) => void;
  /** Per-layout visibility. Omitted for masters, which have no page layout to
   *  be hidden in. `inheritedFrom` names the layout a hide came from when it
   *  was not this one — hiding accumulates down an inherits chain, so a variant
   *  can add a hide but cannot take back one the layout above it made. */
  hidden?: {
    value: boolean;
    layoutLabel: string;
    inheritedFrom: string | null;
    onChange: (hidden: boolean) => void;
  };
}

export function LayoutProperties({
  element,
  placement,
  containers,
  parentPx,
  onChangePlacement,
  onChange,
  onChangeParent,
  hidden,
}: LayoutPropertiesProps) {
  const lock =
    typeof element.aspect_lock === "number" && element.aspect_lock > 0
      ? element.aspect_lock
      : null;

  const handleChange = (field: keyof Placement, value: number) => {
    const next: Placement = { ...placement, [field]: value };
    // An aspect-locked element keeps its ratio when you type into either box.
    // The ratio is in pixels, so it goes through the reference proportions.
    if (lock && (field === "w" || field === "h")) {
      if (field === "w") {
        next.h = round((((next.w / 100) * parentPx.width) / lock / parentPx.height) * 100);
      } else {
        next.w = round(((((next.h / 100) * parentPx.height) * lock) / parentPx.width) * 100);
      }
    }
    onChangePlacement(next);
  };

  const touch = touchTargetWarning(placement, parentPx);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-sm)" }}>
        <NumberField label="X (%)" value={placement.x} onChange={(v) => handleChange("x", v)} />
        <NumberField label="Y (%)" value={placement.y} onChange={(v) => handleChange("y", v)} />
        <NumberField label="Width (%)" value={placement.w} min={0.1} onChange={(v) => handleChange("w", v)} />
        <NumberField label="Height (%)" value={placement.h} min={0.1} onChange={(v) => handleChange("h", v)} />
      </div>

      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
        Percentages of {element.parent ? "the container" : "the page"}. The same panel on a
        bigger screen of the same shape is the same panel, just bigger.
      </div>

      <div>
        <label style={labelStyle}>Container</label>
        <select
          value={element.parent ?? ""}
          onChange={(e) => onChangeParent(e.target.value || null)}
          style={selectStyle}
        >
          <option value="">Page (no container)</option>
          {containers.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          A container carries its contents when it moves, and one visibility rule on it
          shows or hides everything inside. Moving one in or out keeps it exactly where
          it is on screen. You can also drag it there in the Outline.
        </div>
      </div>

      {hidden && (
        <div>
          <label style={labelStyle}>Show in this layout</label>
          <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={!hidden.value}
              disabled={!!hidden.inheritedFrom}
              onChange={(e) => hidden.onChange(!e.target.checked)}
            />
            <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
              {hidden.value ? "Hidden" : "Shown"} in {hidden.layoutLabel}
            </span>
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
            {hidden.inheritedFrom
              ? `Hidden by the ${hidden.inheritedFrom} layout, which this one follows. Show it there to bring it back everywhere.`
              : "Leave a control out of one arrangement without deleting it. Every other layout still shows it."}
          </div>
        </div>
      )}

      <div>
        <label style={labelStyle}>Aspect Lock</label>
        <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
          <input
            type="checkbox"
            checked={lock !== null}
            onChange={(e) =>
              onChange({
                aspect_lock: e.target.checked
                  ? round(
                      ((placement.w / 100) * parentPx.width) /
                        Math.max(0.0001, (placement.h / 100) * parentPx.height),
                    )
                  : null,
              })
            }
          />
          <NumericInput
            step={0.01}
            min={0.01}
            disabled={lock === null}
            value={lock}
            onCommit={(v) => {
              if (v !== undefined) onChange({ aspect_lock: v });
            }}
            style={{ ...selectStyle, width: 90 }}
          />
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>width ÷ height</span>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          Holds the shape when a screen stretches. A locked element shrinks to fit and
          stays centred, so a round indicator stays round.
        </div>
      </div>

      {touch && (
        <div
          style={{
            fontSize: 11,
            padding: "6px 8px",
            borderRadius: 4,
            background: "rgba(255,152,0,0.12)",
            border: "1px solid rgba(255,152,0,0.4)",
            color: "var(--text-primary)",
          }}
        >
          About {touch.widthPx}&times;{touch.heightPx}px on a 1280&times;800 panel, roughly{" "}
          {touch.widthMm}&times;{touch.heightMm}mm on a 10&quot; one. Under about {TOUCH_MIN_MM}mm is
          hard to hit reliably with a finger. Bigger glass gives you more
          millimetres for the same design; smaller glass gives you fewer. This is
          advice, not a limit.
        </div>
      )}
    </div>
  );
}

function round(v: number): number {
  return Math.round(v * 10000) / 10000;
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
};

function NumberField({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: number;
  /** Size fields floor at 0.1 — a zero-width element can't be grabbed back.
   *  Position stays unclamped: free positioning warns rather than prevents. */
  min?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label style={labelStyle}>{label}</label>
      <NumericInput
        step={0.1}
        min={min}
        value={Number.isFinite(value) ? round(value) : 0}
        onCommit={(v) => {
          if (v !== undefined) onChange(v);
        }}
        style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
      />
    </div>
  );
}
