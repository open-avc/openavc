/** Settings > Security with the certificate made here: checking its
 *  fingerprint, downloading the CA, and how to install it on each kind of
 *  device. What the reader chose (the device, the open steps, a copy just
 *  made) lives in useCertificateMadeHere, held by the always-drawn Security
 *  card, so it survives switching the certificate source or HTTPS off and on. */
import { useState } from "react";
import { ChevronDown, ChevronRight, Copy, Download, Smartphone } from "lucide-react";
import { copyToClipboard } from "../../components/shared/clipboard";
import { btnStyle, fieldRow, labelStyle, selectStyle, subCardTitle } from "../../components/system/settingsStyles";
import { showError, showSuccess } from "../../store/toastStore";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import type { TlsCertInfo, TlsStatus } from "../../api/restClient";
import { CA_INSTALL_OS_LABELS, CA_INSTALL_STEPS, detectOs, type CaInstallOs } from "./caInstallSteps";
import { ExpiryBadge } from "./CertificateStatus";

export function useCertificateMadeHere() {
  const [installOs, setInstallOs] = useState<CaInstallOs>(() => detectOs());
  const [installStepsOpen, setInstallStepsOpen] = useState(false);
  const [fingerprintCopied, setFingerprintCopied] = useState(false);

  const copyFingerprint = (fingerprint: string) => {
    copyToClipboard(fingerprint).then((copied) => {
      if (!copied) {
        showError("Couldn't copy to clipboard");
        return;
      }
      setFingerprintCopied(true);
      setTimeout(() => setFingerprintCopied(false), 1500);
    });
  };

  return { installOs, setInstallOs, installStepsOpen, setInstallStepsOpen, fingerprintCopied, copyFingerprint };
}

export type CertificateMadeHereView = ReturnType<typeof useCertificateMadeHere>;

async function downloadCaCertificate() {
  try {
    const blob = await api.downloadCertificate();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "openavc-ca.crt";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showSuccess("CA certificate downloaded. Install it on your panel devices to skip the security warning.");
  } catch (e) {
    showError("Could not download CA certificate: " + parseApiError(e));
  }
}

export function CertificateMadeHere({ tlsStatus, view }: { tlsStatus: TlsStatus | null; view: CertificateMadeHereView }) {
  // The server runs HTTPS on a certificate made here now; until a restart it
  // may still run your own.
  const servedHere = !!tlsStatus?.enabled && tlsStatus.mode === "auto";
  const cert = tlsStatus?.enabled ? tlsStatus.cert ?? null : null;
  return (
    <>
      {servedHere && <PanelAppTrustNote />}
      {cert && (
        <FingerprintCheck fingerprint={cert.fingerprint} copied={view.fingerprintCopied} onCopy={view.copyFingerprint} />
      )}
      <CertificateLine cert={cert} canDownload={servedHere} />
      {servedHere && <InstallSteps view={view} />}
    </>
  );
}

/** The Panel app trusts a certificate made here by itself; the steps below
 *  are for browsers and the iOS panel app. */
function PanelAppTrustNote() {
  return (
    <div style={{
      display: "flex",
      alignItems: "flex-start",
      gap: "var(--space-sm)",
      padding: "var(--space-md)",
      marginBottom: "var(--space-md)",
      background: "rgba(76, 175, 80, 0.08)",
      border: "1px solid rgba(76, 175, 80, 0.3)",
      borderRadius: "var(--border-radius)",
      fontSize: "var(--font-size-sm)",
      color: "var(--text-primary)",
    }}>
      <Smartphone size={16} style={{ color: "rgb(76, 175, 80)", flexShrink: 0, marginTop: 2 }} />
      <span>
        The OpenAVC Panel app (Android v0.1.0-rc6 or newer) trusts this server automatically, with no
        certificate install needed. The instructions below are for web browsers and the iOS panel app.
      </span>
    </div>
  );
}

/** The fingerprint a device shows when the CA is installed on it, to check
 *  the downloaded CA is the one this server serves. */
