import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChildEntitiesPanel } from "./ChildEntitiesPanel";
import type { DeviceInfo } from "../store/api";

// Every child property rendered as a raw text box, so muting a channel meant
// typing the word "true" — one round trip per letter, each overwritten by the
// echo. The driver declared these types all along; they were not being sent.

function device(overrides: Partial<DeviceInfo> = {}): DeviceInfo {
  return {
    device_id: "amp1",
    device_name: "Test Amp",
    name: "Acme Amp",
    category: "audio",
    port: 19700,
    state: {
      "channel.1.mute": false,
      "channel.1.fader": -20,
      "channel.1.name": "Bar Speakers",
      "channel.1.hiz_loz": "LoZ",
    },
    active_errors: [],
    available_errors: {},
    push_state: true,
    children: {
      channel: {
        label: "Channel",
        entries: [{ id: "1", label: "Channel 1" }],
        props: ["mute", "fader", "name", "hiz_loz"],
        prop_defs: {
          mute: { type: "boolean", label: "Mute" },
          fader: { type: "number", label: "Output Level", min: -80, max: 0, step: 0.5, unit: "dB" },
          name: { type: "string", label: "Channel Name" },
          hiz_loz: { type: "enum", label: "Output Mode", values: ["HiZ-70V", "HiZ-100V", "LoZ"] },
        },
      },
    },
    ...overrides,
  } as DeviceInfo;
}

describe("child properties are drawn as what they are", () => {
  it("gives a boolean a switch, not a box you type true into", () => {
    const onStateChange = vi.fn();
    render(<ChildEntitiesPanel device={device()} onStateChange={onStateChange} />);

    const mute = screen.getByRole("checkbox");
    expect(mute).toBeInTheDocument();
    fireEvent.click(mute);
    expect(onStateChange).toHaveBeenCalledWith("channel.1.mute", true);
  });

  it("gives a bounded number a fader that moves under the hand", () => {
    const onStateChange = vi.fn();
    render(<ChildEntitiesPanel device={device()} onStateChange={onStateChange} />);

    const fader = screen.getByLabelText("Output Level") as HTMLInputElement;
    expect(fader.type).toBe("range");
    expect(fader.min).toBe("-80");
    expect(fader.max).toBe("0");

    fireEvent.change(fader, { target: { value: "-6" } });
    expect(fader.value).toBe("-6");           // moved without waiting for a reply
    expect(onStateChange).toHaveBeenCalledWith("channel.1.fader", -6);
  });

  it("gives an enum the values the driver declared", () => {
    const onStateChange = vi.fn();
    render(<ChildEntitiesPanel device={device()} onStateChange={onStateChange} />);

    const mode = screen.getByLabelText("Output Mode") as HTMLSelectElement;
    expect([...mode.options].map((o) => o.value)).toEqual(["HiZ-70V", "HiZ-100V", "LoZ"]);
    fireEvent.change(mode, { target: { value: "HiZ-70V" } });
    expect(onStateChange).toHaveBeenCalledWith("channel.1.hiz_loz", "HiZ-70V");
  });

  it("lets a name be typed a character at a time", () => {
    const onStateChange = vi.fn();
    render(<ChildEntitiesPanel device={device()} onStateChange={onStateChange} />);

    const name = screen.getByLabelText("Channel Name") as HTMLInputElement;
    fireEvent.change(name, { target: { value: "Bar Speakers L" } });
    // Server state still says "Bar Speakers"; the field keeps what was typed.
    expect(name.value).toBe("Bar Speakers L");
  });

  it("falls back to a text field when a simulator sends no types", () => {
    const d = device();
    delete (d.children!.channel as { prop_defs?: unknown }).prop_defs;
    render(<ChildEntitiesPanel device={d} onStateChange={vi.fn()} />);

    // Older simulator, no prop_defs: still usable, just untyped.
    expect(screen.getByLabelText("mute")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
