/** Settings > Security with your own certificate: the one the server runs,
 *  or picking or pasting a certificate and key to upload. The server writes
 *  the files and answers with their paths, which become the unsaved config, so
 *  the next Save points HTTPS at them. What was picked, pasted and uploaded
 *  lives in useCertificateUpload, held by the always-drawn Security card, so it
 *  survives switching the certificate source or HTTPS off and on. */
import { useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, FileCheck2, Upload } from "lucide-react";
import { EnvLock } from "../../components/system/EnvHeldNote";
import { btnStyle, fieldRow, helpText, inputStyle, labelStyle } from "../../components/system/settingsStyles";
import { showError, showSuccess } from "../../store/toastStore";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import type { SystemConfig, TlsCertInfo, TlsStatus, TlsUploadResult } from "../../api/restClient";
import { ExpiryBadge } from "./CertificateStatus";

/** Write one HTTPS setting into the unsaved config. */
export type UpdateTls = <K extends keyof SystemConfig["tls"]>(key: K, value: SystemConfig["tls"][K]) => void;

export function useCertificateUpload(updateTls: UpdateTls) {
  const [pickedCert, setPickedCert] = useState<File | null>(null);
  const [pickedKey, setPickedKey] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<TlsUploadResult | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteCert, setPasteCert] = useState("");
  const [pasteKey, setPasteKey] = useState("");
  const certInputRef = useRef<HTMLInputElement>(null);
  const keyInputRef = useRef<HTMLInputElement>(null);

  const upload = async (cert: File, key: File) => {
    setUploading(true);
    try {
      const result = await api.uploadTlsCert(cert, key);
      setUploadResult(result);
      // Auto-fill the path fields. Marks dirty so the save button enables.
      updateTls("cert_file", result.cert_path);
      updateTls("key_file", result.key_path);
      // Reset picker state.
      setPickedCert(null);
      setPickedKey(null);
      setPasteCert("");
      setPasteKey("");
      setPasteOpen(false);
      if (certInputRef.current) certInputRef.current.value = "";
      if (keyInputRef.current) keyInputRef.current.value = "";
      showSuccess(
        "Certificate uploaded. Save and restart to apply." +
          (result.warnings.includes("is-ca-cert")
            ? " Warning: this looks like a CA cert, not a server cert. Browsers may not trust it."
            : ""),
      );
    } catch (e) {
      showError(parseApiError(e));
    } finally {
      setUploading(false);
    }
  };

  const uploadPicked = () => {
    if (!pickedCert || !pickedKey) return;
    void upload(pickedCert, pickedKey);
  };

  const uploadPasted = () => {
    if (!pasteCert.trim() || !pasteKey.trim()) return;
    const certFile = new File([pasteCert], "cert.pem", { type: "application/x-pem-file" });
    const keyFile = new File([pasteKey], "key.pem", { type: "application/x-pem-file" });
    void upload(certFile, keyFile);
  };

  /** Back to the pickers, with the uploaded files' paths forgotten. */
  const replace = () => {
    setUploadResult(null);
    updateTls("cert_file", "");
    updateTls("key_file", "");
    setPickedCert(null);
    setPickedKey(null);
  };

  return {
    pickedCert, setPickedCert, pickedKey, setPickedKey, uploading, uploadResult,
    pasteOpen, setPasteOpen, pasteCert, setPasteCert, pasteKey, setPasteKey,
    certInputRef, keyInputRef, uploadPicked, uploadPasted, replace,
  };
}

export type CertificateUpload = ReturnType<typeof useCertificateUpload>;

export function YourOwnCertificate({ tls, tlsStatus, upload, providedBlank, held }: {
  tls: SystemConfig["tls"];
  tlsStatus: TlsStatus | null;
  upload: CertificateUpload;
  /** HTTPS is on and a file is missing: Save is refused. */
  providedBlank: boolean;
  /** The variable holding the certificate source, which locks all of this. */
  held: string | undefined;
}) {
  const { uploadResult, replace } = upload;
  return (
    <EnvLock held={held}>
      <ShownCertificate uploadResult={uploadResult} tlsStatus={tlsStatus} onReplace={replace} />

      {/* Hidden while an upload's result shows; Replace brings them back. */}
      {!uploadResult && !(tls.cert_file && tls.key_file) && <CertificatePickers upload={upload} />}

      {providedBlank && !uploadResult && (
        <div style={{ ...helpText, color: "rgb(244, 67, 54)", marginTop: "var(--space-sm)" }}>
          Upload a certificate before saving.
        </div>
      )}
    </EnvLock>
  );
}

/** There is a certificate to show: one just uploaded, or your own that the
 *  server runs. */
function hasShownCertificate(uploadResult: TlsUploadResult | null, tlsStatus: TlsStatus | null): boolean {
  if (uploadResult) return true;
  const runsYourOwn = tlsStatus?.enabled && tlsStatus.mode === "provided";
  return !!(runsYourOwn && tlsStatus.cert);
}

/** One fact about the shown certificate: the upload's, else the running one's. */
function certificateFact<K extends keyof TlsCertInfo>(
  key: K,
  uploadResult: TlsUploadResult | null,
  running: TlsCertInfo | null | undefined,
) {
  return uploadResult?.[key] ?? running?.[key];
}

/** The certificate just uploaded, else the one the server runs as your own;
 *  nothing while there is neither. */
