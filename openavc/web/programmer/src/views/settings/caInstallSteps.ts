/** How to install the CA certificate on each kind of device, and which one
 *  this browser runs on.
 *
 *  The steps are hand-written from current OS docs (Apple Platform Security
 *  Guide, Microsoft "Manage trusted root certificates", Google Chrome Help, and
 *  Mozilla support). Re-check them when a new major OS version ships. */

export type CaInstallOs =
  | "windows"
  | "macos"
  | "ios"
  | "android"
  | "linux-chrome"
  | "linux-firefox";

export const CA_INSTALL_OS_LABELS: Array<{ id: CaInstallOs; label: string }> = [
  { id: "windows", label: "Windows" },
  { id: "macos", label: "macOS" },
  { id: "ios", label: "iOS (iPhone / iPad)" },
  { id: "android", label: "Android" },
  { id: "linux-chrome", label: "Linux (Chrome / Chromium / Edge)" },
  { id: "linux-firefox", label: "Linux (Firefox)" },
];

export const CA_INSTALL_STEPS: Record<CaInstallOs, string[]> = {
  windows: [
    "Double-click the downloaded openavc-ca.crt file.",
    "Click \"Install Certificate…\" and choose \"Local Machine\" (or \"Current User\" if you can't elevate).",
    "Select \"Place all certificates in the following store\", click \"Browse…\", and pick \"Trusted Root Certification Authorities\".",
    "Finish the wizard. Restart any browsers that were already running.",
  ],
  macos: [
    "Double-click the downloaded openavc-ca.crt file to open it in Keychain Access.",
    "Pick the \"System\" keychain (or \"login\" if you only want to trust it for your own user) and add the certificate.",
    "In Keychain Access, find \"OpenAVC Root CA\", double-click it, expand \"Trust\", and set \"When using this certificate\" to \"Always Trust\".",
    "Close the window and authenticate. Restart Safari / Chrome to pick up the new trust setting.",
  ],
  ios: [
    "Email or AirDrop the openavc-ca.crt file to the device, then open it.",
    "iOS will say \"Profile Downloaded\". Open Settings → General → VPN & Device Management and tap the OpenAVC profile to install it.",
    "Open Settings → General → About → Certificate Trust Settings and enable full trust for the OpenAVC root.",
    "Reopen any browser tabs that were already showing a warning.",
  ],
  android: [
    "Copy the openavc-ca.crt file to the device (USB, Drive, or email).",
    "Open Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate → CA certificate.",
    "Acknowledge the warning, tap \"Install anyway\", browse to the file, and confirm.",
    "Some apps only trust user-installed CAs with explicit opt-in. Chrome works out of the box; the OpenAVC Panel app trusts the server automatically and does not need this step.",
  ],
  "linux-chrome": [
    "Open Settings → Privacy and security → Security → Manage certificates → \"Authorities\" tab.",
    "Click \"Import\", select openavc-ca.crt, and confirm.",
    "In the trust dialog, tick \"Trust this certificate for identifying websites\" and click OK.",
    "Restart Chrome / Chromium / Edge.",
  ],
  "linux-firefox": [
    "Open Settings → Privacy & Security, scroll to \"Certificates\", and click \"View Certificates…\".",
    "Switch to the \"Authorities\" tab and click \"Import…\".",
    "Pick openavc-ca.crt and tick \"Trust this CA to identify websites\".",
    "Close the dialog and reload any open OpenAVC tab.",
  ],
};

/** The system this browser runs on, as the install picker starts. */
export function detectOs(): CaInstallOs {
  if (typeof navigator === "undefined") return "windows";
  const ua = navigator.userAgent;
  if (/iPad|iPhone|iPod/i.test(ua)) return "ios";
  if (/Android/i.test(ua)) return "android";
  if (/Macintosh|Mac OS X/i.test(ua)) return "macos";
  if (/Linux/i.test(ua)) {
    if (/Firefox/i.test(ua)) return "linux-firefox";
    return "linux-chrome";
  }
  return "windows";
}
