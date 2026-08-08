import type { PowerControlDef } from "../../store/api";
import { Power } from "lucide-react";

interface Props {
  control: PowerControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function PowerControl({ control, state, onStateChange }: Props) {
  const power = String(state[control.key] || "off");
  const isOn = power === "on" || power === "warming";
  const powerClass = power === "on" ? "on" : power === "warming" ? "warming" : power === "cooling" ? "cooling" : "off";

  return (
    <div className="ctrl-power">
      <div className={`power-led ${powerClass}`} />
      <span className="ctrl-power-label">
        {power === "on" ? "ON" : power === "warming" ? "Warming..." : power === "cooling" ? "Cooling..." : "Standby"}
      </span>
      <button
        className={`ctrl-btn ${isOn ? "active" : ""}`}
        onClick={() => onStateChange(control.key, power === "off" || power === "cooling" ? "on" : "off")}
      >
        <Power size={12} style={{ marginRight: 4 }} />
        {isOn ? "Power Off" : "Power On"}
      </button>
    </div>
  );
}
