/**
 * "Can reach" — the per-element grant for a custom control or a plugin panel
 * element.
 *
 * Both types run somebody else's web page inside the panel, so unlike every
 * other element they have no bindings telling the panel what they touch. This
 * is where that is decided, by the person placing the element: tick a device
 * and the control can read its state and send it commands; tick a variable and
 * it can read and write that variable.
 */
import type { ElementGrant } from "../../../api/types";
import { useProjectStore } from "../../../store/projectStore";

const EMPTY: ElementGrant = { devices: [], variables: [], macros: false, navigate: false };

const boxStyle: React.CSSProperties = {
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-surface)",
  maxHeight: 132,
  overflowY: "auto",
  padding: "4px 6px",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: 11,
  cursor: "pointer",
  padding: "2px 0",
};

const noteStyle: React.CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  padding: "2px 0",
};

export function GrantEditor({
  grant,
  onChange,
}: {
  grant: ElementGrant | undefined;
  onChange: (grant: ElementGrant) => void;
}) {
  const devices = useProjectStore((s) => s.project?.devices) ?? [];
  const variables = useProjectStore((s) => s.project?.variables) ?? [];
  const current: ElementGrant = {
    devices: grant?.devices ?? EMPTY.devices,
    variables: grant?.variables ?? EMPTY.variables,
    macros: grant?.macros ?? false,
    navigate: grant?.navigate ?? false,
  };

  const toggleIn = (list: string[], id: string) =>
    list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

  return (
    <>
      <div style={{ fontSize: 11, fontWeight: 600, paddingTop: 6 }}>Can reach</div>
      <div style={noteStyle}>
        This control sees and controls only what you tick. Everything else in the
        project stays hidden from it.
      </div>

      <div style={noteStyle}>Devices</div>
      <div style={boxStyle}>
        {devices.length === 0 && <div style={noteStyle}>No devices in this project yet.</div>}
        {devices.map((d) => (
          <label key={d.id} style={rowStyle}>
            <input
              type="checkbox"
              checked={current.devices.includes(d.id)}
              onChange={() => onChange({ ...current, devices: toggleIn(current.devices, d.id) })}
            />
            {d.name || d.id}
          </label>
        ))}
      </div>

      <div style={noteStyle}>Variables</div>
      <div style={boxStyle}>
        {variables.length === 0 && <div style={noteStyle}>No variables in this project yet.</div>}
        {variables.map((v) => (
          <label key={v.id} style={rowStyle}>
            <input
              type="checkbox"
              checked={current.variables.includes(v.id)}
              onChange={() => onChange({ ...current, variables: toggleIn(current.variables, v.id) })}
            />
            {v.label || v.id}
          </label>
        ))}
      </div>

      <label style={rowStyle}>
        <input
          type="checkbox"
          checked={current.macros}
          onChange={(e) => onChange({ ...current, macros: e.target.checked })}
        />
        Run macros
      </label>
      <label style={rowStyle}>
        <input
          type="checkbox"
          checked={current.navigate}
          onChange={(e) => onChange({ ...current, navigate: e.target.checked })}
        />
        Change pages
      </label>
    </>
  );
}
