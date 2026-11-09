import { describe, it, expect } from "vitest";
import { certifiedAddresses, trustedCertState } from "./TrustedCertificateCard";
import type { CloudCertStatus } from "../../api/restClient";

const ADAPTERS = [
  { name: "lo", ip: "::1" },
  { name: "eth0", ip: "10.20.30.5" },
  { name: "wlan0", ip: "192.168.1.40" },
] as never[];

describe("certifiedAddresses", () => {
  it("uses the IPv4 this page was opened at", () => {
    expect(certifiedAddresses("192.168.1.40", ADAPTERS, "abc.certs.example.net", 8443)).toEqual({
      certifiedUrl: "https://192-168-1-40.abc.certs.example.net:8443/",
      fallbackUrl: "https://192.168.1.40:8443/",
    });
  });

  it("uses the first adapter with an IPv4 when the page was opened by name", () => {
    expect(certifiedAddresses("room.local", ADAPTERS, "abc.certs.example.net", 9443)).toEqual({
      certifiedUrl: "https://10-20-30-5.abc.certs.example.net:9443/",
      fallbackUrl: "https://10.20.30.5:9443/",
    });
  });

  it("gives no address without an IPv4, and no certified one without a suffix", () => {
    expect(certifiedAddresses("room.local", [], "abc.certs.example.net", 8443)).toEqual({ certifiedUrl: null, fallbackUrl: null });
    expect(certifiedAddresses("10.0.0.2", [], "", 8443)).toEqual({ certifiedUrl: null, fallbackUrl: "https://10.0.0.2:8443/" });
  });
});

describe("trustedCertState", () => {
  const cc = (over: Partial<CloudCertStatus>): CloudCertStatus => ({
    enabled: true, paired: true, available: true, active: false, hostname_suffix: "x", expires_at: null,
    renews_at: null, phase: "idle", last_error: "", last_error_detail: "", last_attempt_at: "", retry_pending: false,
    ...over,
  });

  it.each([
    [{ phase: "issuing", active: true }, false, "requesting"],
    [{ active: true }, false, "waiting_for_https"],
    [{ active: true, last_error: "timeout" }, true, "serving"],
    [{ last_error: "timeout", available: false }, true, "failed"],
    [{ available: false }, true, "waiting_for_cloud"],
    [{}, true, "preparing"],
  ] as const)("reads %o with HTTPS running %s as %s", (over, running, state) => {
    expect(trustedCertState(cc(over as Partial<CloudCertStatus>), running)).toBe(state);
  });
});
