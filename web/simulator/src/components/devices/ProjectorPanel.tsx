import type { DeviceInfo } from "../../store/api";
import { Power } from "lucide-react";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function ProjectorPanel({ device, onStateChange }: Props) {
  const power = String(device.state.power || "off");
  const input = String(device.state.input || "—");
  const lampHours = device.state.lamp_hours ?? device.state.lampHours ?? "—";
  const muteVideo = Boolean(device.state.mute_video);

  const powerClass = power === "on" ? "on" : power === "warming" ? "warming" : power === "cooling" ? "cooling" : "off";

  return (
    <>
      {/* Visual */}
      <div className="device-visual">
        <div style={{ display: "flex", alignItems: "center", gap: 10, width: "100%" }}>
          <div className={`power-led ${powerClass}`} />
          <span style={{ fontSize: 13, fontWeight: 500 }}>
            {power === "on" ? "ON" : power === "warming" ? "Warming..." : power === "cooling" ? "Cooling..." : "Standby"}
          </span>
          {muteVideo && (
            <span style={{ fontSize: 11, color: "var(--color-warning)", marginLeft: "auto" }}>MUTED</span>
          )}
        </div>
      </div>

      {/* State */}
      <div className="state-panel">
        <div className="state-row">
          <span className="state-key">Input</span>
          <span className="state-value">{input}</span>
        </div>
        <div className="state-row">
          <span className="state-key">Lamp Hours</span>
          <span className="state-value">{String(lampHours)}</span>
        </div>
      </div>

      {/* Controls */}
      <div className="controls-panel">
        <button
          className={`ctrl-btn ${power === "on" || power === "warming" ? "active" : ""}`}
          onClick={() => onStateChange("power", power === "off" ? "on" : "off")}
        >
          <Power size={12} style={{ marginRight: 4 }} />
          {power === "off" ? "Power On" : "Power Off"}
        </button>
        {["hdmi1", "hdmi2", "vga", "dvi"].map((inp) => (
          <button
            key={inp}
            className={`ctrl-btn ${input === inp ? "active" : ""}`}
            onClick={() => onStateChange("input", inp)}
          >
            {inp.toUpperCase()}
          </button>
        ))}
      </div>
    </>
  );
}
