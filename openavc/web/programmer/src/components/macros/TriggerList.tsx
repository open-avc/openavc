/**
 * Trigger card list with add menu, expand/collapse, enable toggle, delete.
 * Placed above the steps section in MacroEditor.
 */
import { useState, useEffect, useRef } from "react";
import { Plus, Trash2, ChevronRight, Eye, EyeOff, Clock, Loader2, Play, AlertTriangle, XCircle } from "lucide-react";
import type { TriggerConfig, MacroConfig, DeviceConfig } from "../../api/types";
import { TRIGGER_TYPES, getTriggerType, generateTriggerId, triggerCardKeys } from "./triggerHelpers";
import { issuesAt, issueLabel, issueText, type MacroIssue } from "./macroLint";
import { TriggerEditor } from "./TriggerEditor";
import { cardRun, isFailedRun } from "./triggerRuns";
import { didNotStart } from "./runAnswer";
import { SkipLine } from "./SkipLine";
import { useLogStore } from "../../store/logStore";
import type { SkipNote, TriggerRun } from "../../store/logStore";
import { showSuccess, showError, showInfo } from "../../store/toastStore";
import * as api from "../../api/restClient";
import type { FireNowAnswer } from "../../api/restClient";

/** What the card says about a trigger's last automatic fire.
 *
 *  A trigger that errors every night used to be indistinguishable from one
 *  doing its job: the card flashed the same colour and the only stored tell,
 *  `last_fired`, is set BEFORE the macro runs, so failing looked fresh.
 *
 *  A clean run says so too rather than showing nothing, because "it ran and it
 *  worked" is the answer somebody opens this card to get. A skipped run the
 *  server gave a reason for says the reason instead (`runSkip`), on the
 *  card's skip line. */
const RUN_LABELS: Record<TriggerRun["outcome"], string> = {
  completed: "Ran OK",
  failed: "Last run failed",
  error: "Last run failed",
  cancelled: "Last run cancelled",
  skipped: "Last run skipped",
};

/** "4m ago" — how long since its last run, in the coarsest unit that still
 *  says something. Precision past the minute is noise here. */
