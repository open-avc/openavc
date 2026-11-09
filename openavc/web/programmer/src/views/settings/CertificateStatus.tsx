/** What a certificate's facts look like on Settings: how long it has left,
 *  what is wrong with it in words, and the running certificate's details. */
import { Lock } from "lucide-react";
import { helpText, sectionTitle } from "../../components/system/settingsStyles";
import type { TlsStatus } from "../../api/restClient";

/** Days left, in green, amber under 30 days, red once expired. */
export function ExpiryBadge({ days }: { days: number }) {
  let bg = "rgba(76, 175, 80, 0.15)";
  let color = "rgb(76, 175, 80)";
  let label = `${days} days`;
  if (days < 0) {
    bg = "rgba(244, 67, 54, 0.15)";
    color = "rgb(244, 67, 54)";
    label = "Expired";
  } else if (days < 30) {
    bg = "rgba(255, 152, 0, 0.15)";
    color = "rgb(255, 152, 0)";
    label = `${days} days left`;
  }
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      background: bg,
      color,
    }}>
      {label}
    </span>
  );
}

/** A certificate warning code the server reports, in words. */
function warningLabel(w: string): string {
  switch (w) {
    case "expired": return "Certificate is expired. Generate a new one";
    case "expiring-soon": return "Certificate expires in under 30 days";
    case "hostname-mismatch": return "Certificate does not cover this server's current hostname/IP. Regenerate after IP change";
    default: return w;
  }
}

/** The certificate HTTPS is running with, and the error it last hit. Read
 *  only; filled in once HTTPS runs. */
export function CertificateStatus({ tlsStatus }: { tlsStatus: TlsStatus | null }) {
  return (
    <>
      {tlsStatus?.enabled && tlsStatus.cert && (
        <div style={{
          marginTop: "var(--space-md)",
          padding: "var(--space-md)",
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          fontSize: 12,
          lineHeight: 1.6,
        }}>
          <div style={{ ...sectionTitle, marginBottom: "var(--space-sm)" }}>Current certificate</div>
          <div><strong>Subject:</strong> <code>{tlsStatus.cert.subject}</code></div>
          <div><strong>Issuer:</strong> <code>{tlsStatus.cert.issuer}</code></div>
          <div><strong>SHA-256 fingerprint:</strong>{" "}
            <code style={{ wordBreak: "break-all" }}>{tlsStatus.cert.fingerprint}</code>
          </div>
          <div><strong>Valid for:</strong> {tlsStatus.cert.sans.join(", ")}</div>
          {tlsStatus.cert.warnings.length > 0 && (
            <div style={{ marginTop: "var(--space-sm)", color: "rgb(255, 152, 0)" }}>
              <Lock size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
              {tlsStatus.cert.warnings.map((w) => warningLabel(w)).join("; ")}
            </div>
          )}
        </div>
      )}
      {tlsStatus?.enabled && tlsStatus.error && (
        <div style={{ ...helpText, color: "rgb(244, 67, 54)", marginTop: "var(--space-sm)" }}>
          <Lock size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
          {tlsStatus.error}
        </div>
      )}
    </>
  );
}
