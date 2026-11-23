import { describe, expect, it } from "vitest";
import { TRANSPORT_TRAITS } from "../../api/types";
import { dialsAnAddress, transportTraits } from "./transportTraits";

describe("transportTraits", () => {
  it("reads a transport's row whatever its case", () => {
    expect(transportTraits("SSH")).toBe(TRANSPORT_TRAITS.ssh);
    expect(transportTraits("tcp")?.simulator).toBe("tcp");
  });

  it("has no row for a name that is no transport", () => {
    expect(transportTraits("nonsense")).toBeUndefined();
    expect(transportTraits("toString")).toBeUndefined();
    expect(transportTraits(undefined)).toBeUndefined();
  });

  it("says which transports dial an address of their own", () => {
    const addressed = Object.keys(TRANSPORT_TRAITS).filter(dialsAnAddress);
    expect(addressed).toEqual(["tcp", "udp", "http", "osc", "ssh", "mqtt", "snmp"]);
    expect(dialsAnAddress("serial")).toBe(false);
    expect(dialsAnAddress("bridge")).toBe(false);
    expect(dialsAnAddress("constructor")).toBe(false);
  });
});
