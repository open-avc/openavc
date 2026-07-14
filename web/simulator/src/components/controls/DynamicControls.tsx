import type { ControlDef } from "../../store/api";
import { PowerControl } from "./PowerControl";
import { SelectControl } from "./SelectControl";
import { SliderControl } from "./SliderControl";
import { ToggleControl } from "./ToggleControl";
import { MatrixControl } from "./MatrixControl";
import { MeterControl } from "./MeterControl";
import { PresetControl } from "./PresetControl";
import { GroupControl } from "./GroupControl";
import { IndicatorControl } from "./IndicatorControl";

interface Props {
  controls: ControlDef[];
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function DynamicControls({ controls, state, onStateChange }: Props) {
  return (
    <div className="dynamic-controls">
      {controls.map((ctrl, i) => {
        const key = `${ctrl.type}-${i}`;
        switch (ctrl.type) {
          case "power":
            return <PowerControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "select":
            return <SelectControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "slider":
            return <SliderControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "toggle":
            return <ToggleControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "matrix":
            return <MatrixControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "meters":
            return <MeterControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "presets":
            return <PresetControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "group":
            return <GroupControl key={key} control={ctrl} state={state} onStateChange={onStateChange} />;
          case "indicator":
            return <IndicatorControl key={key} control={ctrl} state={state} />;
          default: {
            // The ControlDef union is compile-time only — the actual list comes
            // from the API, so a driver's simulator.controls can carry a type
            // this build doesn't render (a typo like "meter" for "meters", or a
            // newer type). Don't drop it silently: warn and show a marker so the
            // author sees the failure instead of a blank card.
            const badType = (ctrl as ControlDef).type;
            console.warn(
              `[simulator] Unknown control type "${badType}" — nothing to render. ` +
                "Check the driver's simulator.controls (run `python -m simulator.validate` to catch this).",
            );
            return (
              <div
                key={key}
                className="control-unknown"
                style={{ fontSize: 11, color: "#c0392b", padding: "4px 8px" }}
              >
                Unknown control type: <code>{String(badType)}</code>
              </div>
            );
          }
        }
      })}
    </div>
  );
}
