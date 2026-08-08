import type { DeviceInfo } from "../../store/api";
import { Power, VolumeX, Volume2 } from "lucide-react";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function DisplayPanel({ device, onStateChange }: Props) {
  const power = String(device.state.power || "off");
  const input = String(device.state.input || "—");
  const volume = Number(device.state.volume ?? 0);
  const mute = Boolean(device.state.mute);
  const isOn = power === "on" || power === "active";

  return (
    <>
      {/* Visual — display screen */}
      <div className="device-visual">
        <div className={`display-screen ${isOn ? "on" : "off"}`}>
          {isOn ? input.toUpperCase() : "OFF"}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", fontSize: 12 }}>
          <div className={`power-led ${isOn ? "on" : "off"}`} />
          <span style={{ color: "var(--text-muted)" }}>
            {mute ? <VolumeX size={14} /> : <Volume2 size={14} />}
          </span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: mute ? "var(--color-error)" : "var(--text-secondary)" }}>
            {mute ? "MUTE" : volume}
          </span>
        </div>
      </div>

      {/* Controls */}
      <div className="controls-panel">
        <button
          className={`ctrl-btn ${isOn ? "active" : ""}`}
          onClick={() => onStateChange("power", isOn ? "off" : "on")}
        >
          <Power size={12} style={{ marginRight: 4 }} />
          {isOn ? "Power Off" : "Power On"}
        </button>
        {["hdmi1", "hdmi2", "hdmi3", "dp"].map((inp) => (
          <button
            key={inp}
            className={`ctrl-btn ${input === inp ? "active" : ""}`}
            onClick={() => onStateChange("input", inp)}
          >
            {inp.toUpperCase()}
          </button>
        ))}
      </div>
      <div className="controls-panel" style={{ borderTop: "none", paddingTop: 0 }}>
        <div className="ctrl-slider">
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Vol</span>
          <input
            type="range"
            min={0}
            max={100}
            value={volume}
            onChange={(e) => onStateChange("volume", Number(e.target.value))}
          />
          <span className="value">{volume}</span>
        </div>
        <button
          className={`ctrl-btn ${mute ? "active" : ""}`}
          onClick={() => onStateChange("mute", !mute)}
        >
          {mute ? "Unmute" : "Mute"}
        </button>
      </div>
    </>
  );
}
