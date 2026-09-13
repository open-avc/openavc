import type { TlsStatus } from "../api/restClient";
import { resolvableHostname } from "../api/hostnames";

/** Every address the Panel Access card can offer, and whether it may offer any.
 *
 *  Lifted out of the card so the rules below can be tested without mounting
 *  the Dashboard. The card renders what it is given and derives nothing.
 */
export interface PanelAccess {
  /** Reached over the cloud remote-UI tunnel. Every URL below is then "". */
  tunneled: boolean;
  /** The server is bound to loopback, so nothing on the network can reach it. */
  localOnly: boolean;
  /** The address to show first, certified when a trusted cert is active. */
  panelUrl: string;
  /** The shortest form worth typing, when it differs from panelUrl. */
  shortPanelUrl: string;
  /** mDNS name, for when the address changes. */
  hostnameUrl: string;
  /** What the QR code and the printed sign encode. */
  pairUrl: string;
  qrPanelUrl: string;
  /** panelUrl opens with no browser warning. */
  certified: boolean;
}

const EMPTY: Omit<PanelAccess, "tunneled" | "localOnly"> = {
  panelUrl: "",
  shortPanelUrl: "",
  hostnameUrl: "",
  pairUrl: "",
  qrPanelUrl: "",
  certified: false,
};

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

/** The addresses to publish for a panel, from the server's status and the page.
 *
 *  `loc` is the browser's own location, passed in rather than read, so a test
 *  can put the page anywhere.
 */
export function panelAccess(
  systemStatus: Record<string, unknown>,
  tlsStatus: TlsStatus | null,
  loc: { hostname: string; origin: string },
): PanelAccess {
  const tunneled = systemStatus.tunneled === true;
  const bindAddress = String(systemStatus.bind_address ?? "127.0.0.1");
  const localOnly = bindAddress === "127.0.0.1" || bindAddress === "::1";

  // A tunnelled reader is somewhere else on the internet, and every address
  // this function can build is one that only resolves on the controller's own
  // network. Publishing them there gives a reader URLs that cannot work, and
  // the first of them (the browser's origin without the tunnel's path) lands
  // on the cloud portal, which answers 200 and looks like it worked. So the
  // card is told to say so instead, and there is nothing here for it to print.
  if (tunneled || localOnly) {
    return { tunneled, localOnly, ...EMPTY };
  }

  const localIp = String(systemStatus.local_ip ?? "");
  const mdnsHost = resolvableHostname(String(systemStatus.hostname ?? ""));
  const httpPort = Number(systemStatus.http_port ?? 8080);
  const port80 = systemStatus.port80_active === true;

  const tlsEnabled = tlsStatus?.enabled === true;
  const tlsPort = Number(tlsStatus?.port ?? 8443);
  const redirectHttp = tlsStatus?.redirect_http !== false;
  const cloudCert = tlsStatus?.cloud_cert;
  const certSuffix = cloudCert?.active && cloudCert.hostname_suffix ? cloudCert.hostname_suffix : "";

  // The address the browser is actually using is ground truth. On multi-homed
  // machines (VPN, virtual/second adapters) the server's auto-detected local_ip
  // can be an interface panels can't reach, so prefer the current origin's IP
  // and only fall back to the detected IP on loopback (admin on the box itself)
  // or when the origin isn't an IPv4 (a hostname).
  const isLoopbackHost = loc.hostname === "localhost" || loc.hostname === "127.0.0.1"
    || loc.hostname === "::1" || loc.hostname === "[::1]";
  const lanIp = !isLoopbackHost && IPV4.test(loc.hostname) ? loc.hostname : localIp;

  // Short typed form: the HTTP listener (port-less when the port-80 listener is
  // up). With TLS on, the redirect listener upgrades it — to the certified name
  // when a trusted cert is active — so this is both the easiest URL to type and
  // the most durable one to encode in a QR/poster (it keeps landing right
  // through cert enroll/lapse/renewal). Only TLS-on-without-redirect has no
  // working http form.
  const shortBase = lanIp
    ? (tlsEnabled && !redirectHttp
      ? `https://${lanIp}:${tlsPort}`
      : `http://${lanIp}${port80 ? "" : `:${httpPort}`}`)
    : "";

  // Certified (green-lock) address: the browser-facing IPv4 dash-encoded under
  // the trusted cert's wildcard — same derivation as the Settings card.
  const certifiedBase = certSuffix && lanIp
    ? `https://${lanIp.split(".").join("-")}.${certSuffix}:${tlsPort}`
    : "";

  // Primary display URL: certified when active; otherwise the browser's own
  // origin when it's not loopback; otherwise scheme-and-port matched from the
  // detected IP (never glue the page scheme onto the HTTP port).
  const originBase = !isLoopbackHost
    ? loc.origin
    : (lanIp ? (tlsEnabled ? `https://${lanIp}:${tlsPort}` : `http://${lanIp}${port80 ? "" : `:${httpPort}`}`) : "");
  const primaryBase = certifiedBase || originBase;

  const panelUrl = primaryBase ? `${primaryBase}/panel` : "";
  const qrBase = shortBase || primaryBase;

  return {
    tunneled,
    localOnly,
    panelUrl,
    shortPanelUrl: shortBase && shortBase !== primaryBase ? `${shortBase}/panel` : "",
    // Name-based fallback that survives IP changes. ".local" resolves via mDNS
    // on phones and tablets (a bare machine name only resolves Windows-to-Windows).
    hostnameUrl: mdnsHost && mdnsHost !== localIp
      ? (tlsEnabled && !redirectHttp
        ? `https://${mdnsHost}:${tlsPort}/panel`
        : `http://${mdnsHost}${port80 ? "" : `:${httpPort}`}/panel`)
      : "",
    pairUrl: qrBase ? `${qrBase}/pair` : "",
    qrPanelUrl: qrBase ? `${qrBase}/panel` : "",
    certified: Boolean(certifiedBase && panelUrl),
  };
}
