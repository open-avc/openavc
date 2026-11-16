import { describe, it, expect } from "vitest";
import { memberTitle, memberTone } from "./groupMemberLook";

describe("a group command step's member", () => {
  it("that took the command is done", () => {
    const member = { device_id: "a", name: "A", success: true };
    expect(memberTone(member)).toBe("done");
    expect(memberTitle(member)).toBe("Success");
  });

  it("that the command reached but did not finish is not shown as failed", () => {
    const member = {
      device_id: "a", name: "A", success: false, sent: true, outcome: "partial",
      error: "Port 3 did not come back on.", message: "Port 3 did not come back on.",
    };
    expect(memberTone(member)).toBe("reached");
    expect(memberTitle(member)).toBe("Port 3 did not come back on.");
  });

  it.each(["uncertain", "pending", "failed", "rejected"])(
    "that the command reached with the outcome %s is not shown as failed",
    (outcome) => {
      // Each of these sentences tells the reader to check the equipment.
      const member = { device_id: "a", name: "A", success: false, sent: true, outcome, error: "x" };
      expect(memberTone(member)).toBe("reached");
    },
  );

  it("that the command never reached failed", () => {
    const sentNothing = {
      device_id: "a", name: "A", success: false, sent: false, outcome: "not_sent",
      error: "Reboot was not sent.", message: "A couldn't run Reboot. Contact support.",
    };
    expect(memberTone(sentNothing)).toBe("failed");
    expect(memberTitle(sentNothing)).toBe("A couldn't run Reboot. Contact support.");
    // An older server says nothing of where the command got.
    expect(memberTone({ device_id: "a", name: "A", success: false, error: "Device offline" })).toBe("failed");
  });
});
