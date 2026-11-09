/** Settings' trusted certificate: a browser-trusted certificate the cloud
 *  issues for a paired system, served beside the one HTTPS makes here.
 *  Issuance runs over the cloud connection, so after asking for one the card
 *  watches tls-status until the request settles. The page draws the card
 *  always, so the watch lasts as long as Settings is open, whether or not the
 *  card is showing. */
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Copy, RefreshCw, ShieldCheck } from "lucide-react";
import { copyToClipboard } from "../../components/shared/clipboard";
import { HeldNote, type EnvHold } from "../../components/system/EnvHeldNote";
import { btnStyle, cardStyle, subCardDescription, subCardTitle, warningBox } from "../../components/system/settingsStyles";
import { showError, showSuccess } from "../../store/toastStore";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import type { CloudCertStatus, NetworkAdapter, SystemConfig, TlsStatus } from "../../api/restClient";

/** Friendly text for the typed error codes a failed certificate issuance
 *  reports through tls-status. Unknown codes fall back to the raw code. */
const CLOUD_CERT_ERROR_LABELS: Record<string, string> = {
  rate_limited: "Too many certificate requests today. An automatic retry is scheduled.",
  acme_failed: "The certificate authority could not complete issuance. An automatic retry is scheduled.",
  dns_failed: "Certificate validation failed at the DNS step. An automatic retry is scheduled.",
  not_available: "The cloud service does not offer trusted certificates for this system.",
  busy: "A certificate request is already in progress.",
  timeout: "No response from the cloud. An automatic retry is scheduled.",
  install_failed: "The issued certificate could not be installed on this system.",
  invalid_csr: "The cloud rejected the certificate request.",
  invalid_enrollment: "The cloud sent an unexpected enrollment response.",
  internal: "The cloud hit an internal error during issuance. An automatic retry is scheduled.",
};

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

/** The certified address encodes the IPv4 the browser is using (dash-for-dot)
 *  under the certificate's wildcard; a page opened by hostname uses the first
 *  adapter's IPv4. The fallback is that IPv4 itself, which gets the usual
 *  browser warning. */
export function certifiedAddresses(pageHost: string, adapters: NetworkAdapter[], suffix: string, port: number) {
  const ip = IPV4.test(pageHost) ? pageHost : adapters.find((a) => IPV4.test(a.ip))?.ip ?? null;
  return {
    certifiedUrl: suffix && ip ? `https://${ip.split(".").join("-")}.${suffix}:${port}/` : null,
    fallbackUrl: ip ? `https://${ip}:${port}/` : null,
  };
}

/** Where a requested certificate is, in the order the card says it. */
export type TrustedCertState = "requesting" | "waiting_for_https" | "serving" | "failed" | "waiting_for_cloud" | "preparing";

export function trustedCertState(cloudCert: CloudCertStatus, httpsRunning: boolean): TrustedCertState {
  if (cloudCert.phase !== "idle") return "requesting";
  if (!httpsRunning) return "waiting_for_https";
  if (cloudCert.active) return "serving";
  if (cloudCert.last_error) return "failed";
  if (!cloudCert.available) return "waiting_for_cloud";
  return "preparing";
}

/** What the page lends the card: the saved config's HTTPS switch, and the
 *  page's own reads of the config and tls-status, which the card refreshes. */
export interface TrustedCertificatePage {
  savedTlsEnabled: boolean | undefined;
  setConfig: (config: SystemConfig) => void;
  setTlsStatus: (status: TlsStatus) => void;
  loadTlsStatus: () => void;
  askForRestart: () => void;
}

