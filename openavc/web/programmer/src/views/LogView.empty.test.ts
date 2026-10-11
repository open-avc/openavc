import { describe, it, expect } from "vitest";
import { emptyLogText } from "./LogView";

// An empty list under a filter is a different fact from an empty log: the
// entries are there, the filter hides them.
describe("the Log view's empty list", () => {
  it("says nothing has been logged when nothing has", () => {
    expect(emptyLogText("system", false)).toBe("No log entries yet. System activity will appear here.");
    expect(emptyLogText("state", false)).toBe(
      "No state changes recorded yet. Interact with the system to see live updates.",
    );
  });

  it("says the filter matches nothing when entries are hidden", () => {
    expect(emptyLogText("system", true)).toBe("No log entries match these filters.");
    expect(emptyLogText("state", true)).toBe("No state changes match this device filter.");
  });
});
