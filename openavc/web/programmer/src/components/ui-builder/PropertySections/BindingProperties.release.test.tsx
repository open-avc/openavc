import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { BindingProperties } from "./BindingProperties";
import { ButtonBindingEditor } from "../../shared/ButtonBindingEditor";
import type { ProjectConfig, UIElement } from "../../../api/types";

// The panel sends ui.release at the end of every press whatever the button's
// mode, and the runtime runs do.release for it -- but the Builder offered the
// Release Action row in Tap mode only. A camera driven continuously in Hold
// Repeat had nowhere to put its stop, and a release authored in Tap survived a
// mode change invisibly and kept firing.

const project = {
  devices: [{ id: "cam", name: "Camera" }],
  macros: [],
  ui: { pages: [{ id: "main", name: "Main" }] },
} as unknown as ProjectConfig;

function buttonInMode(mode: string, extra: Record<string, unknown> = {}): UIElement {
  return {
    id: "pan_right", type: "button", label: "Pan Right",
    bindings: { do: { press: [{
      action: "device.command", device: "cam", command: "pan_right",
      ...(mode === "tap" ? {} : { mode }), ...extra,
    }] } },
  } as unknown as UIElement;
}

describe("a panel button's Release Action", () => {
  it("is offered in Tap mode", () => {
    render(<BindingProperties element={buttonInMode("tap")} project={project} onChange={vi.fn()} />);
    expect(screen.getByText("Release Action")).toBeTruthy();
  });

  it("is offered in Hold Repeat mode, where a continuous drive needs its stop", () => {
    render(<BindingProperties element={buttonInMode("hold_repeat")} project={project} onChange={vi.fn()} />);
    expect(screen.getByText("Release Action")).toBeTruthy();
  });

  it("is offered in Tap / Long Press mode", () => {
    render(<BindingProperties element={buttonInMode("tap_hold")} project={project} onChange={vi.fn()} />);
    expect(screen.getByText("Release Action")).toBeTruthy();
  });

  it("is offered in Toggle mode", () => {
    const toggle = buttonInMode("toggle", {
      toggle_key: "device.cam.tracking", toggle_value: true,
    });
    render(<BindingProperties element={toggle} project={project} onChange={vi.fn()} />);
    expect(screen.getByText("Release Action")).toBeTruthy();
  });

  it("shows a release already authored, in a mode it was not authored in", () => {
    const el = buttonInMode("hold_repeat");
    (el.bindings as Record<string, Record<string, unknown>>).do.release = [
      { action: "device.command", device: "cam", command: "pan_stop" },
    ];
    render(<BindingProperties element={el} project={project} onChange={vi.fn()} />);
    expect(screen.getByText("Release Action")).toBeTruthy();
    expect(screen.getByText("cam.pan_stop")).toBeTruthy();
  });

  it("stays off a surface that does not ask for it", () => {
    // The must-not-move half: showRelease is the caller's switch, not the
    // mode's. A control surface deck button has no release to send.
    render(
      <ButtonBindingEditor
        bindings={{ press: [{ action: "device.command", device: "cam", command: "pan_right" }] }}
        project={project}
        onBindingsChange={vi.fn()}
      />,
    );
    expect(screen.queryByText("Release Action")).toBeNull();
  });
});
