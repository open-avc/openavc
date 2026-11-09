/** Saving this server's CA certificate as openavc-ca.crt, for installing on
 *  panels and browsers: from Settings, and from the restart dialog after HTTPS
 *  is switched on. */
import * as api from "../../api/restClient";

/** Fetch the CA certificate and hand it to the browser to save. Throws when
 *  the server has none (HTTPS off, or your own certificate). The object URL
 *  is revoked at once, where downloadBlob waits a turn. */
export async function saveCaCertificate(): Promise<void> {
  const blob = await api.downloadCertificate();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "openavc-ca.crt";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
