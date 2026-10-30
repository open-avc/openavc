import { describe, it, expect, vi, afterEach } from "vitest";
import { reportableOffline } from "./DeviceDetail";
import {
  browserAnswer,
  failureText,
  includedLines,
  platformLine,
  reportableCommandFailure,
  sendReport,
  type SendSteps,
} from "./problemReport";
import { isSameOriginApiUrl } from "../../api/auth";
import type { ProblemReportPreview, ServerSendResult } from "../../api/problemReportClient";
import { rememberContact, rememberedContact } from "./contactMemory";

const REPORT_URL = "https://cloud.openavc.com/api/v1/reports";

// Every offline_reason the server writes (core/connection_fault.py).
describe("Report a problem on the Offline banner", () => {
  const cases: [string, boolean][] = [
    ["no_response", true],
    ["write_stalled", true],
    ["transport_disconnected", true],
    ["auth_failed", true],
    ["connection_refused", true],
    ["unreachable", false],
    ["tls_cert_untrusted", false],
    ["host_key_rejected", false],
    ["client_missing", false],
    ["invalid_config", false],
    ["bridge_offline", false],
    ["no_simulator", false],
    ["", true],
    ["a_code_from_a_newer_server", true],
  ];
  it.each(cases)("%s -> %s", (reason, shown) => {
    expect(reportableOffline(reason)).toBe(shown);
  });
  it("is offered with no reason at all", () => {
    expect(reportableOffline(undefined)).toBe(true);
  });
});

describe("Report a problem after a failed command", () => {
  it.each([
    [500, "", true],
    [502, "", true],
    [502, "command_incomplete", true],
    [409, "command_incomplete", true],
    [409, "", false],
    [400, "", false],
    [404, "", false],
    [503, "", false],
    [undefined, undefined, false],
  ])("status %s code %s -> %s", (status, code, shown) => {
    expect(reportableCommandFailure(status as number | undefined, code as string | undefined)).toBe(shown);
  });
});

function server(over: Partial<ServerSendResult>): ServerSendResult {
  return {
    name: "openavc-problem-report-acme_widget-20261030-1042.zip",
    report_id: "6f1c2b9e-1111-4c4c-9a9a-2b2b2b2b2b2b",
    sent: false,
    reference: null,
    failure: "network",
    status: null,
    report_url: REPORT_URL,
    ...over,
  };
}

function steps(answer: ServerSendResult, over: Partial<SendSteps> = {}) {
  const fetchFile = vi.fn(async () => new Blob(["zip"]));
  const post = vi.fn(async () =>
    new Response(JSON.stringify({ reference: "R-BRWSR1" }), { status: 200 }),
  );
  const s: SendSteps = {
    serverSend: async () => answer,
    fetchFile,
    post,
    remote: false,
    kind: "problem_report",
    openavcVersion: "0.38.0",
    ...over,
  };
  return { s, fetchFile, post: (over.post as typeof post | undefined) ?? post };
}

