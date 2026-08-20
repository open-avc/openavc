/**
 * The inspector for one key: what it does, what it shows, where it sits.
 *
 * Press bindings, label and icon, feedback, and the arrange row (copy, paste,
 * move, swap) that acts on the key as a whole.
 */
import { useState } from "react";
import { X, Trash2, Pin, Play } from "lucide-react";
import { useProjectStore } from "../../../store/projectStore";
import { ButtonBindingEditor } from "../../shared/ButtonBindingEditor";
import type { ButtonBindings } from "../../shared/ButtonBindingEditor";
import { VisibilityProperties } from "../../ui-builder/PropertySections/VisibilityProperties";
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { IconPicker } from "../../ui-builder/IconPicker";
import { MeterFields } from "./FieldEditors";
import { fieldInputStyle, panelHintStyle, panelLabelStyle } from "./styles";
import type { ButtonAssignment } from "./types";

interface ArrangeOps {
  page: number;
  maxPages: number;
  totalKeys: number;
  pageLabel: (p: number) => string;
  clipboardReady: boolean;
  onCopy: () => void;
  onPaste: () => void;
  onMove: (to: { index: number; page: number }) => void;
  onSwap: (to: { index: number; page: number }) => void;
}

export function ControlAssignmentPanel({
  controlId,
  assignment,
  onUpdate,
  onClear,
  onClose,
  allowedActions,
  navigateOptions,
  colorOnly = false,
  keyCount = 0,
  arrange,
  pageName,
  locked,
  onToggleLock,
  lockShadowCount = 0,
  onPress,
  visualDeck = true,
}: {
  controlId: string;
  assignment: ButtonAssignment | undefined;
  onUpdate: (updates: Partial<ButtonAssignment>) => void;
  onClear: () => void;
  onClose: () => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  // Touch keys have no LCD: only the background color (RGB glow) applies.
  colorOnly?: boolean;
  keyCount?: number;
  arrange?: ArrangeOps;
  // Workbench extras: page context in the title, the lock toggle, and a
  // real press (simulate_input) button.
  pageName?: string;
  locked?: boolean;
  onToggleLock?: (locked: boolean) => void;
  lockShadowCount?: number;
  onPress?: () => void;
  // False for display-less decks (foot pedals): hide everything visual.
  visualDeck?: boolean;
}) {
  const project = useProjectStore((s) => s.project);
  const [arrangeMode, setArrangeMode] = useState<"move" | "swap" | null>(null);
  const [targetPage, setTargetPage] = useState(0);
  const [targetKey, setTargetKey] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);

  const currentBindings: ButtonBindings = assignment?.bindings ?? {};
  const controlIndex = parseInt(controlId);
  const keyNoun = colorOnly
    ? `Touch Key ${controlIndex - keyCount + 1}`
    : onToggleLock
      ? `Key ${controlIndex + 1}`
      : `Button ${controlIndex + 1}`;
  const title = locked
    ? `${keyNoun} (every page)`
    : pageName
      ? `${keyNoun} (${pageName})`
      : keyNoun;

  const whatItShows = !visualDeck ? (
    <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
      This model has no display. The label only names the switch in the
      editor, and there is nothing for colors or feedback to change.
    </div>
  ) : (
    <div>
      <label style={panelLabelStyle}>{colorOnly ? "Key Color" : "What It Shows"}</label>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {!colorOnly && (
          <IconPicker
            value={assignment?.icon ?? ""}
            onChange={(icon) => onUpdate({ icon: icon || undefined })}
          />
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <span style={panelHintStyle}>Background</span>
          <InlineColorPicker
            value={assignment?.bg_color ?? ""}
            onChange={(c) => onUpdate({ bg_color: c || undefined })}
          />
        </div>
        {!colorOnly && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <span style={panelHintStyle}>Text</span>
            <InlineColorPicker
              value={assignment?.text_color ?? ""}
              onChange={(c) => onUpdate({ text_color: c || undefined })}
            />
          </div>
        )}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
        {colorOnly
          ? "This key has no display. It glows with this color. Feedback colors override it when active; labels and icons don't apply."
          : "Feedback colors override these when active."}
      </div>
      {!colorOnly && (
        <div style={{ marginTop: "var(--space-md)", display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <div>
            <label style={panelHintStyle}>Live value from state (optional)</label>
            <VariableKeyPicker
              value={assignment?.value_source ?? ""}
              onChange={(key) => onUpdate({ value_source: key || undefined })}
              placeholder="Show a state key's live value..."
            />
          </div>
          {assignment?.value_source && (
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <div style={{ width: 80 }}>
                <label style={panelHintStyle}>Unit</label>
                <input
                  type="text"
                  value={assignment?.unit ?? ""}
                  placeholder="dB, %"
                  onChange={(e) => onUpdate({ unit: e.target.value || undefined })}
                  style={fieldInputStyle}
                />
              </div>
              <div style={{ flex: 1 }}>
                <MeterFields
                  meter={assignment?.meter}
                  onChange={(meter) => onUpdate({ meter })}
                />
              </div>
            </div>
          )}
          <div>
            <label style={panelHintStyle}>Label from state (optional)</label>
            <VariableKeyPicker
              value={assignment?.label_source ?? ""}
              onChange={(key) => onUpdate({ label_source: key || undefined })}
              placeholder="Live label overriding the static one..."
            />
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div
      style={{
        width: 300,
        flexShrink: 0,
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-lg)",
        maxHeight: "100%",
        overflow: "auto",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-sm)" }}>
        <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title}
        </h4>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexShrink: 0 }}>
          {onPress && (
            <button
              onClick={onPress}
              title="Press this key for real (same as pushing it on the deck)"
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "2px 8px", borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)", color: "var(--text-secondary)",
                fontSize: 11, cursor: "pointer",
              }}
            >
              <Play size={11} /> Press
            </button>
          )}
          <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Lock: keep this key identical on every page */}
      {onToggleLock && (
        <div>
          <label
            style={{
              display: "flex", alignItems: "center", gap: "var(--space-sm)",
              fontSize: "var(--font-size-sm)", cursor: "pointer",
              color: "var(--text-primary)",
            }}
          >
            <input
              type="checkbox"
              checked={!!locked}
              onChange={(e) => onToggleLock(e.target.checked)}
              style={{ accentColor: "var(--accent)" }}
            />
            <Pin size={13} style={{ color: locked ? "var(--accent)" : "var(--text-muted)" }} />
            Same on every page
          </label>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
            Locked keys keep this assignment on every page. Great for page
            switchers.
            {!locked && lockShadowCount > 0 && (
              <>
                {" "}
                <span style={{ color: "var(--color-warning, #f59e0b)" }}>
                  {lockShadowCount} page{lockShadowCount === 1 ? " has" : "s have"} something
                  on this key; that stays hidden while it's locked.
                </span>
              </>
            )}
          </div>
        </div>
      )}

      {/* Shared binding editor — same component the web UI Builder uses.
          Surface order: what it does first, then label, press style, feedback. */}
      {project ? (
        <ButtonBindingEditor
          bindings={currentBindings}
          label={assignment?.label ?? ""}
          project={project}
          onBindingsChange={(newBindings) =>
            onUpdate({ bindings: newBindings })
          }
          onLabelChange={(label) => onUpdate({ label: label || undefined })}
          showLabel={!colorOnly}
          showToggleLabels={!colorOnly && visualDeck}
          showFeedback={visualDeck}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          surfaceOrder
        />
      ) : (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading project...</div>
      )}

      {whatItShows}

      {/* More: visibility + arrange, tucked away */}
      <div style={{ border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)", overflow: "hidden" }}>
        <button
          onClick={() => setMoreOpen(!moreOpen)}
          style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            width: "100%", padding: "6px 10px", fontSize: "var(--font-size-sm)",
            background: "var(--bg-surface)", textAlign: "left", cursor: "pointer",
          }}
        >
          <span style={{ fontWeight: 500 }}>More</span>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            visibility{locked ? "" : " · arrange"}
          </span>
        </button>
        {moreOpen && (
          <div style={{ padding: "var(--space-sm)", borderTop: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: "var(--space-lg)" }}>

      {/* Visibility — hide this button based on system state */}
      <div>
        <label style={panelLabelStyle}>Visibility</label>
        <VisibilityProperties
          element={{ bindings: currentBindings as unknown as Record<string, unknown> }}
          onChange={(patch) =>
            onUpdate({
              bindings: patch.bindings as unknown as ButtonBindings,
            })
          }
        />
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          A hidden button shows as a blank key and ignores presses.
        </div>
      </div>

      {/* Arrange: copy/paste/move/swap (page keys only — locked keys are
          everywhere already) */}
      {arrange && !locked && (
        <div>
          <label style={panelLabelStyle}>Arrange</label>
          <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
            <button onClick={arrange.onCopy} disabled={!assignment} style={arrangeBtnStyle(!assignment)}>
              Copy
            </button>
            <button
              onClick={arrange.onPaste}
              disabled={!arrange.clipboardReady}
              title={arrange.clipboardReady ? "Paste the copied assignment here" : "Copy an assignment first"}
              style={arrangeBtnStyle(!arrange.clipboardReady)}
            >
              Paste
            </button>
            <button
              onClick={() => setArrangeMode(arrangeMode === "move" ? null : "move")}
              disabled={!assignment}
              style={arrangeBtnStyle(!assignment, arrangeMode === "move")}
            >
              Move to...
            </button>
            <button
              onClick={() => setArrangeMode(arrangeMode === "swap" ? null : "swap")}
              disabled={!assignment}
              style={arrangeBtnStyle(!assignment, arrangeMode === "swap")}
            >
              Swap with...
            </button>
          </div>
          {arrangeMode && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginTop: "var(--space-sm)" }}>
              <select
                value={targetPage}
                onChange={(e) => setTargetPage(Number(e.target.value))}
                style={{
                  padding: "4px 6px", borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-surface)", color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)", flex: 1,
                }}
              >
                {Array.from({ length: arrange.maxPages }, (_, p) => (
                  <option key={p} value={p}>{arrange.pageLabel(p)}</option>
                ))}
              </select>
              <select
                value={targetKey}
                onChange={(e) => setTargetKey(Number(e.target.value))}
                style={{
                  padding: "4px 6px", borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-surface)", color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)", width: 96,
                }}
              >
                {Array.from({ length: arrange.totalKeys }, (_, k) => (
                  <option key={k} value={k}>Key {k + 1}</option>
                ))}
              </select>
              <button
                onClick={() => {
                  const to = { index: targetKey, page: targetPage };
                  if (arrangeMode === "move") arrange.onMove(to);
                  else arrange.onSwap(to);
                  setArrangeMode(null);
                }}
                style={{
                  padding: "4px 10px", borderRadius: "var(--border-radius)",
                  background: "var(--accent-bg)", color: "white",
                  fontSize: "var(--font-size-sm)", cursor: "pointer",
                }}
              >
                Go
              </button>
            </div>
          )}
          {arrangeMode === "move" && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
              Moving replaces whatever is at the target.
            </div>
          )}
        </div>
      )}
          </div>
        )}
      </div>

      {/* Clear All */}
      <button
        onClick={onClear}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm)",
          borderRadius: "var(--border-radius)",
          background: "transparent",
          border: "1px solid var(--border-color)",
          color: "var(--color-error)",
          fontSize: "var(--font-size-sm)",
          cursor: "pointer",
        }}
      >
        <Trash2 size={12} />
        Clear Assignment
      </button>
    </div>
  );
}

const arrangeBtnStyle = (disabled: boolean, active = false): React.CSSProperties => ({
  padding: "4px 10px",
  borderRadius: "var(--border-radius)",
  border: active ? "1px solid var(--accent)" : "1px solid var(--border-color)",
  background: active ? "var(--accent-dim)" : "var(--bg-hover)",
  color: disabled ? "var(--text-muted)" : "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  cursor: disabled ? "default" : "pointer",
  opacity: disabled ? 0.5 : 1,
});
