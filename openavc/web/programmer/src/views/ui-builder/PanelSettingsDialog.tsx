/**
 * Panel Settings: the project-wide settings the panel reads (its accent and
 * font, the lock and idle behaviour, whether a failed press says why, page
 * transitions and the entry animation), edited as a draft. Save hands the
 * whole draft back as one edit, and only when something changed; Cancel,
 * Escape or the backdrop ask before throwing a changed draft away.
 *
 * Each choice is listed under the value the panel reads (panel.js). Where the
 * project leaves a setting blank the dialog shows a value of its own: for the
 * transition and the stagger, the 200 ms, 30 ms and fade-up that panel.js and
 * the project loader fall back to as well, so a change to one of those is a
 * change in all three; for the accent, #2196F3, which the panel draws only
 * when the theme has no accent of its own.
 */
import { useState, type CSSProperties, type ReactNode } from "react";
import type { UIPage, UISettings } from "../../api/types";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { Modal } from "../../components/shared/Modal";
import { NumericInput } from "../../components/shared/NumericInput";

type Choice = readonly [value: string, label: string];

const FONTS: readonly Choice[] = [
  ["Inter, system-ui, sans-serif", "Inter (Default)"],
  ["system-ui, sans-serif", "System UI"],
  ["'Roboto', sans-serif", "Roboto"],
  ["'Segoe UI', sans-serif", "Segoe UI"],
  ["monospace", "Monospace"],
];

const TRANSITIONS: readonly Choice[] = [
  ["none", "None (instant)"],
  ["fade", "Fade"],
  ["slide-left", "Slide Left"],
  ["slide-right", "Slide Right"],
  ["slide-up", "Slide Up"],
  ["scale", "Scale"],
];

const ENTRIES: readonly Choice[] = [
  ["none", "None (instant)"],
  ["fade", "Fade In"],
  ["fade-up", "Fade Up"],
  ["scale", "Scale In"],
  ["stagger", "Stagger"],
];

const STAGGER_STYLES: readonly Choice[] = [
  ["fade", "Fade"],
  ["fade-up", "Fade Up"],
  ["scale", "Scale"],
];

const LABEL: CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};
const FIELD: CSSProperties = { marginBottom: "var(--space-lg)" };
const INPUT: CSSProperties = { width: "100%" };
const HINT: CSSProperties = { fontSize: 11, color: "var(--text-muted)", marginTop: 2 };
const UNIT: CSSProperties = { fontSize: 11, color: "var(--text-muted)" };
const PICKER: CSSProperties = {
  width: 40,
  height: 32,
  padding: 0,
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  cursor: "pointer",
};

/** What every group of fields is handed: the draft, and a change to it. */
interface DraftProps {
  draft: UISettings;
  patch: (p: Partial<UISettings>) => void;
}

/** A setting's row: its name, the control, and a line saying what it does. */
function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div style={FIELD}>
      <label style={LABEL}>{label}</label>
      {children}
      {hint && <div style={HINT}>{hint}</div>}
    </div>
  );
}

function Choices({ value, choices, onChange }: { value: string; choices: readonly Choice[]; onChange: (value: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={INPUT}>
      {choices.map(([v, text]) => (
        <option key={v} value={v}>{text}</option>
      ))}
    </select>
  );
}

/** A number box: the draft takes a number only once the box holds one. */
function NumberBox({ owner, value, range, unit, onChange }: {
  owner: string;
  value: number;
  range: { min: number; max?: number; step?: number };
  unit?: string;
  onChange: (value: number) => void;
}) {
  const box = (
    <NumericInput
      integer
      {...range}
      owner={owner}
      value={value}
      onCommit={(v) => {
        if (v !== undefined) onChange(v);
      }}
      style={unit ? { ...INPUT, width: 80 } : INPUT}
    />
  );
  if (!unit) return box;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      {box}
      <span style={UNIT}>{unit}</span>
    </div>
  );
}

/** How the panel looks: its accent colour and its font. */
function LookFields({ draft, patch }: DraftProps) {
  return (
    <>
      <Field label="Accent Color">
        <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
          <input
            type="color"
            value={draft.accent_color || "#2196F3"}
            onChange={(e) => patch({ accent_color: e.target.value })}
            style={PICKER}
          />
          <input
            value={draft.accent_color}
            onChange={(e) => patch({ accent_color: e.target.value })}
            placeholder="#2196F3"
            style={{ flex: 1 }}
          />
        </div>
      </Field>
      <Field label="Font Family">
        <Choices value={draft.font_family} choices={FONTS} onChange={(font_family) => patch({ font_family })} />
      </Field>
    </>
  );
}

