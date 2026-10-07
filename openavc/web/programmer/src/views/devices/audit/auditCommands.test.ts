import { describe, expect, it } from "vitest";
import type {
  AuditCommandInfo,
  AuditCommandTrial,
  AuditCommands,
  AuditListen,
  AuditReportDriver,
  AuditSessionState,
} from "../../../api/auditClient";
import {
  answerCounts,
  applyAuditMessage,
  batchableQueries,
  changedText,
  commandMatches,
  commandOrder,
  commandProgress,
  commandStatus,
  driverLines,
  mergeCommands,
  movedParts,
  movedText,
  movingText,
  notTried,
  nowReading,
  paramsText,
  sendWarning,
  stepFor,
  trafficRows,
  trialOutcome,
} from "./auditHelpers";

function command(name: string, extra: Partial<AuditCommandInfo> = {}): AuditCommandInfo {
  return {
    name, label: name, help: "", params: {}, query: false, query_for: "", polled: false,
    sets: {}, available_offline: false, restarts_device_for: 0, needs_input: false, confirm: "",
    suggested: false, ...extra,
  };
}

function trial(extra: Partial<AuditCommandTrial> = {}): AuditCommandTrial {
  return {
    number: 1, command: "set_volume", label: "Set Volume", params: { level: 40 }, attempt: 1,
    batch: false, connect_attempt: 0, sent_at: 10, returned_at: 10.1, ends_at: 12,
    finished_at: 12, status: "done", result: null, error: "", error_type: "",
    traffic: { sent: 1, received: 1, entries: [] }, since_previous: null, extended: 0,
    stopped_early: false, changes: [], already_moving: [], moved: [], device_errors: [], effects: [],
    query: null, refusals: {},
    sent_nothing: false, restart: null, drops: [], summary: "", answer: null, ...extra,
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
    command("power_on", { suggested: true }),
    command("query_power", { query: true, query_for: "power" }),
    command("query_input", { query: true, needs_input: true }),
    command("set_volume", { needs_input: true, suggested: true, help: "Set the master level" }),
  ];

  it("lists every command, the key ones first, each part in the driver's order", () => {
    expect(commandOrder(catalog).map((c) => c.name)).toEqual(
      ["power_on", "set_volume", "query_power", "query_input"],
    );
  });

  it("knows a command not sent yet", () => {
    const tries = [trial({ command: "power_on" })];
    expect(notTried(catalog[0], tries)).toBe(false);
    expect(notTried(catalog[1], tries)).toBe(true);
  });

  it("finds a command by its label, its name or its help", () => {
    const volume = catalog[3];
    expect(commandMatches(volume, "")).toBe(true);
    expect(commandMatches(volume, "MASTER")).toBe(true);
    expect(commandMatches(volume, "set_vol")).toBe(true);
    expect(commandMatches(volume, "input")).toBe(false);
  });

  it("counts only the queries that can run without a value", () => {
    expect(batchableQueries(catalog)).toBe(1);
  });
});

