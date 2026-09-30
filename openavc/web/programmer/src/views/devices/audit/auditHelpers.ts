/**
 * Device audit wizard: the logic and the words, kept out of the components so
 * they can be tested without a browser.
 */
import type {
  AuditActivity,
  AuditActivityKey,
  AuditChangedValue,
  AuditCommandAnswer,
  AuditCommandInfo,
  AuditCommands,
  AuditCommandTrial,
  AuditConflictDevice,
  AuditDriverRun,
  AuditListen,
  AuditMovedValue,
  AuditOutage,
  AuditPreviewStage,
  AuditStatusVariable,
  AuditTrafficEntry,
  AuditReport,
  AuditReportDriver,
  AuditSessionState,
  AuditSettingInfo,
  AuditSettings,
  AuditSettingTrial,
  AuditTimelineEntry,
  AuditVerdict,
} from "../../../api/auditClient";
import type { DiscoveryEvidence } from "../../../api/discoveryClient";

export type AuditStep =
  | "target"
  | "network"
  | "driver"
  | "connection"
  | "listen"
  | "commands"
  | "outage"
  | "report";

/** The steps this wizard has, in order, with their rail labels. */
export const AUDIT_STEPS: { key: AuditStep; label: string }[] = [
  { key: "target", label: "Device" },
  { key: "network", label: "Network check" },
  { key: "driver", label: "Driver" },
  { key: "connection", label: "Connection" },
  { key: "listen", label: "Connect and listen" },
  { key: "commands", label: "Commands" },
  { key: "outage", label: "Power and cable" },
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
  if (session.steps.includes("outage")) return "outage";
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
  if (msg.type === "audit.outage" && typeof msg.run === "number" && msg.outage && session.runs) {
    const index = msg.run;
    if (!session.runs.some((r) => r.index === index)) return { session, timeline };
    const outage = msg.outage as AuditOutage;
    const runs = session.runs.map((r) => {
      if (r.index !== index) return r;
      const kept = r.outages ?? [];
      const at = kept.findIndex((o) => o.number === outage.number);
      return {
        ...r,
        outages: at >= 0 ? kept.map((o, i) => (i === at ? outage : o)) : [...kept, outage],
      };
    });
    return { session: { ...session, runs }, timeline };
  }
  if (msg.type === "audit.settings" && typeof msg.run === "number" && msg.settings && session.runs) {
    const index = msg.run;
    if (!session.runs.some((r) => r.index === index)) return { session, timeline };
    const update = msg.settings as Partial<AuditSettings>;
    const runs = session.runs.map((r) =>
      r.index === index ? { ...r, settings: mergeSettings(r.settings, update) } : r,
    );
    return { session: { ...session, runs }, timeline };
  }
  return { session, timeline };
}

/** An ``audit.settings`` update applied to a run's settings: the setting
 *  trial that changed (merged by its number) and the list as it stands. */
export function mergeSettings(
  before: AuditSettings | undefined,
  update: Partial<AuditSettings>,
): AuditSettings {
  const base: AuditSettings = before ?? { catalog: [], current: null, trials: [] };
  let trials = base.trials;
  for (const t of update.trials ?? []) {
    const at = trials.findIndex((x) => x.number === t.number);
    trials = at >= 0 ? trials.map((x, i) => (i === at ? t : x)) : [...trials, t];
  }
  return {
    catalog: update.catalog ?? base.catalog,
    current: update.current !== undefined ? update.current : base.current,
    trials,
  };
}

/** The setting's last write, if it still needs putting back ("Put it back"). */
export function needsPuttingBack(trial: AuditSettingTrial | undefined): boolean {
  if (!trial || trial.status !== "done") return false;
  const wrote = "at" in trial.write && !trial.write.error;
  return wrote && !trial.restore?.confirmed;
}

/**
 * An ``audit.commands`` update applied to a run's commands: it carries the
 * trial that changed (merged by its number), the batch and what is current,
 * and the command list and the picker values only when those changed.
 */
export function mergeCommands(
  before: AuditCommands | undefined,
  update: Partial<AuditCommands>,
): AuditCommands {
  const base: AuditCommands = before ?? {
    catalog: [], picker_state: {}, batch: null, current: null, trials: [], changed: [],
  };
  let trials = base.trials;
  for (const t of update.trials ?? []) {
    const at = trials.findIndex((x) => x.number === t.number);
    trials = at >= 0 ? trials.map((x, i) => (i === at ? t : x)) : [...trials, t];
  }
  return {
    catalog: update.catalog ?? base.catalog,
    picker_state: update.picker_state ?? base.picker_state,
    batch: update.batch !== undefined ? update.batch : base.batch,
    current: update.current !== undefined ? update.current : base.current,
    trials,
    changed: update.changed ?? base.changed,
  };
}