function timeAgo(seconds: number): string {
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

/** How the macro's run went, said after what it was handed. The button
 *  reported nothing at all for a macro whose every step failed. A macro that
 *  refused the run says why in the server's words (`fireNowResult`). */
const FIRE_NOW_ENDINGS: Record<string, { message: string; kind: "ok" | "bad" | "info" }> = {
  completed: { message: "The macro completed.", kind: "ok" },
  failed: { message: "The macro failed. Open the macro to see which step.", kind: "bad" },
  cancelled: { message: "Something cancelled the macro part-way through.", kind: "info" },
  running: { message: "The macro is still running.", kind: "info" },
};

/** What the person who pressed Fire now is told: the server's sentence on
 *  what the macro was handed as `$trigger.*` (`fired_with`), then how the
 *  run went. */
function fireNowResult(answer: FireNowAnswer): { message: string; kind: "ok" | "bad" | "info" } {
  const lead = answer.fired_with ?? "Trigger fired.";
  if (answer.status === "skipped") {
    return { message: `${lead} ${didNotStart(answer)}`, kind: "info" };
  }
  const ending = FIRE_NOW_ENDINGS[answer.status];
  return ending ? { message: `${lead} ${ending.message}`, kind: ending.kind } : { message: lead, kind: "info" };
}

const SHOW_RESULT = { ok: showSuccess, bad: showError, info: showInfo };

/** Long enough to read what the macro was handed as well as how it ran. */
const FIRE_NOW_TOAST_MS = 10000;

/** Fire now: fire the trigger, then say how its macro's run went. It answered
 *  the same whatever the macro did, so pressing it on a broken trigger looked
 *  like it had worked. */
async function fireNow(triggerId: string): Promise<void> {
  try {
    const result = fireNowResult(await api.testTrigger(triggerId));
    SHOW_RESULT[result.kind](result.message, FIRE_NOW_TOAST_MS);
  } catch (e) {
    showError("Could not fire the trigger.");
    console.error("Fire trigger failed:", e);
  }
}

/** Fire now, turned off on a trigger that never fires: its id is another
 *  trigger's, and firing the id would run that one. */
function FireNowButton({ triggerId, neverFires }: { triggerId: string; neverFires: boolean }) {
  return (
    <button
      onClick={() => fireNow(triggerId)}
      disabled={neverFires}
      style={{ ...iconBtnStyle, color: "var(--accent)", opacity: neverFires ? 0.4 : 1 }}
      title={neverFires
        ? "Fire now is off for a trigger that never fires. Delete it and add it again."
        : "Fire now (bypasses conditions)"}
    >
      <Play size={14} />
    </button>
  );
}

interface TriggerListProps {
  triggers: TriggerConfig[];
  /** The whole macro's issues; this list reads the trigger half. A cron with
   *  the wrong field count or an operator name nothing knows is exactly as
   *  silent as a half-built step. */
  issues?: MacroIssue[];
  devices: DeviceConfig[];
  allMacros: MacroConfig[];
  onUpdate: (triggers: TriggerConfig[]) => void;
}

export function TriggerList({ triggers, issues, devices, allMacros, onUpdate }: TriggerListProps) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [pendingTriggers, setPendingTriggers] = useState<Record<string, { reason: string; waitSeconds?: number; queuePosition?: number }>>({});
  // Why each trigger's latest fire did not go ahead. Read on the tick below,
  // not subscribed: a busy trigger can be skipped many times a second.
  const [skips, setSkips] = useState<Record<string, SkipNote>>(() => useLogStore.getState().triggerSkips);
  const addMenuRef = useRef<HTMLDivElement>(null);

  // "Just fired" highlights come straight from the trigger.fired WS message,
  // which useWebSocket records into this store slice (and auto-clears after the
  // flash). Subscribing to this one slice re-renders only when a trigger fires,
  // not on every log entry.
  const recentlyFired = useLogStore((s) => s.recentlyFired);

  // How each trigger's last fire ENDED, seeded from the server by
  // useTriggerRuns and kept current by trigger.completed. Outlives the flash
  // above, which is the point: the failure happened at 6am.
  const triggerRuns = useLogStore((s) => s.triggerRuns);

  // Poll for trigger pending state every 1s
  useEffect(() => {
    const interval = setInterval(() => {
      const { triggerPending, triggerSkips } = useLogStore.getState();
      setPendingTriggers(triggerPending);
      setSkips(triggerSkips);
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  // Close add menu on outside click
  useEffect(() => {
    if (!showAddMenu) return;
    const handler = (e: MouseEvent) => {
      if (addMenuRef.current && !addMenuRef.current.contains(e.target as Node)) {
        setShowAddMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showAddMenu]);

  const updateTrigger = (index: number, updated: TriggerConfig) => {
    const copy = [...triggers];
    copy[index] = updated;
    onUpdate(copy);
  };

  const deleteTrigger = (index: number) => {
    onUpdate(triggers.filter((_, i) => i !== index));
    if (expandedIdx === index) setExpandedIdx(null);
    else if (expandedIdx !== null && expandedIdx > index) setExpandedIdx(expandedIdx - 1);
  };

  const toggleEnabled = (index: number) => {
    const copy = [...triggers];
    copy[index] = { ...copy[index], enabled: !copy[index].enabled };
    onUpdate(copy);
  };

  const addTrigger = (type: string) => {
    const typeInfo = getTriggerType(type);
    if (!typeInfo) return;
    const newTrigger: TriggerConfig = {
      id: generateTriggerId(),
      ...typeInfo.defaults(),
    } as TriggerConfig;
    onUpdate([...triggers, newTrigger]);
    setExpandedIdx(triggers.length);
    setShowAddMenu(false);
  };

  const cardKeys = triggerCardKeys(triggers);

  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-sm)",
        }}
      >
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
          }}
        >
          Triggers {triggers.length > 0 && `(${triggers.length})`}
        </div>
        <div style={{ position: "relative" }} ref={addMenuRef}>
          <button
            onClick={() => setShowAddMenu(!showAddMenu)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 3,
              padding: "2px 8px",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "transparent",
              color: "var(--text-secondary)",
              fontSize: 11,
              cursor: "pointer",
            }}
          >
            <Plus size={12} /> Add Trigger
          </button>

          {/* Add menu dropdown */}
          {showAddMenu && (
            <div
              style={{
                position: "absolute",
                top: "100%",
                right: 0,
                marginTop: 4,
                minWidth: 280,
                background: "var(--bg-surface)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                zIndex: 10,
              }}
            >
              {TRIGGER_TYPES.map((t) => (
                <div
                  key={t.type}
                  onClick={() => addTrigger(t.type)}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "var(--space-sm)",
                    padding: "var(--space-sm) var(--space-md)",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                  }}
                  onMouseEnter={(e) =>
                    ((e.currentTarget as HTMLElement).style.background = "var(--bg-hover)")
                  }
                  onMouseLeave={(e) =>
                    ((e.currentTarget as HTMLElement).style.background = "transparent")
                  }
                >
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: t.color,
                      flexShrink: 0,
                      marginTop: 5,
                    }}
                  />
                  <div>
                    <div style={{ fontWeight: 500, color: "var(--text-primary)" }}>
                      {t.label}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 1 }}>
                      {t.description}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Trigger cards */}
      {triggers.length === 0 ? (
        <div
          style={{
            padding: "var(--space-md)",
            textAlign: "center",
            color: "var(--text-muted)",
            fontSize: 12,
            border: "1px dashed var(--border-color)",
            borderRadius: "var(--border-radius)",
            lineHeight: 1.5,
          }}
        >
          No triggers. This macro can only run manually or from a UI button.
          <br />
          Add a trigger to automate it with schedules, state changes, or events.
          <br /><br />
          <a href="https://docs.openavc.com/macros-and-triggers#triggers" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
            Learn about triggers
          </a>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          {triggers.map((trigger, i) => {
            const typeInfo = getTriggerType(trigger.type);
            const isFired = trigger.id in recentlyFired;
            const pending = pendingTriggers[trigger.id];
            const lintIssues = issuesAt(issues, "trigger", i);
            const lastRun = triggerRuns[trigger.id];
            const { outcome: runOutcome, skip } = cardRun(lastRun, skips[trigger.id]);
            const runLabel = runOutcome && RUN_LABELS[runOutcome];
            // THE shared rule, so this card and the dashboard cannot disagree.
            const runFailed = isFailedRun(lastRun?.outcome);

            return (
              <div
                key={cardKeys[i]}
                style={{
                  // A failing trigger keeps a red border once the fire flash
                  // has passed. The flash is what a fire looks like; this is
                  // what it came to.
                  border: `1px solid ${
                    isFired ? typeInfo?.color ?? "var(--accent)"
                    : pending ? "#f59e0b"
                    : runFailed ? "#ef4444"
                    : "var(--border-color)"
                  }`,
                  borderRadius: "var(--border-radius)",
                  background: isFired
                    ? `${typeInfo?.color ?? "var(--accent)"}11`
                    : pending
                    ? "rgba(245,158,11,0.06)"
                    : runFailed
                    ? "rgba(239,68,68,0.06)"
                    : "var(--bg-surface)",
                  transition: "border-color 0.3s, background 0.3s",
                  opacity: trigger.enabled ? 1 : 0.5,
                }}
              >
                {/* Card header */}
                <div
                  onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-sm)",
                    padding: "var(--space-sm) var(--space-md)",
                    cursor: "pointer",
                  }}
                >
                  <ChevronRight
                    size={14}
                    style={{
                      transform: expandedIdx === i ? "rotate(90deg)" : "none",
                      transition: "transform 0.15s",
                      color: "var(--text-muted)",
                      flexShrink: 0,
                    }}
                  />
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: "#fff",
                      background: typeInfo?.color ?? "#666",
                      padding: "1px 6px",
                      borderRadius: 3,
                      textTransform: "uppercase",
                      flexShrink: 0,
                    }}
                  >
                    {typeInfo?.label ?? trigger.type}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      fontSize: "var(--font-size-sm)",
                      color: "var(--text-secondary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {typeInfo?.summary(trigger, devices, allMacros) ?? ""}
                  </span>
                  {/* Pending/queued indicator */}
                  {pending && (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        fontSize: 10,
                        fontWeight: 600,
                        color: "#f59e0b",
                        background: "rgba(245,158,11,0.15)",
                        padding: "0 5px",
                        borderRadius: 3,
                        flexShrink: 0,
                      }}
                      title={
                        pending.reason === "queued"
                          ? `Queued (position ${pending.queuePosition})`
                          : `${pending.reason === "debounce" ? "Debouncing" : "Delaying"} ${pending.waitSeconds ?? ""}s`
                      }
                    >
                      {pending.reason === "queued" ? (
                        <><Clock size={10} /> Queued #{pending.queuePosition}</>
                      ) : (
                        <><Loader2 size={10} style={{ animation: "spin 1s linear infinite" }} /> {pending.reason === "debounce" ? "Debouncing" : "Delaying"}</>
                      )}
                    </span>
                  )}
                  {/* How the last fire ended. Nothing at all until it has
                      fired once: never run is not the same as run and fine. */}
                  {runLabel && !pending && (
                    <span
                      title={
                        [
                          lastRun.error,
                          lastRun.endedAt ? `Fired ${timeAgo(lastRun.endedAt)}` : null,
                        ]
                          .filter(Boolean)
                          .join("\n") || undefined
                      }
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        fontSize: 10,
                        fontWeight: runFailed ? 600 : 400,
                        color: runFailed ? "#ef4444" : "var(--text-muted)",
                        background: runFailed ? "rgba(239,68,68,0.15)" : "transparent",
                        padding: runFailed ? "0 5px" : 0,
                        borderRadius: 3,
                        flexShrink: 0,
                      }}
                    >
                      {runFailed && <XCircle size={10} />}
                      {runLabel}
                      {!runFailed && lastRun.endedAt ? ` ${timeAgo(lastRun.endedAt)}` : ""}
                    </span>
                  )}
                  {/* Will not fire as built */}
                  {lintIssues.length > 0 && (
                    <span
                      title={lintIssues.map((x) => `${issueLabel(x)}: ${issueText(x)}`).join("\n")}
                      style={{ display: "flex", flexShrink: 0, color: "#f59e0b" }}
                    >
                      <AlertTriangle size={14} />
                    </span>
                  )}
                  {/* Conditions indicator */}
                  {(trigger.conditions?.length ?? 0) > 0 && (
                    <span
                      style={{
                        fontSize: 10,
                        color: "var(--text-muted)",
                        background: "var(--bg-hover)",
                        padding: "0 4px",
                        borderRadius: 3,
                        flexShrink: 0,
                      }}
                    >
                      {trigger.conditions!.length} cond
                    </span>
                  )}
                  <div
                    style={{ display: "flex", gap: 2, flexShrink: 0 }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <FireNowButton
                      triggerId={trigger.id}
                      neverFires={lintIssues.some((x) => x.same_id_as)}
                    />
                    <button
                      onClick={() => toggleEnabled(i)}
                      style={iconBtnStyle}
                      title={trigger.enabled ? "Disable trigger" : "Enable trigger"}
                    >
                      {trigger.enabled ? <Eye size={14} /> : <EyeOff size={14} />}
                    </button>
                    <button
                      onClick={() => deleteTrigger(i)}
                      style={{ ...iconBtnStyle, color: "#ef4444" }}
                      title="Delete trigger"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* Why its latest fire did not go ahead, until it next fires. */}
                {skip && (
                  <SkipLine
                    skip={skip}
                    style={{ padding: "0 var(--space-md) var(--space-sm) calc(var(--space-md) + 22px)" }}
                  />
                )}

                {/* What this trigger is missing, beside the fields it is about.
                    Only while it is open -- collapsed, the header's mark and the
                    editor's summary line already say it. */}
                {expandedIdx === i && lintIssues.length > 0 && (
                  <div
                    style={{
                      padding: "var(--space-xs) var(--space-md)",
                      fontSize: 12,
                      color: "#f59e0b",
                      background: "rgba(245,158,11,0.08)",
                      borderTop: "1px solid rgba(245,158,11,0.2)",
                    }}
                  >
                    {lintIssues.map((x, n) => (
                      <div key={n} style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                        <AlertTriangle size={12} style={{ flexShrink: 0 }} />
                        <span>
                          {x.path.includes(".") ? `${issueLabel(x)}: ` : ""}
                          {issueText(x)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Expanded editor */}
                {expandedIdx === i && (
                  <div
                    style={{
                      padding: "var(--space-sm) var(--space-md) var(--space-md)",
                      borderTop: "1px solid var(--border-color)",
                    }}
                  >
                    <TriggerEditor
                      trigger={trigger}
                      onChange={(updated) => updateTrigger(i, updated)}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const iconBtnStyle: React.CSSProperties = {
  display: "flex",
  padding: 2,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};
