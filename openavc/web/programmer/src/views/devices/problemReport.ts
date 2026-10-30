/**
 * Report a problem: the rules and the words, kept apart from the dialog so they
 * can be tested on their own.
 *
 * Sending runs in a fixed order (`sendReport`): this server sends first; only
 * when it could not reach the report service at all does the browser send the
 * same saved file, because the browser's certificate store and proxy settings
 * may get through where the server's do not. An answer from the service
 * (stored, too large, too many, refused, storage full) is final, and inside
 * Remote Access the browser never sends: the page's origin there is the
 * cloud's own, and the server is online by definition.
 */
import type {
  ProblemReportPreview,
  SendFailure,
  ServerSendResult,
} from "../../api/problemReportClient";

export const SUPPORT_EMAIL = "support@openavc.com";

/** Report a problem after a failed command: the driver's own fault (500), an
 *  exchange that came apart partway (502), or an outcome the driver could not
 *  confirm (409 `command_incomplete`). Never a bad parameter (400), an unknown
 *  command (404), a device that is not connected (503), or a device that said
 *  no in its own words (a plain 409). */
export function reportableCommandFailure(
  status: number | null | undefined,
  code: string | null | undefined,
): boolean {
  if (status === 500 || status === 502) return true;
  return status === 409 && code === "command_incomplete";
}

export type SendOutcome =
  | { kind: "sent"; reference: string; name: string; via: "server" | "browser" }
  | { kind: "failed"; failure: SendFailure; name: string };

export interface SendSteps {
  /** This server's send (saving the report first, for an audit's Report step). */
  serverSend: () => Promise<ServerSendResult>;
  /** The saved file's bytes, by the name the server's send answered with. */
  fetchFile: (name: string) => Promise<Blob>;
  /** The browser's own POST to the report service. */
  post: (url: string, file: Blob, filename: string, fields: Record<string, string>) => Promise<Response>;
  /** True inside Remote Access: the browser never sends there. */
  remote: boolean;
  kind: "problem_report" | "device_audit";
  openavcVersion: string;
}

/** The report service's answer to the browser, as a send outcome. */
export async function browserAnswer(res: Response, name: string): Promise<SendOutcome> {
  if (res.status === 200) {
    let reference: unknown = null;
    try {
      reference = (await res.json())?.reference;
    } catch {
      /* no reference: treated as not arrived */
    }
    if (typeof reference === "string" && reference) {
      return { kind: "sent", reference, name, via: "browser" };
    }
    return { kind: "failed", failure: "network", name };
  }
  const failure: SendFailure =
    res.status === 413 ? "too_large"
    : res.status === 429 ? "rate_limited"
    : res.status === 507 ? "storage_full"
    : res.status === 404 || res.status === 405 ? "not_configured"
    : res.status >= 500 ? "network"
    : "refused";
  return { kind: "failed", failure, name };
}

/** Send one report: this server, then (after a network failure only, and
 *  never inside Remote Access) the browser. One attempt each; nothing later. */
export async function sendReport(steps: SendSteps): Promise<SendOutcome> {
  const server = await steps.serverSend();
  const name = server.name;
  if (server.sent && server.reference) {
    return { kind: "sent", reference: server.reference, name, via: "server" };
  }
  const failure = server.failure ?? "network";
  if (failure !== "network") return { kind: "failed", failure, name };
  const url = server.report_url ?? "";
  if (steps.remote || !url || !server.report_id) return { kind: "failed", failure, name };
  let file: Blob;
  try {
    file = await steps.fetchFile(name);
  } catch {
    return { kind: "failed", failure: "network", name };
  }
  try {
    const res = await steps.post(url, file, name, {
      report_id: server.report_id,
      kind: steps.kind,
      openavc_version: steps.openavcVersion,
    });
    return await browserAnswer(res, name);
  } catch {
    // No connection, DNS, TLS, a proxy, or the answer withheld from the page.
    return { kind: "failed", failure: "network", name };
  }
}

export function sentText(reference: string): string {
  return `Sent. Your reference is ${reference}.`;
}