function useTrustedCertificate(page: TrustedCertificatePage) {
  const { savedTlsEnabled, setConfig, setTlsStatus, loadTlsStatus, askForRestart } = page;
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  // Watch until the request settles back to idle (issued, or a typed error),
  // for at most two minutes.
  const stopWatching = () => {
    if (pollRef.current === null) return;
    window.clearInterval(pollRef.current);
    pollRef.current = null;
  };

  const watch = () => {
    if (pollRef.current !== null) return;
    let ticks = 0;
    pollRef.current = window.setInterval(async () => {
      ticks += 1;
      try {
        const fresh = await api.getTlsStatus();
        setTlsStatus(fresh);
        const settled = !fresh.cloud_cert || fresh.cloud_cert.phase === "idle";
        if (settled || ticks > 60) stopWatching();
      } catch {
        if (ticks > 60) stopWatching();
      }
    }, 2000);
  };

  useEffect(() => () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
  }, []);

  // HTTPS is off: turn it on and record the enrollment intent in one save.
  // After the restart the cloud connection sees the intent and installs the
  // certificate on its own.
  const turnOnHttpsFirst = async () => {
    await api.updateSystemConfig({
      tls: { enabled: true, cloud_cert: true },
    } as unknown as Partial<SystemConfig>);
    const fresh = await api.getSystemConfig();
    setConfig(fresh);
    loadTlsStatus();
    showSuccess(
      "HTTPS enabled. Restart to finish; the trusted certificate installs automatically after the restart."
    );
    askForRestart();
  };

  const request = async () => {
    const r = await api.enableCloudCert();
    if (r.started) {
      showSuccess("Requesting a trusted certificate...");
      loadTlsStatus();
      watch();
    } else {
      showSuccess(r.message || "Enrollment queued.");
      loadTlsStatus();
    }
  };

  const getTrustedCert = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await (savedTlsEnabled ? request() : turnOnHttpsFirst());
    } catch (e) {
      showError("Could not start enrollment: " + parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const turnOff = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.disableCloudCert();
      showSuccess("Trusted certificate turned off.");
      loadTlsStatus();
      const fresh = await api.getSystemConfig().catch(() => null);
      if (fresh) setConfig(fresh);
    } catch (e) {
      showError("Could not turn off the trusted certificate: " + parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return { busy, getTrustedCert, turnOff };
}

type TrustedCertificate = ReturnType<typeof useTrustedCertificate>;

/** Offered to a paired system, and kept on screen once asked for. */
function isOffered(cloudCert: CloudCertStatus | null | undefined): cloudCert is CloudCertStatus {
  if (!cloudCert) return false;
  return cloudCert.paired || cloudCert.enabled;
}

/** Paired and not yet asked for: drawn as a callout it must be impossible to miss. */
const CALLOUT = { border: "1px solid rgba(76, 175, 80, 0.45)", background: "rgba(76, 175, 80, 0.06)" };

/** A prominent callout while paired and not enrolled, the certificate's
 *  progress once enrolled, and nothing when there is no pairing and nothing
 *  enrolled. */
export function TrustedCertificateCard({ page, tlsStatus, tlsPort, adapters, hold }: {
  page: TrustedCertificatePage;
  tlsStatus: TlsStatus | null;
  /** The HTTPS port on screen, for an answer that does not say its own. */
  tlsPort: number | undefined;
  adapters: NetworkAdapter[];
  hold: EnvHold;
}) {
  const trusted = useTrustedCertificate(page);
  const cloudCert = tlsStatus?.cloud_cert;
  if (!isOffered(cloudCert)) return null;
  return (
    <div style={{ ...cardStyle, ...(cloudCert.paired && !cloudCert.enabled ? CALLOUT : {}) }}>
      <h4 style={subCardTitle}>
        <ShieldCheck
          size={16}
          style={{ verticalAlign: "text-bottom", marginRight: 6, color: "rgb(76, 175, 80)" }}
        />
        Trusted certificate
      </h4>

      {!cloudCert.enabled ? (
        <TrustedCertificateOffer savedTlsEnabled={page.savedTlsEnabled} trusted={trusted} hold={hold} />
      ) : (
        <TrustedCertificateProgress
          cloudCert={cloudCert}
          httpsRunning={!!tlsStatus?.enabled}
          port={tlsStatus?.port ?? tlsPort ?? 8443}
          adapters={adapters}
          trusted={trusted}
          hold={hold}
        />
      )}
    </div>
  );
}

/** Not asked for yet: what it is, and the button that asks (turning HTTPS on
 *  first when it is off). */
function TrustedCertificateOffer({ savedTlsEnabled, trusted, hold }: {
  savedTlsEnabled: boolean | undefined;
  trusted: TrustedCertificate;
  hold: EnvHold;
}) {
  // Getting a trusted certificate also turns HTTPS on, so a held HTTPS switch
  // locks it too while HTTPS is off.
  const held = hold.by("tls", "cloud_cert") ?? (savedTlsEnabled ? undefined : hold.by("tls", "enabled"));
  return (
    <>
      <div style={subCardDescription}>
        Serve a real, browser-trusted HTTPS certificate for this system, with no security
        warnings for anyone on this network, and nothing to install on devices. Included
        with your cloud pairing; the certificate issues and renews automatically.
      </div>
      {!savedTlsEnabled && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-md)" }}>
          HTTPS is currently off. This turns it on (one restart), then the certificate
          installs automatically.
        </div>
      )}
      <HeldNote hold={hold} variable={held} style={{ marginBottom: "var(--space-md)" }} />
      <button
        type="button"
        onClick={trusted.getTrustedCert}
        disabled={trusted.busy || !!held}
        style={{ ...btnStyle, opacity: trusted.busy || held ? 0.5 : 1 }}
      >
        <ShieldCheck size={14} />
        <span>
          {trusted.busy
            ? "Working..."
            : savedTlsEnabled
              ? "Get a trusted certificate"
              : "Turn on HTTPS & get a trusted certificate"}
        </span>
      </button>
    </>
  );
}

const quietLine = { fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-md)" };

