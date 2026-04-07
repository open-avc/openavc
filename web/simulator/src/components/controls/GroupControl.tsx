import type { GroupControlDef } from "../../store/api";
import { DynamicControls } from "./DynamicControls";

interface Props {
  control: GroupControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function GroupControl({ control, state, onStateChange }: Props) {
  return (
    <div className="ctrl-group">
      <div className="ctrl-group-label">{control.label}</div>
      <div className="ctrl-group-content">
        <DynamicControls controls={control.controls} state={state} onStateChange={onStateChange} />
      </div>
    </div>
  );
}
