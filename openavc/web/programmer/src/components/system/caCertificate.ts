/** Saving this server's CA certificate as openavc-ca.crt, for installing on
 *  panels and browsers: from Settings, and from the restart dialog after HTTPS
 *  is switched on. */
import * as api from "../../api/restClient";
import { downloadBlob } from "../../api/downloadFile";

/** Fetch the CA certificate and hand it to the browser to save. Throws when
 *  the server has none (HTTPS off, or your own certificate). */
export async function saveCaCertificate(): Promise<void> {
  downloadBlob(await api.downloadCertificate(), "openavc-ca.crt");
}
