import { describe, expect, it } from "vitest";
import type {
  AuditReportDriver,
  AuditConflictDevice,
  AuditListen,
  AuditReport,
  AuditSessionState,
  AuditTrafficEntry,
} from "../../../api/auditClient";
import {
  ACTIVITY_LABELS,
  ACTIVITY_ORDER,
  applyAuditMessage,
  checkStarted,
  fileSize,
  parseCommunities,
  pauseNotice,
  appendTraffic,
  currentRun,
  displayBytes,
  LIVE_TRAFFIC_KEPT,
  secondsLeft,
  statusValue,
  previewStageLabel,
  stepFor,
  summaryLines,
  driverLines,
  verdictDrivers,
} from "./auditHelpers";

function session(overrides: Partial<AuditSessionState> = {}): AuditSessionState {
  return {
    session_id: "abc123",
    status: "active",
    target: { address: "widget.local", ip: "10.0.0.50" },
    options: { extended: false, snmp_communities: 0 },
    started_at: 1,
    ended_at: null,
    steps: ["target"],
    paused: [],
    report_name: null,
    tester: {},
    check: {
      status: "running",
      error: "",
      activities: ACTIVITY_ORDER.map((key) => ({
        key, status: "pending", message: "", started_at: null, finished_at: null,
      })),
      result: null,
    },
    ...overrides,
  };
}

function device(name: string): AuditConflictDevice {
  return {
    device_id: name.toLowerCase().replace(/ /g, "_"), device_name: name, driver: "acme",
    transport: "tcp", host: "10.0.0.50", port: 23, bridge: "", connected: true, paused: false,
  };
}

describe("where the wizard picks up", () => {
  it("starts at the device step with no session", () => {
    expect(stepFor(null)).toBe("target");
  });
  it("returns to the network check for a running audit", () => {
    expect(stepFor(session({ steps: ["target", "network_check"] }))).toBe("network");
  });
  it("returns to the driver choice once the person made one", () => {
    expect(stepFor(session({ steps: ["target", "network_check", "driver"] }))).toBe("driver");
  });
  it("returns to the report once the person reached it", () => {
    expect(stepFor(session({ steps: ["target", "network_check", "report"] }))).toBe("report");
  });
  it("knows when a report can be taken", () => {
    expect(checkStarted(null)).toBe(false);
    const idle = session();
    idle.check!.status = "idle";
    expect(checkStarted(idle)).toBe(false);
    expect(checkStarted(session())).toBe(true);
  });
});

describe("messages from the server", () => {
  it("patches one activity from a progress message", () => {
    const before = session();
    const { session: after } = applyAuditMessage(before, [], {
      type: "audit.progress",
      session_id: "abc123",
      activity: {
        key: "ports", status: "done", message: "Open: 23.", started_at: 2, finished_at: 3,
      },
    });
    expect(after).not.toBe(before);
    const ports = after!.check!.activities.find((a) => a.key === "ports");
    expect(ports?.message).toBe("Open: 23.");
    expect(after!.check!.activities.find((a) => a.key === "web")?.status).toBe("pending");
  });

  it("replaces the session on a state message", () => {
    const next = session({ status: "finished" });
    const { session: after } = applyAuditMessage(session(), [], {
      type: "audit.state", session_id: "abc123", state: next,
    });
    expect(after).toBe(next);
  });

  it("appends timeline entries", () => {
    const entry = { t: 1, kind: "check.ports", text: "Open: 23." };
    const { timeline } = applyAuditMessage(session(), [], {
      type: "audit.timeline", session_id: "abc123", entry,
    });
    expect(timeline).toEqual([entry]);
  });

  it("ignores another session's messages and keeps its references", () => {
    const before = session();
    const timeline: never[] = [];
    const out = applyAuditMessage(before, timeline, {
      type: "audit.state", session_id: "someone-else", state: session({ status: "finished" }),
    });
    expect(out.session).toBe(before);
    expect(out.timeline).toBe(timeline);
  });
});

