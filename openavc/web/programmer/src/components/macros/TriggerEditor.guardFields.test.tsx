import { afterEach, describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A trigger's guard reads what fired the trigger, as the trigger engine reads
// it: its picker offers the fields that trigger's type hands over, and its
// preview says such a field is known when the trigger fires.

import { ConditionPreview, TriggerEditor } from "./TriggerEditor";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { triggerFieldRows, typedTriggerFieldRow } from "../shared/variableKeyPickerHelpers";
import type { TriggerConfig } from "../../api/types";

afterEach(() => {
  useConnectionStore.setState({ connected: false, liveState: {} });
  useProjectStore.setState({ project: null });
});

describe("the guard picker's trigger fields", () => {
  it("lists only the fields the trigger's type hands over", () => {
    expect(triggerFieldRows("state_change").map((row) => row.key)).toEqual([
      "trigger.key", "trigger.old_value", "trigger.new_value",
    ]);
    expect(triggerFieldRows("startup").map((row) => row.key)).toEqual(["trigger.trigger_type"]);
  });

  it("says what each holds", () => {
    expect(triggerFieldRows("event")).toEqual([{
      key: "trigger.event",
      label: "event",
      description: "The name of the event that fired.",
    }]);
  });

  it("offers a typed field only for a type that hands over its event's fields", () => {
    expect(typedTriggerFieldRow("trigger.source", "event")).toEqual({
      key: "trigger.source",
      label: "source",
      description: "A field of the event.",
    });
    expect(typedTriggerFieldRow("trigger.source", "state_change")).toBeNull();
  });

  it("are offered in the guard's own picker", () => {
    const trigger: TriggerConfig = {
      id: "t1", type: "state_change", enabled: true, state_key: "var.level",
      conditions: [{ key: "", operator: "eq", value: "" }],
    };
    render(<TriggerEditor trigger={trigger} onChange={() => {}} />);
    fireEvent.click(screen.getByText("Key..."));
    expect(screen.getByText("What fired this trigger")).toBeTruthy();
    expect(screen.getByText("Known when the trigger fires.")).toBeTruthy();
    expect(screen.getByText("new_value")).toBeTruthy();
    // A control's field is never handed to a trigger.
    expect(screen.queryByText("tag")).toBeNull();
  });
});

describe("the guard preview on what fired the trigger", () => {
  it("says a guard on a trigger field is known when it fires", () => {
    useConnectionStore.setState({ connected: true, liveState: {} });
    render(<ConditionPreview conditions={[{ key: "trigger.new_value", operator: "eq", value: "on" }]} />);
    expect(screen.getByText(/Evaluated now/).textContent).toBe("Evaluated now: known when the trigger fires");
    expect(screen.getByText("trigger.new_value").parentElement?.textContent).toContain("known when it fires");
  });

  it("says the state's guards hold so far, and the rest are known when it fires", () => {
    useConnectionStore.setState({ connected: true, liveState: { "var.room_active": false } });
    render(
      <ConditionPreview
        conditions={[
          { key: "var.room_active", operator: "eq", value: false },
          { key: "var.input", operator: "eq", value: "$trigger.source" },
        ]}
      />,
    );
    expect(screen.getByText(/Evaluated now/).textContent).toBe(
      "Evaluated now: TRUE so far, the rest is known when the trigger fires",
    );
  });

  it("still says it would not fire when a guard on the state fails", () => {
    useConnectionStore.setState({ connected: true, liveState: { "var.room_active": true } });
    render(
      <ConditionPreview
        conditions={[
          { key: "var.room_active", operator: "eq", value: false },
          { key: "trigger.new_value", operator: "eq", value: "on" },
        ]}
      />,
    );
    expect(screen.getByText(/Evaluated now/).textContent).toBe("Evaluated now: FALSE, trigger would not fire");
  });
});
