import type { DeviceInfo } from "../../store/api";
import { VolumeX, Volume2 } from "lucide-react";
import { normalizeAudioLevel, denormalizeAudioLevel } from "./audioLevelScale";

interface Props {
  device: DeviceInfo;
  onStateChange: (key: string, value: unknown) => void;
}

export function AudioPanel({ device, onStateChange }: Props) {
  const level = Number(device.state.level ?? 0);
  const mute = Boolean(device.state.mute);
  const levelDbRaw = device.state.level_db;
  const levelDb = String(levelDbRaw ?? "");
  // A reported dB reading means `level` is on a dB scale even at 0 dB (which
  // sits in [0,1]); the classifier in audioLevelScale disambiguates so 0 dB
  // isn't misread as a silent 0% fraction.
  const hasDb = levelDbRaw !== undefined && levelDbRaw !== null && levelDbRaw !== "";

  // Normalize level to 0-100 for the meter (0-1 fraction, dB, or 0-100).
  const normalizedLevel = normalizeAudioLevel(level, hasDb);

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
              // Convert the 0-100 slider back to the device's scale (same
              // classification as the meter, so they can't disagree).
              const v = Number(e.target.value);
              onStateChange("level", denormalizeAudioLevel(v, level, hasDb));
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
