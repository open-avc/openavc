import { describe, it, expect } from "vitest";
import {
  childPresence,
  childStateFor,
  countTrouble,
  troublePhrase,
  troubleReasons,
  scanChildTrouble,
  troubleSummary,
} from "./childPresence";

describe("childPresence", () => {
  it("reads a child with no fault as in service", () => {
    expect(childPresence({ online: true }).ok).toBe(true);
  });

  it("reads online:false as not in service even with no reason given", () => {
    // Every driver written before the fault vocabulary does exactly this, and
    // it has to keep working -- the dot is the point, the reason is a bonus.
    const p = childPresence({ online: false });
    expect(p.ok).toBe(false);
    expect(p.reason).toBe("");
  });

  it("carries the code and the sentence when the driver set them", () => {
    const p = childPresence({
      online: false,
      offline_reason: "service_fault",
      offline_detail: "Reachable, but not running.",
    });
    expect(p.reason).toBe("service_fault");
    expect(p.detail).toBe("Reachable, but not running.");
  });

  it("treats an unknown child as present rather than faulted", () => {
    // Undefined is the pre-registration / stale-snapshot case. Drawing it red
    // would flash a wall of faults across every list while it loads.
    expect(childPresence(undefined).ok).toBe(true);
    expect(childPresence({}).ok).toBe(true);
  });

  it("does not read the string \"false\" as offline", () => {
    // A YAML driver used to land the STRING "false" here, which is truthy.
    // The platform fix is upstream (the child_set compiler now coerces by the
    // reserved type); this pins that the renderer never quietly compensated
    // for it, because compensating would have hidden the real bug.
    expect(childPresence({ online: "false" }).ok).toBe(true);
  });
});

describe("childStateFor", () => {
  it("overlays live state on the fetched snapshot", () => {
    const state = childStateFor(
      { "device.mx.decoder.0a1d.online": false },
      "mx",
      "decoder",
      { local_id_padded: "0a1d", state: { online: true, name: "Podium PC" } },
    );
    expect(state.online).toBe(false);
    expect(state.name).toBe("Podium PC");
  });

  it("keeps the snapshot where live state has said nothing yet", () => {
    const state = childStateFor({}, "mx", "decoder", {
      local_id_padded: "0a1d",
      state: { online: false },
    });
    expect(state.online).toBe(false);
  });
});

describe("an empty slot is not a fault", () => {
  // Q-203: seven AT-LINK extension positions on a standalone mixer used to
  // draw seven green dots. Registering them offline fixes that half; this is
  // the other half -- they must not now read as seven faults instead.
  const EMPTY = { online: false, offline_reason: "not_fitted" };

  it("is not in service, and is not trouble either", () => {
    const p = childPresence(EMPTY);
    expect(p.ok).toBe(false);
    expect(p.trouble).toBe(false);
  });

  it("is left out of the count the tab badge draws", () => {
    expect(countTrouble([EMPTY, EMPTY, { online: true }])).toBe(0);
  });

  it("does not put a device-offline child in the same bucket", () => {
    const p = childPresence({ online: false, offline_reason: "parent_offline" });
    expect(p.ok).toBe(false);
    expect(p.trouble).toBe(true);
  });

  it("reads an unrecognised code as trouble rather than as fine", () => {
    // A driver writing a code the taxonomy does not define is a bug, and the
    // safe reading of "I do not know this one" is never "everything is fine".
    expect(childPresence({ online: false, offline_reason: "gremlins" }).trouble)
      .toBe(true);
  });
});

describe("countTrouble", () => {
  it("counts only the children that are in trouble", () => {
    expect(countTrouble([{ online: true }, { online: false }, { online: false }])).toBe(2);
  });
});

describe("troublePhrase", () => {
  it("keeps the wording it has always had for an endpoint that is absent", () => {
    expect(troublePhrase(["not_responding"])).toBe("not answering");
    // Every driver predating the taxonomy leaves the reason empty.
    expect(troublePhrase(["", ""])).toBe("not answering");
  });

  it("says what is actually wrong when every code agrees", () => {
    expect(troublePhrase(["service_fault", "service_fault"]))
      .toBe("reachable, but not running");
    expect(troublePhrase(["parent_offline"]))
      .toBe("unavailable while the device is offline");
  });

  it("falls back to a neutral phrase when the codes disagree", () => {
    // "are not answering" would be a lie about the wedged one.
    expect(troublePhrase(["not_responding", "service_fault"]))
      .toBe("not in service");
  });
});

describe("troubleReasons", () => {
  it("reports one code per child in trouble, and skips empty slots", () => {
    expect(
      troubleReasons([
        { online: true },
        { online: false, offline_reason: "not_fitted" },
        { online: false, offline_reason: "service_fault" },
        { online: false },
      ]),
    ).toEqual(["service_fault", ""]);
  });
});