/** A changed value as a line reads it: "Input: not reported before, hdmi2
 *  now (after 3. Set Input)". */
export function changedText(item: AuditChangedValue): string {
  const show = (v: unknown) =>
    v === null || v === undefined ? "not reported" : typeof v === "boolean" ? (v ? "true" : "false") : String(v);
  const by = item.by ? ` (after ${item.by.number}. ${item.by.label})` : "";
  return `${item.label}: ${show(item.before)} before, ${show(item.now)} now${by}`;
}

/** Every command in one list: the driver's key ones first (the ones it puts
 *  on a device page, as the server marks them ``suggested``), then the rest,
 *  each part in the driver's order. */
export function commandOrder(catalog: AuditCommandInfo[]): AuditCommandInfo[] {
  return [...catalog.filter((c) => c.suggested), ...catalog.filter((c) => !c.suggested)];
}

/** Has this command been sent yet? The "Not tried yet" filter and the count
 *  read the same rule. */
export function notTried(command: AuditCommandInfo, trials: AuditCommandTrial[]): boolean {
  return !trials.some((t) => t.command === command.name);
}

/** A command matches a search by its label, its name or its help. */
export function commandMatches(command: AuditCommandInfo, search: string): boolean {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  return [command.label, command.name, command.help].some((s) => s.toLowerCase().includes(q));
}

