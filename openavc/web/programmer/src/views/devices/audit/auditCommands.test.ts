import { describe, expect, it } from "vitest";
import type {
  AuditCommandInfo,
  AuditCommandTrial,
  AuditCommands,
  AuditReportDriver,
  AuditSessionState,
} from "../../../api/auditClient";
import {
  applyAuditMessage,
  batchableQueries,
  commandGroups,
  driverLines,
  paramsText,
  stepFor,
  trialOutcome,
} from "./auditHelpers";

function command(name: string, extra: Partial<AuditCommandInfo> = {}): AuditCommandInfo {
  return {
    name, label: name, help: "", params: {}, query: false, query_for: "", polled: false,
    sets: {}, available_offline: false, restarts_device_for: 0, needs_input: false, ...extra,
  };
}

function trial(extra: Partial<AuditCommandTrial> = {}): AuditCommandTrial {
  return {
    number: 1, command: "set_volume", label: "Set Volume", params: { level: 40 }, attempt: 1,
    batch: false, connect_attempt: 0, sent_at: 10, returned_at: 10.1, ends_at: 12,
    finished_at: 12, status: "done", result: null, error: "", error_type: "",
    traffic: { sent: 1, received: 1, entries: [] }, ...extra,
  };
}

function session(runs: AuditSessionState["runs"]): AuditSessionState {
  return {
    session_id: "abc123", status: "active", target: { address: "10.0.0.50", ip: "10.0.0.50" },
    options: { extended: false, snmp_communities: 0 }, started_at: 1, ended_at: null,
    steps: ["target"], paused: [], report_name: null, tester: {}, check: null, runs,
  };
}

describe("the command list", () => {
  const catalog = [
    command("power_on"),
    command("query_power", { query: true, query_for: "power" }),
    command("query_input", { query: true, needs_input: true }),
    command("set_volume", { needs_input: true }),
  ];

  it("keeps status queries apart from commands, each in the driver's order", () => {
    const { queries, commands } = commandGroups(catalog);
    expect(queries.map((c) => c.name)).toEqual(["query_power", "query_input"]);
    expect(commands.map((c) => c.name)).toEqual(["power_on", "set_volume"]);
  });

  it("counts only the queries that can run without a value", () => {
    expect(batchableQueries(catalog)).toBe(1);
  });
});

describe("what a command did, in words", () => {
  it("says what went out and what came back", () => {
    expect(trialOutcome(trial())).toBe("Sent 1 message; the device sent 1 reply.");
    expect(trialOutcome(trial({ traffic: { sent: 2, received: 0, entries: [] } }))).toBe(
      "Sent 2 messages; nothing came back.",
    );
  });

  it("is still waiting while the command is watched", () => {
    expect(trialOutcome(trial({ status: "watching", traffic: { sent: 1, received: 0, entries: [] } })))
      .toBe("Sent 1 message; waiting for a reply.");
    expect(trialOutcome(trial({ status: "sending" }))).toBe("Sending.");
  });

  it("gives the refusal when the command was not accepted", () => {
    expect(trialOutcome(trial({ error: "'set_volume': 'level' must be at most 100, got 150" })))
      .toBe("Not accepted: 'set_volume': 'level' must be at most 100, got 150");
  });

  it("writes parameters the way a line reads them", () => {
    expect(paramsText({ level: 40, input: "hdmi1" })).toBe("level 40, input hdmi1");
    expect(paramsText({})).toBe("");
  });
});

describe("following the commands step", () => {
  const commands: AuditCommands = { catalog: [], batch: null, current: 1, trials: [trial()] };

  it("picks up on the commands step once a command was sent", () => {
    const s = session([]);
    s.steps = ["target", "network_check", "driver", "connection", "listen", "commands"];
    expect(stepFor(s)).toBe("commands");
  });

  it("replaces one run's commands from a message", () => {
    const before = session([{
      index: 0, choice: {} as never, started_at: 1, finished_at: null, active: true,
      connection: null,
    }]);
    const { session: after } = applyAuditMessage(before, [], {
      type: "audit.commands", session_id: "abc123", run: 0, commands,
    });
    expect(after!.runs![0].commands).toBe(commands);
    // A run the wizard does not have is left alone.
    const same = applyAuditMessage(before, [], {
      type: "audit.commands", session_id: "abc123", run: 5, commands,
    });
    expect(same.session).toBe(before);
  });

  it("adds the commands sent to the report's summary", () => {
    const d = {
      run: 0, driver: { id: "acme", name: "Acme", version: "1.0.0", modified: false },
      attempts: [{
        status: "done", error: "", started_at: 1, connected_at: 1.5, declared: 2, reported: 2,
        offline: null, contract: { counts: {} }, unprompted_replies: { count: 0 },
        traffic: { count: 4, not_captured: false },
      }],
      commands: { trials: [trial(), trial({ number: 2, error: "No." })] },
    } as unknown as AuditReportDriver;
    expect(driverLines([d]).at(-1)).toEqual({ label: "Commands sent", value: "2, 1 not accepted" });
  });
});
