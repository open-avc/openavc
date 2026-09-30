import { describe, expect, it } from "vitest";
import { describeEvidence } from "./discoveryEvidence";

const ev = (data: Record<string, unknown>) => ({ tier: "enrichment", source: "x", data });

describe("the evidence lines", () => {
  it("says an OUI lookup matched only when it named a vendor", () => {
    expect(describeEvidence(ev({ kind: "oui", value: "aa:bb:cc", vendor: "Acme" })).headline)
      .toBe("OUI lookup matched aa:bb:cc → Acme");
    expect(describeEvidence(ev({ kind: "oui", value: "aa:bb:cc" })).headline)
      .toBe("MAC address prefix aa:bb:cc seen");
  });

  it("says where a manufacturer's name came from", () => {
    const greeting = describeEvidence(ev({
      kind: "vendor_string", value: "acme", raw: "ACME Widget", source_probe_id: "greeting:23",
    }));
    expect(greeting).toEqual({
      headline: 'Manufacturer "acme" named in the greeting on port 23',
      detail: '"ACME Widget"',
    });
    expect(describeEvidence(ev({
      kind: "vendor_string", value: "acme widgets", raw: "Acme Widgets", source_probe_id: "http_server:80",
    }))).toEqual({ headline: 'Manufacturer "acme widgets" named by the web server on port 80', detail: null });
    // A probe's own id is internal: the line says what kind of source it was.
    expect(describeEvidence(ev({
      kind: "vendor_string", value: "acme", raw: "Acme", source_probe_id: "custom_acme_widget_tcp",
      from_kind: "probe",
    })).headline).toBe('Manufacturer "acme" named in a probe reply');
    expect(describeEvidence(ev({
      kind: "vendor_string", value: "acme", raw: "Acme", source_probe_id: "urn:acme:device:W:1",
      from_kind: "ssdp",
    })).headline).toBe('Manufacturer "acme" named in the SSDP description');
  });
});
