import { describe, expect, it } from "vitest";
import { describeEvidence } from "./discoveryEvidence";

const ev = (data: Record<string, unknown>) => ({ tier: "enrichment", source: "x", data });

describe("the evidence lines", () => {
  it("says a binary reply by its pattern or its bytes, never its stray printable bytes", () => {
    // A checksummed binary reply (latin-1 decoded): "N" and "]" are printable.
    const binary = { text: "\xaa\xff\x01\x03N\x0b\x01]", hex: "aaff01034e0b015d" };
    expect(describeEvidence(ev({
      kind: "probe", port: 1515, response: binary, matched_pattern: "hex aa ff",
    })).headline).toBe("TCP probe on port 1515 matched hex aa ff");
    expect(describeEvidence(ev({ kind: "probe", port: 1515, response: binary })).headline)
      .toBe("TCP probe on port 1515 returned hex aa ff 01 03 4e 0b 01 5d");
    // A reply that quotes something itself is set off with single quotes.
    expect(describeEvidence(ev({
      kind: "probe", port: 4321, response: { text: '/acme/model "W-100"\n' },
    })).headline).toBe(`TCP probe on port 4321 returned '/acme/model "W-100"'`);
  });

  it("never shows a probe's own id", () => {
    expect(describeEvidence(ev({
      kind: "probe", source_id: "custom_acme_widget_tcp", port: 23, response: { text: "ACME W-100" },
    }))).toEqual({ headline: 'TCP probe on port 23 returned "ACME W-100"', detail: null });
    expect(describeEvidence(ev({
      kind: "broadcast", source_id: "custom_acme_widget_udp", port: 9131,
      response: { ip: "10.0.0.50" },
    }))).toEqual({ headline: "UDP probe on port 9131 matched", detail: "response from 10.0.0.50" });
  });

  it("names the SSDP and AMX DDP fields in words", () => {
    expect(describeEvidence(ev({
      kind: "ssdp", source_id: "urn:acme:device:W:1", manufacturer: "Acme", friendly_name: "Lobby",
    }))).toEqual({
      headline: "SSDP announcement for urn:acme:device:W:1",
      detail: "manufacturer: Acme; name: Lobby",
    });
    expect(describeEvidence(ev({ kind: "amx_ddp", make: "Acme", model: "W-100" })).headline)
      .toBe("AMX DDP beacon: Acme W-100");
    expect(describeEvidence(ev({ kind: "open_port", value: 23 })).headline).toBe("Port 23 is open");
  });

  it("says whose MAC address prefix it is only when a driver named one", () => {
    expect(describeEvidence(ev({ kind: "oui", value: "aa:bb:cc", vendor: "Acme" })).headline)
      .toBe("MAC address prefix aa:bb:cc belongs to Acme");
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
    // A manufacturer the driver's own probe supplies is the driver's word.
    expect(describeEvidence(ev({
      kind: "vendor_string", value: "acme", raw: "Acme", source_probe_id: "custom_acme_widget_tcp",
      from_kind: "probe", from_driver: true,
    })).headline).toBe('Manufacturer "acme" named by the driver when its probe matched');
    // Listed under several drivers, it names the one whose probe supplied it.
    const names: Record<string, string> = { acme_widget: "Acme Widget" };
    expect(describeEvidence(ev({
      kind: "vendor_string", value: "acme", raw: "Acme", source_probe_id: "custom_acme_widget_tcp",
      from_kind: "probe", from_driver: true, supplied_by: "acme_widget",
    }), (id) => names[id]).headline).toBe(
      'Manufacturer "acme" named by the Acme Widget driver when its probe matched',
    );
  });
});
