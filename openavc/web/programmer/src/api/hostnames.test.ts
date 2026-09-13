import { describe, it, expect } from "vitest";
import { resolvableHostname } from "./hostnames";

/**
 * The mirror of `openavc/utils/hostnames.py`'s rule, pinned against the three
 * hostname shapes the product meets. Two surfaces used to append ".local"
 * unconditionally -- the Dashboard's Panel Access card and the hostname field
 * in Settings > Network -- so a Mac was told to browse to
 * `Aarons-MacBook-Air.local.local`.
 */
describe("resolvableHostname", () => {
  it("gives a bare machine name its mDNS form", () => {
    expect(resolvableHostname("openavc")).toBe("openavc.local");
  });

  it("leaves a name that already ends in .local alone", () => {
    expect(resolvableHostname("Aarons-MacBook-Air.local")).toBe("Aarons-MacBook-Air.local");
  });

  it("leaves a managed host's domain name alone", () => {
    // The site's DNS answers this one; ".local" would answer nowhere. The
    // server's own validator accepts a dotted hostname, so this is reachable
    // by typing one into Settings > Network.
    expect(resolvableHostname("box.corp.example.com")).toBe("box.corp.example.com");
  });

  it("does not treat a trailing dot as a suffix", () => {
    expect(resolvableHostname("openavc.")).toBe("openavc.local");
  });

  it("offers nothing when the host has no name of its own", () => {
    for (const raw of ["", "   ", "localhost", "localhost.localdomain"]) {
      expect(resolvableHostname(raw)).toBe("");
    }
  });
});