/** What the page says when a report did not arrive, by why. */
export function failureText(failure: SendFailure, maxBytes = 20 * 1024 * 1024): string {
  const then = `Download it and email it to ${SUPPORT_EMAIL}.`;
  switch (failure) {
    case "too_large":
      return `This report is over ${Math.round(maxBytes / (1024 * 1024))} MB, too large to send. ${then}`;
    case "rate_limited":
      return `Too many reports have been sent from this network recently. Download it and email it to ${SUPPORT_EMAIL}, or send it again later.`;
    case "storage_full":
      return `Reports can't be received right now. ${then}`;
    case "refused":
      return `This report was refused as unreadable. ${then}`;
    case "network":
    case "not_configured":
    default:
      return `This report couldn't be sent from here. ${then}`;
  }
}

/** A final answer: pressing Send again would get the same one. */
export function finalFailure(failure: SendFailure): boolean {
  return failure !== "network";
}

const INSTALLED: Record<string, string> = {
  windows_installer: ", installed with the Windows installer",
  linux_package: ", installed as a Linux service",
  macos_app: ", installed with the Mac installer",
  docker: ", in Docker",
  git_dev: ", run from source",
};

/** "OpenAVC 0.38.0 on Windows, installed with the Windows installer". */
export function platformLine(
  version: string,
  os: string,
  deployment: string,
): string {
  if (deployment === "android_appliance") return `OpenAVC ${version} on appliance hardware`;
  const first = (os || "").split("-")[0];
  const system = first === "Darwin" ? "macOS" : first;
  return `OpenAVC ${version}${system ? ` on ${system}` : ""}${INSTALLED[deployment] ?? ""}`;
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** A recorded time as the page shows it: the clock time today, else the date too. */
export function clockTime(seconds: number, now = new Date()): string {
  const when = new Date(seconds * 1000);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return when.toDateString() === now.toDateString()
    ? time
    : `${when.toLocaleDateString()} ${time}`;
}

const SOURCE_WORDS: Record<string, string> = {
  built_in: "built into OpenAVC",
  catalog: "from the driver library",
  imported: "imported on this system",
};

function driverLine(preview: ProblemReportPreview): string {
  const driver = preview.driver;
  if (!driver) return "No driver is set for this device";
  const named = [driver.name || driver.id, driver.version].filter(Boolean).join(" ");
  if (!driver.loaded) return `Driver: ${named}, not loaded`;
  const parts = [named];
  if (driver.source && SOURCE_WORDS[driver.source]) parts.push(SOURCE_WORDS[driver.source]);
  if (driver.source === "catalog" && driver.checked) {
    parts.push(driver.modified ? "edited on this system" : "unchanged");
  }
  return `Driver: ${parts.join(", ")}`;
}

function addressText(address: ProblemReportPreview["address"]): string {
  const host = address?.host ? String(address.host) : "";
  const port = address?.port !== undefined && address?.port !== null && address?.port !== ""
    ? String(address.port)
    : "";
  const where = host && port ? `${host}:${port}` : host || port;
  if (!where) return "";
  return address?.bridge ? `${where}, through ${address.bridge}` : where;
}

/** "What's included", one line each, from the preview's own counts. */
export function includedLines(preview: ProblemReportPreview, now = new Date()): string[] {
  const lines = [driverLine(preview)];
  const files = preview.driver?.files ?? [];
  if (files.length === 1) lines.push(`The driver file: ${files[0]}`);
  else if (files.length > 1) lines.push(`The driver files: ${files.join(", ")}`);
  lines.push("This device's settings, with passwords hidden");
  const where = addressText(preview.address);
  if (where) lines.push(`This device's address: ${where}`);
  lines.push(
    preview.changes > 0
      ? `${count(preview.values, "current value", "current values")} and the last ${count(preview.changes, "change", "changes")}`
      : count(preview.values, "current value", "current values"),
  );
  if (preview.messages > 0) {
    const since = preview.window_start ? `, since ${clockTime(preview.window_start, now)}` : "";
    lines.push(
      `The last ${count(preview.messages, "message", "messages")} to and from the device${since}`,
    );
  } else {
    lines.push("No messages to or from the device since it last started");
  }
  if (preview.log_lines > 0) {
    lines.push(`The last ${count(preview.log_lines, "log line", "log lines")} about this device`);
  }
  lines.push(platformLine(preview.openavc_version, preview.os, preview.deployment_type));
  if (preview.simulated) lines.push("Running on the simulator");
  lines.push("Nothing about your other devices or the rest of the project");
  return lines;
}
