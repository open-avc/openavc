import { useState } from "react";
import type { DeviceInfo } from "../store/api";
import { setDeviceState, toggleError } from "../store/api";
import { ProjectorPanel } from "./devices/ProjectorPanel";
import { DisplayPanel } from "./devices/DisplayPanel";
import { SwitcherPanel } from "./devices/SwitcherPanel";
import { AudioPanel } from "./devices/AudioPanel";
import { CameraPanel } from "./devices/CameraPanel";
import { GenericPanel } from "./devices/GenericPanel";
import {
  Projector,
  Monitor,
  ArrowLeftRight,
  AudioLines,
  Camera,
  Box,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

const CATEGORY_ICONS: Record<string, React.ReactNode> = {
  projector: <Projector size={18} />,
  display: <Monitor size={18} />,
  switcher: <ArrowLeftRight size={18} />,
  audio: <AudioLines size={18} />,
  camera: <Camera size={18} />,
};

const CATEGORY_PANELS: Record<string, React.ComponentType<{ device: DeviceInfo; onStateChange: (key: string, value: unknown) => void }>> = {
  projector: ProjectorPanel,
  display: DisplayPanel,
  switcher: SwitcherPanel,
  audio: AudioPanel,
  camera: CameraPanel,
};

export function DeviceCard({ device }: { device: DeviceInfo }) {
  const [errorsOpen, setErrorsOpen] = useState(false);
  const errors = Object.entries(device.available_errors);

  const handleStateChange = (key: string, value: unknown) => {
    setDeviceState(device.device_id, key, value);
  };

  const handleErrorToggle = (mode: string, active: boolean) => {
    toggleError(device.device_id, mode, active);
  };

  const icon = CATEGORY_ICONS[device.category] || <Box size={18} />;
  const Panel = CATEGORY_PANELS[device.category] || GenericPanel;

  return (
    <div className="device-card">
      {/* Header */}
      <div className="device-card-header">
        <div className="icon">{icon}</div>
        <div className="info">
          <div className="name">{device.device_name || device.device_id}</div>
          <div className="driver">{device.name}</div>
        </div>
        {device.real_host ? (
          <div className="port-badge" title="Configured device address">
            {device.real_host}:{device.real_port}
          </div>
        ) : (
          <div className="port-badge">:{device.port}</div>
        )}
      </div>

      {/* Category-specific visual + controls */}
      <Panel device={device} onStateChange={handleStateChange} />

      {/* Error injection */}
      {errors.length > 0 && (
        <div className="errors-panel">
          <div
            className="label"
            style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
            onClick={() => setErrorsOpen(!errorsOpen)}
          >
            {errorsOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Errors ({device.active_errors.length} active)
          </div>
          {errorsOpen && errors.map(([mode, info]) => {
            const active = device.active_errors.includes(mode);
            return (
              <label key={mode} className={`error-toggle ${active ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={active}
                  onChange={(e) => handleErrorToggle(mode, e.target.checked)}
                />
                <span>{info.description || mode}</span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
