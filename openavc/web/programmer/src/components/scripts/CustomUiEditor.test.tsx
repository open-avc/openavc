import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CustomUiEditor } from "./CustomUiEditor";

// Monaco does not run in jsdom (it wants a real layout engine and a worker), and
// none of what is being tested here is inside it: the point is the strip that
// sits UNDER the editor and carries what the last save reported.
vi.mock("@monaco-editor/react", () => ({
  default: () => <div data-testid="monaco" />,
}));

// A custom control runs in a sandboxed frame in the panel, not in this process,
// so there is no console here and nothing in the browser can tell whether the
// markup works. What the server CAN say is what will go wrong in a real space,
// and it says it in the save response. This is where the author reads it --
// the review lives once, on the server, and this is its only surface.

describe("the custom control editor's save review", () => {
  it("shows nothing at all when the save reported nothing", () => {
    render(<CustomUiEditor path="room_map/index.html" source="<div/>" onChange={vi.fn()} />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("lists what the save reported, and counts it", () => {
    render(
      <CustomUiEditor
        path="room_map/index.html"
        source="<div/>"
        onChange={vi.fn()}
        warnings={[
          "room_map/index.html loads 'https://cdn.example.com/chart.js' over the internet.",
          "room_map/index.html never listens for openavc:init.",
        ]}
      />,
    );

    const strip = screen.getByRole("status");
    expect(strip.textContent).toContain("2 things to fix");
    expect(strip.textContent).toContain("cdn.example.com");
    expect(strip.textContent).toContain("openavc:init");
  });

  it("says thing, not things, when there is one", () => {
    render(
      <CustomUiEditor
        path="room_map/index.html"
        source="<div/>"
        onChange={vi.fn()}
        warnings={["room_map/index.html never sets margin: 0."]}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("1 thing to fix");
  });

  it("keeps the editor on screen -- a warning is not a failed save", () => {
    render(
      <CustomUiEditor
        path="room_map/index.html"
        source="<div/>"
        onChange={vi.fn()}
        warnings={["something to fix"]}
      />,
    );
    expect(screen.getByTestId("monaco")).toBeTruthy();
  });

  it("has no strip for a file with nothing to edit", () => {
    // An image in a control's folder: no editor, and no review either.
    render(
      <CustomUiEditor path="room_map/logo.png" source="" onChange={vi.fn()} warnings={["x"]} />,
    );
    expect(screen.queryByRole("status")).toBeNull();
  });
});
