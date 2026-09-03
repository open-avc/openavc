import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DeviceInfo } from "../store/api";
import { setDeviceState, toggleError } from "../store/api";
import { createThrottledWriter } from "../store/stateWriter";
import { ProjectorPanel } from "./devices/ProjectorPanel";
import { DisplayPanel } from "./devices/DisplayPanel";
import { SwitcherPanel } from "./devices/SwitcherPanel";
import { AudioPanel } from "./devices/AudioPanel";
import { CameraPanel } from "./devices/CameraPanel";
import { GenericPanel } from "./devices/GenericPanel";
import { ChildEntitiesPanel } from "./ChildEntitiesPanel";
import { DynamicControls } from "./controls/DynamicControls";
import {
  Projector,
  Monitor,
  ArrowLeftRight,
  AudioLines,
  Camera,
  Box,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
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

export function DeviceCard({ device, expandAll }: {
  device: DeviceInfo;
  /** Header "expand/collapse all" broadcast. The nonce is what makes a repeat
   *  press work: the value alone would be unchanged and the effect would not
   *  run, so pressing "Expand all" after opening one card by hand would do
   *  nothing to the rest. */
  expandAll?: { value: boolean; nonce: number };
}) {
  const [errorsOpen, setErrorsOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // Whether there is anything below the fold worth offering to open. Measured
  // rather than guessed: how much a card holds depends on the driver, and the
  // cheapest wrong answer here is a card that hides content behind no control.
  const [clipped, setClipped] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const errors = Object.entries(device.available_errors);

  // Runs after every render, which is what we want: state arrives from the
  // simulator continuously, and a device that grows a new property mid-session
  // changes the answer. Reading layout in useLayoutEffect keeps the control
  // from flickering in after paint.
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el || expanded) return;
    // +1 absorbs sub-pixel rounding; without it a card can claim to be clipped
    // by a fraction of a pixel and offer to expand onto nothing.
    setClipped(el.scrollHeight > el.clientHeight + 1);
  });

  // A resize changes column width, which rewraps content and changes the
  // answer without any re-render of ours.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      if (!expanded) setClipped(el.scrollHeight > el.clientHeight + 1);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [expanded]);

  useEffect(() => {
    if (expandAll) setExpanded(expandAll.value);
  }, [expandAll?.nonce]);

  // Every control on this card writes through one throttle, so a gesture is a
  // few requests rather than one per pixel. The rejection is swallowed here
  // rather than at each call site: a failed write is already reported by the
  // value not changing, and an uncaught promise from a control is noise.
  const writer = useMemo(
    () =>
      createThrottledWriter((key, value) => {
        setDeviceState(device.device_id, key, value).catch(() => {});
      }),
    [device.device_id],
  );
  // Send whatever the last gesture left queued before this card goes away.
  useEffect(() => () => writer.flush(), [writer]);

  const handleStateChange = (key: string, value: unknown) => {
    writer.write(key, value);
  };

  const handleErrorToggle = (mode: string, active: boolean) => {
    toggleError(device.device_id, mode, active);
  };

  const icon = CATEGORY_ICONS[device.category] || <Box size={18} />;
  const Panel = CATEGORY_PANELS[device.category] || GenericPanel;

  return (
    <div className={`device-card${expanded ? " expanded" : ""}`}>
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

      {/* Push state indicator */}
      <div style={{ padding: "2px 8px", fontSize: 10, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 4 }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: device.push_state ? "var(--accent)" : "var(--border-color)", display: "inline-block" }} />
        {device.push_state ? "Pushes state changes" : "Poll-only (no push)"}
      </div>

      {/* Child entities (v0.5.0) — read-only summary badges. Shown only when
          no modeled roster exists (Python _sim.py devices); auto-generated
          simulators model children and get the full panel below instead. */}
      {!(device.children && Object.keys(device.children).length > 0) &&
        device.child_entities && Object.keys(device.child_entities).length > 0 && (
        <div style={{ padding: "2px 8px", fontSize: 10, color: "var(--text-muted)", display: "flex", flexWrap: "wrap", gap: 6 }}>
          {Object.entries(device.child_entities).map(([type, children]) => {
            const items = Object.entries(children);
            return (
              <span
                key={type}
                title={items.map(([id, c]) => `${id}: ${c.label || id}`).join("\n")}
                style={{ border: "1px solid var(--border-color)", borderRadius: 3, padding: "0 4px" }}
              >
                {items.length} {type}
                {items.length === 1 ? "" : "s"}
              </span>
            );
          })}
        </div>
      )}

      {/* Declarative controls or category-specific panel */}
      <div className="device-card-body" ref={bodyRef}>
        {device.controls && device.controls.length > 0 ? (
          <div className="controls-panel">
            <DynamicControls controls={device.controls} state={device.state} onStateChange={handleStateChange} />
          </div>
        ) : (
          <Panel device={device} onStateChange={handleStateChange} />
        )}
        {/* Per-child state (auto-generated simulators model children) */}
        <ChildEntitiesPanel device={device} onStateChange={handleStateChange} />
      </div>

      {/* Open the card past its height cap. Offered only when something is
          actually hidden, so a small device carries no dead control. */}
      {(clipped || expanded) && (
        <button
          className="device-card-more"
          onClick={() => setExpanded(!expanded)}
          title={expanded ? "Collapse this device" : "Show everything this device has"}
        >
          {expanded ? <ChevronsDownUp size={12} /> : <ChevronsUpDown size={12} />}
          {expanded ? "Show less" : "Show all"}
        </button>
      )}

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
