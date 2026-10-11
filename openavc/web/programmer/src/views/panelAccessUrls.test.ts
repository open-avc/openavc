import { describe, it, expect } from "vitest";
import { panelAccess, panelBase } from "./panelAccessUrls";

const ANY = {
  mode: "any", adapter: {}, adapter_name: "", label: "Any network",
  addresses: ["0.0.0.0"], network_addresses: [], local_only: false,
  held_by: null, problem: null,
};
const THIS_COMPUTER = {
  ...ANY, mode: "this_computer", label: "This computer only",
  addresses: ["127.0.0.1"], local_only: true,
};

const LAN_STATUS = {
  local_ip: "192.168.1.50",
  local_ips: ["192.168.1.50"],
  hostname: "AVC-LOBBY",
  http_port: 8080,
  port80_active: false,
  listen: ANY,
};

const onLan = { hostname: "192.168.1.50", origin: "http://192.168.1.50:8080" };

describe("panelAccess", () => {
  it("publishes the browser's own address on the LAN", () => {
    const a = panelAccess(LAN_STATUS, null, onLan);
    expect(a.tunneled).toBe(false);
    expect(a.panelUrl).toBe("http://192.168.1.50:8080/panel");
    expect(a.hostnameUrl).toBe("http://AVC-LOBBY.local:8080/panel");
    expect(a.qrPanelUrl).toBe("http://192.168.1.50:8080/panel");
  });

  it("prefers the address the browser reached over a stale detected one", () => {
    // A server started before its adapter came up reports loopback. The page
    // is the ground truth whenever it arrived by IP.
    const a = panelAccess({ ...LAN_STATUS, local_ip: "127.0.0.1" }, null, onLan);
    expect(a.panelUrl).toBe("http://192.168.1.50:8080/panel");
    expect(a.shortPanelUrl).toBe("");
  });

  it("offers nothing over a path-mode tunnel", () => {
    // The bug this exists to stop: the origin without the tunnel's own path
    // is the cloud portal, which answers 200 and is not the panel.
    const a = panelAccess(
      { ...LAN_STATUS, tunneled: true },
      null,
      { hostname: "cloud.openavc.com", origin: "https://cloud.openavc.com" },
    );
    expect(a.tunneled).toBe(true);
    expect(a.panelUrl).toBe("");
    expect(a.shortPanelUrl).toBe("");
    expect(a.hostnameUrl).toBe("");
    expect(a.pairUrl).toBe("");
    expect(a.qrPanelUrl).toBe("");
  });

  it("offers nothing over a subdomain-mode tunnel either", () => {
    // Here the page's path carries no tunnel prefix, so only the server's
    // answer distinguishes this from a browser on the LAN.
    const a = panelAccess(
      { ...LAN_STATUS, tunneled: true },
      null,
      {
        hostname: "tunnel-abc123.cloud.openavc.com",
        origin: "https://tunnel-abc123.cloud.openavc.com",
      },
    );
    expect(a.tunneled).toBe(true);
    expect(a.panelUrl).toBe("");
  });

  it("reports a server that accepts this computer only as local only", () => {
    const a = panelAccess({ ...LAN_STATUS, listen: THIS_COMPUTER }, null, onLan);
    expect(a.localOnly).toBe(true);
    expect(a.tunneled).toBe(false);
    expect(a.panelUrl).toBe("");
  });

  it("answers tunnelled first when the server is also loopback-bound", () => {
    // The tunnel proxies to loopback, so Remote Panel reaches this server.
    // Sending the reader to Settings to change the bind address would not.
    const a = panelAccess(
      { ...LAN_STATUS, listen: THIS_COMPUTER, tunneled: true },
      null,
      { hostname: "cloud.openavc.com", origin: "https://cloud.openavc.com" },
    );
    expect(a.tunneled).toBe(true);
    expect(a.localOnly).toBe(true);
  });

  it("publishes the certified address when a trusted cert is active", () => {
    const a = panelAccess(LAN_STATUS, {
      enabled: true,
      port: 8443,
      redirect_http: true,
      cloud_cert: { active: true, hostname_suffix: "sys.openavc.net" },
    } as never, onLan);
    expect(a.panelUrl).toBe("https://192-168-1-50.sys.openavc.net:8443/panel");
    expect(a.certified).toBe(true);
    expect(a.shortPanelUrl).toBe("http://192.168.1.50:8080/panel");
  });

  it("keeps a hostname that already carries a domain", () => {
    // The card appended ".local" unconditionally, so a macOS controller --
    // gethostname() there is already "Sams-MacBook-Air.local" -- published
    // "Sams-MacBook-Air.local.local", which resolves nowhere. The same
    // doubling was on the appliance setup screen (server side, fixed in
    // utils/hostnames.py).
    const a = panelAccess(
      { ...LAN_STATUS, hostname: "Sams-MacBook-Air.local" },
      null,
      onLan,
    );
    expect(a.hostnameUrl).toBe("http://Sams-MacBook-Air.local:8080/panel");
  });

  it("leaves a managed host's domain name alone", () => {
    const a = panelAccess(
      { ...LAN_STATUS, hostname: "box.corp.example.com" },
      null,
      onLan,
    );
    expect(a.hostnameUrl).toBe("http://box.corp.example.com:8080/panel");
  });

  it("offers no hostname URL when the host has no name of its own", () => {
    // A chroot answers "localhost", which is true of every machine and tells
    // a panel nothing.
    const a = panelAccess({ ...LAN_STATUS, hostname: "localhost" }, null, onLan);
    expect(a.hostnameUrl).toBe("");
  });

  it("says why when the chosen adapter is missing", () => {
    const problem = {
      code: "adapter_not_connected",
      message: "Not accepting connections from the network: the Ethernet adapter isn't connected.",
    };
    const a = panelAccess(
      { ...LAN_STATUS, listen: { ...ANY, mode: "adapter", local_only: true, problem } },
      null,
      onLan,
    );
    expect(a.localOnly).toBe(true);
    expect(a.problem).toBe(problem.message);
    expect(a.panelUrl).toBe("");
  });

  it("publishes only the chosen adapter's address", () => {
    // The page was opened on this computer, and the server's local_ip is
    // already the chosen adapter's.
    const adapter = {
      ...ANY, mode: "adapter", adapter_name: "Ethernet", label: "Ethernet only",
      addresses: ["10.20.30.5", "127.0.0.1"], network_addresses: ["10.20.30.5"],
    };
    const a = panelAccess(
      { ...LAN_STATUS, local_ip: "10.20.30.5", local_ips: ["10.20.30.5"], listen: adapter },
      null,
      { hostname: "localhost", origin: "http://localhost:8080" },
    );
    expect(a.panelUrl).toBe("http://10.20.30.5:8080/panel");
    expect(a.qrPanelUrl).toBe("http://10.20.30.5:8080/panel");
  });

  it("never publishes a page address the chosen adapter does not answer on", () => {
    const adapter = {
      ...ANY, mode: "adapter", adapter_name: "Ethernet", label: "Ethernet only",
      addresses: ["10.20.30.5", "127.0.0.1"], network_addresses: ["10.20.30.5"],
    };
    const a = panelAccess(
      { ...LAN_STATUS, local_ip: "10.20.30.5", listen: adapter },
      null,
      { hostname: "192.168.1.50", origin: "http://192.168.1.50:8080" },
    );
    expect(a.panelUrl).toBe("http://10.20.30.5:8080/panel");
  });

  it("drops the port when a port-80 listener is up", () => {
    const a = panelAccess({ ...LAN_STATUS, port80_active: true }, null, {
      hostname: "192.168.1.50",
      origin: "http://192.168.1.50",
    });
    expect(a.panelUrl).toBe("http://192.168.1.50/panel");
    expect(a.hostnameUrl).toBe("http://AVC-LOBBY.local/panel");
  });
});

describe("panelBase", () => {
  const facts = { httpPort: 8080, port80: false, tlsEnabled: false, tlsPort: 8443, redirectHttp: true };

  it("is the HTTP listener, with its port", () => {
    expect(panelBase("10.20.30.5", facts)).toBe("http://10.20.30.5:8080");
  });

  it("drops the port when port 80 is answered", () => {
    expect(panelBase("10.20.30.5", { ...facts, port80: true })).toBe("http://10.20.30.5");
  });

  it("is HTTPS only when nothing on HTTP redirects", () => {
    expect(panelBase("10.20.30.5", { ...facts, tlsEnabled: true })).toBe("http://10.20.30.5:8080");
    expect(panelBase("10.20.30.5", { ...facts, tlsEnabled: true, redirectHttp: false }))
      .toBe("https://10.20.30.5:8443");
  });
});
