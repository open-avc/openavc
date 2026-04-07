import type { DeviceInfo } from "../../store/api";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function GenericPanel({ device, onStateChange }: Props) {
  const entries = Object.entries(device.state);

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
                // Try to preserve type
                const v = e.target.value;
                if (v === "true" || v === "false") onStateChange(key, v === "true");
                else if (!isNaN(Number(v)) && v !== "") onStateChange(key, Number(v));
                else onStateChange(key, v);
              }}
            />
          </div>
        ))}
      </div>
    </>
  );
}
