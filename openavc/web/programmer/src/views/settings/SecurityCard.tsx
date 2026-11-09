/** Settings > Security: the HTTPS switch, where its certificate comes from,
 *  its port and redirect, and the certificate it runs. Every change is a
 *  system.json change that needs a restart, which the page tracks. The card
 *  is always drawn, so it holds what the reader picked, pasted and opened in
 *  the parts below it that come and go with HTTPS and the certificate source. */
import { AlertTriangle } from "lucide-react";
import { EnvLock, HeldNote, heldSetting, type EnvHold } from "../../components/system/EnvHeldNote";
import { cardStyle, fieldRow, helpText, inputStyle, labelStyle, warningBox } from "../../components/system/settingsStyles";
import type { SystemConfig, TlsStatus } from "../../api/restClient";
import { FieldRow, NumberBox, SwitchRow } from "./settingsControls";
import { CertificateMadeHere, useCertificateMadeHere, type CertificateMadeHereView } from "./CertificateMadeHere";
import { CertificateStatus } from "./CertificateStatus";
import { YourOwnCertificate, useCertificateUpload, type CertificateUpload, type UpdateTls } from "./YourOwnCertificate";

type Tls = SystemConfig["tls"];

export function SecurityCard({ tls, savedTlsEnabled, httpPort, tlsStatus, updateTls, hold, tlsPortError, tlsProvidedBlank }: {
  /** HTTPS as on screen: saved, with unsaved changes over it. */
  tls: Tls;
  /** Whether the saved config has HTTPS on, which is what a restart applies. */
  savedTlsEnabled: boolean | undefined;
  httpPort: number;
  tlsStatus: TlsStatus | null;
  updateTls: UpdateTls;
  hold: EnvHold;
  tlsPortError: string | null;
  tlsProvidedBlank: boolean;
}) {
  const upload = useCertificateUpload(updateTls);
  const madeHere = useCertificateMadeHere();
  return (
    <div style={cardStyle}>
      <HttpsSwitch tls={tls} httpPort={httpPort} updateTls={updateTls} hold={hold} />
      <SchemeChangeWarning tls={tls} savedTlsEnabled={savedTlsEnabled} httpPort={httpPort} />
      {tls?.enabled && (
        <HttpsSettings
          tls={tls}
          httpPort={httpPort}
          tlsStatus={tlsStatus}
          updateTls={updateTls}
          hold={hold}
          tlsPortError={tlsPortError}
          tlsProvidedBlank={tlsProvidedBlank}
          upload={upload}
          madeHere={madeHere}
        />
      )}
    </div>
  );
}

/** The switch, and where the Programmer will be once it applies. */
function HttpsSwitch({ tls, httpPort, updateTls, hold }: { tls: Tls; httpPort: number; updateTls: UpdateTls; hold: EnvHold }) {
  return (
    <>
      <SwitchRow
        title="Enable HTTPS"
        help="Encrypt traffic between this server and panels / browsers. Off by default."
        held={heldSetting(hold, "tls", "enabled")}
        checked={!!tls?.enabled}
        onChange={(v) => updateTls("enabled", v)}
      />

      {/* Always shown: where the reader will reach the Programmer. */}
      <div style={{ ...helpText, gridColumn: "1 / -1", marginTop: 0, marginBottom: "var(--space-md)" }}>
        After restart, the Programmer IDE will be at{" "}
        <code>
          {tls?.enabled ? "https" : "http"}://&lt;server&gt;:
          {tls?.enabled ? tls.port : httpPort}/programmer
        </code>
      </div>
    </>
  );
}

/** Which way a saved change of HTTPS moves this page, judged from the scheme
 *  the page was opened over: "to-http", "to-https", or null. */
function schemeChange(tls: Tls, savedTlsEnabled: boolean | undefined): "to-http" | "to-https" | null {
  const pageIsHttps = typeof window !== "undefined" && window.location.protocol === "https:";
  if (!tls) return null;
  if (pageIsHttps && tls.enabled === false && savedTlsEnabled === true) return "to-http";
  if (!pageIsHttps && tls.enabled === true && savedTlsEnabled === false) return "to-https";
  return null;
}

/** A change of scheme moves this page: said before the restart that does it. */
function SchemeChangeWarning({ tls, savedTlsEnabled, httpPort }: { tls: Tls; savedTlsEnabled: boolean | undefined; httpPort: number }) {
  const change = schemeChange(tls, savedTlsEnabled);
  return (
    <>
      {change === "to-http" && (
        <div style={warningBox}>
          <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
          <span>
            You're disabling HTTPS while connected over <code>https://</code>. After restart, this page will be at{" "}
            <code>http://&lt;server&gt;:{httpPort}/programmer</code>. Update any bookmarks pointing to{" "}
            <code>https://</code>.
          </span>
        </div>
      )}
      {change === "to-https" && (
        <div style={warningBox}>
          <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
          <span>
            You're enabling HTTPS while connected over <code>http://</code>. After restart, this page will be at{" "}
            <code>https://&lt;server&gt;:{tls?.port ?? 8443}/programmer</code>. Your browser will show a warning
            until you install the CA certificate (button appears below after restart).
          </span>
        </div>
      )}
    </>
  );
}

