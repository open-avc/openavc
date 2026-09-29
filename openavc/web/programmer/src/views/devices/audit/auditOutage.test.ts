import { describe, expect, it } from "vitest";
import type { AuditOutage, AuditReportDriver, AuditSessionState } from "../../../api/auditClient";
import { applyAuditMessage, driverLines, outageProgress, stepFor } from "./auditHelpers";

function outage(extra: Partial<AuditOutage> = {}): AuditOutage {
  return {
    number: 1, kind: "power_cycle", status: "running", end_reason: "", connect_attempt: 0,
    started_at: 100, finished_at: null, off_at: null, on_at: null, unreachable_at: null,
    reachable_at: null, noticed_at: null, reconnected_at: null, not_noticed_at: null,
    ends_at: null, notice_ceiling_seconds: 300, ping: { used: true, why: "" },
    watch: { liveness_probe: false, probe_every: 0, poll_interval: 5 }, reason: null,
    reasons: [], measured: { noticed_after: null, away_for: null, answered_after_on: null,
      reconnected_after_back: null },
    before: ["power", "volume"], repopulated: { reported_again: [], not_reported_again: ["power", "volume"] },
    announcements: [], summary: "", ...extra,
  };
}

describe("a power or cable test as it runs", () => {
  it("says what each clock shows", () => {
    const o = outage({
      off_at: 101, unreachable_at: 102, noticed_at: 104.5,
      reason: { code: "connection_refused", detail: "The device refused the connection." },
    });
    expect(outageProgress(o, 110)).toEqual([
      { label: "Answers ping", value: "no, for 8 s" },
      { label: "Turned off", value: "you said so" },
      { label: "OpenAVC noticed", value: "2.5 s after it went (The device refused the connection.)" },
    ]);
  });

  it("counts down what OpenAVC has left to notice", () => {
    const o = outage({ kind: "cable_pull", off_at: 100, ping: { used: false, why: "No ping." } });
    expect(outageProgress(o, 160)).toEqual([
      { label: "Cable out", value: "you said so" },
      { label: "OpenAVC noticed", value: "not yet (60 s; 240 s left)" },
    ]);
    expect(outageProgress({ ...o, not_noticed_at: 400 }, 401)[1]).toEqual({
      label: "OpenAVC noticed", value: "not within 5 minutes",
    });
  });

  it("follows the driver back and the values that came back", () => {
    const o = outage({
      off_at: 101, unreachable_at: 102, noticed_at: 103, on_at: 112, reachable_at: 140,
      reconnected_at: 143.2, repopulated: { reported_again: ["power"], not_reported_again: ["volume"] },
      announcements: [{ t: 139, protocol: "ssdp", detail: {} }],
    });
    const lines = outageProgress(o, 150);
    expect(lines[0]).toEqual({ label: "Answers ping", value: "again, after 38 s without" });
    expect(lines.slice(-3)).toEqual([
      { label: "Driver reconnected", value: "3.2 s after the device was back" },
      { label: "Values reported again", value: "1 of 2" },
      { label: "Announcements heard", value: "1" },
    ]);
  });
});

describe("following the power and cable step", () => {
  const session: AuditSessionState = {
    session_id: "abc123", status: "active", target: { address: "10.0.0.5", ip: "10.0.0.5" },
    options: { extended: false, snmp_communities: 0 }, started_at: 1, ended_at: null,
    steps: ["target", "listen", "commands", "outage"], paused: [], report_name: null, tester: {},
    check: null,
    runs: [{ index: 0, choice: {} as never, started_at: 1, finished_at: null, active: true,
      connection: null }],
  };

  it("picks up on the step and merges a test by its number", () => {
    expect(stepFor(session)).toBe("outage");
    const first = applyAuditMessage(session, [], {
      type: "audit.outage", session_id: "abc123", run: 0, outage: outage(),
    }).session!;
    const again = applyAuditMessage(first, [], {
      type: "audit.outage", session_id: "abc123", run: 0,
      outage: outage({ status: "done", summary: "OpenAVC noticed." }),
    }).session!;
    expect(again.runs![0].outages!.map((o) => o.status)).toEqual(["done"]);
  });

  it("puts each finished test in the report's summary", () => {
    const d = {
      run: 0, driver: { id: "acme", name: "Acme", version: "1.0.0", modified: false },
      attempts: [{
        status: "done", error: "", started_at: 1, connected_at: 1.5, declared: 2, reported: 2,
        offline: null, contract: { counts: {} }, unprompted_replies: { count: 0 },
        traffic: { count: 4, not_captured: false },
      }],
      outages: [outage({ status: "done", summary: "OpenAVC noticed the device was gone." })],
    } as unknown as AuditReportDriver;
    expect(driverLines([d]).at(-1)).toEqual({
      label: "Power cycle", value: "OpenAVC noticed the device was gone.",
    });
  });
});
