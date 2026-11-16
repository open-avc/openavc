import { describe, expect, it } from "vitest";
import { keptMessage, problemsToFix, refusedMessage, reloadedMessage } from "./driverProblems";

const RELAY = "State variable 'relay': unknown type 'bool'; use 'boolean'";

describe("problemsToFix", () => {
  it("counts one problem in the singular and puts it on its own line", () => {
    expect(problemsToFix([RELAY])).toBe(`The driver check found 1 problem to fix:\n• ${RELAY}`);
  });

  it("lists several, one a line", () => {
    expect(problemsToFix(["a", "b"])).toBe("The driver check found 2 problems to fix:\n• a\n• b");
  });
});

describe("keptMessage", () => {
  it("says a file that will not parse will not load", () => {
    expect(keptMessage({ status: "saved", syntax_error: "SyntaxError: x", line: 4 })).toBe(
      "Saved, but this driver will not load: SyntaxError: x"
    );
  });

  it("lists what the driver check found in a file it kept", () => {
    expect(keptMessage({ status: "saved", issues: [RELAY] })).toBe(
      `Saved.\nThe driver check found 1 problem to fix:\n• ${RELAY}`
    );
  });

  it("says nothing about a clean file", () => {
    expect(keptMessage({ status: "saved" })).toBeNull();
  });
});

describe("refusedMessage", () => {
  it("lists the driver check's problems", () => {
    expect(refusedMessage({ status: "error", error: "The driver check found 1 problem to fix first", issues: [RELAY] })).toBe(
      `Not saved. The file on disk is unchanged and still loads.\nThe driver check found 1 problem to fix:\n• ${RELAY}`
    );
  });

  it("keeps the syntax error's sentence", () => {
    expect(refusedMessage({ status: "error", error: "SyntaxError: x", line: 4 })).toBe(
      "Not saved. SyntaxError: x. The file on disk is unchanged and still loads."
    );
  });
});

describe("reloadedMessage", () => {
  it("is a plain success with nothing to fix", () => {
    expect(reloadedMessage(0, [])).toEqual({ text: "Driver reloaded", problems: false });
    expect(reloadedMessage(2, undefined)).toEqual({
      text: "Driver reloaded, 2 device(s) reconnected",
      problems: false,
    });
  });

  it("names what the driver check found in the driver that loaded", () => {
    expect(reloadedMessage(1, [RELAY])).toEqual({
      text: `Driver reloaded, 1 device(s) reconnected.\nThe driver check found 1 problem to fix:\n• ${RELAY}`,
      problems: true,
    });
  });
});