/** How the panel locks, idles, and answers a press that failed. */
function LockIdleAndErrorFields({ draft, patch, pages }: DraftProps & { pages: UIPage[] }) {
  const pageChoices = pages.map((p): Choice => [p.id, `${p.name} (${p.id})`]);
  return (
    <>
      <Field label="Lock Code (PIN)" hint="4-6 digit PIN to lock the touch panel. Leave empty to disable.">
        <input
          value={draft.lock_code}
          onChange={(e) => patch({ lock_code: e.target.value.replace(/[^0-9]/g, "").slice(0, 6) })}
          placeholder="Leave empty for no lock"
          style={INPUT}
        />
      </Field>
      <Field label="Idle Timeout (seconds)" hint="Return to idle page after this many seconds of inactivity. 0 = disabled.">
        <NumberBox
          owner="panel-settings/idle_timeout_seconds"
          value={draft.idle_timeout_seconds}
          range={{ min: 0 }}
          onChange={(idle_timeout_seconds) => patch({ idle_timeout_seconds })}
        />
      </Field>
      <Field label="Idle Page">
        <Choices value={draft.idle_page} choices={pageChoices} onChange={(idle_page) => patch({ idle_page })} />
      </Field>
      <div style={FIELD}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={draft.show_error_messages !== false}
            onChange={(e) => patch({ show_error_messages: e.target.checked })}
          />
          Show a message when a control fails
        </label>
        <div style={HINT}>
          A press that does not reach its device puts the reason across the
          panel for about five seconds. Turn this off in a space that shows
          its own status.
        </div>
      </div>
    </>
  );
}

/** How the panel moves: between pages, and as a page's controls appear. */
function MotionFields({ draft, patch }: DraftProps) {
  const transition = draft.page_transition || "none";
  const stagger = draft.element_entry === "stagger";
  return (
    <>
      <Field label="Page Transition">
        <Choices value={transition} choices={TRANSITIONS} onChange={(page_transition) => patch({ page_transition })} />
      </Field>
      {transition !== "none" && (
        <Field label="Transition Duration">
          <NumberBox
            owner="panel-settings/page_transition_duration"
            value={draft.page_transition_duration || 200}
            range={{ min: 50, max: 1000, step: 50 }}
            unit="ms"
            onChange={(page_transition_duration) => patch({ page_transition_duration })}
          />
        </Field>
      )}
      <Field label="Element Entry Animation">
        <Choices value={draft.element_entry || "none"} choices={ENTRIES} onChange={(element_entry) => patch({ element_entry })} />
      </Field>
      {stagger && (
        <Field label="Stagger Style" hint="How each element animates in as the stagger sweeps across the page.">
          <Choices
            value={draft.element_stagger_style || "fade-up"}
            choices={STAGGER_STYLES}
            onChange={(element_stagger_style) => patch({ element_stagger_style })}
          />
        </Field>
      )}
      {stagger && (
        <Field label="Stagger Delay">
          <NumberBox
            owner="panel-settings/element_stagger_ms"
            value={draft.element_stagger_ms || 30}
            range={{ min: 10, max: 200, step: 10 }}
            unit="ms per element"
            onChange={(element_stagger_ms) => patch({ element_stagger_ms })}
          />
        </Field>
      )}
    </>
  );
}

function DialogButtons({ dirty, onCancel, onSave }: { dirty: boolean; onCancel: () => void; onSave: () => void }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)", marginTop: "var(--space-lg)" }}>
      <button
        onClick={onCancel}
        style={{
          padding: "var(--space-sm) var(--space-lg)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          border: "none",
          cursor: "pointer",
          color: "var(--text-primary)",
        }}
      >
        Cancel
      </button>
      <button
        onClick={onSave}
        disabled={!dirty}
        style={{
          padding: "var(--space-sm) var(--space-lg)",
          borderRadius: "var(--border-radius)",
          background: dirty ? "var(--accent-bg)" : "var(--bg-hover)",
          border: "none",
          cursor: dirty ? "pointer" : "default",
          opacity: dirty ? 1 : 0.5,
          color: dirty ? "#fff" : "var(--text-muted)",
          fontWeight: 600,
        }}
      >
        Save
      </button>
    </div>
  );
}

export function PanelSettingsDialog({
  settings,
  pages,
  onUpdate,
  onClose,
}: {
  settings: UISettings;
  pages: UIPage[];
  onUpdate: (s: UISettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<UISettings>(settings);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings);

  const handleCancel = () => {
    if (dirty) { setShowDiscardConfirm(true); return; }
    onClose();
  };

  const handleSave = () => {
    if (dirty) onUpdate(draft);
    onClose();
  };

  const patch = (p: Partial<UISettings>) => setDraft((d) => ({ ...d, ...p }));

  return (
    <Modal
      onClose={handleCancel}
      label="Panel Settings"
      panelStyle={{
        padding: "var(--space-xl)",
        width: 480,
        maxHeight: "85vh",
        overflow: "auto",
      }}
    >
      <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>Panel Settings</h3>
      <LookFields draft={draft} patch={patch} />
      <LockIdleAndErrorFields draft={draft} patch={patch} pages={pages} />
      <MotionFields draft={draft} patch={patch} />
      <DialogButtons dirty={dirty} onCancel={handleCancel} onSave={handleSave} />
      {showDiscardConfirm && (
        <ConfirmDialog
          title="Discard Changes"
          message="Discard unsaved settings changes?"
          confirmLabel="Discard"
          destructive
          onConfirm={onClose}
          onCancel={() => setShowDiscardConfirm(false)}
        />
      )}
    </Modal>
  );
}
