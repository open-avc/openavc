import type { DiscoveryEvidence } from "../api/discoveryClient";
import { cleanBannerText, readsAsText } from "./discoveryViewHelpers";

// --- Evidence list ("Why?" reveal) ---
//
// Shared by the Discovery results and the device audit, so a signal reads
// the same wherever it is shown.
//
// Renders each evidence record using the user-facing phrasing below,
// dispatched on `data.kind` rather than the
// internal tier value. The raw `data.kind` strings (`mdns`, `ssdp`,
// `amx_ddp`, `broadcast`, `probe`, `oui`, `snmp_pen`, `hostname`,
// `open_port`, `vendor_string`) are stable per the API contract;
// the strings below are the natural-English versions of
// those.

/** ``driverName`` maps a driver id to its name, so a manufacturer a driver's
 *  probe supplied can say which driver: a line listed under several drivers
 *  would otherwise read "the driver" as each of them. */
export function describeEvidence(
  ev: DiscoveryEvidence,
  driverName?: (id: string) => string | undefined,
): { headline: string; detail: string | null } {
  const data = ev.data as Record<string, unknown>;
  const kind = typeof data.kind === "string" ? (data.kind as string) : null;
  const sourceId = typeof data.source_id === "string" ? (data.source_id as string) : null;

  const txtExcerpt = (txt: Record<string, unknown>): string =>
    Object.entries(txt)
      .slice(0, 4)
      .map(([k, v]) => `${k}=${typeof v === "string" ? v.slice(0, 60) : JSON.stringify(v).slice(0, 60)}`)
      .join(", ");

  switch (kind) {
    case "mdns": {
      const service = sourceId ?? "(unknown service)";
      const txt = data.txt && typeof data.txt === "object" ? (data.txt as Record<string, unknown>) : null;
      const instance = typeof data.instance === "string" ? (data.instance as string) : null;
      const parts: string[] = [];
      if (instance) parts.push(`instance ${instance}`);
      if (txt && Object.keys(txt).length > 0) parts.push(`TXT ${txtExcerpt(txt)}`);
      return {
        headline: `mDNS announcement on ${service}`,
        detail: parts.length > 0 ? parts.join("; ") : null,
      };
    }
    case "ssdp": {
      const urn = sourceId ?? "(unknown device type)";
      const fields: string[] = [];
      const names = { manufacturer: "manufacturer", model: "model", friendly_name: "name", server: "server" };
      for (const [f, name] of Object.entries(names)) {
        const v = data[f];
        if (typeof v === "string" && v) fields.push(`${name}: ${v}`);
      }
      return {
        headline: `SSDP announcement for ${urn}`,
        detail: fields.length > 0 ? fields.join("; ") : null,
      };
    }
    case "amx_ddp": {
      const named = [data.make, data.model].filter((v) => typeof v === "string" && v).join(" ");
      return { headline: named ? `AMX DDP beacon: ${named}` : "AMX DDP beacon", detail: null };
    }
    case "broadcast": {
      const port = typeof data.port === "number" ? (data.port as number) : null;
      const matchedPattern = typeof data.matched_pattern === "string"
        ? (data.matched_pattern as string) : null;
      const response = data.response && typeof data.response === "object"
        ? (data.response as Record<string, unknown>) : {};
      const ip = typeof response.ip === "string" ? (response.ip as string) : null;
      const txt = data.txt && typeof data.txt === "object" ? (data.txt as Record<string, unknown>) : null;
      const parts: string[] = [];
      if (ip) parts.push(`response from ${ip}`);
      if (txt && Object.keys(txt).length > 0) parts.push(txtExcerpt(txt));
      // The sentence: "UDP probe on port <port> matched <regex/hex pattern>"
      const headline = port !== null && matchedPattern
        ? `UDP probe on port ${port} matched ${matchedPattern}`
        : port !== null
          ? `UDP probe on port ${port} matched`
          : matchedPattern
            ? `UDP probe matched ${matchedPattern}`
            : "UDP probe matched";
      return { headline, detail: parts.length > 0 ? parts.join("; ") : null };
    }
    case "probe": {
      const port = typeof data.port === "number" ? (data.port as number) : null;
      const matchedPattern = typeof data.matched_pattern === "string"
        ? (data.matched_pattern as string) : null;
      const response = data.response && typeof data.response === "object"
        ? (data.response as Record<string, unknown>) : {};
      const text = typeof response.text === "string" ? (response.text as string) : null;
      const hex = typeof response.hex === "string" ? (response.hex as string) : "";
      const excerpt = text && readsAsText(text) ? cleanBannerText(text).text.slice(0, 80) || null : null;
      // An excerpt that quotes something itself is set off with single quotes.
      const quoted = excerpt?.includes('"') ? `'${excerpt}'` : `"${excerpt}"`;
      const portLabel = port !== null ? `on port ${port}` : null;
      // The sentences for active probes:
      //   "TCP probe on port <port> returned <response excerpt>"   (readable text)
      //   "TCP probe on port <port> matched <regex/hex pattern>"   (binary match)
      //   "TCP probe on port <port> answered"                       (connect-only)
      // Prefer the excerpt when the response decodes to readable
      // text; otherwise fall back to the matched pattern, then to
      // the connect-only "answered" form (companion verified the
      // listener but didn't supply a banner or a pattern).
      let head: string;
      if (excerpt) {
        head = portLabel
          ? `TCP probe ${portLabel} returned ${quoted}`
          : `TCP probe returned ${quoted}`;
      } else if (matchedPattern) {
        head = portLabel
          ? `TCP probe ${portLabel} matched ${matchedPattern}`
          : `TCP probe matched ${matchedPattern}`;
      } else if (hex) {
        const bytes = (hex.match(/../g) ?? []).slice(0, 40).join(" ");
        head = portLabel
          ? `TCP probe ${portLabel} returned hex ${bytes}`
          : `TCP probe returned hex ${bytes}`;
      } else {
        head = portLabel ? `TCP probe ${portLabel} answered` : "TCP probe answered";
      }
      return { headline: head, detail: null };
    }
    case "oui": {
      const prefix = typeof data.value === "string" ? (data.value as string) : "(unknown prefix)";
      const vendor = typeof data.vendor === "string" ? (data.vendor as string) : null;
      // A vendor is there only when a driver's oui: hint names the prefix;
      // without one the MAC was seen and matched nothing.
      return {
        headline: vendor
          ? `MAC address prefix ${prefix} belongs to ${vendor}`
          : `MAC address prefix ${prefix} seen`,
        detail: null,
      };
    }
    case "hostname": {
      const hostname = typeof data.value === "string" ? (data.value as string) : "(unknown hostname)";
      const matchedPattern = typeof data.matched_pattern === "string"
        ? (data.matched_pattern as string) : null;
      // The sentence: "Hostname pattern <regex> matched <hostname>"
      const headline = matchedPattern
        ? `Hostname pattern ${matchedPattern} matched ${hostname}`
        : `Hostname ${hostname} observed`;
      return { headline, detail: null };
    }
    case "snmp_pen": {
      const pen = typeof data.value === "number" || typeof data.value === "string"
        ? String(data.value) : "(unknown)";
      const sysdescr = typeof data.sysdescr === "string" ? (data.sysdescr as string) : null;
      return { headline: `SNMP enterprise number ${pen}`, detail: sysdescr };
    }
    case "vendor_string": {
      const value = typeof data.value === "string" ? (data.value as string) : "(unknown)";
      const raw = typeof data.raw === "string" && data.raw.toLowerCase() !== value
        ? (data.raw as string) : null;
      const from = typeof data.source_probe_id === "string" ? (data.source_probe_id as string) : "";
      const kind = typeof data.from_kind === "string" ? (data.from_kind as string) : "";
      const supplier = typeof data.supplied_by === "string" ? driverName?.(data.supplied_by) : undefined;
      const where = data.from_driver !== true
        ? vendorStringWhere(from, kind)
        : supplier
          ? `by the ${supplier} driver when its probe matched`
          : "by the driver when its probe matched";
      return {
        headline: `Manufacturer "${value}" named ${where}`,
        detail: raw ? `"${raw}"` : null,
      };
    }
    case "open_port": {
      const port = data.value;
      return { headline: `Port ${port} is open`, detail: null };
    }
    default:
      return { headline: ev.source || "(no signal)", detail: null };
  }
}