describe("where each command stands", () => {
  it("is not tried until it is sent, then waits for the person's answer", () => {
    expect(commandStatus([])).toEqual({ key: "not_tried", text: "Not tried yet" });
    expect(commandStatus([trial({ status: "watching" })]).text).toBe("Watching");
    expect(commandStatus([trial()])).toEqual({ key: "waiting", text: "Waiting for your answer" });
  });

  it("takes the word from the answer to its last try", () => {
    const yes = trial({ answer: { answer: "yes", note: "", at: 1 } });
    const no = trial({ number: 2, answer: { answer: "no", note: "", at: 2 } });
    expect(commandStatus([yes]).text).toBe("Worked");
    expect(commandStatus([yes, no])).toEqual({ key: "no", text: "Did not work" });
    expect(commandStatus([no, trial({ number: 3 })]).key).toBe("waiting");
  });

  it("says when it was refused or sent with the status queries", () => {
    expect(commandStatus([trial({ error: "Level must be at most 100." })]).text).toBe("Not accepted");
    expect(commandStatus([trial({ batch: true })]).text).toBe("Sent with the status queries");
  });

  it("counts the commands tried and answered", () => {
    const catalog = [command("power_on"), command("set_volume"), command("query_power")];
    expect(commandProgress(catalog, [])).toBe("3 commands, none tried yet");
    const tries = [
      trial({ command: "power_on", answer: { answer: "yes", note: "", at: 1 } }),
      trial({ number: 2, command: "set_volume" }),
      trial({ number: 3, command: "power_on" }),
      trial({ number: 4, command: "set_volume", answer: { answer: "partly", note: "", at: 4 } }),
    ];
    // power_on's last try is unanswered, so one of the two is answered.
    expect(commandProgress(catalog, tries)).toBe("2 of 3 commands tried, 1 answered");
    expect(commandProgress([], tries)).toBe("");
  });

  it("counts the key commands tried apart", () => {
    const catalog = [
      command("power_on", { suggested: true }),
      command("mute", { suggested: true }),
      command("query_power"),
    ];
    expect(commandProgress(catalog, [])).toBe("3 commands, none tried yet · Suggested: 0 of 2 tried");
    const tries = [
      trial({ command: "query_power" }),
      trial({ number: 2, command: "power_on", answer: { answer: "yes", note: "", at: 2 } }),
    ];
    expect(commandProgress(catalog, tries)).toBe(
      "2 of 3 commands tried, 1 answered · Suggested: 1 of 2 tried",
    );
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

  it("takes the server's sentence once the window has closed", () => {
    expect(trialOutcome(trial({ summary: "volume is now 40, as the driver says it should be." })))
      .toBe("Volume is now 40, as the driver says it should be.");
    // While it is watched the counts are what there is.
    expect(trialOutcome(trial({ status: "watching", summary: "" })))
      .toBe("Sent 1 message; the device sent 1 reply.");
  });

  it("gives the refusal when the command was not accepted", () => {
    expect(trialOutcome(trial({ error: "'set_volume': 'level' must be at most 100, got 150" })))
      .toBe("Not accepted: 'set_volume': 'level' must be at most 100, got 150");
  });

  it("writes parameters the way a line reads them, each named as its field is", () => {
    expect(paramsText({ level: 40, input_id: "hdmi1" })).toBe("Level 40, Input ID hdmi1");
    expect(
      paramsText({ lvl: 40, gain_db: -6 }, { lvl: { type: "integer", label: "Level" } }),
    ).toBe("Level 40, Gain (dB) -6");
    expect(paramsText({})).toBe("");
  });
});

describe("before a command is sent", () => {
  it("sends at once when the driver asks nothing", () => {
    expect(sendWarning(command("power_on"))).toBe("");
  });

  it("says the driver's confirmation and the restart first", () => {
    expect(sendWarning(command("reboot", { confirm: "The device restarts.", restarts_device_for: 60 })))
      .toBe(
        "The device restarts. This command restarts the device: the driver says it is off the " +
          "network for up to 60 seconds. The audit times how long it takes to come back.",
      );
  });

  it("writes a change the way a line reads it", () => {
    const mute = { key: "mute", label: "Mute", first: false, last: true, times: 1, already_moving: false };
    const level = { key: "level", label: "Level", first: 3, last: 9, times: 7, already_moving: true };
    expect(movedText(mute)).toBe("Mute: No to Yes");
    expect(movedText({ ...mute, first: null, last: "hdmi1" })).toBe("Mute: not reported to hdmi1");
    expect(movingText(level)).toBe("Level (7 times)");
    expect(movingText({ ...level, times: 1 })).toBe("Level");
    // What the command moved, apart from what was already changing.
    const online = { key: "online", label: "Online", first: true, last: true, times: 2, already_moving: false, went_back: true };
    const { moved, moving, wentBack } = movedParts(trial({ moved: [mute, level, online] }));
    expect(moved).toEqual([mute]);
    expect(moving).toEqual([level]);
    // One that ended where it began is set apart, not counted as the command's.
    expect(wentBack).toEqual([online]);
    expect(movedParts(trial({ moved: undefined as never })).moved).toEqual([]);
  });
});

describe("following the commands step", () => {
  it("merges an update by trial number and keeps the list it did not send", () => {
    const catalog = [command("power_on")];
    const before = {
      catalog, picker_state: {}, batch: null, current: 1, trials: [trial({ status: "watching" })],
      changed: [],
    };
    const done = trial({ status: "done", summary: "power is now true." });
    const after = mergeCommands(before, { batch: null, current: null, trials: [done] });
    expect(after.catalog).toBe(catalog);
    expect(after.trials).toEqual([done]);
    expect(after.current).toBeNull();
    const next = mergeCommands(after, { current: 2, trials: [trial({ number: 2 })] });
    expect(next.trials.map((t) => t.number)).toEqual([1, 2]);
    expect(mergeCommands(undefined, { catalog }).catalog).toBe(catalog);
  });

  const commands: AuditCommands = {
    catalog: [], picker_state: {}, batch: null, current: 1, trials: [trial()], changed: [],
  };

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
    expect(after!.runs![0].commands).toEqual(commands);
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
    // The same command twice: two sent, one command.
    expect(driverLines([d]).at(-1)).toEqual({ label: "Commands sent", value: "2, 1 different, 1 not accepted" });
    const found = {
      ...d,
      commands: {
        trials: [
          trial({ label: "Power On", sent_nothing: true }),
          trial({
            number: 2, label: "Reboot",
            restart: { declared_seconds: 60, went_away_after: 4, back_after: 48.4, away_for: 44.4,
              within_declared: true },
          }),
        ],
      },
    } as unknown as AuditReportDriver;
    expect(driverLines([found]).slice(-2)).toEqual([
      { label: "Sent nothing", value: "Power On: the driver said it succeeded, but nothing was sent" },
      { label: "Restart", value: "Reboot: back 48.4 s after the command (the driver says up to 60 s)" },
    ]);
  });
});

describe("did the device do it", () => {
  it("counts the answers in the buttons' order", () => {
    const at = 1;
    expect(answerCounts([
      trial({ answer: { answer: "no", note: "", at } }),
      trial({ answer: { answer: "yes", note: "", at } }),
      trial({ answer: { answer: "yes", note: "", at } }),
      trial({ answer: { answer: "cant_tell", note: "", at } }),
      trial(),
    ])).toBe("2 yes, 1 no, 1 could not tell");
    expect(answerCounts([trial()])).toBe("");
  });
});

