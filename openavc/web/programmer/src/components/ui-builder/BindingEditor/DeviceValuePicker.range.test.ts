import { describe, expect, it } from "vitest";
import type { UIElement } from "../../../api/types";
import { driverRangeOverreach, driverRangeTarget, type DeviceStateVarDef } from "./DeviceValuePicker";

// An invented Acme amplifier's volume, and a fader bound to it.
const fader = (min: unknown, max: unknown) =>
  ({ id: "f1", type: "fader", min, max }) as unknown as UIElement;
const declared = (min: unknown, max: unknown) =>
  ({ type: "integer", label: "Volume", min, max }) as unknown as DeviceStateVarDef;

// A bound that is no usable number: the AI's page review skips the same ones
// (is_number_value in openavc/drivers/usable_number.py).
const NOT_A_BOUND = [true, false, Infinity, -Infinity, NaN, "5", null];

describe("the driver's declared range", () => {
  it("is matched and compared when both ends are numbers", () => {
    expect(driverRangeTarget(fader(-1, 0), declared(0, 100))).toMatchObject({ min: 0, max: 100 });
    expect(driverRangeOverreach(fader(-1, 101), declared(0, 100))).toEqual([
      "its Min of -1 is below the 0 the driver declares",
      "its Max of 101 is above the 100 the driver declares",
    ]);
  });

  it.each(NOT_A_BOUND)("is no bound to match or compare when it is %s", (bound) => {
    expect(driverRangeTarget(fader(-1, 0), declared(bound, bound))).toBeNull();
    expect(driverRangeOverreach(fader(-1, 101), declared(bound, bound))).toEqual([]);
  });

  it.each(NOT_A_BOUND)("compares no end the control writes as %s", (bound) => {
    expect(driverRangeOverreach(fader(bound, bound), declared(0, 100))).toEqual([]);
  });
});
