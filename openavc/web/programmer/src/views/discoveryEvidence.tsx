import type { DiscoveryEvidence } from "../api/discoveryClient";
import { cleanBannerText } from "./discoveryViewHelpers";

// --- Evidence list ("Why?" reveal) ---
//
// Shared by the Discovery results and the device audit, so a signal reads
// the same wherever it is shown.
//
// Renders each evidence record using the user-facing phrasing from the
// Discovery spec §10 — dispatched on `data.kind` rather than the
// internal tier value. The raw `data.kind` strings (`mdns`, `ssdp`,
// `amx_ddp`, `broadcast`, `probe`, `oui`, `snmp_pen`, `hostname`,
// `open_port`, `vendor_string`) are stable per the API contract in
// spec §11; the strings below are the natural-English versions of
// those.

export function describeEvidence(ev: DiscoveryEvidence): { headline: string; detail: string | null } {
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
      for (const f of ["manufacturer", "model", "friendly_name", "server"] as const) {
        const v = data[f];
        if (typeof v === "string" && v) fields.push(`${f}: ${v}`);
      }
      return {
        headline: `SSDP NOTIFY for ${urn}`,
        detail: fields.length > 0 ? fields.join("; ") : null,
      };
    }
    case "amx_ddp": {
      const make = typeof data.make === "string" ? data.make : "?";
      const model = typeof data.model === "string" ? data.model : "?";
      return { headline: `AMX DDP beacon (make=${make}, model=${model})`, detail: null };
    }
    case "broadcast": {
      const probeId = sourceId ?? "(unknown probe)";
      const port = typeof data.port === "number" ? (data.port as number) : null;
      const matchedPattern = typeof data.matched_pattern === "string"
        ? (data.matched_pattern as string) : null;
      const response = data.response && typeof data.response === "object"
        ? (data.response as Record<string, unknown>) : {};
      const ip = typeof response.ip === "string" ? (response.ip as string) : null;
      const txt = data.txt && typeof data.txt === "object" ? (data.txt as Record<string, unknown>) : null;
      const parts: string[] = [`probe ${probeId}`];
      if (ip) parts.push(`response from ${ip}`);
      if (txt && Object.keys(txt).length > 0) parts.push(txtExcerpt(txt));
      // Spec §10 row: "UDP probe on port <port> matched <regex/hex pattern>"
      const headline = port !== null && matchedPattern
        ? `UDP probe on port ${port} matched ${matchedPattern}`
        : port !== null
          ? `UDP probe on port ${port} matched`
          : matchedPattern
            ? `UDP probe matched ${matchedPattern}`
            : "UDP probe matched";
      return { headline, detail: parts.join("; ") };
    }
    case "probe": {
      const probeId = sourceId ?? "(unknown probe)";
      const port = typeof data.port === "number" ? (data.port as number) : null;
      const matchedPattern = typeof data.matched_pattern === "string"
        ? (data.matched_pattern as string) : null;
      const response = data.response && typeof data.response === "object"
        ? (data.response as Record<string, unknown>) : {};
      const text = typeof response.text === "string" ? (response.text as string) : null;
      const excerpt = text ? cleanBannerText(text).text.slice(0, 80) || null : null;
      const portLabel = port !== null ? `on port ${port}` : null;
      // Spec §10 rows for active probes:
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
          ? `TCP probe ${portLabel} returned "${excerpt}"`
          : `TCP probe returned "${excerpt}"`;
      } else if (matchedPattern) {
        head = portLabel
          ? `TCP probe ${portLabel} matched ${matchedPattern}`
          : `TCP probe matched ${matchedPattern}`;
      } else {
        head = portLabel ? `TCP probe ${portLabel} answered` : "TCP probe answered";
      }
      return { headline: head, detail: `probe ${probeId}` };
    }
    case "oui": {
      const prefix = typeof data.value === "string" ? (data.value as string) : "(unknown prefix)";
      const vendor = typeof data.vendor === "string" ? (data.vendor as string) : null;
      return {
        headline: vendor
          ? `OUI lookup matched ${prefix} → ${vendor}`
          : `OUI lookup matched ${prefix}`,
        detail: null,
      };
    }
    case "hostname": {
      const hostname = typeof data.value === "string" ? (data.value as string) : "(unknown hostname)";
      const matchedPattern = typeof data.matched_pattern === "string"
        ? (data.matched_pattern as string) : null;
      // Spec §10 row: "Hostname pattern <regex> matched <hostname>"
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
      const raw = typeof data.raw === "string" && data.raw !== value ? (data.raw as string) : null;
      return {
        headline: `Manufacturer alias matched ${value}`,
        detail: raw ? `from probe response "${raw}"` : null,
      };
    }
    case "open_port": {
      const port = data.value;
      return { headline: `Port ${port} observed open`, detail: null };
    }
    default:
      return { headline: ev.source || "(no signal)", detail: null };
  }
}

export function EvidenceList({ evidence }: { evidence: DiscoveryEvidence[] }) {
  if (evidence.length === 0) {
    return (
      <div style={{ marginTop: 4, fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
        No evidence collected.
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
        const { headline, detail } = describeEvidence(e);
        return (
          <div key={i} style={{ marginBottom: 4 }}>
            <span style={{ color: "var(--text)" }}>{headline}</span>
            {detail && (
              <span style={{ marginLeft: 8, fontStyle: "italic" }}>{detail}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
