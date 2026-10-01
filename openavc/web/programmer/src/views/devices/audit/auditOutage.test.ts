import { describe, expect, it } from "vitest";
import type { AuditOutage, AuditReportDriver, AuditSessionState } from "../../../api/auditClient";
import {
  applyAuditMessage,
  driverLines,
  outageNextMark,
  outageNowText,
  outageProgress,
  stepFor,
} from "./auditHelpers";

function outage(extra: Partial<AuditOutage> = {}): AuditOutage {
  return {
    number: 1, kind: "power_cycle", status: "running", end_reason: "", end_code: "", connect_attempt: 0,
    started_at: 100, finished_at: null, off_at: null, on_at: null, unreachable_at: null,
    reachable_at: null, noticed_at: null, reconnected_at: null, not_noticed_at: null,
    dropped_again_at: null, reconnected_again_at: null,
    ends_at: null, notice_ceiling_seconds: 300, ping: { used: true, why: "" },
    watch: { liveness_probe: false, probe_every: 0, notice_within: 0, poll_interval: 5 }, reason: null,
    reasons: [], measured: { noticed_after: null, away_for: null, answered_after_on: null,
      reconnected_after_back: null, dropped_again_after: null, reconnected_again_after: null },
    before: ["power", "volume"], repopulated: { reported_again: [], not_reported_again: ["power", "volume"] },
    announcements: [], summary: "", ...extra,
  };
}

describe("what to do now, while a test runs", () => {
  it("walks a power cycle through off, back on, and the reconnect", () => {
    const o = outage();
    expect(outageNowText(o, 100)).toBe("Now turn the device off, and press I turned it off as you do.");
    expect(outageNextMark(o)).toBe("off");
    const off = { ...o, off_at: 101 };
    expect(outageNowText(off, 110)).toBe(
      "Leave it off for about 10 seconds, then turn it back on and press I turned it back on.",
    );
    expect(outageNextMark(off)).toBe("on");
    const on = { ...off, on_at: 115 };
    expect(outageNowText(on, 116)).toBe("Waiting for the driver to reconnect.");
    expect(outageNextMark(on)).toBe("");
    expect(outageNowText({ ...on, reconnected_at: 120 }, 121)).toBe(
      "Watching the status values come back. The test ends on its own.",
    );
    // Once the driver has settled the test knows when it ends, and counts down.
    expect(outageNowText({ ...on, reconnected_at: 120, ends_at: 140.2 }, 121)).toBe(
      "Watching the status values come back. The test ends on its own in 20 s.",
    );
  });

  it("says when the device is back and OpenAVC never noticed it went", () => {
    const back = outage({ off_at: 101, on_at: 115, ends_at: 145 });
    expect(outageNowText(back, 120)).toBe(
      "OpenAVC has not noticed the device went away. The test ends in 25 s.",
    );
  });

  it("keeps the power off until a driver that probes has noticed", () => {
    const off = outage({
      off_at: 101, watch: { liveness_probe: true, probe_every: 30, notice_within: 66, poll_interval: 5 },
    });
    expect(outageNowText(off, 110)).toBe(
      "Leave it off until OpenAVC notices it is gone. This driver checks every 30 s, so it can " +
        "take up to 66 s. The table below shows it.",
    );
    expect(outageNextMark(off)).toBe("");
    const noticed = { ...off, noticed_at: 150 };
    expect(outageNowText(noticed, 151)).toBe("Now turn it back on and press I turned it back on.");
    expect(outageNextMark(noticed)).toBe("on");
  });

  it("never reads a running clock below zero", () => {
    // The ping loss came in after the page last read its own clock.
    const lost = outage({ off_at: 101, unreachable_at: 104.4 });
    const rows = outageProgress(lost, 100);
    expect(rows.find((r) => r.label === "Answers ping")?.value).toBe("no, for 0 s");
    expect(rows.find((r) => r.label === "OpenAVC noticed")?.value).toBe("not yet (0 s so far, 300 s left)");
  });

  it("keeps a pulled cable out until OpenAVC notices or the limit passes", () => {
    const pulled = outage({ kind: "cable_pull", off_at: 101 });
    // No liveness check: say it may not notice, and hold the plug-in back.
    expect(outageNowText(pulled, 110)).toBe(
      "Leave it unplugged until OpenAVC notices it is gone, up to 5 minutes. This driver does not " +
        "check on its own whether the device is still there, so OpenAVC may not notice at all. " +
        "The table below shows it.",
    );
    expect(outageNextMark(pulled)).toBe("");
    const checked = { ...pulled, watch: { liveness_probe: true, probe_every: 30, notice_within: 66, poll_interval: 5 } };
    expect(outageNowText(checked, 110)).toBe(
      "Leave it unplugged until OpenAVC notices it is gone. This driver checks every 30 s, so it " +
        "can take up to 66 s. The table below shows it.",
    );
    for (const seen of [{ noticed_at: 130 }, { not_noticed_at: 401 }]) {
      expect(outageNowText({ ...pulled, ...seen }, 410)).toBe(
        "Now plug the cable back in and press I plugged it back in.",
      );
      expect(outageNextMark({ ...pulled, ...seen })).toBe("on");
    }
    // The device answering ping again means the cable is back, noticed or not.
    expect(outageNextMark({ ...pulled, unreachable_at: 102, reachable_at: 140 })).toBe("on");
  });
});

describe("a connection that drops again after the reconnect", () => {
  const back = outage({
    off_at: 100, unreachable_at: 100, noticed_at: 130, on_at: 140, reachable_at: 157,
    reconnected_at: 158, dropped_again_at: 160.7,
  });

  it("says so, and waits for the driver", () => {
    expect(outageNowText(back, 165)).toBe(
      "The connection dropped again. Waiting for the driver to reconnect.",
    );
    const lines = outageProgress(back, 170);
    expect(lines).toContainEqual({ label: "Dropped again", value: "2.7 s after reconnecting" });
    expect(lines).toContainEqual({ label: "Reconnected again", value: "not yet" });
  });

  it("counts how long the driver took to be back again", () => {
    const again = { ...back, reconnected_again_at: 166.2 };
    expect(outageProgress(again, 170)).toContainEqual({
      label: "Reconnected again", value: "5.5 s later",
    });
    expect(outageNowText(again, 170)).toBe(
      "Watching the status values come back. The test ends on its own.",
    );
    expect(outageProgress({ ...back, status: "done" }, 400)).toContainEqual({
      label: "Reconnected again", value: "no",
    });
  });
});

describe("a power or cable test as it runs", () => {
  it("says what each clock shows", () => {
    const o = outage({
      off_at: 101, unreachable_at: 102, noticed_at: 104.5,
      reason: { code: "connection_refused", detail: "The device refused the connection." },
    });
    expect(outageProgress(o, 110)).toEqual([
      { label: "Answers ping", value: "no, for 8 s" },
      { label: "Turned off", value: "yes" },
      { label: "OpenAVC noticed", value: "2.5 s after it went away (The device refused the connection)" },
    ]);
  });

  it("counts down what OpenAVC has left to notice", () => {
    const o = outage({ kind: "cable_pull", off_at: 100, ping: { used: false, why: "No ping." } });
    expect(outageProgress(o, 160)).toEqual([
      { label: "Cable out", value: "yes" },
      { label: "OpenAVC noticed", value: "not yet (60 s so far, 240 s left)" },
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
    expect(lines[0]).toEqual({ label: "Answers ping", value: "yes again, after 38.0 s without an answer" });
    expect(lines).toContainEqual({ label: "Turned back on", value: "yes, after 11.0 s off" });
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
