import { describe, expect, it } from "vitest";
import { configuredOrigin, restartOrigin } from "./restartPollHelpers";

describe("restart destination", () => {
  it("stays on a product proxy while its internal listener restarts", () => {
    expect(restartOrigin("http://product:18493", "http://product:8080", "http://product:8080"))
      .toBe("http://product:18493");
  });
  it("keeps an HTTPS proxy origin when the internal server uses HTTP", () => {
    expect(restartOrigin("https://product", "http://product:8080", "http://product:8080"))
      .toBe("https://product");
  });
  it("follows a port change when the browser used the configured listener", () => {
    expect(restartOrigin("http://product:8080", "http://product:8080", "http://product:8082"))
      .toBe("http://product:8082");
  });
  it("follows a direct HTTP to HTTPS change", () => {
    expect(restartOrigin("http://product:8080", "http://product:8080", "https://product:8443"))
      .toBe("https://product:8443");
  });
  it("does not switch proxy origin when its internal protocol changes", () => {
    expect(restartOrigin("https://product", "http://product:8080", "https://product:8443"))
      .toBe("https://product");
  });
  it("has no alternate destination when the initial listener is unknown", () => {
    expect(restartOrigin("http://product:18493", null, "http://product:8080"))
      .toBe("http://product:18493");
  });
  it("builds an IPv6 origin without corrupting the address", () => {
    expect(configuredOrigin({ network: { http_port: 8080 } }, "http://[::1]:18493"))
      .toBe("http://[::1]:8080");
  });
  it("normalizes default HTTPS ports for direct access", () => {
    expect(configuredOrigin({ tls: { enabled: true, port: 443 } }, "http://product:8080"))
      .toBe("https://product");
  });
});
