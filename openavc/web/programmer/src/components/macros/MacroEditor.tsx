import { useState, useRef, useEffect } from "react";
import {
  Play,
  FileCode,
  Plus,
  Trash2,
  Copy,
  Clipboard,
  ChevronUp,
  ChevronDown,
  ChevronRight,
  Check,
  X,
  Loader2,
  AlertTriangle,
  GripVertical,
  Clock,
  CheckCircle,
  XCircle,
  LayoutTemplate,
  GitBranch,
  HelpCircle,
} from "lucide-react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { MacroConfig, MacroStep, TriggerConfig } from "../../api/types";
import { useLogStore } from "../../store/logStore";
import type { StepError, ConditionalResult, GroupCommandResult, MacroLastRun, StepPathSegment } from "../../store/logStore";
import { StepEditor } from "./StepEditor";
import { TriggerList } from "./TriggerList";
import {
  STEP_TYPES,
  getStepType,
  copyStep,
  getClipboardStep,
  hasClipboardStep,
  STEP_TEMPLATES,
  getMacroCallers,
  getMacroCallees,
  detectCircularDependency,
} from "./macroHelpers";
import { getStepIds, applyStepReorder, adjustExpandedAfterMove } from "./stepDndHelpers";
import {
  usePluginMacroActions,
  findPluginAction,
  defaultPluginActionParams,
  pluginActionSummary,
} from "./pluginMacroActions";
import { CopyButton } from "../shared/CopyButton";
import { issuesAt, issueLabel, issueSummary, type MacroIssue } from "./macroLint";
import * as api from "../../api/restClient";

interface SortableStepItemProps {
  id: string;
  step: MacroStep;
  index: number;
  isFirst: boolean;
  isLast: boolean;
  isExpanded: boolean;
  isActive: boolean;
  stepError: StepError | undefined;
  /** What this step will not do as built, from the platform's own rules. */
  lintIssues: MacroIssue[];
  conditionalResult: ConditionalResult | undefined;
  groupResult: GroupCommandResult | undefined;
  devices: { id: string; name: string }[];
  allMacros: MacroConfig[];
  macroId: string;
  onToggleExpand: () => void;
  onMoveStep: (index: number, direction: -1 | 1) => void;
  onDeleteStep: (index: number) => void;
  onDuplicateStep: (index: number) => void;
  onCopyStep: (index: number) => void;
  onUpdateStep: (index: number, updated: MacroStep) => void;
  activeStepPath?: StepPathSegment[];
}