function ShownCertificate({ uploadResult, tlsStatus, onReplace }: {
  uploadResult: TlsUploadResult | null;
  tlsStatus: TlsStatus | null;
  onReplace: () => void;
}) {
  if (!hasShownCertificate(uploadResult, tlsStatus)) return null;
  const fact = <K extends keyof TlsCertInfo>(key: K) => certificateFact(key, uploadResult, tlsStatus?.cert);
  return (
    <div style={{
      padding: "var(--space-md)",
      background: "var(--bg-elevated)",
      borderRadius: "var(--border-radius)",
      marginBottom: "var(--space-md)",
      border: "1px solid var(--border-color)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: "var(--space-xs)" }}>
        <FileCheck2 size={16} style={{ color: "rgb(76, 175, 80)" }} />
        <strong style={{ fontSize: "var(--font-size-sm)" }}>
          {uploadResult ? "Certificate uploaded" : "Active certificate"}
        </strong>
      </div>
      <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6 }}>
        <div>
          Valid until <strong>
            {new Date(fact("expires_at") ?? "").toLocaleDateString()}
          </strong>
          {" "}
          <ExpiryBadge days={fact("days_until_expiry") ?? 0} />
        </div>
        <div><strong>Subject:</strong> <code>{fact("subject")}</code></div>
        <div style={{ wordBreak: "break-all" }}>
          <strong>SHA-256:</strong> <code>{fact("fingerprint")}</code>
        </div>
        <div><strong>Valid for:</strong> {(fact("sans") ?? []).join(", ") || "—"}</div>
        {uploadResult?.warnings.includes("is-ca-cert") && (
          <div style={{ marginTop: "var(--space-xs)", color: "rgb(255, 152, 0)" }}>
            <AlertTriangle size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
            This looks like a CA certificate, not a server certificate. Most browsers won't trust it.
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onReplace}
        style={{
          ...btnStyle,
          background: "transparent",
          border: "1px solid var(--border-color)",
          color: "var(--text-primary)",
          marginTop: "var(--space-sm)",
        }}
      >
        Replace certificate
      </button>
    </div>
  );
}

/** Pick the certificate and key files and upload them, or paste them. */
function CertificatePickers({ upload }: { upload: CertificateUpload }) {
  const { pickedCert, setPickedCert, pickedKey, setPickedKey, uploading, certInputRef, keyInputRef, uploadPicked } = upload;
  const cannotUpload = !pickedCert || !pickedKey || uploading;
  return (
    <>
      <PickedFileField
        label="Certificate"
        accept=".pem,.crt,.cer,application/x-pem-file,application/x-x509-ca-cert"
        inputRef={certInputRef}
        picked={pickedCert}
        onPick={setPickedCert}
      />
      <PickedFileField
        label="Private key"
        accept=".pem,.key,application/x-pem-file"
        inputRef={keyInputRef}
        picked={pickedKey}
        onPick={setPickedKey}
        help={<>
          The private key matching the certificate above. PEM format, no passphrase.
          Files are written to the server's data directory; you don't need filesystem access.
        </>}
      />
      <div style={fieldRow}>
        <div />
        <button
          type="button"
          onClick={uploadPicked}
          disabled={cannotUpload}
          style={{
            ...btnStyle,
            opacity: cannotUpload ? 0.5 : 1,
            alignSelf: "flex-start",
          }}
        >
          <Upload size={14} />
          <span>{uploading ? "Uploading..." : "Upload certificate"}</span>
        </button>
      </div>

      <PastePem upload={upload} />
    </>
  );
}

/** One file to pick, and what was picked. */
function PickedFileField({ label, accept, inputRef, picked, onPick, help }: {
  label: string;
  accept: string;
  inputRef: React.RefObject<HTMLInputElement | null>;
  picked: File | null;
  onPick: (file: File | null) => void;
  help?: React.ReactNode;
}) {
  return (
    <div style={fieldRow}>
      <label style={labelStyle}>{label}</label>
      <div>
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
          style={{ fontSize: "var(--font-size-sm)" }}
        />
        {picked && (
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
            Selected: <code>{picked.name}</code> ({Math.ceil(picked.size / 100) / 10} KB)
          </div>
        )}
      </div>
      {help && <span style={helpText}>{help}</span>}
    </div>
  );
}

/** For a certificate that arrived as text in an email: paste both and use them. */
function PastePem({ upload }: { upload: CertificateUpload }) {
  const { pasteOpen, setPasteOpen, pasteCert, setPasteCert, pasteKey, setPasteKey, uploading, uploadPasted } = upload;
  const cannotUse = !pasteCert.trim() || !pasteKey.trim() || uploading;
  return (
    <div style={{ marginTop: "var(--space-md)" }}>
      <button
        type="button"
        onClick={() => setPasteOpen((v) => !v)}
        style={{
          background: "none",
          border: "none",
          color: "var(--text-secondary)",
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: 0,
          fontSize: "var(--font-size-sm)",
        }}
      >
        {pasteOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Or paste PEM contents</span>
      </button>
      {pasteOpen && (
        <div style={{ marginTop: "var(--space-sm)", display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <textarea
            placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
            value={pasteCert}
            onChange={(e) => setPasteCert(e.target.value)}
            rows={6}
            style={{ ...inputStyle, fontFamily: "monospace", fontSize: 12, resize: "vertical" }}
          />
          <textarea
            placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
            value={pasteKey}
            onChange={(e) => setPasteKey(e.target.value)}
            rows={6}
            style={{ ...inputStyle, fontFamily: "monospace", fontSize: 12, resize: "vertical" }}
          />
          <button
            type="button"
            onClick={uploadPasted}
            disabled={cannotUse}
            style={{
              ...btnStyle,
              opacity: cannotUse ? 0.5 : 1,
              alignSelf: "flex-start",
            }}
          >
            <Upload size={14} />
            <span>{uploading ? "Uploading..." : "Use pasted certificate"}</span>
          </button>
        </div>
      )}
    </div>
  );
}