/** How many status queries "Run all status queries" would send. */
export function batchableQueries(catalog: AuditCommandInfo[]): number {
  return catalog.filter((c) => c.query && !c.needs_input).length;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export type CommandStatusKey =
  | "not_tried"
  | "watching"
  | "not_accepted"
  | "batch"
  | "waiting"
  | AuditCommandAnswer;

/** Where one command stands, from its tries (oldest first): its last try
 *  decides, and the person's answer is the word once there is one. */
export function commandStatus(trials: AuditCommandTrial[]): { key: CommandStatusKey; text: string } {
  const last = trials[trials.length - 1];
  if (!last) return { key: "not_tried", text: "Not tried" };
  if (last.status !== "done") return { key: "watching", text: "Watching" };
  if (last.error) return { key: "not_accepted", text: "Not accepted" };
  if (last.batch) return { key: "batch", text: "Sent with the status queries" };
  if (!last.answer) return { key: "waiting", text: "Waiting for your answer" };
  const words: Record<AuditCommandAnswer, string> = {
    yes: "Worked", no: "Did not work", partly: "Partly worked", cant_tell: "Could not tell",
  };
  return { key: last.answer.answer, text: words[last.answer.answer] };
}

/** How far through the commands the person is: "3 of 58 commands tried,
 *  2 answered · Key: 2 of 4 tried". A command counts as answered when its
 *  last try has an answer; the key part is there when the driver has key ones. */
export function commandProgress(catalog: AuditCommandInfo[], trials: AuditCommandTrial[]): string {
  const n = catalog.length;
  if (n === 0) return "";
  const names = new Set(catalog.map((c) => c.name));
  const last = new Map<string, AuditCommandTrial>();
  for (const t of trials) if (names.has(t.command)) last.set(t.command, t);
  const answered = [...last.values()].filter((t) => t.answer).length;
  const all =
    last.size === 0
      ? `${plural(n, "command", "commands")}, none tried yet`
      : `${last.size} of ${plural(n, "command", "commands")} tried, ${answered} answered`;
  const key = catalog.filter((c) => c.suggested);
  if (key.length === 0) return all;
  const keyTried = key.filter((c) => last.has(c.name)).length;
  return `${all} · Key: ${keyTried} of ${key.length} tried`;
}

/** The device setting to try first: a name-like one (a string) if the
 *  audit can write one, else the first it can write; null when it can write none. */
export function suggestedSetting(catalog: AuditSettingInfo[]): string | null {
  const writable = catalog.filter((s) => s.can_write);
  const text = writable.find((s) => (s.definition.type ?? "string") === "string");
  return (text ?? writable[0])?.key ?? null;
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
    const outageNames: Record<string, string> = { power_cycle: "Power cycle", cable_pull: "Cable pull" };
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
    const written = d.settings?.trials ?? [];
    if (written.length > 0) {
      const readBack = written.filter((t) => "confirmed" in t.write && t.write.confirmed).length;
      const back = written.filter((t) => t.restore?.confirmed).length;
      lines.push({
        label: `Settings written${suffix}`,
        value: `${written.length}: ${readBack} read back, ${back} put back`,
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
      const changed = (d.commands?.changed ?? []).filter((c) => !c.on_its_own);
      if (changed.length > 0) {
        lines.push({
          label: `Values changed${suffix}`,
          value: changed.map((c) => c.label).join(", "),
        });
      }
      const answered = answerCounts(trials);
      if (answered) lines.push({ label: `Did it happen${suffix}`, value: answered });
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
    for (const o of d.outages ?? []) {
      if (!o.summary) continue;
      lines.push({ label: `${outageNames[o.kind] ?? o.kind}${suffix}`, value: o.summary });
    }
  });
  return lines;
}

/** The drivers a verdict lists, in the server's order (the one it matched,
 *  then its candidates, then every other driver a signal points at), each
 *  with the signals that point at it. */
export function verdictDrivers(
  verdict: Pick<AuditVerdict, "drivers" | "explanation">,
): { id: string; name: string; signals: DiscoveryEvidence[] }[] {
  const pointed = new Set(Object.keys(verdict.explanation.drivers));
  return Object.entries(verdict.drivers)
    .filter(([id]) => pointed.has(id))
    .map(([id, d]) => ({
      id,
      name: d.name || id,
      signals: verdict.explanation.signals.filter((s) => s.drivers.includes(id)).map((s) => s.evidence),
    }));
}

/** The names of the drivers one evidence record points at, as the verdict's
 *  explanation lists them; null for a record the explanation does not list. */
export function signalDrivers(
  verdict: Pick<AuditVerdict, "drivers" | "explanation">,
  evidence: DiscoveryEvidence,
): string[] | null {
  const hit = verdict.explanation.signals.find((s) => s.evidence.source === evidence.source);
  if (!hit) return null;
  return hit.drivers.map((id) => verdict.drivers[id]?.name || id);
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

/** How many of the values a command moved its sentence names (the server's
 *  ``MOVED_IN_SENTENCE`` in openavc/audit/commands.py); past that the step
 *  lists them all. */
export const MOVED_IN_SENTENCE = 3;

/** What a command's window saw, split: the values it moved, and the ones
 *  that were already changing before it was sent (a meter, a clock). */
export function movedParts(trial: AuditCommandTrial): {
  moved: AuditMovedValue[];
  moving: AuditMovedValue[];
  wentBack: AuditMovedValue[];
} {
  const all = trial.moved ?? [];
  return {
    moved: all.filter((m) => !m.already_moving && !m.went_back),
    moving: all.filter((m) => m.already_moving),
    wentBack: all.filter((m) => !m.already_moving && m.went_back),
  };
}

/** A value a command moved, as a line reads it: "Mute: false to true". */
export function movedText(m: AuditMovedValue): string {
  const show = (v: unknown) =>
    v === null || v === undefined ? "not reported" : typeof v === "boolean" ? (v ? "true" : "false") : String(v);
  return `${m.label}: ${show(m.first)} to ${show(m.last)}`;
}

/** A value that kept changing, as a line reads it: "AC Line Voltage (7 times)". */
export function movingText(m: AuditMovedValue): string {
  return m.times > 1 ? `${m.label} (${m.times} times)` : m.label;
}

/** The buttons of "Did it happen?", in order. */
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

/** The seconds from `from` to `to` (or now), one decimal, or null. */
export function secondsBetween(from: number | null, to: number | null): number | null {
  if (from === null || to === null) return null;
  return Math.round((to - from) * 10) / 10;
}

/** Seconds elapsed on a running clock: never below zero, since the page's
 *  clock and the server's times are read at different moments. */
function elapsed(from: number, now: number): number {
  return Math.max(0, secondsBetween(from, now) ?? 0);
}

/** A power cycle whose driver notices through its liveness probe, and has not
 *  yet: the person waits for it before turning the device back on. */
function awaitingProbe(o: AuditOutage): boolean {
  return (
    o.kind === "power_cycle" && o.watch.liveness_probe
    && o.noticed_at === null && o.not_noticed_at === null
  );
}

/** What to do now, while a test runs. */
export function outageNowText(o: AuditOutage): string {
  const gone = o.off_at !== null || o.unreachable_at !== null;
  const back = o.on_at !== null || o.reachable_at !== null;
  const cable = o.kind === "cable_pull";
  if (!gone) {
    return cable
      ? "Now unplug the network cable, and press I unplugged it as you do."
      : "Now turn the device off, and press I turned it off as you do.";
  }
  if (!back) {
    if (awaitingProbe(o)) {
      return (
        `Leave it off until OpenAVC notices it is gone. This driver checks every ` +
        `${o.watch.probe_every} s, so it can take up to ${Math.ceil(o.watch.notice_within)} s. ` +
        `The table below shows it.`
      );
    }
    if (!cable) {
      return o.noticed_at !== null
        ? "Now turn it back on and press I turned it back on."
        : "Leave it off for about 10 seconds, then turn it back on and press I turned it back on.";
    }
    if (o.noticed_at === null && o.not_noticed_at === null) {
      const minutes = Math.round(o.notice_ceiling_seconds / 60) || 1;
      return (
        `Leave it unplugged until OpenAVC notices. The table below shows it.` +
        (o.watch.liveness_probe
          ? ""
          : ` This driver does not check on its own whether the device is still there, so OpenAVC ` +
            `may not notice. The test says so when the ${minutes} minutes are up.`)
      );
    }
    return "Now plug the cable back in and press I plugged it back in.";
  }
  if (o.reconnected_at === null) return "Waiting for the driver to reconnect.";
  if (o.dropped_again_at !== null && o.reconnected_again_at === null) {
    return "The connection dropped again. Waiting for the driver to reconnect.";
  }
  return "Watching the status values come back. The test ends on its own.";
}

/** The mark the person should press next: "off", "on", or none. */
export function outageNextMark(o: AuditOutage): "off" | "on" | "" {
  if (o.off_at === null && o.unreachable_at === null) return "off";
  if (o.on_at !== null) return "";
  if (o.kind === "cable_pull" && o.noticed_at === null && o.not_noticed_at === null && o.reachable_at === null) {
    return "";
  }
  if (awaitingProbe(o) && o.reachable_at === null) return "";
  return "on";
}

/** Where a power or cable test stands, one line per clock, as the step shows it. */
export function outageProgress(o: AuditOutage, now: number): { label: string; value: string }[] {
  const lines: { label: string; value: string }[] = [];
  const gone = o.unreachable_at ?? o.off_at;
  const back = o.reachable_at ?? o.on_at;
  const offWord = o.kind === "power_cycle" ? "Turned off" : "Cable out";
  const onWord = o.kind === "power_cycle" ? "Turned back on" : "Cable back in";
  if (o.ping.used) {
    lines.push({
      label: "Answers ping",
      value: o.unreachable_at === null
        ? "yes"
        : o.reachable_at === null
          ? `no, for ${elapsed(o.unreachable_at, now)} s`
          : `again, after ${secondsBetween(o.unreachable_at, o.reachable_at)} s without`,
    });
  }
  if (o.off_at !== null) lines.push({ label: offWord, value: "you said so" });
  if (o.noticed_at !== null) {
    const after = secondsBetween(gone, o.noticed_at);
    const why = (o.reason?.detail || o.reason?.code || "").replace(/\.$/, "");
    lines.push({
      label: "OpenAVC noticed",
      value: `${after !== null ? `${Math.max(0, after)} s after it went` : "yes"}${why ? ` (${why})` : ""}`,
    });
  } else if (o.not_noticed_at !== null) {
    lines.push({
      label: "OpenAVC noticed",
      value: `not within ${Math.round(o.notice_ceiling_seconds / 60) || 1} minutes`,
    });
  } else if (gone !== null && o.status === "running") {
    const waited = elapsed(gone, now);
    const left = Math.max(0, Math.ceil(o.notice_ceiling_seconds - waited));
    lines.push({ label: "OpenAVC noticed", value: `not yet (${waited} s; ${left} s left)` });
  }
  if (o.on_at !== null) lines.push({ label: onWord, value: "you said so" });
  if (o.reconnected_at !== null) {
    const after = secondsBetween(back, o.reconnected_at);
    lines.push({
      label: "Driver reconnected",
      value: after !== null && after >= 0 ? `${after} s after the device was back` : "yes",
    });
    if (o.dropped_again_at !== null) {
      lines.push({
        label: "Dropped again",
        value: `${secondsBetween(o.reconnected_at, o.dropped_again_at)} s after reconnecting`,
      });
      lines.push({
        label: "Reconnected again",
        value: o.reconnected_again_at !== null
          ? `${secondsBetween(o.dropped_again_at, o.reconnected_again_at)} s later`
          : o.status === "running" ? "not yet" : "no",
      });
    }
    const again = o.repopulated.reported_again.length;
    const total = again + o.repopulated.not_reported_again.length;
    if (total > 0) lines.push({ label: "Values reported again", value: `${again} of ${total}` });
  }
  if (o.announcements.length > 0) {
    lines.push({ label: "Announcements heard", value: String(o.announcements.length) });
  }
  return lines;
}
