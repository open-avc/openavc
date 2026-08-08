import { useState } from "react";
import type { DeviceInfo } from "../store/api";
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
                const value = device.state[key];
                return (
                  <div key={key} className="state-row">
                    <span className="state-key" style={{ paddingLeft: 10 }}>{prop}</span>
                    <input
                      style={{ width: 100, textAlign: "right", fontSize: 12, padding: "2px 4px" }}
                      value={String(value ?? "")}
                      onChange={(e) => onStateChange(key, coerce(e.target.value))}
                    />
                  </div>
                );
              })}
            </div>
          )),
        )}
    </div>
  );
}
