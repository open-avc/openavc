import type { DeviceInfo } from "../../store/api";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function GenericPanel({ device, onStateChange }: Props) {
  // Keys owned by the modeled child roster render in the Children panel;
  // keep the raw list to the device-level state.
  const childKeys = new Set<string>();
  for (const [type, info] of Object.entries(device.children ?? {})) {
    for (const entry of info.entries) {
      for (const prop of info.props) {
        childKeys.add(`${type}.${entry.id}.${prop}`);
      }
    }
  }
  const entries = Object.entries(device.state).filter(([key]) => !childKeys.has(key));

  return (
    <>
      {/* State key-value list */}
      <div className="state-panel">
        {entries.length === 0 && (
          <div style={{ color: "var(--text-muted)", fontSize: 12, fontStyle: "italic" }}>
            No state variables
          </div>
        )}
        {entries.map(([key, value]) => (
          <div key={key} className="state-row">
            <span className="state-key">{key}</span>
            <input
              style={{ width: 100, textAlign: "right", fontSize: 12, padding: "2px 4px" }}
              value={String(value ?? "")}
              onChange={(e) => {
                // Try to preserve type; only decimal-looking finite values
                // become numbers (hex strings and overflowing literals like
                // 1e999 stay strings — Infinity would JSON-serialize to null)
                const v = e.target.value;
                if (v === "true" || v === "false") onStateChange(key, v === "true");
                else if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(v.trim()) && Number.isFinite(Number(v))) onStateChange(key, Number(v));
                else onStateChange(key, v);
              }}
            />
          </div>
        ))}
      </div>
    </>
  );
}
