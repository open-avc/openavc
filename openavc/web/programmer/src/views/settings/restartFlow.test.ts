import { describe, it, expect } from "vitest";
import { restartTargets } from "./restartFlow";
import type { SystemConfig } from "../../api/restClient";

const page = { origin: "http://192.168.1.40:8080", protocol: "http:" };

function config(tls: { enabled: boolean; port?: number }, httpPort = 8080): SystemConfig {
  return { tls, network: { http_port: httpPort } } as unknown as SystemConfig;
}

const stay = { cutOff: false, hosts: [""] };

describe("restartTargets", () => {
  it("waits at this page's address behind a proxy, whatever is saved", () => {
    expect(restartTargets(page, "http://192.168.1.40:9999", config({ enabled: true, port: 8443 }), stay)).toEqual({
      urls: ["http://192.168.1.40:8080/programmer"],
      localUrl: "http://localhost:8080/programmer",
      targetUrl: "http://192.168.1.40:8080/programmer",
      alsoTry: [],
      isProtocolSwitch: false,
      expectsNewCert: false,
    });
  });

  it("waits at the saved listener when this page reached the server directly", () => {
    const t = restartTargets(page, page.origin, config({ enabled: true, port: 9443 }), stay);
    expect(t.targetUrl).toBe("https://192.168.1.40:9443/programmer");
    expect(t.isProtocolSwitch).toBe(true);
    expect(t.expectsNewCert).toBe(true);
  });

  it("knows no new certificate is coming when the page is already on HTTPS", () => {
    const https = { origin: "https://192.168.1.40:8443", protocol: "https:" };
    const t = restartTargets(https, https.origin, config({ enabled: false }, 8080), stay);
    expect(t.targetUrl).toBe("http://192.168.1.40:8080/programmer");
    expect(t.isProtocolSwitch).toBe(true);
    expect(t.expectsNewCert).toBe(false);
    const kept = restartTargets(https, https.origin, config({ enabled: true }), stay);
    expect(kept.targetUrl).toBe("https://192.168.1.40:8443/programmer");
    expect(kept.isProtocolSwitch).toBe(false);
    expect(kept.expectsNewCert).toBe(false);
  });

  it("moves to 127.0.0.1 on the same scheme and port", () => {
    const t = restartTargets(page, null, config({ enabled: false }), { cutOff: false, hosts: ["127.0.0.1"] });
    expect(t.targetUrl).toBe("http://127.0.0.1:8080/programmer");
    expect(t.alsoTry).toEqual([]);
  });

  it("tries the later addresses too when not cut off", () => {
    const t = restartTargets(page, null, config({ enabled: false }), { cutOff: false, hosts: ["", "127.0.0.1"] });
    expect(t.targetUrl).toBe("http://192.168.1.40:8080/programmer");
    expect(t.alsoTry).toEqual(["http://127.0.0.1:8080/programmer"]);
  });

  it("waits only at the new choice's addresses when cut off", () => {
    const t = restartTargets(page, null, config({ enabled: false }), { cutOff: true, hosts: ["10.20.30.5", "10.20.30.6"] });
    expect(t.targetUrl).toBe("http://10.20.30.5:8080/programmer");
    expect(t.alsoTry).toEqual(["http://10.20.30.5:8080/programmer", "http://10.20.30.6:8080/programmer"]);
  });

  it("waits at this computer's own address when cut off with nowhere to go", () => {
    const t = restartTargets(page, null, config({ enabled: false }), { cutOff: true, hosts: [] });
    expect(t.targetUrl).toBe("http://localhost:8080/programmer");
    expect(t.alsoTry).toEqual([]);
    expect(t.urls).toEqual([]);
  });
});
