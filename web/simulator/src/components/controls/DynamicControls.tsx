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
          default:
            return null;
        }
      })}
    </div>
  );
}
