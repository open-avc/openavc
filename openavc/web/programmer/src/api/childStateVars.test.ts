import { describe, it, expect } from "vitest";
import {
  childDisplayName,
  childReadingDeclarations,
  childSchemaFor,
  childVarDefForSuffix,
} from "./childStateVars";
import type { ChildEntitiesListResponse } from "./types";

/**
 * What a child reading declares, and what it is called.
 *
 * The reading that made this necessary: a per-channel level declared
 * `number, -80 to 0 dB` on the wire, which arrived at the Monitor form with
 * nothing at all — so it was offered the tick-the-values shape and could not
 * be given a range. The name matters as much: thirty-two channels all called
 * "Output Level" is a Dashboard nobody can read.
 */

const FADER = {
  type: "number",
  label: "Output Level (dB)",
  min: -80,
  max: 0,
  step: 0.5,
  unit: "dB",
};

function payload(): ChildEntitiesListResponse {
  return {
    device_id: "amp",
    child_entity_types: {
      channel: {
        label: "Channel",
        label_plural: "Channels",
        state_variables: {
          fader: FADER,
          mute: { type: "boolean", label: "Mute" },
        },
      },
      component: {
        label: "Component",
        // A dynamic type declares only the platform keys; each child carries
        // its own discovered controls.
        state_variables: { online: { type: "boolean" } },
        dynamic: true,
      },
    },
    children: {
      channel: [
        {
          local_id: 1,
          local_id_padded: "01",
          label: "",
          display_name: "House Left",
          config: {},
          registered: true,
          state: {},
        },
        {
          local_id: 2,
          local_id_padded: "02",
          label: "",
          display_name: "",
          config: {},
          registered: true,
          state: {},
        },
      ],
      component: [
        {
          local_id: "gain_1",
          local_id_padded: "gain_1",
          label: "Room Gain",
          display_name: "Room Gain",
          config: {},
          registered: true,
          state: {},
          schema: { position: { type: "number", label: "Position", min: 0, max: 1 } },
        },
      ],
    },
  } as unknown as ChildEntitiesListResponse;
}

describe("childSchemaFor", () => {
  it("prefers the child's own discovered schema over its type's", () => {
    const resp = payload();
    const dynamic = resp.children.component[0];
    expect(Object.keys(childSchemaFor(resp, "component", dynamic))).toEqual([
      "position",
    ]);
  });

  it("falls back to the type-level schema every sibling shares", () => {
    const resp = payload();
    expect(childSchemaFor(resp, "channel", resp.children.channel[0]).fader)
      .toEqual(FADER);
  });

  it("is empty for a type the payload does not carry", () => {
    const resp = payload();
    expect(childSchemaFor(resp, "nope", resp.children.channel[0])).toEqual({});
  });
});

describe("childVarDefForSuffix", () => {
  it("resolves a child reading down to the driver's declaration", () => {
    expect(childVarDefForSuffix(payload(), "channel.01.fader")).toEqual(FADER);
  });

  it("resolves a dynamic child against its own schema", () => {
    expect(childVarDefForSuffix(payload(), "component.gain_1.position")?.max)
      .toBe(1);
  });

  it("answers nothing for a device-level key, an unknown child, or no payload", () => {
    expect(childVarDefForSuffix(payload(), "power")).toBeNull();
    expect(childVarDefForSuffix(payload(), "channel.99.fader")).toBeNull();
    expect(childVarDefForSuffix(payload(), "channel.01.nosuch")).toBeNull();
    expect(childVarDefForSuffix(null, "channel.01.fader")).toBeNull();
  });
});

describe("childDisplayName", () => {
  it("uses the name the child is known by", () => {
    const resp = payload();
    const tdef = resp.child_entity_types.channel;
    expect(childDisplayName("channel", tdef, resp.children.channel[0]))
      .toBe("House Left");
  });

  it("falls back to the type and the id when nothing has named it", () => {
    const resp = payload();
    const tdef = resp.child_entity_types.channel;
    expect(childDisplayName("channel", tdef, resp.children.channel[1]))
      .toBe("Channel 2");
  });
});

describe("childReadingDeclarations", () => {
  it("keys every reading by its suffix under device.<id>.", () => {
    const decls = childReadingDeclarations(payload());
    expect([...decls.keys()].sort()).toEqual([
      "channel.01.fader",
      "channel.01.mute",
      "channel.02.fader",
      "channel.02.mute",
      "component.gain_1.position",
    ]);
  });

  it("carries the range and unit through, which is the whole point", () => {
    const fader = childReadingDeclarations(payload()).get("channel.01.fader");
    expect(fader?.type).toBe("number");
    expect(fader?.min).toBe(-80);
    expect(fader?.max).toBe(0);
    expect(fader?.unit).toBe("dB");
  });

  it("names the child first, so two channels are not one name twice", () => {
    const decls = childReadingDeclarations(payload());
    expect(decls.get("channel.01.fader")?.label).toBe("House Left · Output Level (dB)");
    expect(decls.get("channel.02.fader")?.label).toBe("Channel 2 · Output Level (dB)");
  });

  it("uses the property name when the driver declared no label", () => {
    const resp = payload();
    resp.child_entity_types.channel.state_variables.fader = { type: "number" };
    expect(childReadingDeclarations(resp).get("channel.01.fader")?.label)
      .toBe("House Left · fader");
  });

  it("is empty rather than absent when there is no payload", () => {
    expect(childReadingDeclarations(null).size).toBe(0);
  });
});