function SortableStepItem({
  id,
  step,
  index,
  isFirst,
  isLast,
  isExpanded,
  isActive,
  stepError,
  lintIssues,
  conditionalResult,
  groupResult,
  devices,
  allMacros,
  macroId,
  onToggleExpand,
  onMoveStep,
  onDeleteStep,
  onDuplicateStep,
  onCopyStep,
  onUpdateStep,
  activeStepPath,
}: SortableStepItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    border: `1px solid ${isActive ? "var(--accent)" : "var(--border-color)"}`,
    borderRadius: "var(--border-radius)",
    background: isActive ? "rgba(33, 150, 243, 0.08)" : "var(--bg-surface)",
    // Keep above siblings while dragging
    zIndex: isDragging ? 10 : undefined,
    position: "relative",
  };

  const typeInfo = getStepType(step.action);
  const { actions: pluginActionsList } = usePluginMacroActions();
  const pluginAction = !typeInfo ? findPluginAction(pluginActionsList, step.action) : undefined;
  const isMissingPlugin = !typeInfo && !pluginAction && step.action.includes(".");

  const labelText = typeInfo?.label ?? pluginAction?.label ?? (isMissingPlugin ? "Missing" : step.action);
  const labelBg = typeInfo?.color ?? (pluginAction ? "#a855f7" : isMissingPlugin ? "#ef4444" : "#666");
  const summaryText = typeInfo
    ? typeInfo.summary(step, devices as any)
    : pluginAction
      ? pluginActionSummary(step, pluginAction)
      : isMissingPlugin
        ? `(plugin not installed) ${step.action}`
        : "";

  return (
    <div ref={setNodeRef} style={style}>
      {/* Step header */}
      <div
        onClick={onToggleExpand}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "var(--space-sm) var(--space-md)",
          cursor: "pointer",
        }}
      >
        {/* Drag handle */}
        <div
          ref={setActivatorNodeRef}
          {...attributes}
          {...listeners}
          onClick={(e) => e.stopPropagation()}
          style={{
            cursor: "grab",
            padding: "var(--space-2xs) 0",
            color: "var(--text-muted)",
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
          }}
          title="Drag to reorder"
        >
          <GripVertical size={14} />
        </div>
        <ChevronRight
          size={14}
          style={{
            transform: isExpanded ? "rotate(90deg)" : "none",
            transition: "transform 0.15s",
            color: "var(--text-muted)",
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: "var(--font-size-2xs)",
            fontWeight: "var(--font-weight-semibold)",
            color: "#fff",
            background: labelBg,
            padding: "var(--space-2xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            textTransform: "uppercase",
            letterSpacing: "var(--tracking-wide)",
            flexShrink: 0,
          }}
        >
          {labelText}
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
          {summaryText}
        </span>
        <div
          style={{
            display: "flex",
            gap: "var(--space-2xs)",
            flexShrink: 0,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => onMoveStep(index, -1)}
            disabled={isFirst}
            style={iconBtnStyle}
            title="Move up"
          >
            <ChevronUp size={14} />
          </button>
          <button
            onClick={() => onMoveStep(index, 1)}
            disabled={isLast}
            style={iconBtnStyle}
            title="Move down"
          >
            <ChevronDown size={14} />
          </button>
          <button
            onClick={() => onCopyStep(index)}
            style={iconBtnStyle}
            title="Copy step to clipboard"
          >
            <Clipboard size={14} />
          </button>
          <button
            onClick={() => onDuplicateStep(index)}
            style={iconBtnStyle}
            title="Duplicate step"
          >
            <Copy size={14} />
          </button>
          <button
            onClick={() => onDeleteStep(index)}
            style={{ ...iconBtnStyle, color: "var(--color-error)" }}
            title="Delete step"
          >
            <Trash2 size={14} />
          </button>
        </div>
        {/* Incomplete as built -- amber, and nothing about it blocks a save */}
        {lintIssues.length > 0 && (
          <span
            title={lintIssues.map((i) => `${issueLabel(i)}: ${i.message}`).join("\n")}
            style={{ display: "flex", flexShrink: 0, color: "#f59e0b" }}
          >
            <AlertTriangle size={14} />
          </span>
        )}
        {/* Step result indicators */}
        {stepError && (
          <span title={stepError.error} style={{ display: "flex", flexShrink: 0 }}>
            <XCircle size={14} style={{ color: "#ef4444" }} />
          </span>
        )}
        {conditionalResult && (
          <span
            style={{
              fontSize: "var(--font-size-2xs)",
              fontWeight: "var(--font-weight-semibold)",
              padding: "0 var(--space-xs)",
              borderRadius: "var(--border-radius)",
              flexShrink: 0,
              background: conditionalResult.conditionResult ? "rgba(16,185,129,0.2)" : "rgba(239,68,68,0.2)",
              color: conditionalResult.conditionResult ? "#10b981" : "#ef4444",
            }}
            title={`Condition on '${conditionalResult.conditionKey}' evaluated ${conditionalResult.conditionResult ? "TRUE" : "FALSE"} → ${conditionalResult.branch} branch`}
          >
            {conditionalResult.conditionResult ? "TRUE" : "FALSE"}
          </span>
        )}
      </div>

      {/* Step error detail */}
      {stepError && (
        <div
          style={{
            padding: "var(--space-xs) var(--space-md)",
            fontSize: "var(--font-size-sm)",
            color: "#ef4444",
            background: "rgba(239,68,68,0.08)",
            borderTop: "1px solid rgba(239,68,68,0.2)",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
          }}
        >
          <AlertTriangle size={12} style={{ flexShrink: 0 }} />
          {stepError.error}
        </div>
      )}

      {/* What this step is missing, beside the fields it is about. Only while
          the step is open: collapsed, the header's mark and the summary at the
          top of the editor already say it, and saying it three times is noise. */}
      {isExpanded && lintIssues.length > 0 && (
        <div
          style={{
            padding: "var(--space-xs) var(--space-md)",
            fontSize: "var(--font-size-sm)",
            color: "#f59e0b",
            background: "rgba(245,158,11,0.08)",
            borderTop: "1px solid rgba(245,158,11,0.2)",
          }}
        >
          {lintIssues.map((issue, n) => (
            <div key={n} style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              <AlertTriangle size={12} style={{ flexShrink: 0 }} />
              <span>
                {issue.path.includes(".") ? `${issueLabel(issue)}: ` : ""}
                {issue.message}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Group command per-device results */}
      {groupResult && (
        <div
          style={{
            padding: "var(--space-xs) var(--space-md)",
            fontSize: "var(--font-size-sm)",
            borderTop: "1px solid var(--border-color)",
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-xs)",
          }}
        >
          {groupResult.deviceResults.map((dr) => (
            <span
              key={dr.device_id}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-2xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-xs)",
                background: dr.success ? "rgba(16,185,129,0.12)" : "rgba(239,68,68,0.12)",
                color: dr.success ? "#10b981" : "#ef4444",
              }}
              title={dr.success ? "Success" : dr.error ?? "Failed"}
            >
              {dr.success ? <CheckCircle size={10} /> : <XCircle size={10} />}
              {dr.name}
            </span>
          ))}
        </div>
      )}

      {/* Expanded editor */}
      {isExpanded && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-md) var(--space-md)",
            borderTop: "1px solid var(--border-color)",
          }}
        >
          <StepEditor
            step={step}
            macros={allMacros}
            currentMacroId={macroId}
            onChange={(updated) => onUpdateStep(index, updated)}
            activeStepPath={isActive ? activeStepPath : undefined}
          />
        </div>
      )}
    </div>
  );
}

