import type { DeviceInfo } from "../../store/api";
import { VolumeX, Volume2 } from "lucide-react";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function AudioPanel({ device, onStateChange }: Props) {
  const level = Number(device.state.level ?? 0);
  const mute = Boolean(device.state.mute);
  const levelDb = String(device.state.level_db ?? "");

  // Normalize level to 0-100 for visual display
  // Level might be 0-1 (QSC), -100 to 12 (Biamp dB), or 0-100
  const normalizedLevel = level <= 1 && level >= 0 ? level * 100 : Math.max(0, Math.min(100, level + 100));

  return (
    <>
      {/* Visual — level meter */}
      <div className="device-visual">
        <div className="audio-meters" style={{ height: 60 }}>
          {[0, 1, 2, 3].map((ch) => (
            <div key={ch} className="audio-meter-bar">
              <div
                className="audio-meter-fill"
                style={{
                  height: `${mute ? 0 : normalizedLevel}%`,
                  background: normalizedLevel > 80 ? "var(--color-warning)" : "var(--accent)",
                }}
              />
            </div>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", fontSize: 12 }}>
          {mute ? <VolumeX size={14} color="var(--color-error)" /> : <Volume2 size={14} />}
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
            {levelDb || String(level)}
          </span>
          {mute && <span style={{ color: "var(--color-error)", fontSize: 11, marginLeft: "auto" }}>MUTED</span>}
        </div>
      </div>

      {/* Controls */}
      <div className="controls-panel">
        <div className="ctrl-slider">
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Level</span>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(normalizedLevel)}
            onChange={(e) => {
              // Convert back to the device's scale
              const v = Number(e.target.value);
              if (level <= 1 && level >= 0) {
                onStateChange("level", v / 100);
              } else {
                onStateChange("level", v);
              }
            }}
          />
          <span className="value">{Math.round(normalizedLevel)}</span>
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
