import { describe, it, expect } from "vitest";
import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { CutOffRestartLines, CutOffSaved, CutOffWarning, type CutOffFacts } from "./ListenCutOff";

// The appliance's own screen shows only the panel, so nothing the cut-off
// dialog says may send anyone there: before saving, while the server
// restarts, and once this page has lost it, it names the ways into the
// Programmer that are left.

function appliance(over: Partial<CutOffFacts> = {}): CutOffFacts {
  return {
    connectedThrough: "192.168.1.40",
    mode: "this_computer",
    adapterName: "",
    programmerUrls: [],
    localUrl: "http://localhost:8080/programmer",
    ownScreenOpensProgrammer: false,
    previousLabel: "Any network",
    minutes: 10,
    cloudPaired: false,
    consequences: [],
    ...over,
  };
}

const textOf = (node: ReactElement) => render(node).container.textContent ?? "";
const warning = (f: CutOffFacts) => textOf(<CutOffWarning facts={f} />);
const restarting = (f: CutOffFacts) => textOf(<CutOffRestartLines facts={f} />);
const saved = (f: CutOffFacts) => textOf(<CutOffSaved facts={f} />);

const ETHERNET_ONLY = { mode: "adapter" as const, adapterName: "eth0", programmerUrls: ["http://10.20.30.5:8080/programmer"] };
const UNPLUGGED = { mode: "adapter" as const, adapterName: "eth0", programmerUrls: [] };

describe("the cut-off dialog on the appliance", () => {
  it("never offers this computer's own screen", () => {
    for (const f of [
      appliance(),
      appliance({ cloudPaired: true }),
      appliance(ETHERNET_ONLY),
      appliance(UNPLUGGED),
    ]) {
      for (const said of [warning(f), restarting(f), saved(f)]) {
        expect(said).not.toContain("own screen");
        expect(said).not.toContain("localhost");
      }
    }
  });

  it("with no way into the Programmer, says when it goes back instead of where to keep working", () => {
    const f = appliance({ consequences: ["Nobody can open the Programmer (remote access through OpenAVC Cloud isn't set up)."] });
    expect(warning(f)).not.toContain("To keep working");
    expect(warning(f)).toContain("Nobody can open the Programmer (remote access through OpenAVC Cloud isn't set up).");
    expect(warning(f)).toContain("OpenAVC goes back to Any network 10 minutes after the restart.");
    expect(restarting(f)).toBe(
      "This page will stop loading here.OpenAVC goes back to Any network 10 minutes after the restart.",
    );
    expect(saved(f)).toContain("Saved. Nobody can open the Programmer.");
  });

  it("names remote access through OpenAVC Cloud as the way in, once", () => {
    const f = appliance({ cloudPaired: true });
    expect(warning(f)).toContain("To keep working on it, use remote access through OpenAVC Cloud.");
    expect(warning(f).split("OpenAVC Cloud").length - 1).toBe(1);
    expect(warning(f)).toContain("If nobody opens the Programmer that way within 10 minutes, OpenAVC goes back to Any network.");
    expect(saved(f)).toContain("Saved. Open the Programmer with remote access through OpenAVC Cloud.");
  });

  it("under one adapter, names its addresses, or the adapter until it has one", () => {
    expect(warning(appliance(ETHERNET_ONLY))).toContain(
      "To keep working on it, openhttp://10.20.30.5:8080/programmerfrom a device on the eth0 network.",
    );
    expect(saved(appliance(ETHERNET_ONLY))).toContain("on a device on the eth0 network.");
    expect(warning(appliance(UNPLUGGED))).toContain(
      "To keep working on it, open the Programmer from a device on the eth0 network once that adapter has an address.",
    );
    expect(warning(appliance({ ...UNPLUGGED, cloudPaired: true }))).toContain(
      "Remote access through OpenAVC Cloud keeps working.",
    );
  });
});