describe("the words", () => {
  it("names one paused device", () => {
    expect(pauseNotice([device("Lobby Display")])).toBe(
      "Lobby Display in this project uses this device. OpenAVC pauses it while the audit " +
        "runs and reconnects it when you finish.",
    );
  });
  it("names several paused devices", () => {
    expect(pauseNotice([device("Lobby Display"), device("Hall Display"), device("Bar")])).toBe(
      "Lobby Display, Hall Display, and Bar in this project use this device. OpenAVC pauses " +
        "them while the audit runs and reconnects them when you finish.",
    );
  });
  it("says nothing when nothing needs pausing", () => {
    expect(pauseNotice([])).toBe("");
  });
  it("labels every activity, with no dashes", () => {
    for (const key of ACTIVITY_ORDER) {
      expect(ACTIVITY_LABELS[key]).toBeTruthy();
      expect(ACTIVITY_LABELS[key]).not.toMatch(/[—–]/);
    }
  });
  it("reads communities one per comma or line, public left out", () => {
    expect(parseCommunities(" widgets, public,\nrack-7 ,widgets")).toEqual(["widgets", "rack-7"]);
  });
  it("sizes files", () => {
    expect(fileSize(512)).toBe("512 bytes");
    expect(fileSize(20480)).toBe("20 KB");
    expect(fileSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

describe("the on-screen summary", () => {
  const report = {
    report_version: 1,
    complete: true,
    target: { address: "widget.local", ip: "10.0.0.50", hostname: null, same_subnet: true },
    device: {
      reported: {
        manufacturer: "Acme", model: "Widget 3000", firmware: "1.2", serial_number: null,
        device_name: null, hostname: null, mac: null,
      },
    },
    catalog: {},
    footprint: {
      ping: { result: "timeout" },
      mac: { address: "aa:bb:cc:00:11:22", source: "arp" },
      ports: { checked: 72, range: "standard", open: [23, 80], refused: [], filtered: [] },
      web: {
        "80": { status_line: "HTTP/1.0 401 Unauthorized", title: "Widget", www_authenticate: "Basic", error: "" },
      },
      mdns: { services: [{ service_type: "_acme._tcp.local" }] },
      ssdp: null,
      amx_ddp: null,
      snmp: { answered: false },
    },
    verdict: {} as AuditReport["verdict"],
    limits: [],
  } as unknown as AuditReport;

  it("reads in order and says each fact plainly", () => {
    expect(summaryLines(report)).toEqual([
      { label: "Device", value: "Acme Widget 3000" },
      { label: "Firmware", value: "1.2" },
      { label: "Address", value: "widget.local (10.0.0.50)" },
      { label: "MAC address", value: "aa:bb:cc:00:11:22" },
      { label: "Ping", value: "No answer" },
      { label: "Open ports", value: "23, 80" },
      { label: "Web pages", value: '80: HTTP/1.0 401 Unauthorized, "Widget", asks for sign-in' },
      { label: "Announcements", value: "mDNS (1 service)" },
      { label: "SNMP", value: "No answer" },
    ]);
  });

  it("adds what each driver did, and names the driver when there are several", () => {
    const attempt = {
      status: "done", error: "", started_at: 100, connected_at: 100.5, declared: 7,
      reported: 5, offline: null, contract: { counts: { unmatched_response: 3 } },
      unprompted_replies: { count: 0 }, traffic: { count: 40, not_captured: false },
    };
    const failed = {
      ...attempt, connected_at: null, reported: 0, contract: { counts: {} },
      offline: { code: "auth_failed", detail: "The device refused the password.", next_step: "" },
    };
    const one = { run: 0, driver: { id: "acme", name: "Acme", version: "1.2.0", modified: false },
      attempts: [attempt] } as unknown as AuditReportDriver;
    expect(driverLines([one])).toEqual([
      { label: "Driver", value: "Acme 1.2.0" },
      { label: "Connected", value: "Yes, 0.5 s after starting" },
      { label: "Status values", value: "5 of 7 reported" },
      { label: "Replies not understood", value: "3 matched none of the driver's rules" },
    ]);
    const two = { run: 1, driver: { id: "acme2", name: "Acme Two", version: "", modified: true },
      attempts: [failed] } as unknown as AuditReportDriver;
    const unused = { run: 2, driver: { id: "x", name: "X", version: "", modified: false },
      attempts: [] } as unknown as AuditReportDriver;
    expect(driverLines([one, two, unused]).map((l) => l.label)).toEqual([
      "Driver (1)", "Connected (1)", "Status values (1)", "Replies not understood (1)",
      "Driver (2)", "Connected (2)", "Status values (2)",
    ]);
    expect(driverLines([two])[0].value).toBe("Acme Two, a modified copy");
    expect(driverLines([two])[1].value).toBe("No: The device refused the password.");
    // What the person entered names the device ahead of what it reported.
    const entered = {
      ...report,
      device: { ...report.device, entered: { manufacturer: "Acme", model: "W-100", firmware: null } },
    } as AuditReport;
    expect(summaryLines(entered).slice(0, 2)).toEqual([
      { label: "Device", value: "Acme W-100" },
      { label: "Firmware", value: "1.2" },
    ]);
  });

  it("names the drivers a verdict points at", () => {
    expect(
      verdictDrivers({ acme_widget: ["probe:x"], acme_gadget: ["oui:aa"] }, {
        acme_widget: { name: "Acme Widget" },
      }),
    ).toEqual([
      { id: "acme_widget", name: "Acme Widget", sources: ["probe:x"] },
      { id: "acme_gadget", name: "acme_gadget", sources: ["oui:aa"] },
    ]);
  });
});

describe("showing bytes and the connection preview", () => {
  it("writes text out with its line endings, and anything else as hex", () => {
    expect(displayBytes("PWR?\r", "5057523f0d")).toBe('"PWR?\\r"');
    expect(displayBytes("\xaa\x11\x01", "aa1101")).toBe("aa 11 01");
    expect(displayBytes("", "")).toBe("");
  });
  it("names each stage, with its cadence", () => {
    expect(previewStageLabel("sign_in", 0, 0)).toBe("Sign in");
    expect(previewStageLabel("poll", 10, 0)).toBe("Status polling, every 10 seconds");
    expect(previewStageLabel("poll", 1, 0)).toBe("Status polling, every second");
    expect(previewStageLabel("keep_alive", 0, 30)).toBe("Keep-alive check, every 30 seconds");
  });
  it("takes the last run as the current one", () => {
    expect(currentRun(null)).toBeNull();
    expect(currentRun(session({ runs: [] }))).toBeNull();
  });
});

describe("connect and listen", () => {
  const entry = (seq: number): AuditTrafficEntry => ({
    seq, t: seq, direction: "tx", channel: "tcp", hex: "41", text: "A",
  });

  it("keeps the newest traffic, for this audit only", () => {
    const one = appendTraffic([], { type: "audit.traffic", session_id: "abc123", entries: [entry(1)] }, "abc123");
    expect(one.map((e) => e.seq)).toEqual([1]);
    expect(appendTraffic(one, { type: "audit.traffic", session_id: "other", entries: [entry(2)] }, "abc123")).toBe(one);
    const many = Array.from({ length: LIVE_TRAFFIC_KEPT + 5 }, (_, i) => entry(i));
    const kept = appendTraffic([], { type: "audit.traffic", session_id: "abc123", entries: many }, "abc123");
    expect(kept.length).toBe(LIVE_TRAFFIC_KEPT);
    expect(kept[0].seq).toBe(5);
  });

  it("patches one run's listening state", () => {
    const s = session({
      runs: [{ index: 0, choice: {} as never, started_at: 1, finished_at: null, active: true, connection: null }],
    });
    const listen = { status: "listening" } as unknown as AuditListen;
    const next = applyAuditMessage(s, [], { type: "audit.listen", session_id: "abc123", run: 0, listen });
    expect(next.session?.runs?.[0].listen).toBe(listen);
    const ignored = applyAuditMessage(s, [], { type: "audit.listen", session_id: "abc123", run: 3, listen });
    expect(ignored.session).toBe(s);
  });

  it("counts down only while listening", () => {
    const listen = { status: "listening", ends_at: 110 } as unknown as AuditListen;
    expect(secondsLeft(listen, 100.2)).toBe(10);
    expect(secondsLeft({ ...listen, status: "done" }, 100)).toBeNull();
    expect(secondsLeft(undefined, 100)).toBeNull();
  });

  it("says a value was not reported rather than showing nothing", () => {
    const v = { name: "power", label: "Power", type: "boolean", value: null, reported: false,
      first_reported_at: null, problem: "", sources: [] };
    expect(statusValue(v)).toBe("Not reported");
    expect(statusValue({ ...v, reported: true, value: false })).toBe("false");
    expect(statusValue({ ...v, reported: true, value: 12 })).toBe("12");
  });
});
