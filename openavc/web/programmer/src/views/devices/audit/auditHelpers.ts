/**
 * Device audit wizard: the logic and the words, kept out of the components so
 * they can be tested without a browser.
 */
import type {
  AuditActivity,
  AuditActivityKey,
  AuditCommandAnswer,
  AuditCommandInfo,
  AuditCommands,
  AuditCommandTrial,
  AuditConflictDevice,
  AuditDriverRun,
  AuditListen,
  AuditPreviewStage,
  AuditStatusVariable,
  AuditTrafficEntry,
  AuditReport,
  AuditReportDriver,
  AuditSessionState,
  AuditTimelineEntry,
} from "../../../api/auditClient";

export type AuditStep =
  | "target"
  | "network"
  | "driver"
  | "connection"
  | "listen"
  | "commands"
  | "report";

/** The steps this wizard has, in order, with their rail labels. */
export const AUDIT_STEPS: { key: AuditStep; label: string }[] = [
  { key: "target", label: "Device" },
  { key: "network", label: "Network check" },
  { key: "driver", label: "Driver" },
  { key: "connection", label: "Connection" },
  { key: "listen", label: "Connect and listen" },
  { key: "commands", label: "Commands" },
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
  if (session.steps.includes("commands")) return "commands";
  if (session.steps.includes("listen")) return "listen";
  if (session.steps.includes("connection")) return "connection";
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
  if (msg.type === "audit.listen" && typeof msg.run === "number" && msg.listen && session.runs) {
    const index = msg.run;
    if (!session.runs.some((r) => r.index === index)) return { session, timeline };
    const runs = session.runs.map((r) =>
      r.index === index ? { ...r, listen: msg.listen as AuditListen } : r,
    );
    return { session: { ...session, runs }, timeline };
  }
  if (msg.type === "audit.commands" && typeof msg.run === "number" && msg.commands && session.runs) {
    const index = msg.run;
    if (!session.runs.some((r) => r.index === index)) return { session, timeline };
    const update = msg.commands as Partial<AuditCommands>;
    const runs = session.runs.map((r) =>
      r.index === index ? { ...r, commands: mergeCommands(r.commands, update) } : r,
    );
    return { session: { ...session, runs }, timeline };
  }
  return { session, timeline };
}

/**
 * An ``audit.commands`` update applied to a run's commands: it carries the
 * trial that changed (merged by its number), the batch and what is current,
 * and the command list only when that changed.
 */
export function mergeCommands(
  before: AuditCommands | undefined,
  update: Partial<AuditCommands>,
): AuditCommands {
  const base: AuditCommands = before ?? { catalog: [], batch: null, current: null, trials: [] };
  let trials = base.trials;
  for (const t of update.trials ?? []) {
    const at = trials.findIndex((x) => x.number === t.number);
    trials = at >= 0 ? trials.map((x, i) => (i === at ? t : x)) : [...trials, t];
  }
  return {
    catalog: update.catalog ?? base.catalog,
    batch: update.batch !== undefined ? update.batch : base.batch,
    current: update.current !== undefined ? update.current : base.current,
    trials,
  };
}

/** The driver's status queries and its other commands, each in the driver's order. */
export function commandGroups(catalog: AuditCommandInfo[]): {
  queries: AuditCommandInfo[];
  commands: AuditCommandInfo[];
} {
  return {
    queries: catalog.filter((c) => c.query),
    commands: catalog.filter((c) => !c.query),
  };
}

/** How many status queries "Run all status queries" would send. */
export function batchableQueries(catalog: AuditCommandInfo[]): number {
  return catalog.filter((c) => c.query && !c.needs_input).length;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** One command's outcome in a sentence, as it stands now. Once its window
 *  has closed the server's sentence is the word (the timeline and the report
 *  say the same). */
export function trialOutcome(trial: AuditCommandTrial): string {
  if (trial.status === "sending") return "Sending.";
  if (trial.error) return `Not accepted: ${trial.error}`;
  if (trial.status === "done" && trial.summary) {
    return trial.summary.charAt(0).toUpperCase() + trial.summary.slice(1);
  }
  const { sent, received } = trial.traffic;
  const went = `Sent ${plural(sent, "message", "messages")}`;
  const back =
    received > 0
      ? `the device sent ${plural(received, "reply", "replies")}`
      : trial.status === "watching"
        ? "waiting for a reply"
        : "nothing came back";
  return `${went}; ${back}.`;
}

