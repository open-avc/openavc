import { describe, expect, it } from "vitest";
import type {
  AuditConflictDevice,
  AuditReport,
  AuditSessionState,
} from "../../../api/auditClient";
import {
  ACTIVITY_LABELS,
  ACTIVITY_ORDER,
  applyAuditMessage,
  checkStarted,
  fileSize,
  parseCommunities,
  pauseNotice,
  currentRun,
  displayBytes,
  previewStageLabel,
  stepFor,
  summaryLines,
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
