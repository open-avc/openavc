import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// A condition's operator picker shows the operator the project holds. The
// runtime takes other spellings of an operator (">=", "greater_than"), and a
// condition saved with one used to show the picker's first choice, "equals",
// whatever it said.

import { ConditionEditor } from "./ConditionEditor";

function operatorPicker(): HTMLSelectElement {
  return screen.getByRole("option", { name: "equals" }).closest("select") as HTMLSelectElement;
}

const selectedText = (select: HTMLSelectElement) => select.options[select.selectedIndex]?.text;

describe("ConditionEditor's operator picker", () => {
  it("shows another spelling of an operator as that operator, in its own words", () => {
    render(<ConditionEditor condition={{ key: "var.level", operator: ">=", value: 5 }} onChange={() => {}} />);
    const picker = operatorPicker();
    expect(picker.value).toBe(">=");
    expect(selectedText(picker)).toBe("greater or equal (>=)");
  });

  it("shows an operator nobody knows as written, marked not valid", () => {
    render(<ConditionEditor condition={{ key: "var.level", operator: "contains", value: "a" }} onChange={() => {}} />);
    const picker = operatorPicker();
    expect(picker.value).toBe("contains");
    expect(selectedText(picker)).toBe("contains (not valid)");
  });

  it("lists each operator once by its own name when the project holds one", () => {
    render(<ConditionEditor condition={{ key: "var.level", operator: "gte", value: 5 }} onChange={() => {}} />);
    const picker = operatorPicker();
    expect(picker.value).toBe("gte");
    expect([...picker.options].map((o) => o.value)).toEqual(
      ["eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"],
    );
  });

  it("explains another spelling of an operator as that operator", () => {
    render(<ConditionEditor condition={{ key: "var.level", operator: "greater_or_equal", value: 5 }} onChange={() => {}} />);
    expect(screen.getByText("Numeric comparison: value >= target")).toBeTruthy();
    expect(screen.getByPlaceholderText("Value")).toBeTruthy();
  });

  it("asks for no value for an operator that reads none", () => {
    render(<ConditionEditor condition={{ key: "var.level", operator: "truthy" }} onChange={() => {}} />);
    expect(screen.queryByPlaceholderText("Value")).toBeNull();
  });
});