/** A command's parameters as a line reads them: "level 40, input hdmi1". */
export function paramsText(params: Record<string, unknown>): string {
  return Object.entries(params)
    .map(([k, v]) => `${k} ${String(v)}`)
    .join(", ");
}

/** The live traffic the wizard keeps (the report has all of it). */
export const LIVE_TRAFFIC_KEPT = 500;

/** Append an ``audit.traffic`` batch to what the wizard shows. */
export function appendTraffic(
  kept: AuditTrafficEntry[],
  msg: Record<string, unknown>,
  sessionId: string | null,
): AuditTrafficEntry[] {
  if (msg.type !== "audit.traffic" || msg.session_id !== sessionId) return kept;
  const entries = (msg.entries as AuditTrafficEntry[] | undefined) ?? [];
  if (entries.length === 0) return kept;
  const next = [...kept, ...entries];
  return next.length > LIVE_TRAFFIC_KEPT ? next.slice(next.length - LIVE_TRAFFIC_KEPT) : next;
}

/** Seconds left in the listening window, or null when not listening. */
export function secondsLeft(listen: AuditListen | undefined, now: number): number | null {
  if (!listen || !listen.ends_at) return null;
  if (listen.status !== "listening" && listen.status !== "not_connected") return null;
  return Math.max(0, Math.ceil(listen.ends_at - now));
}

