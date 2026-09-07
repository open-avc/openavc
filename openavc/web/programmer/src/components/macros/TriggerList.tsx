/**
 * Trigger card list with add menu, expand/collapse, enable toggle, delete.
 * Placed above the steps section in MacroEditor.
 */
import { useState, useEffect, useRef } from "react";
import { Plus, Trash2, ChevronRight, Eye, EyeOff, Clock, Loader2, Play, AlertTriangle, XCircle } from "lucide-react";
import type { TriggerConfig, MacroConfig, DeviceConfig } from "../../api/types";
import { TRIGGER_TYPES, getTriggerType, generateTriggerId } from "./triggerHelpers";
import { issuesAt, issueLabel, type MacroIssue } from "./macroLint";
import { TriggerEditor } from "./TriggerEditor";
import { isFailedRun } from "./triggerRuns";
import { useLogStore } from "../../store/logStore";
import type { TriggerRun } from "../../store/logStore";
import { showSuccess, showError, showInfo } from "../../store/toastStore";
import * as api from "../../api/restClient";

/** What the card says about a trigger's last automatic fire.
 *
 *  A trigger that errors every night used to be indistinguishable from one
 *  doing its job: the card flashed the same colour and the only stored tell,
 *  `last_fired`, is set BEFORE the macro runs, so failing looked fresh.
 *
 *  A clean run says so too rather than showing nothing, because "it ran and it
 *  worked" is the answer somebody opens this card to get. */
const RUN_LABELS: Record<TriggerRun["outcome"], string> = {
  completed: "Ran OK",
  failed: "Last run failed",
  error: "Last run failed",
  cancelled: "Last run cancelled",
  skipped: "Last run skipped",
};

/** "4m ago" — how long since it last fired, in the coarsest unit that still
 *  says something. Precision past the minute is noise here. */
function timeAgo(seconds: number): string {
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

/** What the person who pressed Fire now is told. The button reported nothing
 *  at all for a macro whose every step failed. */
const FIRE_NOW_RESULTS: Record<string, { message: string; kind: "ok" | "bad" | "info" }> = {
  completed: { message: "Trigger fired. The macro completed.", kind: "ok" },
  failed: {
    message: "Trigger fired, but the macro failed. Open the macro to see which step.",
    kind: "bad",
  },
  cancelled: {
    message: "Trigger fired. Something cancelled the macro part-way through.",
    kind: "info",
  },
  skipped: {
    message:
      "Trigger fired, but the macro did not start — its own Overlap or Cooldown setting refused the run.",
    kind: "info",
  },
  running: { message: "Trigger fired. The macro is still running.", kind: "info" },
};

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
      const tp = useLogStore.getState().triggerPending;
      setPendingTriggers(tp);
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
            const runLabel = lastRun ? RUN_LABELS[lastRun.outcome] : null;
            // THE shared rule, so this card and the dashboard cannot disagree.
            const runFailed = isFailedRun(lastRun?.outcome);

            return (
              <div
                key={trigger.id}
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
                          lastRun.firedAt ? `Fired ${timeAgo(lastRun.firedAt)}` : null,
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
                      {!runFailed && lastRun.firedAt ? ` ${timeAgo(lastRun.firedAt)}` : ""}
                    </span>
                  )}
                  {/* Will not fire as built */}
                  {lintIssues.length > 0 && (
                    <span
                      title={lintIssues.map((x) => `${issueLabel(x)}: ${x.message}`).join("\n")}
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
                    <button
                      onClick={async () => {
                        try {
                          // It answered the same whatever the macro did, so
                          // pressing this on a broken trigger looked like it
                          // had worked.
                          const { status } = await api.testTrigger(trigger.id);
                          const result = FIRE_NOW_RESULTS[status];
                          if (!result) {
                            showInfo("Trigger fired.");
                          } else if (result.kind === "ok") {
                            showSuccess(result.message);
                          } else if (result.kind === "bad") {
                            showError(result.message);
                          } else {
                            showInfo(result.message);
                          }
                        } catch (e) {
                          showError("Could not fire the trigger.");
                          console.error("Fire trigger failed:", e);
                        }
                      }}
                      style={{ ...iconBtnStyle, color: "var(--accent)" }}
                      title="Fire now (bypasses conditions)"
                    >
                      <Play size={14} />
                    </button>
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
                          {x.message}
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
