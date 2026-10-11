import { describe, it, expect } from "vitest";
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// The Display Label's hint is the name forms show when the label is left
// blank. It follows the Name field as it is typed, not only once the new name
// is committed on blur: typing "lvl" beside a hint still reading "Param1" says
// the label will be the old name.

import { ParamEditor } from "./CommandBuilder";
import type { DriverParamDef } from "../../api/types";

function Harness() {
  const [params, setParams] = useState<Record<string, DriverParamDef>>({
    param1: { type: "string" },
  });
  return (
    <ParamEditor params={params} childTypes={[]} stateKeys={[]} onChange={setParams} />
  );
}

describe("parameter Display Label hint", () => {
  it("follows the name while it is being typed", () => {
    render(<Harness />);
    const name = screen.getByDisplayValue("param1");
    fireEvent.change(name, { target: { value: "lvl" } });
    expect(screen.getByPlaceholderText("Lvl")).toBeTruthy();
    expect(screen.queryByPlaceholderText("Param1")).toBeNull();
  });

  it("keeps the committed name's hint while the field is blank", () => {
    render(<Harness />);
    const name = screen.getByDisplayValue("param1");
    fireEvent.change(name, { target: { value: "" } });
    expect(screen.getByPlaceholderText("Param1")).toBeTruthy();
  });

  it("matches the committed name after the rename lands", () => {
    render(<Harness />);
    const name = screen.getByDisplayValue("param1");
    fireEvent.change(name, { target: { value: "input_id" } });
    fireEvent.blur(name);
    expect(screen.getByDisplayValue("input_id")).toBeTruthy();
    expect(screen.getByPlaceholderText("Input ID")).toBeTruthy();
  });
});
