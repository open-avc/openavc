/**
 * The secondary info screen: a row of display items, each a heading over a
 * live value (or static text), with an optional icon, unit and meter.
 */
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { IconPicker } from "../../ui-builder/IconPicker";
import { MeterFields, ZoneFeedbackFields } from "./FieldEditors";
import { fieldInputStyle, panelHintStyle } from "./styles";
import type { DisplayFeedback, MeterConfig } from "./types";

interface InfoItem {
  label?: string;
  source?: string;
  key?: string;
  text?: string;
  icon?: string;
  unit?: string;
  meter?: MeterConfig | boolean;
  feedback?: DisplayFeedback;
  items?: InfoItem[];
}

// One info-screen display element: heading + live value (or static text)
// + icon/unit/meter/feedback.
function InfoItemFields({
  item,
  onChange,
  showTextMode = true,
}: {
  item: InfoItem;
  onChange: (item: InfoItem) => void;
  showTextMode?: boolean;
}) {
  const update = (patch: Partial<InfoItem>) => onChange({ ...item, ...patch });
  const isText = item.source === "text";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {showTextMode && (
        <div>
          <label style={panelHintStyle}>Shows</label>
          <select
            value={isText ? "text" : "state"}
            onChange={(e) =>
              update(
                e.target.value === "text"
                  ? { source: "text" }
                  : { source: "state" }
              )
            }
            style={fieldInputStyle}
          >
            <option value="state">A live state value</option>
            <option value="text">Static text</option>
          </select>
        </div>
      )}
      {isText ? (
        <div>
          <label style={panelHintStyle}>Text</label>
          <input
            type="text"
            value={item.text ?? ""}
            onChange={(e) => update({ text: e.target.value })}
            placeholder="Text shown on the screen"
            style={fieldInputStyle}
          />
        </div>
      ) : (
        <div>
          <label style={panelHintStyle}>State key</label>
          <VariableKeyPicker
            value={item.key ?? ""}
            onChange={(key) => update({ key })}
            placeholder="Pick a state key to display..."
          />
        </div>
      )}
      <div>
        <label style={panelHintStyle}>Heading (optional, shown above)</label>
        <input
          type="text"
          value={item.label ?? ""}
          onChange={(e) => update({ label: e.target.value || undefined })}
          placeholder="e.g. Room Temp"
          style={fieldInputStyle}
        />
      </div>
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        <div style={{ flex: 1 }}>
          <label style={panelHintStyle}>Icon (optional)</label>
          <IconPicker
            value={item.icon ?? ""}
            onChange={(icon) => update({ icon: icon || undefined })}
          />
        </div>
        <div style={{ width: 80 }}>
          <label style={panelHintStyle}>Unit</label>
          <input
            type="text"
            value={item.unit ?? ""}
            placeholder="dB, %"
            onChange={(e) => update({ unit: e.target.value || undefined })}
            style={fieldInputStyle}
          />
        </div>
      </div>
      {!isText && (
        <>
          <MeterFields
            meter={item.meter}
            onChange={(meter) => update({ meter })}
          />
          <ZoneFeedbackFields
            feedback={item.feedback}
            onChange={(feedback) => update({ feedback })}
          />
        </>
      )}
    </div>
  );
}

export function InfoStripEditor({
  config,
  onConfigChange,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const infoStrip = (config.info_strip as InfoItem | undefined) ?? undefined;

  // Mode mirrors the runtime: no config (or source "clock") = clock.
  const mode = !infoStrip
    ? "clock"
    : infoStrip.source === "blank"
      ? "blank"
      : infoStrip.source === "clock"
        ? "clock"
        : Array.isArray(infoStrip.items) && infoStrip.items.length > 0
          ? "items"
          : infoStrip.source === "text"
            ? "text"
            : infoStrip.key || infoStrip.label || infoStrip.icon
              ? "state"
              : "clock";

  const setInfoStrip = (value: InfoItem | undefined) => {
    if (value === undefined) {
      const { info_strip: _drop, ...rest } = config;
      onConfigChange(rest);
    } else {
      onConfigChange({ ...config, info_strip: value });
    }
  };

  const items = infoStrip?.items ?? [];

  return (
    <div style={{ maxWidth: 560 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", maxWidth: 320 }}>
        <div>
          <label style={panelHintStyle}>Show</label>
          <select
            value={mode}
            onChange={(e) => {
              const next = e.target.value;
              if (next === "clock") setInfoStrip(undefined);
              else if (next === "blank") setInfoStrip({ source: "blank" });
              else if (next === "text") setInfoStrip({ source: "text", text: infoStrip?.text ?? "" });
              else if (next === "items") setInfoStrip({ items: [{}, {}] });
              else setInfoStrip({ source: "state", key: infoStrip?.key ?? "" });
            }}
            style={fieldInputStyle}
          >
            <option value="clock">A clock (default)</option>
            <option value="state">A live state value</option>
            <option value="text">Static text</option>
            <option value="items">Two items side by side</option>
            <option value="blank">Nothing (blank)</option>
          </select>
        </div>

        {(mode === "state" || mode === "text") && infoStrip && (
          <InfoItemFields
            item={infoStrip}
            onChange={(item) => setInfoStrip({ ...item })}
            showTextMode={false}
          />
        )}

        {mode === "items" &&
          [0, 1].map((i) => (
            <div
              key={i}
              style={{
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                padding: "var(--space-sm)",
              }}
            >
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                {i === 0 ? "Left item" : "Right item"}
              </div>
              <InfoItemFields
                item={items[i] ?? {}}
                onChange={(item) => {
                  const next = [items[0] ?? {}, items[1] ?? {}];
                  next[i] = item;
                  setInfoStrip({ ...(infoStrip ?? {}), items: next });
                }}
              />
            </div>
          ))}
      </div>
    </div>
  );
}