function FingerprintCheck({ fingerprint, copied, onCopy }: {
  fingerprint: string;
  copied: boolean;
  onCopy: (fingerprint: string) => void;
}) {
  return (
    <div style={{
      padding: "var(--space-md)",
      marginBottom: "var(--space-md)",
      background: "var(--bg-elevated)",
      border: "1px solid var(--border-color)",
      borderRadius: "var(--border-radius)",
    }}>
      <div style={{ ...subCardTitle, marginBottom: "var(--space-xs)" }}>Verify fingerprint</div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-sm)", lineHeight: 1.5 }}>
        When you install this certificate on a device, the device will show this same SHA-256
        fingerprint. They should match exactly.
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
        <code style={{
          fontFamily: "var(--font-mono, monospace)",
          fontSize: 12,
          padding: "var(--space-xs) var(--space-sm)",
          background: "var(--bg-surface)",
          border: "1px solid var(--border-color)",
          borderRadius: 4,
          wordBreak: "break-all",
          flex: 1,
          minWidth: 240,
        }}>
          {fingerprint}
        </code>
        <button
          type="button"
          onClick={() => onCopy(fingerprint)}
          style={{
            ...btnStyle,
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
            border: "1px solid var(--border-color)",
          }}
        >
          <Copy size={14} />
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
    </div>
  );
}

/** When the certificate expires, or that one is made at the next restart,
 *  and the CA download while one made here is served. */
function CertificateLine({ cert, canDownload }: { cert: TlsCertInfo | null; canDownload: boolean }) {
  return (
    <div style={fieldRow}>
      <label style={labelStyle}>Certificate</label>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
        {cert ? (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
            <span style={{ fontSize: "var(--font-size-sm)" }}>
              Valid until{" "}
              <strong>{new Date(cert.expires_at).toLocaleDateString()}</strong>
            </span>
            <ExpiryBadge days={cert.days_until_expiry} />
          </div>
        ) : (
          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            The self-signed certificate will be generated on the next restart.
          </span>
        )}
        {canDownload && (
          <button
            type="button"
            onClick={downloadCaCertificate}
            style={{
              ...btnStyle,
              background: "var(--bg-elevated)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-color)",
              alignSelf: "flex-start",
              marginTop: "var(--space-xs)",
            }}
          >
            <Download size={14} />
            <span>Download CA certificate</span>
          </button>
        )}
      </div>
    </div>
  );
}

/** Which kind of device to install the CA on, and its steps, folded away
 *  until asked for: it is not a one-click flow on any of them. */
function InstallSteps({ view }: { view: CertificateMadeHereView }) {
  const { installOs, setInstallOs, installStepsOpen, setInstallStepsOpen } = view;
  return (
    <div style={fieldRow}>
      <label style={labelStyle} htmlFor="ca-install-os">Install on</label>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
        <select
          id="ca-install-os"
          value={installOs}
          onChange={(e) => setInstallOs(e.target.value as CaInstallOs)}
          style={selectStyle}
        >
          {CA_INSTALL_OS_LABELS.map((opt) => (
            <option key={opt.id} value={opt.id}>{opt.label}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setInstallStepsOpen((v) => !v)}
          style={{
            ...btnStyle,
            background: "transparent",
            color: "var(--text-primary)",
            border: "1px solid var(--border-color)",
            justifyContent: "flex-start",
            marginTop: "var(--space-xs)",
          }}
        >
          {installStepsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>How to install</span>
        </button>
        {installStepsOpen && (
          <ol style={{
            marginTop: "var(--space-sm)",
            padding: "var(--space-md) var(--space-md) var(--space-md) var(--space-xl)",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-sm)",
            lineHeight: 1.6,
            color: "var(--text-primary)",
          }}>
            {CA_INSTALL_STEPS[installOs].map((step, i) => (
              <li key={i} style={{ marginBottom: "var(--space-xs)" }}>{step}</li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
