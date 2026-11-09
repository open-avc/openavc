import { describe, it, expect } from "vitest";
import { advertiseHelp } from "./NetworkCard";

describe("advertiseHelp", () => {
  it("says there is nothing to announce when only this computer can connect", () => {
    expect(advertiseHelp("this_computer", "Ethernet")).toBe(
      "Nothing on the network can connect, so there is nothing to announce.",
    );
  });

  it("names the one network that can connect", () => {
    expect(advertiseHelp("adapter", "Ethernet 9")).toBe(
      "Lets the OpenAVC Panel app on the Ethernet 9 network find this system in its list. Turning this off hides the system from the list; devices can still connect by address.",
    );
  });

  it("says the chosen network when the adapter has no name", () => {
    expect(advertiseHelp("adapter", "")).toContain("on the chosen network find this system");
  });

  it("names no network when any can connect, whatever adapter is named", () => {
    expect(advertiseHelp("any", "Ethernet 9")).toBe(
      "Lets the OpenAVC Panel app find this system in its list. Turning this off hides the system from the list; devices can still connect by address.",
    );
  });
});
