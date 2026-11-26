import { afterEach, describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// A condition's value may name a key ($var.wanted): the runtime compares with
// that key's value, so the editor says which key, and what it holds now.

import { ConditionEditor } from "./ConditionEditor";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";

afterEach(() => {
  useConnectionStore.setState({ liveState: {} });
  useProjectStore.setState({ project: null });
});

describe("ConditionEditor with a value that names a key", () => {
  it("says which key the value is compared with, and what it holds now", () => {
    useConnectionStore.setState({ liveState: { "device.proj.power": "on", "var.wanted": "off" } });
    render(
      <ConditionEditor
        condition={{ key: "device.proj.power", operator: "eq", value: "$var.wanted" }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(/Compared with var\.wanted/).textContent).toBe("Compared with var.wanted: off");
  });

  it("names the key before it has a value", () => {
    render(
      <ConditionEditor
        condition={{ key: "device.proj.power", operator: "ne", value: "$var.wanted" }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(/Compared with var\.wanted/).textContent).toBe("Compared with var.wanted");
  });

  it("says nothing of a key for a value written as itself", () => {
    render(
      <ConditionEditor condition={{ key: "device.proj.power", operator: "eq", value: "on" }} onChange={() => {}} />,
    );
    expect(screen.queryByText(/Compared with/)).toBeNull();
  });

  it("keeps a reference in a text box where a true or false key offers a picker", () => {
    useProjectStore.setState({
      project: { variables: [{ id: "flag", type: "boolean" }] } as never,
    });
    render(
      <ConditionEditor condition={{ key: "var.flag", operator: "eq", value: "$var.other" }} onChange={() => {}} />,
    );
    expect((screen.getByPlaceholderText("Value") as HTMLInputElement).value).toBe("$var.other");
  });
});