interface MacroEditorProps {
  macro: MacroConfig;
  /** What this macro will not do as built (POST /api/macros/validate). */
  issues?: MacroIssue[];
  allMacros: MacroConfig[];
  devices: { id: string; name: string }[];
  onUpdate: (updated: MacroConfig) => void;
  onConvertToScript: () => void;
}

export function MacroEditor({
  macro,
  issues,
  allMacros,
  devices,
  onUpdate,
  onConvertToScript,
}: MacroEditorProps) {
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const addMenuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!showAddMenu && !showTemplates) return;
    const handler = (e: MouseEvent) => {
      if (addMenuRef.current && !addMenuRef.current.contains(e.target as Node)) {
        setShowAddMenu(false);
        setShowTemplates(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showAddMenu, showTemplates]);
  const [, setClipboardRevision] = useState(0); // force re-render on copy

  const { actions: pluginMacroActions, refresh: refreshPluginActions } = usePluginMacroActions();

  const macroProgress = useLogStore((s) => s.macroProgress);
  const isRunning =
    macroProgress.macroId === macro.id && macroProgress.status === "running";
  const isDone =
    macroProgress.macroId === macro.id && macroProgress.status === "completed";
  const isError =
    macroProgress.macroId === macro.id && macroProgress.status === "error";

  // Step-level feedback from store (snapshot read to avoid rapid re-renders)
  const stepErrors = useLogStore((s) => s.stepErrors);
  const conditionalResults = useLogStore((s) => s.conditionalResults);
  const groupResults = useLogStore((s) => s.groupResults);
  const lastRun = useLogStore((s) => s.lastRun);
  const showLastRun = lastRun && lastRun.macroId === macro.id && !isRunning;

  const handleTest = async () => {
    try {
      await api.executeMacro(macro.id);
    } catch (e) {
      console.error("Macro execute failed:", e);
    }
  };

  const handleCancel = async () => {
    try {
      await api.cancelMacro(macro.id);
    } catch (e) {
      console.error("Macro cancel failed:", e);
    }
  };

  const updateStep = (index: number, updated: MacroStep) => {
    const steps = [...macro.steps];
    steps[index] = updated;
    onUpdate({ ...macro, steps });
  };

  const deleteStep = (index: number) => {
    const step = macro.steps[index];
    const hasContent = step.action === "conditional"
      ? ((step.then_steps?.length ?? 0) > 0 || (step.else_steps?.length ?? 0) > 0)
      : step.device || step.command || step.key || step.event || step.macro;
    if (hasContent && !confirm(`Delete this ${getStepType(step.action)?.label ?? step.action} step?`)) return;
    const steps = macro.steps.filter((_, i) => i !== index);
    onUpdate({ ...macro, steps });
    if (expandedStep === index) setExpandedStep(null);
  };

  const duplicateStep = (index: number) => {
    const original = macro.steps[index];
    const copy = { ...original };
    const steps = [...macro.steps];
    steps.splice(index + 1, 0, copy);
    onUpdate({ ...macro, steps });
    setExpandedStep(index + 1);
  };

  const handleCopyStep = (index: number) => {
    copyStep(macro.steps[index]);
    setClipboardRevision((r) => r + 1);
  };

  const handlePasteStep = () => {
    const step = getClipboardStep();
    if (!step) return;
    onUpdate({ ...macro, steps: [...macro.steps, step] });
    setExpandedStep(macro.steps.length);
    setShowAddMenu(false);
  };

  const addTemplate = (templateSteps: MacroStep[]) => {
    const copies = templateSteps.map((s) => ({ ...s }));
    onUpdate({ ...macro, steps: [...macro.steps, ...copies] });
    setExpandedStep(macro.steps.length);
    setShowTemplates(false);
    setShowAddMenu(false);
  };

  // Circular dependency detection (8.7)
  const circularWarning = detectCircularDependency(macro.id, allMacros);

  // Dependency info (8.6)
  const callers = getMacroCallers(macro.id, allMacros);
  const calleeIds = getMacroCallees(macro.id, allMacros);
  const callees = calleeIds.map((id) => allMacros.find((m) => m.id === id)).filter(Boolean);

  const moveStep = (index: number, direction: -1 | 1) => {
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= macro.steps.length) return;
    const steps = [...macro.steps];
    [steps[index], steps[newIndex]] = [steps[newIndex], steps[index]];
    onUpdate({ ...macro, steps });
    setExpandedStep(newIndex);
  };

  const addStep = (action: string) => {
    const typeInfo = getStepType(action);
    let newStep: MacroStep | null = null;
    if (typeInfo) {
      newStep = { action, ...typeInfo.defaults() };
    } else {
      const pluginAction = findPluginAction(pluginMacroActions, action);
      if (pluginAction) {
        newStep = { action, params: defaultPluginActionParams(pluginAction) };
      }
    }
    if (!newStep) return;
    onUpdate({ ...macro, steps: [...macro.steps, newStep] });
    setExpandedStep(macro.steps.length);
    setShowAddMenu(false);
  };

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // One id space for everything sortable: SortableContext items, the ids
  // the step rows register, and the React keys all read from stepIds.
  const stepIdMapRef = useRef(new WeakMap<object, string>());
  const stepIdCounterRef = useRef(0);
  const stepIds = getStepIds(macro.steps, stepIdMapRef.current, stepIdCounterRef);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const result = applyStepReorder(macro.steps, stepIds, String(active.id), String(over.id));
    if (!result) return;
    onUpdate({ ...macro, steps: result.steps });
    // Keep the expanded step pointing at the same step after the move
    setExpandedStep(adjustExpandedAfterMove(expandedStep, result.oldIndex, result.newIndex));
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          padding: "var(--space-md)",
          borderBottom: "1px solid var(--border-color)",
          flexShrink: 0,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <input
            type="text"
            value={macro.name}
            onChange={(e) => onUpdate({ ...macro, name: e.target.value })}
            style={{
              width: "100%",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              fontSize: "var(--font-size-base)",
              fontWeight: "var(--font-weight-semibold)",
            }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginTop: "var(--space-2xs)", paddingLeft: "var(--space-2xs)" }}>
            <code style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {macro.id}
            </code>
            <CopyButton value={macro.id} title="Copy macro ID" />
            <span style={{ color: "var(--border-color)", margin: "0 var(--space-xs)" }}>|</span>
            <label style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              Cancel group:
              <span title="Macros in the same cancel group interrupt each other. Example: put System On and System Off in the same group so starting one cancels the other.">
                <HelpCircle size={11} style={{ opacity: 0.5 }} />
              </span>
              <input
                type="text"
                list="cancel-groups"
                value={macro.cancel_group ?? ""}
                onChange={(e) => onUpdate({ ...macro, cancel_group: e.target.value || undefined })}
                placeholder="none"
                title="Macros in the same cancel group interrupt each other. Use this for System On / System Off pairs."
                style={{
                  width: 100,
                  padding: "var(--space-2xs) var(--space-xs)",
                  fontSize: "var(--font-size-xs)",
                  fontFamily: "var(--font-mono)",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  color: "var(--text-primary)",
                }}
              />
              <datalist id="cancel-groups">
                {[...new Set(allMacros.filter(m => m.cancel_group && m.id !== macro.id).map(m => m.cancel_group!))].map(g => (
                  <option key={g} value={g} />
                ))}
              </datalist>
            </label>
            <span style={{ color: "var(--border-color)", margin: "0 var(--space-xs)" }}>|</span>
            <label style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              Overlap:
              <span title="What happens when this macro is fired again while it's still running, from any source (trigger, script, button, REST, AI, or another macro). Allow: run concurrently (default). Skip: ignore the new run while one is in progress. Queue: wait for the running one to finish, then run. A trigger can still add its own overlap rule; the stricter of the two applies.">
                <HelpCircle size={11} style={{ opacity: 0.5 }} />
              </span>
              <select
                value={macro.overlap ?? "allow"}
                onChange={(e) => onUpdate({ ...macro, overlap: e.target.value === "allow" ? undefined : (e.target.value as "skip" | "queue") })}
                title="How concurrent re-runs of this macro are handled, from every entry point."
                style={{
                  padding: "var(--space-2xs) var(--space-xs)",
                  fontSize: "var(--font-size-xs)",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  color: "var(--text-primary)",
                }}
              >
                <option value="allow">Allow</option>
                <option value="skip">Skip</option>
                <option value="queue">Queue</option>
              </select>
            </label>
            <span style={{ color: "var(--border-color)", margin: "0 var(--space-xs)" }}>|</span>
            <label style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              Cooldown:
              <span title="Minimum seconds between starts of this macro, enforced no matter what fires it. 0 = no cooldown.">
                <HelpCircle size={11} style={{ opacity: 0.5 }} />
              </span>
              <input
                type="number"
                min={0}
                step={0.5}
                value={macro.cooldown_seconds ?? 0}
                onChange={(e) => {
                  const n = parseFloat(e.target.value);
                  onUpdate({ ...macro, cooldown_seconds: Number.isFinite(n) && n > 0 ? n : undefined });
                }}
                title="Minimum seconds between starts of this macro (0 = off)."
                style={{
                  width: 48,
                  padding: "var(--space-2xs) var(--space-xs)",
                  fontSize: "var(--font-size-xs)",
                  fontFamily: "var(--font-mono)",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  color: "var(--text-primary)",
                }}
              />
              s
            </label>
          </div>
        </div>
        <button
          onClick={handleTest}
          disabled={isRunning}
          style={{
            ...btnStyle,
            background: isDone
              ? "#10b981"
              : isError
              ? "#ef4444"
              : "var(--accent)",
            opacity: isRunning ? 0.7 : 1,
          }}
        >
          {isRunning ? (
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
          ) : isDone ? (
            <Check size={14} />
          ) : isError ? (
            <X size={14} />
          ) : (
            <Play size={14} />
          )}
          Test
        </button>
        <button
          onClick={handleCancel}
          disabled={!isRunning}
          style={{
            ...btnStyle,
            background: isRunning ? "#ef4444" : "var(--bg-hover)",
            opacity: isRunning ? 1 : 0.4,
          }}
          title="Cancel running macro"
        >
          <X size={14} />
          Cancel
        </button>
        <button
          onClick={() => onUpdate({ ...macro, stop_on_error: !macro.stop_on_error })}
          title={macro.stop_on_error ? "Macro will stop if a step fails" : "Macro will continue if a step fails"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: macro.stop_on_error ? "rgba(244,67,54,0.15)" : "var(--bg-hover)",
            color: macro.stop_on_error ? "#ef4444" : "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            border: "none",
            cursor: "pointer",
          }}
        >
          <AlertTriangle size={14} /> {macro.stop_on_error ? "Stop on Error" : "Continue on Error"}
        </button>
        <button onClick={onConvertToScript} style={btnStyle}>
          <FileCode size={14} />
          To Script
        </button>
      </div>

      {/* Circular dependency warning (8.7) */}
      {circularWarning && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            padding: "var(--space-sm) var(--space-md)",
            background: "rgba(239,68,68,0.1)",
            borderBottom: "1px solid rgba(239,68,68,0.3)",
            fontSize: "var(--font-size-sm)",
            color: "#ef4444",
          }}
        >
          <AlertTriangle size={14} style={{ flexShrink: 0 }} />
          <span>
            <strong>Circular dependency detected:</strong>{" "}
            {circularWarning.map((id) => {
              const m = allMacros.find((mac) => mac.id === id);
              return m?.name ?? id;
            }).join(" → ")}
          </span>
        </div>
      )}

      {/* What will not run as built. Amber and never a blocker: half-built is
          what editing looks like, and the save path stays shape-only. */}
      {(issues?.length ?? 0) > 0 && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2xs)",
            padding: "var(--space-sm) var(--space-md)",
            background: "rgba(245,158,11,0.08)",
            borderBottom: "1px solid rgba(245,158,11,0.3)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", color: "#f59e0b", fontWeight: "var(--font-weight-semibold)" }}>
            <AlertTriangle size={14} style={{ flexShrink: 0 }} />
            {issueSummary(issues!)} won't run as built
          </div>
          {issues!.map((issue, n) => (
            <div key={n} style={{ paddingLeft: "var(--space-xl)" }}>
              <strong style={{ color: "var(--text-primary)", fontWeight: "var(--font-weight-medium)" }}>
                {issueLabel(issue)}
              </strong>
              : {issue.message}
            </div>
          ))}
        </div>
      )}

      {/* Dependency tree (8.6) */}
      {(callers.length > 0 || callees.length > 0) && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "var(--space-sm)",
            padding: "var(--space-xs) var(--space-md)",
            borderBottom: "1px solid var(--border-color)",
            fontSize: "var(--font-size-xs)",
            color: "var(--text-muted)",
          }}
        >
          <GitBranch size={12} style={{ flexShrink: 0 }} />
          {callers.length > 0 && (
            <span>
              Called by: {callers.map((m) => (
                <span key={m.id} style={{ color: "#ec4899", fontWeight: "var(--font-weight-medium)" }}>{m.name}</span>
              )).reduce<React.ReactNode[]>((acc, el, i) => i === 0 ? [el] : [...acc, ", ", el], [])}
            </span>
          )}
          {callers.length > 0 && callees.length > 0 && (
            <span style={{ color: "var(--border-color)" }}>|</span>
          )}
          {callees.length > 0 && (
            <span>
              Calls: {callees.map((m) => (
                <span key={m!.id} style={{ color: "#ec4899", fontWeight: "var(--font-weight-medium)" }}>{m!.name}</span>
              )).reduce<React.ReactNode[]>((acc, el, i) => i === 0 ? [el] : [...acc, ", ", el], [])}
            </span>
          )}
        </div>
      )}

      {/* Triggers + Steps */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-md)" }}>
        {/* Triggers section */}
        <TriggerList
          triggers={macro.triggers ?? []}
          issues={issues}
          devices={devices as any}
          allMacros={allMacros}
          onUpdate={(triggers: TriggerConfig[]) => onUpdate({ ...macro, triggers })}
        />

        {/* Steps */}
        {macro.steps.length === 0 ? (
          <div
            style={{
              padding: "var(--space-xl)",
              textAlign: "center",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-muted)",
              lineHeight: "var(--line-relaxed)",
            }}
          >
            <div style={{ fontSize: "var(--font-size-base)", marginBottom: "var(--space-sm)" }}>
              This macro has no steps yet
            </div>
            <div style={{ fontSize: "var(--font-size-sm)" }}>
              A macro is a sequence of actions that run in order, like powering
              on devices, switching inputs, and setting room variables.
              Click <strong>Add Step</strong> below to build your sequence.
            </div>
          </div>
        ) : (
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={stepIds} strategy={verticalListSortingStrategy}>
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
                {macro.steps.map((step, i) => (
                  <SortableStepItem
                    key={stepIds[i]}
                    id={stepIds[i]}
                    step={step}
                    index={i}
                    isFirst={i === 0}
                    isLast={i === macro.steps.length - 1}
                    isExpanded={expandedStep === i}
                    isActive={isRunning && macroProgress.activeStepPath.length > 0 && macroProgress.activeStepPath[0] === i}
                    stepError={stepErrors.find((e) => e.stepIndex === i)}
                    lintIssues={issuesAt(issues, "step", i)}
                    conditionalResult={step.action === "conditional" ? conditionalResults.find((r) => r.stepIndex === i) : undefined}
                    groupResult={step.action === "group.command" ? groupResults.find((g) => g.stepIndex === i) : undefined}
                    devices={devices}
                    allMacros={allMacros}
                    macroId={macro.id}
                    onToggleExpand={() => setExpandedStep(expandedStep === i ? null : i)}
                    onMoveStep={moveStep}
                    onDeleteStep={deleteStep}
                    onDuplicateStep={duplicateStep}
                    onCopyStep={handleCopyStep}
                    onUpdateStep={updateStep}
                    activeStepPath={isRunning && macroProgress.activeStepPath[0] === i ? macroProgress.activeStepPath.slice(1) : undefined}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        )}

        {/* Last run summary */}
        {showLastRun && (
          <LastRunSummary lastRun={lastRun!} />
        )}

        {/* Add step button */}
        <div ref={addMenuRef} style={{ marginTop: "var(--space-md)", position: "relative" }}>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <button
              onClick={() => { setShowAddMenu(!showAddMenu); setShowTemplates(false); }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-sm) var(--space-md)",
                borderRadius: "var(--border-radius)",
                border: "1px dashed var(--border-color)",
                background: "transparent",
                color: "var(--text-secondary)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
                flex: 1,
                justifyContent: "center",
              }}
            >
              <Plus size={14} /> Add Step
            </button>
            <button
              onClick={() => { setShowTemplates(!showTemplates); setShowAddMenu(false); }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-sm) var(--space-md)",
                borderRadius: "var(--border-radius)",
                border: "1px dashed var(--border-color)",
                background: "transparent",
                color: "var(--text-secondary)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
              }}
              title="Insert a pre-built step template"
            >
              <LayoutTemplate size={14} /> Templates
            </button>
          </div>

          {showAddMenu && (
            <div
              style={{
                position: "absolute",
                top: "100%",
                left: 0,
                right: 0,
                marginTop: "var(--space-xs)",
                background: "var(--bg-surface)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                zIndex: 10,
              }}
            >
              {/* Paste from clipboard */}
              {hasClipboardStep() && (
                <>
                  <div
                    onClick={handlePasteStep}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-sm)",
                      padding: "var(--space-sm) var(--space-md)",
                      cursor: "pointer",
                      fontSize: "var(--font-size-sm)",
                      borderBottom: "1px solid var(--border-color)",
                    }}
                    onMouseEnter={(e) =>
                      ((e.currentTarget as HTMLElement).style.background =
                        "var(--bg-hover)")
                    }
                    onMouseLeave={(e) =>
                      ((e.currentTarget as HTMLElement).style.background =
                        "transparent")
                    }
                  >
                    <Clipboard size={14} style={{ color: "var(--accent)", flexShrink: 0 }} />
                    <div>
                      <div style={{ fontWeight: "var(--font-weight-medium)", color: "var(--accent)" }}>Paste Copied Step</div>
                      <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>
                        {(() => { const s = getClipboardStep(); return s ? getStepType(s.action)?.summary(s, devices as any) ?? s.action : ""; })()}
                      </div>
                    </div>
                  </div>
                </>
              )}
              {STEP_TYPES.map((t) => (
                <div
                  key={t.action}
                  onClick={() => addStep(t.action)}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "var(--space-sm)",
                    padding: "var(--space-sm) var(--space-md)",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                  }}
                  onMouseEnter={(e) =>
                    ((e.currentTarget as HTMLElement).style.background =
                      "var(--bg-hover)")
                  }
                  onMouseLeave={(e) =>
                    ((e.currentTarget as HTMLElement).style.background =
                      "transparent")
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
                    <div style={{ fontWeight: "var(--font-weight-medium)", color: "var(--text-primary)" }}>{t.label}</div>
                    <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>{t.description}</div>
                  </div>
                </div>
              ))}
              {pluginMacroActions.length > 0 && (
                <>
                  <div style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "var(--space-xs) var(--space-md)",
                    fontSize: "var(--font-size-2xs)",
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    fontWeight: "var(--font-weight-semibold)",
                    letterSpacing: "var(--tracking-wide)",
                    borderTop: "1px solid var(--border-color)",
                    marginTop: "var(--space-xs)",
                    background: "var(--bg-primary)",
                  }}>
                    <span>Plugin Actions</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        refreshPluginActions();
                      }}
                      title="Refresh plugin actions"
                      style={{
                        background: "transparent",
                        border: "none",
                        color: "var(--text-muted)",
                        cursor: "pointer",
                        fontSize: "var(--font-size-xs)",
                        padding: 0,
                      }}
                    >
                      ↻
                    </button>
                  </div>
                  {(() => {
                    // Group by plugin name for the menu
                    const groups = new Map<string, typeof pluginMacroActions>();
                    for (const a of pluginMacroActions) {
                      const arr = groups.get(a.plugin_name) ?? [];
                      arr.push(a);
                      groups.set(a.plugin_name, arr);
                    }
                    return Array.from(groups.entries()).map(([pluginName, actions]) => (
                      <div key={pluginName}>
                        <div style={{
                          padding: "var(--space-xs) var(--space-md)",
                          fontSize: "var(--font-size-2xs)",
                          color: "var(--text-muted)",
                          fontWeight: "var(--font-weight-semibold)",
                        }}>
                          {pluginName}
                        </div>
                        {actions.map((a) => (
                          <div
                            key={a.action_type}
                            onClick={() => addStep(a.action_type)}
                            style={{
                              display: "flex",
                              alignItems: "flex-start",
                              gap: "var(--space-sm)",
                              padding: "var(--space-sm) var(--space-md)",
                              cursor: "pointer",
                              fontSize: "var(--font-size-sm)",
                            }}
                            onMouseEnter={(e) =>
                              ((e.currentTarget as HTMLElement).style.background =
                                "var(--bg-hover)")
                            }
                            onMouseLeave={(e) =>
                              ((e.currentTarget as HTMLElement).style.background =
                                "transparent")
                            }
                          >
                            <span
                              style={{
                                width: 8,
                                height: 8,
                                borderRadius: "50%",
                                background: "#a855f7",
                                flexShrink: 0,
                                marginTop: "var(--space-xs)",
                              }}
                            />
                            <div>
                              <div style={{ fontWeight: "var(--font-weight-medium)", color: "var(--text-primary)" }}>{a.label}</div>
                              {a.description && (
                                <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>{a.description}</div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    ));
                  })()}
                </>
              )}
            </div>
          )}

          {/* Templates dropdown (8.2) */}
          {showTemplates && (
            <div
              style={{
                position: "absolute",
                top: "100%",
                right: 0,
                marginTop: "var(--space-xs)",
                minWidth: 320,
                background: "var(--bg-surface)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                zIndex: 10,
              }}
            >
              <div style={{ padding: "var(--space-sm) var(--space-md)", fontSize: "var(--font-size-xs)", color: "var(--text-muted)", borderBottom: "1px solid var(--border-color)" }}>
                Pre-built step patterns. Edit them after inserting.
              </div>
              {STEP_TEMPLATES.map((t) => (
                <div
                  key={t.id}
                  onClick={() => addTemplate(t.steps)}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "var(--space-sm)",
                    padding: "var(--space-sm) var(--space-md)",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                  }}
                  onMouseEnter={(e) =>
                    ((e.currentTarget as HTMLElement).style.background =
                      "var(--bg-hover)")
                  }
                  onMouseLeave={(e) =>
                    ((e.currentTarget as HTMLElement).style.background =
                      "transparent")
                  }
                >
                  <LayoutTemplate size={14} style={{ color: "var(--accent)", flexShrink: 0, marginTop: "var(--space-2xs)" }} />
                  <div>
                    <div style={{ fontWeight: "var(--font-weight-medium)", color: "var(--text-primary)" }}>{t.label}</div>
                    <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>{t.description}</div>
                    <div style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>
                      {t.steps.length} steps
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Keyframe for spinner */}
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function LastRunSummary({ lastRun }: { lastRun: MacroLastRun }) {
  const durationSec = (lastRun.duration / 1000).toFixed(1);
  const time = new Date(lastRun.completedAt).toLocaleTimeString();
  const hasErrors = lastRun.stepErrors.length > 0;
  const isSuccess = lastRun.status === "completed" && !hasErrors;

  return (
    <div
      style={{
        marginTop: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        border: `1px solid ${isSuccess ? "rgba(16,185,129,0.3)" : "rgba(239,68,68,0.3)"}`,
        background: isSuccess ? "rgba(16,185,129,0.06)" : "rgba(239,68,68,0.06)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: hasErrors ? "var(--space-xs)" : 0 }}>
        {isSuccess ? (
          <CheckCircle size={14} style={{ color: "#10b981" }} />
        ) : (
          <XCircle size={14} style={{ color: "#ef4444" }} />
        )}
        <span style={{ fontWeight: "var(--font-weight-semibold)", color: "var(--text-primary)" }}>
          Last run: {isSuccess ? "Completed" : lastRun.status === "error" ? "Failed" : "Completed with errors"}
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          <Clock size={11} style={{ verticalAlign: "middle", marginRight: "var(--space-2xs)" }} />
          {durationSec}s at {time}
        </span>
      </div>
      {lastRun.stepErrors.map((err, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-2xs) 0",
            color: "#ef4444",
            fontSize: "var(--font-size-xs)",
          }}
        >
          <XCircle size={11} style={{ flexShrink: 0 }} />
          <span style={{ fontWeight: "var(--font-weight-medium)" }}>Step {err.stepIndex + 1}:</span>
          <span>{err.error}</span>
          {err.device && <span style={{ color: "var(--text-muted)" }}>({err.device})</span>}
        </div>
      ))}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const iconBtnStyle: React.CSSProperties = {
  display: "flex",
  padding: "var(--space-2xs)",
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};
