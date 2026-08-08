import type { DeviceInfo } from "../../store/api";
import { Camera } from "lucide-react";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function CameraPanel({ device, onStateChange }: Props) {
  const power = String(device.state.power || "off");
  const tally = String(device.state.tally || "off");
  const zoom = Number(device.state.zoom ?? 50);
  const preset = String(device.state.preset ?? "—");
  const isOn = power === "on";

  const tallyColor = tally === "program" ? "var(--color-error)" : tally === "preview" ? "var(--color-success)" : "var(--text-muted)";

  return (
    <>
      {/* Visual */}
      <div className="device-visual">
        <div style={{ display: "flex", alignItems: "center", gap: 10, width: "100%" }}>
          <Camera size={24} color={isOn ? "var(--accent)" : "var(--text-muted)"} />
          <div style={{
            width: 12, height: 12, borderRadius: "50%",
            background: tallyColor,
            boxShadow: tally !== "off" ? `0 0 8px ${tallyColor}` : "none",
          }} />
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {tally === "program" ? "PROGRAM" : tally === "preview" ? "PREVIEW" : ""}
          </span>
          <div className={`power-led ${isOn ? "on" : "off"}`} style={{ marginLeft: "auto" }} />
        </div>
      </div>

      {/* State */}
      <div className="state-panel">
        <div className="state-row">
          <span className="state-key">Power</span>
          <span className={`state-value ${isOn ? "on" : "off"}`}>{power}</span>
        </div>
        <div className="state-row">
          <span className="state-key">Zoom</span>
          <span className="state-value">{zoom}%</span>
        </div>
        <div className="state-row">
          <span className="state-key">Preset</span>
          <span className="state-value">{preset}</span>
        </div>
      </div>

      {/* Controls */}
      <div className="controls-panel">
        <button
          className={`ctrl-btn ${isOn ? "active" : ""}`}
          onClick={() => onStateChange("power", isOn ? "off" : "on")}
        >
          {isOn ? "Power Off" : "Power On"}
        </button>
        {["off", "program", "preview"].map((t) => (
          <button
            key={t}
            className={`ctrl-btn ${tally === t ? "active" : ""}`}
            onClick={() => onStateChange("tally", t)}
          >
            {t === "off" ? "Tally Off" : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
        <div className="ctrl-slider">
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Zoom</span>
          <input
            type="range"
            min={0}
            max={100}
            value={zoom}
            onChange={(e) => onStateChange("zoom", Number(e.target.value))}
          />
          <span className="value">{zoom}</span>
        </div>
      </div>
    </>
  );
}