describe("what changed", () => {
  it("says what each value was, is, and which command moved it", () => {
    expect(changedText({
      key: "input", label: "Input", before: null, now: "hdmi2", by: { number: 3, label: "Set Input" },
      on_its_own: false,
    })).toBe("Input: not reported before, hdmi2 now (after 3. Set Input)");
    expect(changedText({
      key: "power", label: "Power", before: false, now: true, by: null, on_its_own: false,
    })).toBe("Power: No before, Yes now");
  });

  it("leaves what changes without the audit out of the report's count", () => {
    const d = {
      run: 0, driver: { id: "acme", name: "Acme", version: "1.0.0", modified: false },
      attempts: [{
        status: "done", error: "", started_at: 1, connected_at: 1.5, declared: 2, reported: 2,
        offline: null, contract: { counts: {} }, unprompted_replies: { count: 0 },
        traffic: { count: 4, not_captured: false },
      }],
      commands: {
        trials: [trial()],
        changed: [
          { key: "mute", label: "Mute", before: false, now: true, by: null, on_its_own: false },
          { key: "level", label: "Level", before: 3, now: 9, by: null, on_its_own: true },
        ],
      },
    } as unknown as AuditReportDriver;
    expect(driverLines([d]).find((l) => l.label === "Values changed")?.value).toBe("Mute");
  });

  it("keeps the last list when an update does not carry one", () => {
    const changed = [{ key: "power", label: "Power", before: false, now: true, by: null, on_its_own: false }];
    const before = {
      catalog: [], picker_state: {}, batch: null, current: null, trials: [], changed,
    };
    expect(mergeCommands(before, { current: 1 }).changed).toBe(changed);
    expect(mergeCommands(before, { changed: [] }).changed).toEqual([]);
  });
});

describe("what a command sets reads now", () => {
  const table = {
    variables: [{ name: "volume", label: "Volume", value: 20, reported: true }],
    children: { output: { "01": { mute: true } } },
    child_labels: {},
    settings: [],
  } as unknown as AuditListen["status_table"];
  const cmd = (sets: Record<string, unknown>, params: Record<string, unknown> = {}) =>
    ({ sets, params } as unknown as AuditCommandInfo);
  const output = { child_id: { type: "child_id", child_type: "output" } };

  it("says the value to put back", () => {
    expect(nowReading(cmd({ volume: "{level}" }), {}, table)).toBe("Now: Volume 20");
    expect(nowReading(cmd({ mute: true }, output), { child_id: "1" }, table)).toBe(
      "Now: output 01 mute Yes",
    );
    expect(nowReading(cmd({}), {}, table)).toBe("");
    expect(nowReading(cmd({ input: "{source}" }), {}, table)).toBe("");
  });

  it("reads the channel from whichever parameter picks it", () => {
    // An amplifier names its child parameter "channel", not "child_id", and
    // has an input 01 beside its channel 01 (seen on the bench: no Now: line).
    const amp = {
      ...table,
      children: { input: { "01": { mute: false } }, channel: { "01": { mute: true } } },
    } as unknown as AuditListen["status_table"];
    const mute = cmd({ mute: true }, {
      channel: { type: "child_id", child_type: "channel", label: "Channel" },
    });
    expect(nowReading(mute, { channel: "1" }, amp)).toBe("Now: channel 01 mute Yes");
    expect(nowReading(mute, {}, amp)).toBe("");
  });
});

describe("a command's traffic as the step lists it", () => {
  const entry = (seq: number, text: string) => ({
    seq, t: seq, direction: seq === 1 ? "tx" : "rx", channel: "tcp", hex: "", text,
  }) as AuditCommandTrial["traffic"]["entries"][number];

  it("lists every entry when nothing was left out", () => {
    const rows = trafficRows({ sent: 1, received: 1, entries: [entry(1, "MUTE 1"), entry(2, "OK")] });
    expect(rows.map((r) => (r.kind === "entry" ? r.entry.text : r.text))).toEqual(["MUTE 1", "OK"]);
  });

  it("says where the server left entries out, between the command's own and the newest", () => {
    // A poll that landed in Input Mute Off's window pushed the command out of
    // the newest 40; the step now keeps the first ones and says the rest are
    // in the report.
    const rows = trafficRows({
      sent: 30, received: 37, left_out: 27, left_out_after: 2,
      entries: [entry(1, "SICM 1,0"), entry(2, "SICM ACK"), entry(60, "GOCM 0,0")],
    });
    expect(rows.map((r) => (r.kind === "entry" ? r.entry.text : r.text))).toEqual([
      "SICM 1,0", "SICM ACK", "27 more messages in between; the report has every one.", "GOCM 0,0",
    ]);
    expect(trafficRows({ sent: 1, received: 41, left_out: 1, left_out_after: 20, entries: [] })[0])
      .toEqual({ kind: "gap", text: "1 more message in between; the report has every one." });
  });
});
