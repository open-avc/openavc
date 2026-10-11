import { describe, it, expect } from "vitest";

import { commandResultText } from "./commandParams";

// What the device page says after Send: the command went out, and what the
// device answered when the driver hands an answer back. It used to print the
// server's reply as it came, {"success": true, "result": true}.
describe("commandResultText", () => {
  it("says the command was sent when there is no answer to show", () => {
    expect(commandResultText("Power On", { success: true, result: true })).toEqual({ text: "Power On sent." });
    expect(commandResultText("Power On", { success: true, result: null })).toEqual({ text: "Power On sent." });
    expect(commandResultText("Power On", { success: true })).toEqual({ text: "Power On sent." });
  });

  it("adds a short answer in words", () => {
    expect(commandResultText("Get Level", { success: true, result: -12.5 })).toEqual({
      text: "Get Level sent. The device answered: -12.5",
    });
    expect(commandResultText("Get Name", { success: true, result: "Lobby" })).toEqual({
      text: "Get Name sent. The device answered: Lobby",
    });
    expect(commandResultText("Is Muted", { success: true, result: false })).toEqual({
      text: "Is Muted sent. The device answered: No",
    });
  });

  it("shows a structured answer below the line", () => {
    const out = commandResultText("Status", { success: true, result: { power: "on", input: 2 } });
    expect(out.text).toBe("Status sent. The device answered:");
    expect(out.detail).toBe(JSON.stringify({ power: "on", input: 2 }, null, 2));
  });
});
