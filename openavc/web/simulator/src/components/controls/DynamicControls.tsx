import type { ControlDef } from "../../store/controls.gen";
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
            // Every type the contract declares has its case above: a type added
            // to the generated union with no case here stops the build at this
            // line. The list itself comes from the API, so a driver loaded with
            // a type this build does not draw (a typo like "meter" for "meters",
            // or a newer type) still lands here: warn and show a marker in its
            // place so the author sees it instead of a blank card.
            const unhandled: never = ctrl;
            const badType = (unhandled as ControlDef).type;
            console.warn(
              `[simulator] Unknown control type "${badType}", shown as a marker. ` +
                "Check the driver's simulator.controls (`python -m openavc.drivers.check` names the types it can be).",
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