/** The states that are one quiet sentence. */
const WAITING_LINES: Partial<Record<TrustedCertState, string>> = {
  waiting_for_https:
    "Waiting for HTTPS to start (restart pending). The trusted certificate will be served as soon as the HTTPS listener is running.",
  waiting_for_cloud:
    "Waiting for the cloud connection. The certificate installs automatically once this system is connected.",
  preparing: "Preparing the certificate...",
};

/** Asked for: where it is, and the button that turns it off. */
function TrustedCertificateProgress({ cloudCert, httpsRunning, port, adapters, trusted, hold }: {
  cloudCert: CloudCertStatus;
  httpsRunning: boolean;
  port: number;
  adapters: NetworkAdapter[];
  trusted: TrustedCertificate;
  hold: EnvHold;
}) {
  const state = trustedCertState(cloudCert, httpsRunning);
  const held = hold.by("tls", "cloud_cert");
  return (
    <>
      {state === "requesting" && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
          <RefreshCw size={14} style={{ color: "var(--text-muted)" }} />
          <span>Requesting a certificate from the cloud... this usually takes under a minute.</span>
        </div>
      )}
      {WAITING_LINES[state] && <div style={quietLine}>{WAITING_LINES[state]}</div>}
      {state === "serving" && <ServingLines cloudCert={cloudCert} port={port} adapters={adapters} />}
      {state === "failed" && <FailedLines cloudCert={cloudCert} trusted={trusted} />}

      <HeldNote hold={hold} variable={held} style={{ marginBottom: "var(--space-sm)" }} />
      <TurnOffButton trusted={trusted} held={held} />
    </>
  );
}

function TurnOffButton({ trusted, held }: { trusted: TrustedCertificate; held: string | undefined }) {
  const locked = trusted.busy || !!held;
  return (
    <button
      type="button"
      onClick={trusted.turnOff}
      disabled={locked}
      style={{
        ...btnStyle,
        background: "transparent",
        border: "1px solid var(--border-color)",
        color: "var(--text-primary)",
        opacity: locked ? 0.5 : 1,
      }}
    >
      Turn off trusted certificate
    </button>
  );
}

/** Served: for which names, the address that gets it, the fallback, and
 *  when it expires and renews. */
function ServingLines({ cloudCert, port, adapters }: { cloudCert: CloudCertStatus; port: number; adapters: NetworkAdapter[] }) {
  const pageHost = typeof window !== "undefined" ? window.location.hostname : "";
  const { certifiedUrl, fallbackUrl } = certifiedAddresses(pageHost, adapters, cloudCert.hostname_suffix, port);
  return (
    <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.7, marginBottom: "var(--space-md)" }}>
      <div style={{ color: "rgb(76, 175, 80)", marginBottom: "var(--space-xs)" }}>
        Serving a trusted certificate for <code>*.{cloudCert.hostname_suffix}</code>
      </div>
      {certifiedUrl && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexWrap: "wrap" }}>
          <span>Certified address:</span>
          <code style={{ wordBreak: "break-all" }}>{certifiedUrl}</code>
          <button
            type="button"
            title="Copy certified address"
            onClick={() => { copyToClipboard(certifiedUrl).then((ok) => { if (ok) showSuccess("Copied."); }); }}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-secondary)", padding: 2, display: "inline-flex" }}
          >
            <Copy size={12} />
          </button>
        </div>
      )}
      {fallbackUrl && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Manual fallback if a device can't resolve the certified address (no internet,
          or the router blocks it): <code>{fallbackUrl}</code> (shows the usual
          browser warning. <code>localhost</code> and bare-IP addresses always use the
          self-signed certificate; only the certified address gets the trusted one).
        </div>
      )}
      {cloudCert.expires_at && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Expires {new Date(cloudCert.expires_at).toLocaleDateString()}
          {cloudCert.renews_at && (
            <>, renews automatically around {new Date(cloudCert.renews_at).toLocaleDateString()}</>
          )}
          .
        </div>
      )}
    </div>
  );
}

/** The last issuance failed: why, in words, and asking again now. */
function FailedLines({ cloudCert, trusted }: { cloudCert: CloudCertStatus; trusted: TrustedCertificate }) {
  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <div style={warningBox}>
        <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
        <span>
          {CLOUD_CERT_ERROR_LABELS[cloudCert.last_error] ?? `Issuance failed (${cloudCert.last_error}).`}
          {cloudCert.last_error_detail && (
            <>
              {" "}
              <span style={{ color: "var(--text-muted)" }}>{cloudCert.last_error_detail}</span>
            </>
          )}
        </span>
      </div>
      <button
        type="button"
        onClick={trusted.getTrustedCert}
        disabled={trusted.busy}
        style={{ ...btnStyle, opacity: trusted.busy ? 0.5 : 1, marginTop: "var(--space-sm)" }}
      >
        <RefreshCw size={14} />
        <span>{trusted.busy ? "Working..." : "Try again now"}</span>
      </button>
    </div>
  );
}