/** Where a manufacturer string came from, as a line reads it. The server keys
 *  free text as ``greeting:<port>``, ``http_server:<port>`` or ``ssdp_server``,
 *  and a manufacturer field by the kind of evidence that carried it (a probe's
 *  own id is internal and never shown). One the driver's probe supplies
 *  (``from_driver``) is said before this is asked. */
function vendorStringWhere(from: string, kind: string): string {
  const [what, port] = from.split(":");
  if (what === "greeting" && port) return `in the greeting on port ${port}`;
  if (what === "http_server" && port) return `by the web server on port ${port}`;
  if (from === "ssdp_server") return "in the SSDP server name";
  if (kind === "ssdp") return "in the SSDP description";
  if (kind === "mdns") return "in an mDNS announcement";
  if (kind === "amx_ddp") return "in the AMX DDP beacon";
  return "in a probe reply";
}

/**
 * The evidence, one line each. ``pointsAt`` (the device audit's) names the
 * drivers each signal points at, or returns null to say nothing for it;
 * ``driverName`` is passed to ``describeEvidence``.
 */
export function EvidenceList({
  evidence,
  pointsAt,
  driverName,
}: {
  evidence: DiscoveryEvidence[];
  pointsAt?: (ev: DiscoveryEvidence) => string[] | null;
  driverName?: (id: string) => string | undefined;
}) {
  if (evidence.length === 0) {
    return (
      <div style={{ marginTop: 4, fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
        No signals found.
      </div>
    );
  }
  return (
    <div style={{
      marginTop: 4, padding: "var(--space-sm)",
      background: "var(--bg-input)", borderRadius: "var(--radius)",
      fontSize: "var(--font-size-xs)", color: "var(--text-muted)",
    }}>
      {evidence.map((e, i) => {
        const { headline, detail } = describeEvidence(e, driverName);
        const drivers = pointsAt ? pointsAt(e) : null;
        return (
          <div key={i} style={{ marginBottom: 4 }}>
            <span style={{ color: "var(--text)" }}>{headline}</span>
            {detail && (
              <span style={{ marginLeft: 8, fontStyle: "italic" }}>{detail}</span>
            )}
            {drivers && (
              <div style={{ paddingLeft: "var(--space-md)", overflowWrap: "anywhere" }}>
                {drivers.length === 0
                  ? "No driver uses this signal."
                  : `Points at ${drivers.length === 1 ? "" : `${drivers.length} drivers: `}${drivers.join(", ")}`}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
