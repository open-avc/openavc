import { describe, it, expect } from "vitest";
import { landingUrl, probeInit, statusProbeUrl, waitOrigins, withHost } from "./restartPollHelpers";

const HERE = "http://192.168.1.40:8080";

describe("waitOrigins", () => {
  it("waits only at the expected address on an ordinary restart", () => {
    // Never the page's own origin as a fallback: an unrelated healthy
    // listener there is not this server.
    expect(waitOrigins("http://192.168.1.40:9090", [], false)).toEqual(["http://192.168.1.40:9090"]);
  });

  it("adds the address a change of who can connect moves the page to", () => {
    expect(waitOrigins("http://127.0.0.1:8080", ["http://127.0.0.1:8080"], false))
      .toEqual(["http://127.0.0.1:8080"]);
  });

  it("waits only at the new choice's addresses for a browser it cuts off", () => {
    expect(waitOrigins(HERE, ["http://10.20.30.5:8080"], true)).toEqual(["http://10.20.30.5:8080"]);
    expect(waitOrigins(HERE, [], true)).toEqual([]);
  });
});

describe("withHost", () => {
  it("keeps the scheme and port", () => {
    expect(withHost("https://product:8443", "127.0.0.1")).toBe("https://127.0.0.1:8443");
    expect(withHost("http://[::1]:8080", "10.20.30.5")).toBe("http://10.20.30.5:8080");
  });
});

describe("asking the status", () => {
  it("reads every address, its own and any other, never an opaque answer", () => {
    expect(probeInit().mode).toBe("cors");
    // The page's own sign-in reaches its own origin (the cloud's remote
    // access authorizes each request with it), and no other.
    expect(probeInit().credentials).toBe("same-origin");
  });

  it("asks at the server root, and through the page's own prefix at the page's own origin", () => {
    expect(statusProbeUrl(HERE, { origin: HERE, prefix: "" })).toBe("http://192.168.1.40:8080/api/startup-status");
    expect(statusProbeUrl("http://127.0.0.1:8080", { origin: HERE, prefix: "/tunnel/abc" }))
      .toBe("http://127.0.0.1:8080/api/startup-status");
    const cloud = { origin: "https://cloud.example.com", prefix: "/tunnel/abc" };
    expect(statusProbeUrl(cloud.origin, cloud)).toBe("https://cloud.example.com/tunnel/abc/api/startup-status");
    expect(landingUrl("/programmer", cloud.origin, cloud)).toBe("https://cloud.example.com/tunnel/abc/programmer");
    expect(landingUrl("/programmer", "https://127.0.0.1:8443", cloud)).toBe("https://127.0.0.1:8443/programmer");
  });
});
