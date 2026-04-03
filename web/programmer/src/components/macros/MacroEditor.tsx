import { useState } from "react";
import {
  Play,
  FileCode,
  Plus,
  Trash2,
  Copy,
  ChevronUp,
  ChevronDown,
  ChevronRight,
  Check,
  X,
  Loader2,
  AlertTriangle,
  GripVertical,
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
import { StepEditor } from "./StepEditor";
import { TriggerList } from "./TriggerList";
import { STEP_TYPES, getStepType } from "./macroHelpers";
import { CopyButton } from "../shared/CopyButton";
import * as api from "../../api/restClient";

interface SortableStepItemProps {
  id: string;
  step: MacroStep;
  index: number;
  isFirst: boolean;
  isLast: boolean;
  isExpanded: boolean;
  isActive: boolean;
  devices: { id: string; name: string }[];
  allMacros: MacroConfig[];
  macroId: string;
  onToggleExpand: () => void;
  onMoveStep: (index: number, direction: -1 | 1) => void;
  onDeleteStep: (index: number) => void;
  onDuplicateStep: (index: number) => void;
  onUpdateStep: (index: number, updated: MacroStep) => void;
}

function SortableStepItem({
  id,
  step,
  index,
  isFirst,
  isLast,
  isExpanded,
  isActive,
  devices,
  allMacros,
  macroId,
  onToggleExpand,
  onMoveStep,
  onDeleteStep,
  onDuplicateStep,
  onUpdateStep,
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
            padding: "2px 0",
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
          {typeInfo?.label ?? step.action}
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
          {typeInfo?.summary(step, devices as any) ?? ""}
        </span>
        <div
          style={{
            display: "flex",
            gap: 2,
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
      </div>

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
          />
        </div>
      )}
    </div>
  );
}

interface MacroEditorProps {
  macro: MacroConfig;
  allMacros: MacroConfig[];
  devices: { id: string; name: string }[];
  onUpdate: (updated: MacroConfig) => void;
  onConvertToScript: () => void;
}

export function MacroEditor({
  macro,
  allMacros,
  devices,
  onUpdate,
  onConvertToScript,
}: MacroEditorProps) {
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [showAddMenu, setShowAddMenu] = useState(false);

  const macroProgress = useLogStore((s) => s.macroProgress);
  const isRunning =
    macroProgress.macroId === macro.id && macroProgress.status === "running";
  const isDone =
    macroProgress.macroId === macro.id && macroProgress.status === "completed";
  const isError =
    macroProgress.macroId === macro.id && macroProgress.status === "error";

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
    if (!typeInfo) return;
    const newStep: MacroStep = { action, ...typeInfo.defaults() };
    onUpdate({ ...macro, steps: [...macro.steps, newStep] });
    setExpandedStep(macro.steps.length);
    setShowAddMenu(false);
  };

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const stepIds = macro.steps.map((_, i) => `step-${i}`);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = stepIds.indexOf(String(active.id));
    const newIndex = stepIds.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    const steps = [...macro.steps];
    const [moved] = steps.splice(oldIndex, 1);
    steps.splice(newIndex, 0, moved);
    onUpdate({ ...macro, steps });
    // Update expanded step to follow the moved item
    if (expandedStep === oldIndex) {
      setExpandedStep(newIndex);
    } else if (expandedStep !== null) {
      // Adjust expanded index if it shifted due to the move
      if (oldIndex < expandedStep && newIndex >= expandedStep) {
        setExpandedStep(expandedStep - 1);
      } else if (oldIndex > expandedStep && newIndex <= expandedStep) {
        setExpandedStep(expandedStep + 1);
      }
    }
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
              padding: "6px 10px",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              fontSize: "var(--font-size-md)",
              fontWeight: 600,
            }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2, paddingLeft: 2 }}>
            <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {macro.id}
            </code>
            <CopyButton value={macro.id} title="Copy macro ID" />
            <span style={{ color: "var(--border-color)", margin: "0 4px" }}>|</span>
            <label style={{ fontSize: 11, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 4 }}>
              Cancel group:
              <input
                type="text"
                list="cancel-groups"
                value={macro.cancel_group ?? ""}
                onChange={(e) => onUpdate({ ...macro, cancel_group: e.target.value || undefined })}
                placeholder="none"
                title="Macros in the same cancel group interrupt each other. Use this for System On / System Off pairs."
                style={{
                  width: 100,
                  padding: "1px 4px",
                  fontSize: 11,
                  fontFamily: "var(--font-mono)",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: 3,
                  color: "var(--text-primary)",
                }}
              />
              <datalist id="cancel-groups">
                {[...new Set(allMacros.filter(m => m.cancel_group && m.id !== macro.id).map(m => m.cancel_group!))].map(g => (
                  <option key={g} value={g} />
                ))}
              </datalist>
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

      {/* Triggers + Steps */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-md)" }}>
        {/* Triggers section */}
        <TriggerList
          triggers={macro.triggers ?? []}
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
              color: "var(--text-muted)",
              lineHeight: 1.6,
            }}
          >
            <div style={{ fontSize: "var(--font-size-md)", marginBottom: "var(--space-sm)" }}>
              This macro has no steps yet
            </div>
            <div style={{ fontSize: "var(--font-size-sm)" }}>
              A macro is a sequence of actions that run in order — like powering
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
                    key={`step-${i}`}
                    id={`step-${i}`}
                    step={step}
                    index={i}
                    isFirst={i === 0}
                    isLast={i === macro.steps.length - 1}
                    isExpanded={expandedStep === i}
                    isActive={isRunning && macroProgress.stepIndex === i}
                    devices={devices}
                    allMacros={allMacros}
                    macroId={macro.id}
                    onToggleExpand={() => setExpandedStep(expandedStep === i ? null : i)}
                    onMoveStep={moveStep}
                    onDeleteStep={deleteStep}
                    onDuplicateStep={duplicateStep}
                    onUpdateStep={updateStep}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        )}

        {/* Add step button */}
        <div style={{ marginTop: "var(--space-md)", position: "relative" }}>
          <button
            onClick={() => setShowAddMenu(!showAddMenu)}
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
              width: "100%",
              justifyContent: "center",
            }}
          >
            <Plus size={14} /> Add Step
          </button>

          {showAddMenu && (
            <div
              style={{
                position: "absolute",
                top: "100%",
                left: 0,
                right: 0,
                marginTop: 4,
                background: "var(--bg-surface)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                zIndex: 10,
              }}
            >
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
                      marginTop: 5,
                    }}
                  />
                  <div>
                    <div style={{ fontWeight: 500, color: "var(--text-primary)" }}>{t.label}</div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 1 }}>{t.description}</div>
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
  padding: 2,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};
