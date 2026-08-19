import { useState } from "react";
import type { ChildPropDef, DeviceInfo } from "../store/api";
import { useEditable } from "./controls/useEditable";
import { ChevronDown, ChevronRight } from "lucide-react";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

/** Coerce an edited text value the same way the raw state rows do. */
function coerce(v: string): unknown {
  if (v === "true" || v === "false") return v === "true";
  if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(v.trim()) && Number.isFinite(Number(v))) {
    return Number(v);
  }
  return v;
}

/** One child property, drawn as whatever the driver declared it to be.
 *
 *  Everything here used to be a text box, including the booleans -- a channel
 *  mute was something you turned on by typing the word "true", one round trip
 *  per letter. The driver has always declared these types; they just were not
 *  being sent. */
function PropRow({ name, def, value, onChange }: {
  name: string;
  def: ChildPropDef | undefined;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const type = def?.type ?? "string";
  const label = def?.label || name;

  if (type === "boolean") {
    return (
      <div className="state-row">
        <span className="state-key" style={{ paddingLeft: 10 }} title={label}>{label}</span>
        <input
          type="checkbox"
          checked={value === true || value === "true"}
          onChange={(e) => onChange(e.target.checked)}
          aria-label={label}
        />
      </div>
    );
  }

  if (type === "enum" && def?.values?.length) {
    return (
      <div className="state-row">
        <span className="state-key" style={{ paddingLeft: 10 }} title={label}>{label}</span>
        <select
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          aria-label={label}
        >
          {!def.values.includes(String(value ?? "")) && (
            <option value={String(value ?? "")}>{String(value ?? "")}</option>
          )}
          {def.values.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>
    );
  }

  const bounded =
    (type === "number" || type === "integer") &&
    typeof def?.min === "number" && typeof def?.max === "number";

  if (bounded) {
    return (
      <NumberRow name={label} def={def!} value={value} onChange={onChange} />
    );
  }

  return <TextRow name={label} value={value} onChange={onChange} />;
}

function NumberRow({ name, def, value, onChange }: {
  name: string;
  def: ChildPropDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const min = def.min as number;
  const max = def.max as number;
  const step = def.step ?? (def.type === "integer" ? 1 : 0.1);
  const server = Math.max(min, Math.min(max, Number(value ?? min) || 0));
  const slider = useEditable(server);
  return (
    <div className="state-row">
      <span className="state-key" style={{ paddingLeft: 10 }} title={name}>{name}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={slider.value}
        style={{ flex: 1, margin: "0 6px", accentColor: "var(--accent)" }}
        onChange={(e) => {
          const next = Number(e.target.value);
          slider.edit(next);
          onChange(next);
        }}
        onPointerUp={slider.commit}
        onPointerCancel={slider.commit}
        onBlur={slider.commit}
        aria-label={name}
      />
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, minWidth: 44, textAlign: "right" }}>
        {slider.value}{def.unit ? ` ${def.unit}` : ""}
      </span>
    </div>
  );
}

function TextRow({ name, value, onChange }: {
  name: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const field = useEditable(String(value ?? ""));
  return (
    <div className="state-row">
      <span className="state-key" style={{ paddingLeft: 10 }} title={name}>{name}</span>
      <input
        style={{ width: 100, textAlign: "right", fontSize: 12, padding: "2px 4px" }}
        value={field.value}
        onChange={(e) => {
          field.edit(e.target.value);
          onChange(coerce(e.target.value));
        }}
        onBlur={field.commit}
        aria-label={name}
      />
    </div>
  );
}

/** Per-child state for modeled child entities. Values are read live from the
 *  flat state dict ("<type>.<id>.<prop>" keys), so the per-key WS updates
 *  that patch device.state keep every row current. */
export function ChildEntitiesPanel({ device, onStateChange }: Props) {
  const [open, setOpen] = useState(true);
  const children = device.children;
  if (!children || Object.keys(children).length === 0) return null;

  const childCount = Object.values(children).reduce(
    (n, t) => n + t.entries.length,
    0,
  );

  return (
    <div className="state-panel" style={{ borderTop: "1px solid var(--border-color)" }}>
      <div
        className="label"
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)", padding: "4px 0" }}
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Children ({childCount})
      </div>
      {open &&
        Object.entries(children).map(([type, info]) =>
          info.entries.map((entry) => (
            <div key={`${type}.${entry.id}`} style={{ marginBottom: 4 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", padding: "2px 0" }}>
                {entry.label}
              </div>
              {info.props.map((prop) => {
                const key = `${type}.${entry.id}.${prop}`;
                return (
                  <PropRow
                    key={key}
                    name={prop}
                    def={info.prop_defs?.[prop]}
                    value={device.state[key]}
                    onChange={(v) => onStateChange(key, v)}
                  />
                );
              })}
            </div>
          )),
        )}
    </div>
  );
}
