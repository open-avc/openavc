import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A driver's discovery block may write one mdns, ssdp or amx_ddp fingerprint
// on its own instead of in a list, and the platform reads it as a list of
// one. The Discovery tab shows that fingerprint, and an edit writes the list.

import { DiscoveryHintsEditor } from "./DiscoveryHintsEditor";
import type { DriverDefinition } from "../../api/types";

function draft(discovery: unknown): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    transport: "tcp",
    discovery,
  } as unknown as DriverDefinition;
}

describe("A fingerprint written on its own", () => {
  it("is shown like one in a list", () => {
    render(
      <DiscoveryHintsEditor
        draft={draft({
          mdns: "_acme._tcp.local.",
          ssdp: { device_type: "urn:acme-com:device:Widget:1" },
          amx_ddp: { make: "Acme" },
        })}
        onUpdate={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue("_acme._tcp.local.")).toBeTruthy();
    expect(screen.getByDisplayValue("urn:acme-com:device:Widget:1")).toBeTruthy();
    expect(screen.getByDisplayValue("Acme")).toBeTruthy();
  });

  it("is kept, as a list, when another fingerprint is edited", () => {
    const onUpdate = vi.fn();
    render(
      <DiscoveryHintsEditor
        draft={draft({
          mdns: "_acme._tcp.local.",
          ssdp: { device_type: "urn:acme-com:device:Widget:1" },
          amx_ddp: { make: "Acme" },
        })}
        onUpdate={onUpdate}
      />,
    );
    fireEvent.change(screen.getByDisplayValue("_acme._tcp.local."), {
      target: { value: "_acme-ctl._tcp.local." },
    });
    expect(onUpdate.mock.calls.at(-1)?.[0].discovery).toEqual({
      mdns: ["_acme-ctl._tcp.local."],
      ssdp: [{ device_type: "urn:acme-com:device:Widget:1" }],
      amx_ddp: [{ make: "Acme" }],
    });
  });
});
