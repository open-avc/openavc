/** The rules that refuse Settings' Save before anything is sent. The server
 *  refuses the same saves; checking here puts the sentence beside the field
 *  before the click instead of a 400 after it. A new rule goes here, with the
 *  sentence its card shows from the result. */
import { passwordTooShortToSave } from "../../api/auth";
import type { SystemConfig } from "../../api/restClient";
import { REDACTED } from "./settingsControls";

/** The fields as on screen (saved, with unsaved changes over them), and the
 *  unsaved changes alone. */
export interface SaveCheckInput {
  tls: Partial<SystemConfig["tls"]>;
  httpPort: number;
  auth: SystemConfig["auth"];
  dirtyAuth: Partial<SystemConfig["auth"]> | undefined;
}

export interface SaveChecks {
  /** What is wrong with the HTTPS port, or null. */
  tlsPortError: string | null;
  /** Your own certificate is chosen with HTTPS on, and a file is missing. */
  tlsProvidedBlank: boolean;
  /** An API key with no Programmer password, which no browser can sign in with. */
  apiKeyNeedsPassword: boolean;
  /** A password typed in this edit is under the floor first-run setup applies. */
  passwordTooShort: boolean;
  /** Any of them refuses the save. */
  blocked: boolean;
}

/** The HTTPS port's problem. Its own port only matters while HTTPS is on,
 *  which the caller decides. */
function tlsPortProblem(port: number | undefined, httpPort: number): string | null {
  if (port === undefined) return null;
  if (port === httpPort) return "HTTPS port must differ from HTTP port";
  if (port < 1 || port > 65535) return "Port must be between 1 and 65535";
  return null;
}

export function saveChecks({ tls, httpPort, auth, dirtyAuth }: SaveCheckInput): SaveChecks {
  const tlsPortError = tlsPortProblem(tls.port, httpPort);
  // The cert mode is driven by tls.auto_generate (true => auto self-sign,
  // false => user-supplied paths), not by whether the path fields happen to
  // be populated.
  const tlsProvidedBlank =
    !tls.auto_generate && !!tls.enabled && (!tls.cert_file?.trim() || !tls.key_file?.trim());

  // An API key claims the system but is only ever accepted in the X-API-Key
  // header, which a browser can't attach to a page it is opening. So a key with
  // no password leaves the Programmer unreachable from every browser, with only
  // the filesystem to undo it. A stored value reads back as REDACTED, which
  // counts as set.
  const apiKeyNeedsPassword = !!auth.api_key && !auth.programmer_password;
  // Block the save only when this edit is what puts it there. A system already
  // in that state (its key came from OPENAVC_API_KEY, or from a hand-written
  // system.json) still has to be able to change its log level -- the server
  // draws the same line.
  const credentialEdited =
    !!dirtyAuth && ("api_key" in dirtyAuth || "programmer_password" in dirtyAuth);
  const apiKeySaveBlocked = apiKeyNeedsPassword && credentialEdited;
  // The floor first-run setup applies; the server refuses the same save with
  // the same sentence. Only a password typed in this edit: a stored one reads
  // back as REDACTED, and an empty field removes the password.
  const passwordTooShort =
    !!dirtyAuth &&
    "programmer_password" in dirtyAuth &&
    auth.programmer_password !== REDACTED &&
    passwordTooShortToSave(auth.programmer_password ?? "");

  const blocked =
    (!!tls.enabled && (tlsPortError !== null || tlsProvidedBlank)) || apiKeySaveBlocked || passwordTooShort;
  return { tlsPortError, tlsProvidedBlank, apiKeyNeedsPassword, passwordTooShort, blocked };
}
