import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ActionPicker } from "./ActionPicker";
import { actionIncompleteCheck } from "../uiBuilderHelpers";
import * as api from "../../../api/restClient";
import type { ProjectConfig } from "../../../api/types";

// The runtime half of this is a control calling a script function with the
// arguments the author wrote, and emitting an event. Without the editor those
// are fields nobody can author, so the two halves are pinned together here:
// what the picker WRITES is what the dispatcher reads.

const project = { devices: [], macros: [], ui: { pages: [] } } as unknown as ProjectConfig;

const FUNCTIONS = [
  {
    script: "room", function: "select_source", doc: "Route a source.",
    params: [
      { name: "source", required: true, type: "str" },
      { name: "level", required: false, default: 0, type: "int" },
    ],
    accepts_extra: false,
  },
  {
    script: "lights", function: "select_source", doc: "",
    params: [], accepts_extra: false,
  },
];

/**
 * Choose a function the way a person does: open the picker, type enough to
 * find the one you want, click it. Both scripts define `select_source`, so
 * the search text is the qualified id -- which is exactly the ambiguity the
 * tests below exist to pin.
 */
async function pickFunction(qualified: string): Promise<void> {
  // The list arrives asynchronously; the trigger is there from the start.
  await screen.findByTestId("script-function-picker");
  fireEvent.click(screen.getByTestId("script-function-picker"));
  fireEvent.change(screen.getByPlaceholderText("Search functions..."), {
    target: { value: qualified },
  });
  const row = await screen.findByText(qualified);
  fireEvent.click(row);
}

beforeEach(() => {
  vi.spyOn(api, "getScriptFunctions").mockResolvedValue(FUNCTIONS);
});

describe("Script Function action", () => {
  it("records which script the function came from, so two of one name stay apart", async () => {
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{ action: "script.call" }}
        project={project}
        onChange={onChange}
      />,
    );
    await pickFunction("lights.select_source");
    expect(onChange).toHaveBeenCalledWith({
      action: "script.call", function: "select_source", params: {}, script: "lights",
    });
  });

  it("draws the function's own parameters, and a number stays a number", async () => {
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{ action: "script.call", function: "select_source", script: "room", params: {} }}
        project={project}
        onChange={onChange}
      />,
    );
    // Both declared parameters are offered, marked as the script declares them.
    expect(await screen.findByText("source")).toBeTruthy();
    expect(screen.getByText("level")).toBeTruthy();
    expect(screen.getByText("required")).toBeTruthy();
    expect(screen.getByText("default: 0")).toBeTruthy();

    // A driver coerces its own params by declared type; a Python function does
    // not, so `level=7` must not reach it as the string "7".
    const level = screen.getByPlaceholderText("Enter level...");
    fireEvent.change(level, { target: { value: "7" } });
    expect(onChange).toHaveBeenLastCalledWith({
      action: "script.call", function: "select_source", script: "room",
      params: { level: 7 },
    });
  });

  it("the control's own value can be handed to a parameter, uncoerced", async () => {
    // The "$" toggle is how a slider's position reaches an argument, and it
    // seeds this slot's own token. An int parameter must not turn "$value"
    // into NaN on the way past: what it resolves to at press time is what
    // decides its type.
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{ action: "script.call", function: "select_source", script: "room", params: {} }}
        project={project}
        onChange={onChange}
        eventTokens={[{ key: "value", label: "This control's value" }]}
      />,
    );
    await screen.findByText("level");
    const toggles = screen.getAllByTitle("Use a dynamic value read from state at runtime");
    fireEvent.click(toggles[toggles.length - 1]);
    expect(onChange).toHaveBeenLastCalledWith({
      action: "script.call", function: "select_source", script: "room",
      params: { level: "$value" },
    });
  });

  it("says so when the named function is not in any enabled script", async () => {
    render(
      <ActionPicker
        value={{ action: "script.call", function: "gone" }}
        project={project}
        onChange={vi.fn()}
      />,
    );
    expect(await screen.findByText(/does not/i)).toBeTruthy();
  });

  it("switching function drops the previous one's parameters", async () => {
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{
          action: "script.call", function: "select_source", script: "room",
          params: { source: "laptop" },
        }}
        project={project}
        onChange={onChange}
      />,
    );
    await pickFunction("lights.select_source");
    expect(onChange).toHaveBeenLastCalledWith({
      action: "script.call", function: "select_source", params: {}, script: "lights",
    });
  });
});

describe("Emit Event action", () => {
  it("is offered as an action type", () => {
    render(<ActionPicker value={null} project={project} onChange={vi.fn()} />);
    expect(screen.getByRole("option", { name: "Emit Event" })).toBeTruthy();
  });

  it("writes the event name and a payload the author names", () => {
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{ action: "event.emit", event: "custom.select_source", payload: {} }}
        project={project}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText("Add value"));
    expect(onChange).toHaveBeenLastCalledWith({
      action: "event.emit", event: "custom.select_source", payload: { value: "" },
    });
  });

  it("renames a payload key without reordering the rows", () => {
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{ action: "event.emit", event: "custom.x", payload: { a: "1", b: "2" } }}
        project={project}
        onChange={onChange}
      />,
    );
    const keys = screen.getAllByPlaceholderText("name");
    fireEvent.change(keys[0], { target: { value: "source" } });
    const written = onChange.mock.lastCall![0].payload as Record<string, unknown>;
    expect(written).toEqual({ source: "1", b: "2" });
    // Order matters here and object equality does not check it: a rename that
    // rebuilds by spread moves the row to the end, under a cursor that is still
    // typing in it.
    expect(Object.keys(written)).toEqual(["source", "b"]);
  });

  it("is Incomplete until it names an event", () => {
    expect(actionIncompleteCheck({ action: "event.emit" })).toBe(true);
    expect(actionIncompleteCheck({ action: "event.emit", event: "custom.x" })).toBe(false);
  });
});
