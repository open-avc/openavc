// Pure logic behind the Discovery view (no React imports) so the
// node-harness regression suite can exercise it directly.

// Generic protocol fallbacks for ports the backend baseline + driver
// catalog don't already cover. Vendor-specific labels (Samsung MDC,
// Crestron CIP, PJLink, etc.) come from `portLabels` in the discovery
// store — the backend builds that map from loaded drivers + community
// catalog so it stays in sync with the catalog without UI changes.
export const PORT_LABELS: Record<number, string> = {
  23: "Telnet",
  80: "HTTP",
  443: "HTTPS",
  5900: "VNC",
  8080: "HTTP alt",
  9090: "HTTP alt",
};

// Merge the generic PORT_LABELS fallbacks with the driver/catalog-supplied
// labels from the backend. The dynamic labels win: a driver that declares
// port 5900/9090 carries the vendor name, which must not be shadowed by the
// generic "VNC" / "HTTP alt" fallback.
export function mergePortLabels(dynamic: Record<number | string, string>): Record<number, string> {
  const merged: Record<number, string> = { ...PORT_LABELS };
  for (const [k, v] of Object.entries(dynamic)) {
    merged[Number(k)] = v;
  }
  return merged;
}

// The snmp_community field for a scan/save payload. The stored community is
// a credential the config endpoint never returns, so a blank input means
// "keep the stored value" and the field must be omitted entirely (undefined
// is dropped by JSON.stringify).
export function snmpCommunityField(input: string): string | undefined {
  return input.trim() === "" ? undefined : input;
}
