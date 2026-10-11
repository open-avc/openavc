import { describe, it, expect } from "vitest";
import { offlineNextStep } from "./DeviceDetail";

// The last line of the device page's Offline banner: what happens next and
// what to do. It names the button that is on the page, and says nothing that
// assumes what OpenAVC runs on.
describe("the Offline banner's next step", () => {
  it("tells a refused sign-in to fix the credentials with Edit", () => {
    const line = offlineNextStep("auth_failed", true);
    expect(line).toContain("with Edit, then press Reconnect");
    expect(line).not.toMatch(/\bPC\b/);
    expect(line).not.toContain("Edit Device");
  });

  it("keeps retrying a device that is simply not there", () => {
    expect(offlineNextStep("unreachable", false)).toBe(
      "Reconnecting automatically, and will keep trying until the device comes back.",
    );
  });
});
