import { describe, expect, it } from "vitest";
import type {
  AuditReportDriver,
  AuditSessionState,
  AuditSettingInfo,
  AuditSettingTrial,
  AuditSettings,
} from "../../../api/auditClient";
import {
  applyAuditMessage,
  driverLines,
  mergeSettings,
  needsPuttingBack,
  settingsCount,
  suggestedSetting,
} from "./auditHelpers";

function trial(extra: Partial<AuditSettingTrial> = {}): AuditSettingTrial {
  return {
    number: 1, key: "device_name", label: "Device name", original: "Lobby", value: "Boardroom",
    started_at: 1, status: "done",
    write: { at: 1, error: "", confirmed: true, value: "Boardroom", after: 0.4 },
    restore: { at: 2, error: "", confirmed: true, value: "Lobby", after: 0.3, automatic: false },
    summary: "Wrote Device name = Boardroom: the device reported it back after 0.4 s.",
    ...extra,
  };
}

describe("device settings", () => {
  it("counts the settings tried, each once", () => {
    const catalog = [{ key: "a" }, { key: "b" }, { key: "c" }] as AuditSettingInfo[];
    expect(settingsCount({ catalog, trials: [] })).toBe("3 settings, none tried yet");
    expect(settingsCount({ catalog, trials: [{ key: "a" }, { key: "a" }, { key: "c" }] }))
      .toBe("2 of 3 settings tried");
    expect(settingsCount({ catalog: catalog.slice(0, 1), trials: [] })).toBe("1 setting, none tried yet");
  });
  it("merges an update by trial number and keeps the list", () => {
    const catalog = [{
      key: "device_name", label: "Device name", help: "", definition: { type: "string" },
      state_key: "device_name", value: "Lobby", can_write: true, reason: "",
    }];
    const before: AuditSettings = { catalog, current: 1, trials: [trial({ status: "writing" })] };
    const done = trial();
    const after = mergeSettings(before, { current: null, trials: [done] });
    expect(after.trials).toEqual([done]);
    expect(after.current).toBeNull();
    expect(mergeSettings(after, { catalog: [] }).catalog).toEqual([]);
  });

  it("suggests a name-like setting first, else the first the audit can write", () => {
    const setting = (key: string, type: string, can_write = true): AuditSettingInfo => ({
      key, label: key, help: "", definition: { type } as AuditSettingInfo["definition"],
      state_key: key, value: "x", can_write, reason: can_write ? "" : "cannot read",
    });
    expect(suggestedSetting([setting("standby", "enum"), setting("name", "string")])).toBe("name");
    expect(suggestedSetting([setting("name", "string", false), setting("standby", "enum")]))
      .toBe("standby");
    expect(suggestedSetting([setting("name", "string", false)])).toBeNull();
    expect(suggestedSetting([])).toBeNull();
  });

  it("offers Put it back only when the write landed and the original did not come back", () => {
    expect(needsPuttingBack(trial())).toBe(false);
    expect(needsPuttingBack(trial({
      restore: { at: 2, error: "", confirmed: false, value: "Boardroom", after: 5 },
    }))).toBe(true);
    expect(needsPuttingBack(trial({ restore: null }))).toBe(true);
    // A write that never happened has nothing to put back.
    expect(needsPuttingBack(trial({
      write: { at: 1, error: "The device is not connected.", confirmed: false, value: null },
      restore: null,
    }))).toBe(false);
    // One that failed after sending bytes, or that the audit ended mid read-back, may have.
    expect(needsPuttingBack(trial({
      write: { at: 1, error: "Timed out.", confirmed: false, value: null, sent: true },
      restore: null,
    }))).toBe(true);
    expect(needsPuttingBack(trial({
      write: { at: 1, error: "", confirmed: false, value: null, interrupted: true },
      restore: null,
    }))).toBe(true);
    expect(needsPuttingBack(trial({ status: "restoring", restore: null }))).toBe(false);
    expect(needsPuttingBack(undefined)).toBe(false);
  });

  it("follows a run's settings from a message", () => {
    const session: AuditSessionState = {
      session_id: "abc123", status: "active", target: { address: "10.0.0.5", ip: "10.0.0.5" },
      options: { extended: false, snmp_communities: 0 }, started_at: 1, ended_at: null,
      steps: [], paused: [], report_name: null, tester: {}, check: null,
      runs: [{ index: 0, choice: {} as never, started_at: 1, finished_at: null, active: true,
        connection: null }],
    };
    const { session: after } = applyAuditMessage(session, [], {
      type: "audit.settings", session_id: "abc123", run: 0,
      settings: { catalog: [], current: 1, trials: [trial({ status: "writing" })] },
    });
    expect(after!.runs![0].settings!.current).toBe(1);
  });

  it("counts the settings written in the report's summary", () => {
    const d = {
      run: 0, driver: { id: "acme", name: "Acme", version: "1.0.0", modified: false },
      attempts: [{
        status: "done", error: "", started_at: 1, connected_at: 1.5, declared: 2, reported: 2,
        offline: null, contract: { counts: {} }, unprompted_replies: { count: 0 },
        traffic: { count: 4, not_captured: false },
      }],
      settings: { trials: [trial(), trial({ number: 2, restore: null })] },
    } as unknown as AuditReportDriver;
    expect(driverLines([d]).find((l) => l.label === "Settings written")).toEqual({
      label: "Settings written", value: "2 (2 read back, 1 put back)",
    });
  });
});
