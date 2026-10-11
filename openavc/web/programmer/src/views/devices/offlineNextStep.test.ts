import { describe, it, expect } from "vitest";
import { hasSignInFields, offlineNextStep } from "./DeviceDetail";

// The last line of the device page's Offline banner: what happens next and
// what to do. It names the button that is on the page, and says nothing that
// assumes what OpenAVC runs on.
//
// A refused sign-in is not always a wrong password: a TV whose pairing prompt
// was declined, a bridge that has to be paired with its link button, or a
// device whose own setting blocks the account. The line above (the driver's
// sentence) says which; this line only points at Edit when the driver has
// sign-in details there.
describe("the Offline banner's next step", () => {
  it("points a refused sign-in at the line above, and at Edit when the sign-in details are there", () => {
    const line = offlineNextStep("auth_failed", true, true);
    expect(line).toBe(
      "Not retrying, so repeated sign-ins cannot lock OpenAVC out of the device. "
      + "Fix the cause above (the sign-in details are under Edit), then press Reconnect.",
    );
    expect(line).not.toMatch(/\bPC\b/);
    expect(line).not.toContain("Edit Device");
  });

  it("never sends a device with nothing to sign in with to Edit", () => {
    expect(offlineNextStep("auth_failed", true, false)).toBe(
      "Not retrying, so repeated sign-ins cannot lock OpenAVC out of the device. "
      + "Fix the cause above, then press Reconnect.",
    );
  });

  it("keeps retrying a device that is simply not there", () => {
    expect(offlineNextStep("unreachable", false, true)).toBe(
      "Reconnecting automatically, and will keep trying until the device comes back.",
    );
  });
});

describe("whether a driver has sign-in details under Edit", () => {
  it("is yes for a password or key field", () => {
    expect(hasSignInFields({
      host: { type: "string" },
      password: { type: "string", secret: true },
    })).toBe(true);
    expect(hasSignInFields({ pin: { type: "password" } })).toBe(true);
  });

  it("is no for a device that pairs or has no credentials", () => {
    expect(hasSignInFields({
      host: { type: "string" },
      port: { type: "integer" },
      mac_address: { type: "string" },
    })).toBe(false);
    expect(hasSignInFields(undefined)).toBe(false);
  });
});
