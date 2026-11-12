import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectConfig, UIElement } from "../../../api/types";
import { BasicProperties } from "./BasicProperties";

// The ID field renames the control on Enter or a click away, and throws the
// typed text away on Escape.

const project = { devices: [], macros: [], ui: { pages: [] } } as unknown as ProjectConfig;

function renderIdField() {
  const onRename = vi.fn();
  render(
    <BasicProperties
      element={{ id: "btn_1", type: "button" } as UIElement}
      project={project}
      pages={[]}
      onChange={() => {}}
      onRename={onRename}
    />,
  );
  const field = screen.getByTitle("Rename element (Enter to commit, Esc to cancel)") as HTMLInputElement;
  field.focus();
  fireEvent.change(field, { target: { value: "power_btn" } });
  return { field, onRename };
}

afterEach(cleanup);

describe("the control's ID field", () => {
  it("throws the typed name away on Escape", () => {
    const { field, onRename } = renderIdField();

    fireEvent.keyDown(field, { key: "Escape" });

    expect(onRename).not.toHaveBeenCalled();
    expect(field.value).toBe("btn_1");
  });

  it("renames on the next edit after an Escape", () => {
    const { field, onRename } = renderIdField();
    fireEvent.keyDown(field, { key: "Escape" });

    field.focus();
    fireEvent.change(field, { target: { value: "power_btn" } });
    fireEvent.keyDown(field, { key: "Enter" });

    expect(onRename).toHaveBeenCalledOnce();
    expect(onRename).toHaveBeenCalledWith("power_btn");
  });

  it("renames on Enter", () => {
    const { field, onRename } = renderIdField();

    fireEvent.keyDown(field, { key: "Enter" });

    expect(onRename).toHaveBeenCalledWith("power_btn");
  });

  it("renames on a click away", () => {
    const { field, onRename } = renderIdField();

    field.blur();

    expect(onRename).toHaveBeenCalledWith("power_btn");
  });
});
