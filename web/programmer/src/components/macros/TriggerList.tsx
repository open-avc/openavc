/**
 * Trigger card list with add menu, expand/collapse, enable toggle, delete.
 * Placed above the steps section in MacroEditor.
 */
import { useState, useEffect, useRef } from "react";
import { Plus, Trash2, ChevronRight, Eye, EyeOff } from "lucide-react";
import type { TriggerConfig, MacroConfig, DeviceConfig } from "../../api/types";
import { TRIGGER_TYPES, getTriggerType, generateTriggerId } from "./triggerHelpers";
import { TriggerEditor } from "./TriggerEditor";
import { useLogStore } from "../../store/logStore";

interface TriggerListProps {
  triggers: TriggerConfig[];
  devices: DeviceConfig[];
  allMacros: MacroConfig[];
  onUpdate: (triggers: TriggerConfig[]) => void;
}

export function TriggerList({ triggers, devices, allMacros, onUpdate }: TriggerListProps) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [recentlyFired, setRecentlyFired] = useState<Set<string>>(new Set());
  const addMenuRef = useRef<HTMLDivElement>(null);

  // Poll for trigger.fired events every 2s instead of subscribing to logEntries
  // (avoids re-rendering on every log entry)
  const lastCheckedRef = useRef(0);
  useEffect(() => {
    const interval = setInterval(() => {
      const entries = useLogStore.getState().logEntries;
      if (entries.length === lastCheckedRef.current) return;
      // Only check new entries since last poll
      const newEntries = entries.slice(lastCheckedRef.current);
      lastCheckedRef.current = entries.length;
      for (const entry of newEntries.slice(-10)) {
        if (entry.message?.includes("[TRIGGER]") && entry.message?.includes("fired")) {
          const match = entry.message.match(/\btrg_\w+/);
          if (match) {
            setRecentlyFired((prev) => {
              const next = new Set(prev);
              next.add(match[0]);
              return next;
            });
            setTimeout(() => {
              setRecentlyFired((prev) => {
                const next = new Set(prev);
                next.delete(match[0]);
                return next;
              });
            }, 1500);
          }
        }
      }
    }, 2000);
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
          No triggers — this macro can only run manually or from a UI button.
          <br />
          Add a trigger to automate it with schedules, state changes, or events.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          {triggers.map((trigger, i) => {
            const typeInfo = getTriggerType(trigger.type);
            const isFired = recentlyFired.has(trigger.id);

            return (
              <div
                key={trigger.id}
                style={{
                  border: `1px solid ${
                    isFired ? typeInfo?.color ?? "var(--accent)" : "var(--border-color)"
                  }`,
                  borderRadius: "var(--border-radius)",
                  background: isFired
                    ? `${typeInfo?.color ?? "var(--accent)"}11`
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
