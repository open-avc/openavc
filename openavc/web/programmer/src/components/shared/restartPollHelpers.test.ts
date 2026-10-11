import { describe, it, expect } from "vitest";
import { probeInit, probeReached, waitOrigins, withHost } from "./restartPollHelpers";

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

describe("probing another address", () => {
  it("reads the page's own origin and fetches any other one no-cors", () => {
    expect(probeInit(HERE, HERE).mode).toBe("cors");
    // Another address cannot be read across origins, but an opaque answer
    // still says the server is up there.
    expect(probeInit("http://10.20.30.5:8080", HERE).mode).toBe("no-cors");
    expect(probeReached({ ok: false, type: "opaque" })).toBe(true);
    expect(probeReached({ ok: true, type: "basic" })).toBe(true);
    expect(probeReached({ ok: false, type: "basic" })).toBe(false);
  });
});
