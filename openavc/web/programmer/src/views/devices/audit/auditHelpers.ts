/**
 * Device audit wizard: the logic and the words, kept out of the components so
 * they can be tested without a browser.
 */
import type {
  AuditActivity,
  AuditActivityKey,
  AuditConflictDevice,
  AuditReport,
  AuditSessionState,
  AuditTimelineEntry,
} from "../../../api/auditClient";

export type AuditStep = "target" | "network" | "driver" | "report";

/** The steps this wizard has, in order, with their rail labels. */
export const AUDIT_STEPS: { key: AuditStep; label: string }[] = [
  { key: "target", label: "Device" },
  { key: "network", label: "Network check" },
  { key: "driver", label: "Driver" },
  { key: "report", label: "Report" },
];

/** What each network-check activity is called on screen, in order. */
export const ACTIVITY_LABELS: Record<AuditActivityKey, string> = {
  address: "Checking the address",
  ports: "Open ports",
  greetings: "What each port says",
  web: "Web pages and certificates",
  announcements: "Network announcements",
  snmp: "SNMP",
  probes: "Each driver's identification check",
};

export const ACTIVITY_ORDER: AuditActivityKey[] = [
  "address", "ports", "greetings", "web", "announcements", "snmp", "probes",
];

/** The step to show for a session: where a reopened wizard picks up. */
export function stepFor(session: AuditSessionState | null): AuditStep {
  if (!session) return "target";
  if (session.steps.includes("report")) return "report";
  if (session.steps.includes("driver")) return "driver";
  return "network";
}

/** True once the network check has been started (a report can be taken). */
export function checkStarted(session: AuditSessionState | null): boolean {
  const status = session?.check?.status;
  return !!status && status !== "idle";
}

/**
 * One WebSocket message applied to what the wizard holds. Messages for
 * another session are ignored. Returns new objects only when something
 * changed, so a store keeps its references otherwise.
 */
export function applyAuditMessage(
  session: AuditSessionState | null,
  timeline: AuditTimelineEntry[],
  msg: Record<string, unknown>,
): { session: AuditSessionState | null; timeline: AuditTimelineEntry[] } {
  if (!session || msg.session_id !== session.session_id) return { session, timeline };
  if (msg.type === "audit.state" && msg.state && typeof msg.state === "object") {
    return { session: msg.state as AuditSessionState, timeline };
  }
  if (msg.type === "audit.progress" && msg.activity && session.check) {
    const activity = msg.activity as AuditActivity;
    const activities = session.check.activities.map((a) =>
      a.key === activity.key ? activity : a,
    );
    return { session: { ...session, check: { ...session.check, activities } }, timeline };
  }
  if (msg.type === "audit.timeline" && msg.entry) {
    return { session, timeline: [...timeline, msg.entry as AuditTimelineEntry] };
  }
  return { session, timeline };
}

/** The sentence the first step shows about project devices at the address. */
export function pauseNotice(devices: AuditConflictDevice[]): string {
  if (devices.length === 0) return "";
  const names = devices.map((d) => d.device_name);
  const who =
    names.length === 1
      ? `${names[0]} in this project uses`
      : `${joinNames(names)} in this project use`;
  const it = names.length === 1 ? "it" : "them";
  return (
    `${who} this device. OpenAVC pauses ${it} while the audit runs and ` +
    `reconnects ${it} when you finish.`
  );
}

function joinNames(names: string[]): string {
  if (names.length <= 2) return names.join(" and ");
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

/** SNMP communities typed in the options, one per comma or line. */
export function parseCommunities(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(/[,\n]/)) {
    const value = part.trim();
    if (value && value !== "public" && !out.includes(value)) out.push(value);
  }
  return out;
}

/** "3 KB", "1.2 MB": a file size for the recent-reports list. */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** One line of the on-screen summary. */
export interface SummaryLine {
  label: string;
  value: string;
}

/**
 * The on-screen summary of a report, in the order the report reads: what the
 * device is, how it answered, what it announced. Lines with nothing to say
 * are left out.
 */
export function summaryLines(report: AuditReport): SummaryLine[] {
  const lines: SummaryLine[] = [];
  const reported = report.device.reported;
  const identity = [reported.manufacturer, reported.model].filter(Boolean).join(" ");
  if (identity) lines.push({ label: "Device", value: identity });
  if (reported.firmware) lines.push({ label: "Firmware", value: reported.firmware });
  if (reported.device_name) lines.push({ label: "Name", value: reported.device_name });

  const target = report.target;
  lines.push({
    label: "Address",
    value: target.ip && target.ip !== target.address ? `${target.address} (${target.ip})` : target.address,
  });
  const fp = report.footprint;
  if (fp.mac?.address) lines.push({ label: "MAC address", value: fp.mac.address });

  const ping = fp.ping?.result;
  if (ping) {
    lines.push({
      label: "Ping",
      value: ping === "alive" ? "Answers" : ping === "timeout" ? "No answer" : "Could not be sent",
    });
  }
  const ports = fp.ports;
  if (ports) {
    lines.push({
      label: "Open ports",
      value: ports.open.length > 0 ? ports.open.join(", ") : `None of the ${ports.checked} checked`,
    });
  }
  const pages = Object.entries(fp.web ?? {}).map(([port, page]) => {
    const bits = [page.status_line || page.error || "no answer"];
    if (page.title) bits.push(`"${page.title}"`);
    if (page.www_authenticate) bits.push("asks for sign-in");
    return `${port}: ${bits.join(", ")}`;
  });
  if (pages.length > 0) lines.push({ label: "Web pages", value: pages.join("; ") });

  const heard: string[] = [];
  if (fp.mdns && fp.mdns.services.length > 0) {
    heard.push(`mDNS (${fp.mdns.services.length} service${fp.mdns.services.length === 1 ? "" : "s"})`);
  }
  if (fp.ssdp) heard.push("SSDP");
  if (fp.amx_ddp) heard.push("AMX DDP");
  lines.push({ label: "Announcements", value: heard.length > 0 ? heard.join(", ") : "None heard" });

  if (fp.snmp) {
    const descr = fp.snmp.values?.sysDescr;
    lines.push({
      label: "SNMP",
      value: fp.snmp.answered ? descr || "Answers" : "No answer",
    });
  }
  return lines;
}

/** The driver names a verdict lists, strongest first, each once. */
export function verdictDrivers(
  drivers: Record<string, string[]>,
  names: Record<string, { name: string }>,
): { id: string; name: string; sources: string[] }[] {
  return Object.entries(drivers).map(([id, sources]) => ({
    id,
    name: names[id]?.name || id,
    sources,
  }));
}
