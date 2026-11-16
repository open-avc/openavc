import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ProjectConfig, UIElement } from "../../../api/types";
import { MatrixFields } from "./MatrixFields";

// The Cell Size box shows and writes what the panel draws. A cell size of 0 is
// unset in the project schema, the cell fitting itself to the box, so a typed
// 0 stores nothing and a 0 already saved shows as Auto.

const project = { devices: [], ui: { pages: [], settings: {} } } as unknown as ProjectConfig;

function cellSizeBox(style: Record<string, unknown> | undefined, onChange = vi.fn()) {
  const element = { id: "m", type: "matrix", ...(style ? { style } : {}) } as unknown as UIElement;
  const { container } = render(<MatrixFields element={element} project={project} macros={[]} onChange={onChange} />);
  const label = [...container.querySelectorAll("label")].find((n) => n.textContent === "Cell Size");
  const input = label?.parentElement?.querySelector("input");
  if (!input) throw new Error("no Cell Size box");
  return { input, onChange };
}

describe("the matrix's Cell Size box", () => {
  it("stores no cell size for a typed 0, the cell the panel fits to the box", () => {
    const { input, onChange } = cellSizeBox({ crosspoint_active_color: "#0f0", cell_size: 3 });
    fireEvent.change(input, { target: { value: "0" } });
    expect(onChange).toHaveBeenLastCalledWith({ style: { crosspoint_active_color: "#0f0", cell_size: undefined } });
  });

  it("shows a saved 0 as Auto, the way it shows none", () => {
    for (const style of [undefined, {}, { cell_size: 0 }]) {
      const { input } = cellSizeBox(style);
      expect(input.value).toBe("");
      expect(input.getAttribute("placeholder")).toBe("Auto");
    }
  });

  it("stores and shows a size somebody typed, in rem on disk", () => {
    const { input, onChange } = cellSizeBox(undefined);
    fireEvent.change(input, { target: { value: "56" } });
    expect(onChange).toHaveBeenLastCalledWith({ style: { cell_size: 4 } });
    expect(cellSizeBox({ cell_size: 4 }).input.value).toBe("56");
  });
});