describe("scanChildTrouble", () => {
  const TYPES = {
    decoder: { label: "Decoder", label_plural: "Decoders", label_field: "name" },
  };

  it("finds the endpoints that are down and names them the way the list does", () => {
    const groups = scanChildTrouble(
      {
        "device.mx.decoder.0a1b.online": true,
        "device.mx.decoder.0a1b.name": "Cable Box",
        "device.mx.decoder.0a1d.online": false,
        "device.mx.decoder.0a1d.name": "Podium PC",
        "device.mx.decoder.0a1e.online": false,
        "device.mx.decoder.0a1e.name": "Rear Cam",
        "device.mx.decoder.0a1e.label": "Camera 2",
      },
      "mx",
      TYPES,
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].total).toBe(3);
    // Authored label wins over the device's own name; the device's name is
    // used where nobody typed one. Same order as child_display_name.
    expect(groups[0].names).toEqual(["Podium PC", "Camera 2"]);
  });

  it("falls back to the id when nothing has named the child", () => {
    const groups = scanChildTrouble(
      { "device.mx.decoder.0a1d.online": false },
      "mx",
      TYPES,
    );
    expect(groups[0].names).toEqual(["0a1d"]);
  });

  it("ignores keys belonging to the device itself", () => {
    const groups = scanChildTrouble(
      { "device.mx.connected": false, "device.mx.power": false },
      "mx",
      TYPES,
    );
    expect(groups).toEqual([]);
  });

  it("ignores another device's children", () => {
    const groups = scanChildTrouble(
      { "device.other.decoder.0a1d.online": false },
      "mx",
      TYPES,
    );
    expect(groups).toEqual([]);
  });

  it("leaves empty slots out of the banner entirely", () => {
    // The device banner is the loudest thing on the page. Seven "extension
    // slots not answering" on a standalone mixer is the same false alarm the
    // green dots were, pointed the other way.
    const groups = scanChildTrouble(
      {
        "device.atdm.link_extension.1.online": false,
        "device.atdm.link_extension.1.offline_reason": "not_fitted",
        "device.atdm.link_extension.2.online": false,
        "device.atdm.link_extension.2.offline_reason": "not_fitted",
      },
      "atdm",
      { link_extension: { label: "Extension", label_plural: "Extensions" } },
    );
    expect(groups).toEqual([]);
  });

  it("carries each child's code so the sentence can be worded from it", () => {
    const groups = scanChildTrouble(
      {
        "device.mx.decoder.0a1d.online": false,
        "device.mx.decoder.0a1d.offline_reason": "service_fault",
        "device.mx.decoder.0a1e.online": false,
        "device.mx.decoder.0a1e.offline_reason": "not_fitted",
      },
      "mx",
      TYPES,
    );
    expect(groups[0].reasons).toEqual(["service_fault"]);
    // The empty slot is still counted in the roster it is part of.
    expect(groups[0].total).toBe(2);
  });

  it("handles a child property whose own name contains dots", () => {
    // Q-SYS control names look like `input.1.gain`. The scan splits on the
    // FIRST two dots only, so the rest stays one property name.
    const groups = scanChildTrouble(
      {
        "device.core.component.Mixer.input.1.gain": 0,
        "device.core.component.Mixer.online": false,
      },
      "core",
      { component: { label: "Component", label_plural: "Components" } },
    );
    expect(groups[0].names).toEqual(["Mixer"]);
    expect(groups[0].total).toBe(1);
  });
});

describe("troubleSummary", () => {
  it("says nothing at all when everything is fine", () => {
    // The banner must not exist on a healthy device, the way an offline
    // banner does not exist on a connected one.
    expect(troubleSummary([])).toBeNull();
    expect(
      troubleSummary([
        { noun: "Decoder", nounPlural: "Decoders", names: [], reasons: [], total: 8 },
      ]),
    ).toBeNull();
  });

  it("counts against the roster and agrees in number", () => {
    const s = troubleSummary([
      {
        noun: "Decoder", nounPlural: "Decoders",
        names: ["Podium PC", "Rear Cam"], reasons: ["", ""], total: 8,
      },
    ]);
    expect(s?.headline).toBe("2 of 8 decoders are not answering.");
    expect(s?.names).toBe("Podium PC, Rear Cam");
  });

  it("reads as a singular sentence for one endpoint", () => {
    const s = troubleSummary([
      {
        noun: "Decoder", nounPlural: "Decoders",
        names: ["Podium PC"], reasons: ["not_responding"], total: 8,
      },
    ]);
    expect(s?.headline).toBe("1 of 8 decoder is not answering.");
  });

  it("joins two types into one sentence", () => {
    const s = troubleSummary([
      {
        noun: "Encoder", nounPlural: "Encoders",
        names: ["Apple TV"], reasons: [""], total: 4,
      },
      {
        noun: "Decoder", nounPlural: "Decoders",
        names: ["Podium PC"], reasons: [""], total: 8,
      },
    ]);
    expect(s?.headline).toBe(
      "1 of 4 encoder and 1 of 8 decoder are not answering.",
    );
  });

  it("words the headline for what is actually wrong", () => {
    // "are not answering" is a lie about an endpoint that is answering fine
    // and simply not running what it exists to run.
    const s = troubleSummary([
      {
        noun: "Endpoint", nounPlural: "Endpoints",
        names: ["Podium PC"], reasons: ["service_fault"], total: 4,
      },
    ]);
    expect(s?.headline).toBe("1 of 4 endpoint is reachable, but not running.");
  });

  it("goes neutral when the endpoints are down for different reasons", () => {
    const s = troubleSummary([
      {
        noun: "Endpoint", nounPlural: "Endpoints",
        names: ["Podium PC", "Rear Cam"],
        reasons: ["service_fault", "not_responding"], total: 4,
      },
    ]);
    expect(s?.headline).toBe("2 of 4 endpoints are not in service.");
  });

  it("caps the names so a bad day on a big frame cannot take the page", () => {
    const names = Array.from({ length: 40 }, (_, i) => `Port ${i + 1}`);
    const s = troubleSummary([
      {
        noun: "Output", nounPlural: "Outputs",
        names, reasons: names.map(() => ""), total: 96,
      },
    ]);
    expect(s?.headline).toBe("40 of 96 outputs are not answering.");
    expect(s?.names).toBe(
      "Port 1, Port 2, Port 3, Port 4, Port 5, Port 6 and 34 more",
    );
  });
});