describe("the order Send tries things in", () => {
  it("stops at the server when it sent the report", async () => {
    const { s, post } = steps(server({ sent: true, reference: "R-7K3QXM", failure: null, status: 200 }));
    expect(await sendReport(s)).toMatchObject({ kind: "sent", reference: "R-7K3QXM", via: "server" });
    expect(post).not.toHaveBeenCalled();
  });

  it("sends the same saved file from the browser after a network failure", async () => {
    const { s, fetchFile, post } = steps(server({}));
    expect(await sendReport(s)).toMatchObject({ kind: "sent", reference: "R-BRWSR1", via: "browser" });
    expect(fetchFile).toHaveBeenCalledWith("openavc-problem-report-acme_widget-20261030-1042.zip");
    expect(post).toHaveBeenCalledWith(
      REPORT_URL,
      expect.any(Blob),
      "openavc-problem-report-acme_widget-20261030-1042.zip",
      { report_id: "6f1c2b9e-1111-4c4c-9a9a-2b2b2b2b2b2b", kind: "problem_report", openavc_version: "0.38.0" },
    );
  });

  it.each([
    ["too_large", 413],
    ["rate_limited", 429],
    ["refused", 400],
    ["storage_full", 507],
    ["not_configured", 404],
  ] as const)("never tries the browser after the service's %s (%s)", async (failure, status) => {
    const { s, post, fetchFile } = steps(server({ failure, status }));
    expect(await sendReport(s)).toMatchObject({ kind: "failed", failure });
    expect(post).not.toHaveBeenCalled();
    expect(fetchFile).not.toHaveBeenCalled();
  });

  it("never tries the browser inside Remote Access", async () => {
    const { s, post } = steps(server({}), { remote: true });
    expect(await sendReport(s)).toMatchObject({ kind: "failed", failure: "network" });
    expect(post).not.toHaveBeenCalled();
  });

  it("has nothing to try with no report address", async () => {
    const { s, post } = steps(server({ report_url: "" }));
    expect(await sendReport(s)).toMatchObject({ kind: "failed", failure: "network" });
    expect(post).not.toHaveBeenCalled();
  });

  it("gives up after the browser cannot connect either", async () => {
    const post = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const { s } = steps(server({}), { post });
    expect(await sendReport(s)).toMatchObject({ kind: "failed", failure: "network" });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("words the service's answer to the browser as it does the server's", async () => {
    const name = "r.zip";
    const at = (status: number, body = "{}") => browserAnswer(new Response(body, { status }), name);
    expect(await at(413)).toMatchObject({ failure: "too_large" });
    expect(await at(429)).toMatchObject({ failure: "rate_limited" });
    expect(await at(400)).toMatchObject({ failure: "refused" });
    expect(await at(507)).toMatchObject({ failure: "storage_full" });
    expect(await at(405)).toMatchObject({ failure: "not_configured" });
    expect(await at(503)).toMatchObject({ failure: "network" });
    expect(await at(200, "{}")).toMatchObject({ failure: "network" });
  });
});

describe("the browser's send never carries this session's credential", () => {
  it("is not a same-origin API request from a LAN Programmer", () => {
    expect(isSameOriginApiUrl(REPORT_URL, "http://192.168.1.50:8080/programmer/")).toBe(false);
    expect(isSameOriginApiUrl(REPORT_URL, "https://192.168.1.50:8443/programmer/#devices")).toBe(false);
  });
});

describe("the words", () => {
  it("names the reason and the remedy for every failure", () => {
    expect(failureText("network")).toBe(
      "This report couldn't be sent from here. Download it and email it to support@openavc.com.",
    );
    expect(failureText("too_large")).toBe(
      "This report is over 20 MB, too large to send. Download it and email it to support@openavc.com.",
    );
    for (const f of ["too_large", "rate_limited", "storage_full", "refused", "not_configured"] as const) {
      expect(failureText(f)).toContain("support@openavc.com");
      expect(failureText(f)).not.toMatch(/—/);
    }
  });

  it("puts the system into words", () => {
    expect(platformLine("0.38.0", "Windows-11-10.0.22631-SP0", "windows_installer")).toBe(
      "OpenAVC 0.38.0 on Windows, installed with the Windows installer",
    );
    expect(platformLine("0.38.0", "macOS-15.1-arm64-arm-64bit", "git_dev")).toBe(
      "OpenAVC 0.38.0 on macOS, run from source",
    );
    expect(platformLine("0.38.0", "Linux-6.1.0-aarch64-with-glibc2.36", "docker")).toBe(
      "OpenAVC 0.38.0 on Linux, in Docker",
    );
    expect(platformLine("0.38.0", "Linux-5.10-aarch64", "android_appliance")).toBe(
      "OpenAVC 0.38.0 on appliance hardware",
    );
    expect(platformLine("0.38.0", "Linux-6.8.0-x86_64", "unknown")).toBe("OpenAVC 0.38.0 on Linux");
  });

  it("lists what the report holds from the preview's own counts", () => {
    const now = new Date(2026, 9, 30, 12, 0);
    const preview: ProblemReportPreview = {
      device: { id: "hall_widget", name: "Hall Widget" },
      driver: {
        id: "acme_widget", name: "Acme Widget", version: "1.4.0", source: "catalog",
        modified: false, listed: true, checked: true, files: ["utility/acme_widget.avcdriver"],
        loaded: true,
      },
      address: { transport: "tcp", host: "192.168.1.50", port: 20060 },
      values: 42, changes: 118, messages: 312,
      window_start: new Date(2026, 9, 30, 10, 42).getTime() / 1000,
      log_lines: 64, serial_found: false, simulated: true,
      openavc_version: "0.38.0", os: "Windows-11-10.0.22631-SP0", deployment_type: "windows_installer",
      report_url: REPORT_URL, report_host: "cloud.openavc.com", max_bytes: 20971520,
    };
    const lines = includedLines(preview, now);
    expect(lines[0]).toBe("Driver: Acme Widget 1.4.0, from the driver library, unchanged");
    expect(lines).toContain("The driver file: utility/acme_widget.avcdriver");
    expect(lines).toContain("This device's address: 192.168.1.50:20060");
    expect(lines).toContain("42 current values and the last 118 changes");
    expect(lines.some((l) => /^The last 312 messages to and from the device, since 10:42/.test(l))).toBe(true);
    expect(lines).toContain("The last 64 log lines about this device");
    expect(lines).toContain("OpenAVC 0.38.0 on Windows, installed with the Windows installer");
    expect(lines).toContain("Running on the simulator");
    expect(lines[lines.length - 1]).toBe("Nothing about your other devices or the rest of the project");
  });

  it("never calls a driver imported when the driver library could not be read", () => {
    const preview = {
      driver: {
        id: "acme_widget", name: "Acme Widget", version: "1.4.0", source: "imported",
        modified: false, listed: false, checked: false, files: [], loaded: true,
      },
      address: {}, values: 0, changes: 0, messages: 0, window_start: null, log_lines: 0,
      serial_found: false, simulated: false, openavc_version: "0.38.0", os: "Linux-6.8", deployment_type: "docker",
    } as unknown as ProblemReportPreview;
    expect(includedLines(preview)[0]).toBe(
      "Driver: Acme Widget 1.4.0, not checked: the driver library could not be reached",
    );
  });
});

describe("the remembered name and email", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("comes back as typed, trimmed", () => {
    rememberContact({ name: " Pat ", email: "pat@example.com" });
    expect(rememberedContact()).toEqual({ name: "Pat", email: "pat@example.com" });
  });

  it("starts empty when the browser blocks storage", () => {
    // A real Storage keeps its methods on the prototype (assigning one on the
    // instance stores an item of that name); the setup file may install a
    // plain object instead.
    const target = typeof Storage !== "undefined" && localStorage instanceof Storage
      ? Storage.prototype
      : localStorage;
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.spyOn(target, "getItem").mockImplementation(blocked);
    vi.spyOn(target, "setItem").mockImplementation(blocked);
    expect(() => rememberContact({ name: "Pat", email: "" })).not.toThrow();
    expect(rememberedContact()).toEqual({ name: "", email: "" });
  });
});
