/**
 * Trigger card list with add menu, expand/collapse, enable toggle, delete.
 * Placed above the steps section in MacroEditor.
 */
import { useState, useEffect, useRef } from "react";
import { Plus, Trash2, ChevronRight, Eye, EyeOff, Clock, Loader2, Play, AlertTriangle } from "lucide-react";
import type { TriggerConfig, MacroConfig, DeviceConfig } from "../../api/types";
import { TRIGGER_TYPES, getTriggerType, generateTriggerId } from "./triggerHelpers";
import { issuesAt, issueLabel, type MacroIssue } from "./macroLint";
import { TriggerEditor } from "./TriggerEditor";
import { useLogStore } from "../../store/logStore";
import * as api from "../../api/restClient";

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
            letterSpacing: "var(--tracking-wide)",
            fontWeight: "var(--font-weight-semibold)",
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
              gap: "var(--space-xs)",
              padding: "var(--space-2xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "transparent",
              color: "var(--text-secondary)",
              fontSize: "var(--font-size-xs)",
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
                marginTop: "var(--space-xs)",
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
                      marginTop: "var(--space-xs)",
                    }}
                  />
                  <div>
                    <div style={{ fontWeight: "var(--font-weight-medium)", color: "var(--text-primary)" }}>
                      {t.label}
                    </div>
                    <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>
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
            fontSize: "var(--font-size-sm)",
            border: "1px dashed var(--border-color)",
            borderRadius: "var(--border-radius)",
            lineHeight: "var(--line-base)",
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

            return (
              <div
                key={trigger.id}
                style={{
                  border: `1px solid ${
                    isFired ? typeInfo?.color ?? "var(--accent)"
                    : pending ? "#f59e0b"
                    : "var(--border-color)"
                  }`,
                  borderRadius: "var(--border-radius)",
                  background: isFired
                    ? `${typeInfo?.color ?? "var(--accent)"}11`
                    : pending
                    ? "rgba(245,158,11,0.06)"
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
                      fontSize: "var(--font-size-xs)",
                      fontWeight: "var(--font-weight-semibold)",
                      color: "#fff",
                      background: typeInfo?.color ?? "#666",
                      padding: "var(--space-2xs) var(--space-sm)",
                      borderRadius: "var(--border-radius)",
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
                        gap: "var(--space-xs)",
                        fontSize: "var(--font-size-2xs)",
                        fontWeight: "var(--font-weight-semibold)",
                        color: "#f59e0b",
                        background: "rgba(245,158,11,0.15)",
                        padding: "0 var(--space-xs)",
                        borderRadius: "var(--border-radius)",
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
                        fontSize: "var(--font-size-2xs)",
                        color: "var(--text-muted)",
                        background: "var(--bg-hover)",
                        padding: "0 var(--space-xs)",
                        borderRadius: "var(--border-radius)",
                        flexShrink: 0,
                      }}
                    >
                      {trigger.conditions!.length} cond
                    </span>
                  )}
                  <div
                    style={{ display: "flex", gap: "var(--space-2xs)", flexShrink: 0 }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={async () => {
                        try { await api.testTrigger(trigger.id); } catch (e) { console.error("Fire trigger failed:", e); }
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
                      fontSize: "var(--font-size-sm)",
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
  padding: "var(--space-2xs)",
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};
