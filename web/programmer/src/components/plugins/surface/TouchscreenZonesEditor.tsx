/**
 * The touch strip, edited as zones.
 *
 * The runtime generates a zone per dial on its own; taking the strip over here
 * seeds from exactly those zones, so the first edit changes one thing rather
 * than replacing the whole strip.
 */
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useProjectStore } from "../../../store/projectStore";
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { IconPicker } from "../../ui-builder/IconPicker";
import { ActionListEditor, MeterFields, ZoneFeedbackFields } from "./FieldEditors";
import { defaultZonesFromDials } from "./deckHelpers";
import { dialTestBtnStyle, fieldInputStyle, panelHintStyle } from "./styles";
import type { DialAssignment, TouchZone } from "./types";

export function TouchscreenZonesEditor({
  config,
  onConfigChange,
  allowedActions,
  navigateOptions,
  initialExpanded = null,
  dials = [],
  dialCount = 0,
  onSimulate,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  // The workbench canvas opens the editor on the zone that was clicked.
  initialExpanded?: number | null;
  // For seeding custom zones from the current per-dial readouts.
  dials?: DialAssignment[];
  dialCount?: number;
  // Fire real touch input (simulate_input) to test a zone.
  onSimulate?: (payload: Record<string, unknown>) => void;
}) {
  const project = useProjectStore((s) => s.project);
  const touchscreen =
    (config.touchscreen as { zones?: TouchZone[]; idle?: string } | undefined) ?? {};
  const zones = touchscreen.zones ?? [];
  const [expandedZone, setExpandedZone] = useState<number | null>(initialExpanded);

  const setZones = (next: TouchZone[]) => {
    onConfigChange({
      ...config,
      touchscreen: { ...touchscreen, zones: next },
    });
  };
  const updateZone = (i: number, patch: Partial<TouchZone>) =>
    setZones(zones.map((z, j) => (j === i ? { ...z, ...patch } : z)));
  const removeZone = (i: number) => {
    setZones(zones.filter((_, j) => j !== i));
    setExpandedZone(null);
  };
  // Center x of a zone in strip pixels, for the test buttons.
  const zoneCenter = (i: number) => {
    const slot = 800 / Math.max(1, zones.length);
    const z = zones[i] ?? {};
    const x = typeof z.x === "number" ? z.x : i * slot;
    const w = typeof z.w === "number" ? z.w : slot;
    return Math.round(x + w / 2);
  };

  if (!project) return null;

  if (zones.length === 0) {
    const anyDialConfigured = dials.some(
      (d) => d.label || d.icon || d.adjust?.key || d.press?.length || d.cw?.length
    );
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {anyDialConfigured
            ? "The strip is showing one readout per dial (label, icon, live value, meter) — each readout is edited on its dial, so click a knob in the picture to change one. Take over the strip with custom zones when you want your own layout — meters, status panels, wider faders."
            : dialCount > 0
              ? "Nothing is set up yet, so the strip shows a clock. Click a knob in the picture to configure a dial and its readout takes over this strip, or build your own layout with custom zones."
              : "Add zones to put live values, meters, and touch actions on the strip."}
        </div>
        <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
          {dialCount > 0 && (
            <button
              onClick={() => {
                setZones(defaultZonesFromDials(dials, dialCount));
                setExpandedZone(0);
              }}
              style={{
                padding: "5px 10px", borderRadius: "var(--border-radius)",
                background: "var(--accent-bg)", color: "white",
                fontSize: 12, cursor: "pointer",
              }}
              title="Copy the current per-dial readouts into editable zones"
            >
              Customize zones — start from the current ones
            </button>
          )}
          <button
            onClick={() => {
              setZones([{}]);
              setExpandedZone(0);
            }}
            style={{
              padding: "5px 10px", borderRadius: "var(--border-radius)",
              border: "1px dashed var(--border-color)", background: "transparent",
              color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
            }}
          >
            Start empty
          </button>
        </div>
        <div>
          <label style={panelHintStyle}>When nothing is configured</label>
          <select
            value={touchscreen.idle === "blank" ? "blank" : "clock"}
            onChange={(e) =>
              onConfigChange({
                ...config,
                touchscreen: {
                  ...touchscreen,
                  idle: e.target.value === "blank" ? "blank" : undefined,
                },
              })
            }
            style={fieldInputStyle}
          >
            <option value="clock">Show a clock</option>
            <option value="blank">Stay blank</option>
          </select>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 560 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        Custom zones own the whole strip (the per-dial readouts are replaced).
        Zones split it evenly unless given pixel bounds; tapping a zone runs
        its actions.
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {zones.map((zone, i) => {
          const isExpanded = expandedZone === i;
          return (
            <div
              key={i}
              style={{
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                overflow: "hidden",
              }}
            >
              <button
                onClick={() => setExpandedZone(isExpanded ? null : i)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  width: "100%", padding: "6px 10px", fontSize: "var(--font-size-sm)",
                  background: "var(--bg-surface)", textAlign: "left", cursor: "pointer",
                }}
              >
                <span style={{ fontWeight: 500 }}>
                  Zone {i + 1}{zone.label ? ` — ${zone.label}` : ""}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {zone.value_source || "no value"}
                </span>
              </button>
              {isExpanded && (
                <div style={{
                  padding: "var(--space-sm)",
                  borderTop: "1px solid var(--border-color)",
                  display: "flex", flexDirection: "column", gap: "var(--space-sm)",
                }}>
                  {onSimulate && (
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                      <span style={{ fontSize: 11, color: "var(--text-muted)", flex: 1 }}>Try it</span>
                      <button
                        onClick={() => onSimulate({ type: "touch", x: zoneCenter(i) })}
                        title="Tap this zone for real"
                        style={dialTestBtnStyle}
                      >
                        Tap
                      </button>
                      <button
                        onClick={() =>
                          onSimulate({ type: "touch", x: zoneCenter(i), touch_type: "long" })
                        }
                        title="Long-press this zone for real"
                        style={dialTestBtnStyle}
                      >
                        Long
                      </button>
                      <button
                        onClick={() =>
                          onSimulate({
                            type: "touch", x: zoneCenter(i) - 40,
                            x_out: zoneCenter(i) + 40, touch_type: "drag",
                          })
                        }
                        title="Swipe right across this zone"
                        style={dialTestBtnStyle}
                      >
                        Swipe →
                      </button>
                      <button
                        onClick={() =>
                          onSimulate({
                            type: "touch", x: zoneCenter(i) + 40,
                            x_out: zoneCenter(i) - 40, touch_type: "drag",
                          })
                        }
                        title="Swipe left across this zone"
                        style={dialTestBtnStyle}
                      >
                        ← Swipe
                      </button>
                    </div>
                  )}
                  <div>
                    <label style={panelHintStyle}>Label</label>
                    <input
                      type="text"
                      value={zone.label ?? ""}
                      onChange={(e) => updateZone(i, { label: e.target.value || undefined })}
                      placeholder="Text shown in the zone"
                      style={{
                        width: "100%", padding: "4px 6px",
                        borderRadius: "var(--border-radius)",
                        border: "1px solid var(--border-color)",
                        background: "var(--bg-surface)", color: "var(--text-primary)",
                        fontSize: "var(--font-size-sm)",
                      }}
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Label from state (optional, overrides Label)</label>
                    <VariableKeyPicker
                      value={zone.label_source ?? ""}
                      onChange={(key) => updateZone(i, { label_source: key || undefined })}
                      placeholder="Pick a state key for the label..."
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Show value from state</label>
                    <VariableKeyPicker
                      value={zone.value_source ?? ""}
                      onChange={(key) => updateZone(i, { value_source: key || undefined })}
                      placeholder="Pick a state key to display..."
                    />
                  </div>
                  <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                    <div style={{ flex: 1 }}>
                      <label style={panelHintStyle}>Icon (optional)</label>
                      <IconPicker
                        value={zone.icon ?? ""}
                        onChange={(icon) => updateZone(i, { icon: icon || undefined })}
                      />
                    </div>
                    <div style={{ width: 80 }}>
                      <label style={panelHintStyle}>Unit</label>
                      <input
                        type="text"
                        value={zone.unit ?? ""}
                        placeholder="dB, %"
                        onChange={(e) => updateZone(i, { unit: e.target.value || undefined })}
                        style={fieldInputStyle}
                      />
                    </div>
                  </div>
                  <MeterFields
                    meter={zone.meter}
                    bounds={zone.drag_adjust}
                    allowAuto
                    onChange={(meter) => updateZone(i, { meter })}
                  />
                  <ZoneFeedbackFields
                    feedback={zone.feedback}
                    onChange={(feedback) => updateZone(i, { feedback })}
                  />
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      <span style={panelHintStyle}>Background</span>
                      <InlineColorPicker
                        value={zone.bg_color ?? ""}
                        onChange={(c) => updateZone(i, { bg_color: c || undefined })}
                      />
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      <span style={panelHintStyle}>Text</span>
                      <InlineColorPicker
                        value={zone.text_color ?? ""}
                        onChange={(c) => updateZone(i, { text_color: c || undefined })}
                      />
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                    {([["Position (px, optional)", "x"], ["Width (px, optional)", "w"]] as const).map(
                      ([fieldLabel, field]) => (
                        <div key={field} style={{ flex: 1 }}>
                          <label style={panelHintStyle}>{fieldLabel}</label>
                          <input
                            type="number"
                            value={zone[field] ?? ""}
                            placeholder="auto"
                            onChange={(e) =>
                              updateZone(i, {
                                [field]: e.target.value === "" ? undefined : Number(e.target.value),
                              })
                            }
                            style={{
                              width: "100%", padding: "4px 6px",
                              borderRadius: "var(--border-radius)",
                              border: "1px solid var(--border-color)",
                              background: "var(--bg-surface)", color: "var(--text-primary)",
                              fontSize: "var(--font-size-sm)",
                            }}
                          />
                        </div>
                      )
                    )}
                  </div>
                  <div>
                    <label style={panelHintStyle}>Tap actions</label>
                    <ActionListEditor
                      actions={zone.touch ?? []}
                      onChange={(touch) => updateZone(i, { touch: touch.length ? touch : undefined })}
                      project={project}
                      allowedActions={allowedActions}
                      navigateOptions={navigateOptions}
                      addLabel="Add tap action"
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Long-press actions (optional — falls back to tap)</label>
                    <ActionListEditor
                      actions={zone.long_touch ?? []}
                      onChange={(long_touch) =>
                        updateZone(i, { long_touch: long_touch.length ? long_touch : undefined })
                      }
                      project={project}
                      allowedActions={allowedActions}
                      navigateOptions={navigateOptions}
                      addLabel="Add long-press action"
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Swipe adjusts a value (optional)</label>
                    <VariableKeyPicker
                      value={zone.drag_adjust?.key ?? ""}
                      onChange={(key) =>
                        updateZone(i, {
                          drag_adjust: key ? { ...(zone.drag_adjust ?? {}), key } : undefined,
                        })
                      }
                      placeholder="Pick a variable to adjust by swiping..."
                    />
                    {zone.drag_adjust?.key && (
                      <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
                        {(["step", "min", "max"] as const).map((field) => (
                          <div key={field} style={{ flex: 1 }}>
                            <label style={panelHintStyle}>
                              {field[0].toUpperCase() + field.slice(1)}
                            </label>
                            <input
                              type="number"
                              value={zone.drag_adjust?.[field] ?? ""}
                              placeholder={field === "step" ? "1" : "none"}
                              onChange={(e) =>
                                updateZone(i, {
                                  drag_adjust: {
                                    ...(zone.drag_adjust ?? {}),
                                    [field]: e.target.value === "" ? undefined : Number(e.target.value),
                                  },
                                })
                              }
                              style={{
                                width: "100%", padding: "4px 6px",
                                borderRadius: "var(--border-radius)",
                                border: "1px solid var(--border-color)",
                                background: "var(--bg-surface)", color: "var(--text-primary)",
                                fontSize: "var(--font-size-sm)",
                              }}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                    {zone.drag_adjust?.key && (
                      <div style={{ marginTop: "var(--space-xs)" }}>
                        <label
                          style={{
                            display: "flex", alignItems: "center", gap: "var(--space-sm)",
                            fontSize: "var(--font-size-sm)", cursor: "pointer",
                            color: "var(--text-primary)",
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={!!zone.drag_adjust?.fader}
                            onChange={(e) =>
                              updateZone(i, {
                                drag_adjust: {
                                  ...(zone.drag_adjust ?? {}),
                                  fader: e.target.checked || undefined,
                                },
                              })
                            }
                            disabled={
                              zone.drag_adjust?.min === undefined ||
                              zone.drag_adjust?.max === undefined
                            }
                            style={{ accentColor: "var(--accent)" }}
                          />
                          Touch fader
                        </label>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                          {zone.drag_adjust?.min === undefined || zone.drag_adjust?.max === undefined
                            ? "Set Min and Max to enable: taps and swipes will jump straight to the touched position."
                            : "Taps and swipes set the value to the touched position (replaces the tap actions for this zone)."}
                        </div>
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => removeZone(i)}
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "center",
                      gap: "var(--space-xs)", padding: "var(--space-xs)",
                      borderRadius: "var(--border-radius)", background: "transparent",
                      border: "1px solid var(--border-color)", color: "var(--color-error)",
                      fontSize: "var(--font-size-sm)", cursor: "pointer",
                    }}
                  >
                    <Trash2 size={12} />
                    Remove Zone
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <button
        onClick={() => {
          setZones([...zones, {}]);
          setExpandedZone(zones.length);
        }}
        style={{
          marginTop: "var(--space-sm)",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 4,
          padding: "5px 10px", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
        }}
      >
        + Add custom zone
      </button>
    </div>
  );
}
