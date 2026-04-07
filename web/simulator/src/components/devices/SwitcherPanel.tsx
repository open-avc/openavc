import type { DeviceInfo } from "../../store/api";
import { ArrowRight, VolumeX, Volume2 } from "lucide-react";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function SwitcherPanel({ device, onStateChange }: Props) {
  const input = Number(device.state.input ?? 1);
  const volume = Number(device.state.volume ?? 0);
  const mute = Boolean(device.state.mute);
  const signalActive = Boolean(device.state.signal_active ?? device.state.signal_present ?? true);

  const inputs = [1, 2, 3, 4, 5, 6, 7, 8];

  return (
    <>
      {/* Visual — routing display */}
      <div className="device-visual">
        <div className="switcher-matrix">
          <div className="switcher-ports">
            {inputs.map((i) => (
              <div
                key={i}
                className={`switcher-port ${i === input ? "active" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => onStateChange("input", i)}
              >
                {i}
              </div>
            ))}
          </div>
          <div className="switcher-route-arrow">
            <ArrowRight size={20} />
          </div>
          <div>
            <div className="switcher-port active">OUT</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", fontSize: 11 }}>
          <div style={{
            width: 8, height: 8, borderRadius: "50%",
            background: signalActive ? "var(--color-success)" : "var(--text-muted)",
          }} />
          <span style={{ color: "var(--text-muted)" }}>
            {signalActive ? "Signal detected" : "No signal"}
          </span>
        </div>
      </div>

      {/* State */}
      <div className="state-panel">
        <div className="state-row">
          <span className="state-key">Active Input</span>
          <span className="state-value">{input}</span>
        </div>
        <div className="state-row">
          <span className="state-key">Volume</span>
          <span className="state-value">{volume}</span>
        </div>
        <div className="state-row">
          <span className="state-key">Mute</span>
          <span className={`state-value ${mute ? "on" : "off"}`}>{mute ? "Yes" : "No"}</span>
        </div>
      </div>

      {/* Controls */}
      <div className="controls-panel">
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
          {mute ? <VolumeX size={12} /> : <Volume2 size={12} />}
          <span style={{ marginLeft: 4 }}>{mute ? "Unmute" : "Mute"}</span>
        </button>
      </div>
    </>
  );
}