/** A status value as the table shows it. */
export function statusValue(v: AuditStatusVariable): string {
  if (!v.reported || v.value === null || v.value === undefined) return "Not reported";
  if (typeof v.value === "boolean") return v.value ? "true" : "false";
  return String(v.value);
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

/** The current driver run: the last one. */
export function currentRun(session: AuditSessionState | null): AuditDriverRun | null {
  const runs = session?.runs ?? [];
  return runs.length > 0 ? runs[runs.length - 1] : null;
}

/** Bytes as a person reads them: the text with its control characters
 *  written out when it is text, otherwise the hex in pairs. */
export function displayBytes(text: string, hex: string): string {
  const printable = [...text].every((c) => {
    const code = c.charCodeAt(0);
    return (code >= 0x20 && code < 0x7f) || c === "\r" || c === "\n" || c === "\t";
  });
  if (printable && text.length > 0) {
    return `"${text.replace(/\r/g, "\\r").replace(/\n/g, "\\n").replace(/\t/g, "\\t")}"`;
  }
  return (hex.match(/.{1,2}/g) ?? []).join(" ");
}

/** What each preview stage is called on screen. */
export function previewStageLabel(
  stage: AuditPreviewStage,
  pollInterval: number,
  keepAliveInterval: number,
): string {
  if (stage === "sign_in") return "Sign in";
  if (stage === "start_up") return "Start-up steps";
  if (stage === "poll") {
    return pollInterval > 0 ? `Status polling, every ${seconds(pollInterval)}` : "Status polling";
  }
  return keepAliveInterval > 0
    ? `Keep-alive check, every ${seconds(keepAliveInterval)}`
    : "Keep-alive check";
}

function seconds(n: number): string {
  const rounded = Math.round(n * 10) / 10;
  return rounded === 1 ? "second" : `${rounded} seconds`;
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
  const entered = report.device.entered;
  // What the person said the device is, else what it reported.
  const identity =
    [entered?.manufacturer, entered?.model].filter(Boolean).join(" ") ||
    [reported.manufacturer, reported.model].filter(Boolean).join(" ");
  if (identity) lines.push({ label: "Device", value: identity });
  const firmware = entered?.firmware || reported.firmware;
  if (firmware) lines.push({ label: "Firmware", value: firmware });
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
  lines.push(...driverLines(report.drivers ?? []));
  return lines;
}

/** The summary's lines for each driver tested: which one, whether it
 *  connected, how many status values it reported, what it did not understand.
 *  With several drivers each line names its driver. */
export function driverLines(drivers: AuditReportDriver[]): SummaryLine[] {
  const lines: SummaryLine[] = [];
  const tested = drivers.filter((d) => d.attempts.length > 0);
  const several = tested.length > 1;
  tested.forEach((d, i) => {
    const suffix = several ? ` (${i + 1})` : "";
    const name = [d.driver.name, d.driver.version].filter(Boolean).join(" ");
    lines.push({
      label: `Driver${suffix}`,
      value: d.driver.modified ? `${name}, a modified copy` : name,
    });
    const last = d.attempts[d.attempts.length - 1];
    let connected: string;
    if (last.connected_at) {
      connected = `Yes, ${(last.connected_at - last.started_at).toFixed(1)} s after starting`;
    } else {
      const reason = last.offline?.detail || last.offline?.code || last.error;
      connected = reason ? `No: ${reason}` : "No";
    }
    lines.push({ label: `Connected${suffix}`, value: connected });
    lines.push({
      label: `Status values${suffix}`,
      value: `${last.reported} of ${last.declared} reported`,
    });
    if (last.traffic.not_captured) {
      lines.push({
        label: `Traffic${suffix}`,
        value: "Not captured: the driver manages its own connection",
      });
    }
    const unmatched = last.contract.counts.unmatched_response ?? 0;
    if (unmatched > 0) {
      lines.push({
        label: `Replies not understood${suffix}`,
        value: `${unmatched} matched none of the driver's rules`,
      });
    }
    const trials = d.commands?.trials ?? [];
    if (trials.length > 0) {
      const refused = trials.filter((t) => t.error).length;
      lines.push({
        label: `Commands sent${suffix}`,
        value:
          refused > 0
            ? `${trials.length}, ${refused} not accepted`
            : String(trials.length),
      });
      const silent = [...new Set(trials.filter((t) => t.sent_nothing).map((t) => t.label))];
      if (silent.length > 0) {
        lines.push({
          label: `Sent nothing${suffix}`,
          value: `${silent.join(", ")}: the driver said it succeeded, but nothing was sent`,
        });
      }
      const answered = answerCounts(trials);
      if (answered) lines.push({ label: `Did the device do it${suffix}`, value: answered });
      for (const t of trials) {
        const r = t.restart;
        if (!r || r.back_after === null) continue;
        lines.push({
          label: `Restart${suffix}`,
          value:
            `${t.label}: back ${r.back_after} s after the command ` +
            `(the driver declares ${r.declared_seconds} s)`,
        });
      }
    }
  });
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

/** What pressing Send on this command should say first, or "" to send at
 *  once: the driver's own confirmation, and a restart warning. */
export function sendWarning(command: AuditCommandInfo): string {
  const parts: string[] = [];
  if (command.confirm) parts.push(command.confirm);
  if (command.restarts_device_for > 0) {
    parts.push(
      `This command restarts the device: the driver says it is off the network for up to ` +
        `${command.restarts_device_for} seconds. The audit times how long it takes to come back.`,
    );
  }
  return parts.join(" ");
}

/** A status value change as a line reads it: "power: false to true". */
export function changeText(change: { key: string; old: unknown; new: unknown }): string {
  const show = (v: unknown) => (v === null || v === undefined ? "nothing" : String(v));
  return `${change.key}: ${show(change.old)} to ${show(change.new)}`;
}

/** The buttons of "Did the device do it?", in order. */
export const ANSWER_CHOICES: { key: AuditCommandAnswer; label: string }[] = [
  { key: "yes", label: "Yes" },
  { key: "no", label: "No" },
  { key: "partly", label: "Partly" },
  { key: "cant_tell", label: "Can't tell from here" },
];

/** The person's answers across the commands: "2 yes, 1 no", or "" when
 *  none was given. */
export function answerCounts(trials: AuditCommandTrial[]): string {
  const words: Record<AuditCommandAnswer, string> = {
    yes: "yes", no: "no", partly: "partly", cant_tell: "could not tell",
  };
  const counts = new Map<AuditCommandAnswer, number>();
  for (const t of trials) {
    if (t.answer) counts.set(t.answer.answer, (counts.get(t.answer.answer) ?? 0) + 1);
  }
  return ANSWER_CHOICES.filter((c) => counts.has(c.key))
    .map((c) => `${counts.get(c.key)} ${words[c.key]}`)
    .join(", ");
}
