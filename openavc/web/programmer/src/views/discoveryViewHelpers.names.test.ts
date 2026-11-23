import { describe, it, expect } from "vitest";

import { compareScannedNames } from "./discoveryViewHelpers";

// Devices of one state are listed by what each is called: the server's
// `name`, which is also the card's heading. An identified device is called
// by its driver's name, so it sorts there, not under the model string the
// scan happened to read.
describe("compareScannedNames", () => {
  const player = { name: "Acme Media Player", model: "ZX200BRD" };
  const display = { name: "Beta Display", model: "A-100" };
  const lower = { name: "acme Amplifier", model: null };

  it("orders by the name the card is headed with, not the model", () => {
    expect([display, player].sort(compareScannedNames)).toEqual([player, display]);
  });

  it("ignores letter case", () => {
    expect([player, lower].sort(compareScannedNames)).toEqual([lower, player]);
  });
});