/** Everything HTTPS needs once it is switched on. */
function HttpsSettings({ tls, httpPort, tlsStatus, updateTls, hold, tlsPortError, tlsProvidedBlank, upload, madeHere }: {
  tls: Tls;
  httpPort: number;
  tlsStatus: TlsStatus | null;
  updateTls: UpdateTls;
  hold: EnvHold;
  tlsPortError: string | null;
  tlsProvidedBlank: boolean;
  upload: CertificateUpload;
  madeHere: CertificateMadeHereView;
}) {
  // The cert mode is driven by tls.auto_generate (true => auto self-sign,
  // false => user-supplied paths), not by whether the path fields happen to
  // be populated.
  const tlsCertMode: "auto" | "provided" = tls?.auto_generate ? "auto" : "provided";
  const tlsCertHeld = hold.by("tls", "auto_generate") ?? hold.by("tls", "cert_file") ?? hold.by("tls", "key_file");
  return (
    <>
      <CertificateSourceField mode={tlsCertMode} held={tlsCertHeld} hold={hold} updateTls={updateTls} />

      {tlsCertMode === "auto" && <CertificateMadeHere tlsStatus={tlsStatus} view={madeHere} />}

      {tlsCertMode === "provided" && (
        <YourOwnCertificate tls={tls} tlsStatus={tlsStatus} upload={upload} providedBlank={tlsProvidedBlank} held={tlsCertHeld} />
      )}

      <HttpsPortField port={tls.port} error={tlsPortError} hold={hold} updateTls={updateTls} />

      <RedirectField checked={!!tls.redirect_http} httpPort={httpPort} hold={hold} updateTls={updateTls} />

      <CertificateStatus tlsStatus={tlsStatus} />

      {/* Panel-device install hint */}
      <div style={{ ...helpText, gridColumn: "1 / -1", marginTop: "var(--space-md)" }}>
        <strong>Panel apps:</strong> the OpenAVC Panel app detects HTTPS automatically via mDNS once both the
        server and the app are updated to a release with HTTPS support. To suppress the browser warning on each
        device, download the CA certificate (button above) and install it on the device.
      </div>
    </>
  );
}

/** Made here, or your own. Going back to one made here forgets your own
 *  certificate's file paths. */
function CertificateSourceField({ mode, held, hold, updateTls }: {
  mode: "auto" | "provided";
  held: string | undefined;
  hold: EnvHold;
  updateTls: UpdateTls;
}) {
  return (
    <div style={{ ...fieldRow, gridTemplateColumns: "200px 1fr" }}>
      <label style={labelStyle}>Certificate source</label>
      <EnvLock held={held} style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", cursor: "pointer" }}>
          <input
            type="radio"
            name="tls-cert-source"
            checked={mode === "auto"}
            onChange={() => {
              updateTls("auto_generate", true);
              updateTls("cert_file", "");
              updateTls("key_file", "");
            }}
          />
          <span>Auto-generate (recommended)</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", cursor: "pointer" }}>
          <input
            type="radio"
            name="tls-cert-source"
            checked={mode === "provided"}
            onChange={() => updateTls("auto_generate", false)}
          />
          <span>Use my own certificate</span>
        </label>
      </EnvLock>
      <HeldNote hold={hold} variable={held} style={{ gridColumn: "2" }} />
    </div>
  );
}

/** An emptied box reads as 8443; the page's Save rules judge the number. */
function HttpsPortField({ port, error, hold, updateTls }: {
  port: number;
  error: string | null;
  hold: EnvHold;
  updateTls: UpdateTls;
}) {
  return (
    <FieldRow
      label="HTTPS port"
      htmlFor="cfg-tls-port"
      held={heldSetting(hold, "tls", "port")}
      after={error && (
        <span style={{ ...helpText, color: "rgb(244, 67, 54)" }}>
          {error}
        </span>
      )}
    >
      <NumberBox
        id="cfg-tls-port"
        min={1}
        max={65535}
        style={{
          ...inputStyle,
          borderColor: error ? "rgb(244, 67, 54)" : (inputStyle.borderColor as string),
        }}
        value={port}
        fallback={8443}
        onChange={(n) => updateTls("port", n)}
      />
    </FieldRow>
  );
}

function RedirectField({ checked, httpPort, hold, updateTls }: {
  checked: boolean;
  httpPort: number;
  hold: EnvHold;
  updateTls: UpdateTls;
}) {
  return (
    <SwitchRow
      title="Redirect HTTP to HTTPS"
      help={<>
        Old links to <code>http://&lt;server&gt;:{httpPort}</code> redirect automatically. Turn off only
        if a reverse proxy in front of OpenAVC handles HTTP.
      </>}
      held={heldSetting(hold, "tls", "redirect_http")}
      checked={checked}
      onChange={(v) => updateTls("redirect_http", v)}
    />
  );
}
