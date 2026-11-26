import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// The trigger editor and the trigger list read a State Change trigger's
// operator, and a guard's, as the trigger engine does: a trigger saved with a
// blank operator fires on any change, and another spelling of an operator
// (">=", "greater_than") is that operator. The editor used to show a blank
// operator as "Changes (any value)" with a Value box under it, and the picker's
// first choice for a spelling it did not list.

import { TriggerEditor } from "./TriggerEditor";
import { getTriggerType } from "./triggerHelpers";
import type { TriggerConfig } from "../../api/types";

const stateTrigger = (patch: Partial<TriggerConfig>): TriggerConfig => ({
  id: "t1",
  type: "state_change",
  enabled: true,
  state_key: "var.level",
  conditions: [],
  ...patch,
});

const pickerWith = (option: string) =>
  screen.getByRole("option", { name: option }).closest("select") as HTMLSelectElement;

const selectedText = (select: HTMLSelectElement) => select.options[select.selectedIndex]?.text;

const summary = (trigger: TriggerConfig) => getTriggerType("state_change")!.summary(trigger, [], []);

describe("a State Change trigger's own operator", () => {
  it("reads a blank operator as any change, with no value to give", () => {
    render(<TriggerEditor trigger={stateTrigger({ state_operator: "" })} onChange={() => {}} />);
    expect(pickerWith("Changes (any value)").value).toBe("any");
    expect(screen.queryByPlaceholderText("Value")).toBeNull();
  });

  it("shows another spelling of an operator as that operator", () => {
    render(
      <TriggerEditor trigger={stateTrigger({ state_operator: "greater_than", state_value: 5 })} onChange={() => {}} />,
    );
    const picker = pickerWith("Changes (any value)");
    expect(picker.value).toBe("greater_than");
    expect(selectedText(picker)).toBe("Greater Than (greater_than)");
    expect(screen.getByPlaceholderText("Value")).toBeTruthy();
  });

  it("is summed up as the engine reads it", () => {
    expect(summary(stateTrigger({ state_operator: "" }))).toBe("var.level changes");
    expect(summary(stateTrigger({ state_operator: null as unknown as string }))).toBe("var.level changes");
    expect(summary(stateTrigger({ state_operator: "greater_than", state_value: 5 }))).toBe("var.level > 5");
    expect(summary(stateTrigger({ state_operator: "gte", state_value: 5 }))).toBe("var.level >= 5");
  });
});

describe("a trigger's guard", () => {
  it("shows another spelling of an operator as that operator", () => {
    render(
      <TriggerEditor
        trigger={stateTrigger({
          state_operator: "any",
          conditions: [{ key: "var.mode", operator: "!=", value: "off" }],
        })}
        onChange={() => {}}
      />,
    );
    const picker = pickerWith("Is Truthy");
    expect(picker.value).toBe("!=");
    expect(selectedText(picker)).toBe("Not Equals (!=)");
  });
});
