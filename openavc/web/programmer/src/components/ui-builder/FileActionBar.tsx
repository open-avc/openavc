/**
 * The Theme Studio's file buttons, above the editor's sections, and the line
 * under them that says how the last file action went (`statusLine.ts` runs the
 * actions and keeps the line). A custom theme offers Save Changes, Save as Copy,
 * Discard, Export and Delete; a built-in one says it is read-only in Save
 * Changes' place, offers Save as Custom instead of Save as Copy, and cannot be
 * deleted.
 *
 * Each button is held by its own rule, and how a held button looks (its cursor,
 * how faded it is) follows a rule of its own that is not always the same one:
 * Save Changes is held and looks it while an action runs or nothing is unsaved;
 * Save as Custom is held while an action runs or the name is blank, but looks
 * held only while an action runs; Discard is held while an action runs or
 * nothing is unsaved, but looks held only while nothing is unsaved; Delete is
 * held while an action runs and shows it by its cursor alone.
 */
import type { CSSProperties } from "react";
import { AlertTriangle, Copy, Download, FilePlus, RotateCcw, Save, Trash2 } from "lucide-react";
import type { StatusLine } from "./statusLine";

/** What the buttons do; the Studio's handlers. */
export interface FileActions {
  saveChanges: () => void;
  saveAsCustom: () => void;
  discard: () => void;
  exportFile: () => void;
  /** Ask whether to delete the theme. */
  askDelete: () => void;
}

interface FileActionBarProps {
  isCustom: boolean;
  isDirty: boolean;
  /** A file action is running. */
  busy: boolean;
  /** The theme's name, as typed. */
  name: string;
  actions: FileActions;
}

const BUTTON: CSSProperties = { display: "flex", alignItems: "center", gap: 4, padding: "6px 12px" };

function SaveChangesButton({ isDirty, busy, onClick }: { isDirty: boolean; busy: boolean; onClick: () => void }) {
  const held = busy || !isDirty;
  return (
    <button
      onClick={onClick}
      disabled={held}
      title={isDirty ? "Save changes to this theme file" : "No changes to save"}
      style={{
        ...BUTTON,
        background: "var(--accent-bg)",
        color: "#fff",
        border: "none",
        borderRadius: 4,
        cursor: held ? "not-allowed" : "pointer",
        fontSize: 12,
        fontWeight: 600,
        opacity: held ? 0.55 : 1,
      }}
    >
      <Save size={14} /> Save Changes
    </button>
  );
}

function ReadOnlyNote() {
  return (
    <div
      title="Built-in themes can't be modified. Save as Custom to keep your edits."
      style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        padding: "6px 10px",
        background: "rgba(255,167,38,0.12)",
        color: "#ffa726",
        border: "1px solid rgba(255,167,38,0.3)",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
      }}
    >
      <AlertTriangle size={12} /> Built-in (read-only)
    </div>
  );
}

/** Save as Copy for a custom theme, Save as Custom, the bar's main button, for a built-in one. */
const SAVE_AS = {
  custom: {
    title: "Save a new copy under a different name",
    background: "var(--bg-hover)", color: "var(--text-primary)", border: "1px solid var(--border-color)",
    Icon: Copy, label: "Save as Copy",
  },
  builtin: {
    title: "Create an editable custom copy",
    background: "var(--accent-bg)", color: "#fff", border: "none",
    Icon: FilePlus, label: "Save as Custom",
  },
};

function SaveAsButton({ isCustom, busy, name, onClick }: { isCustom: boolean; busy: boolean; name: string; onClick: () => void }) {
  const { title, background, color, border, Icon, label } = isCustom ? SAVE_AS.custom : SAVE_AS.builtin;
  return (
    <button
      onClick={onClick}
      disabled={busy || !name.trim()}
      title={title}
      style={{
        ...BUTTON,
        background,
        color,
        border,
        borderRadius: 4,
        cursor: busy ? "not-allowed" : "pointer",
        fontSize: 12,
        opacity: busy ? 0.55 : 1,
      }}
    >
      <Icon size={14} />
      {label}
    </button>
  );
}

function DiscardButton({ isDirty, busy, onClick }: { isDirty: boolean; busy: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={busy || !isDirty}
      title={isDirty ? "Reload theme and discard your edits" : "No edits to discard"}
      style={{
        ...BUTTON,
        background: "var(--bg-hover)",
        border: "1px solid var(--border-color)",
        borderRadius: 4,
        cursor: !isDirty ? "not-allowed" : "pointer",
        fontSize: 12,
        color: "var(--text-secondary)",
        opacity: !isDirty ? 0.5 : 1,
      }}
    >
      <RotateCcw size={14} /> Discard
    </button>
  );
}

function ExportButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title="Download theme JSON"
      style={{
        ...BUTTON,
        background: "var(--bg-hover)",
        border: "1px solid var(--border-color)",
        borderRadius: 4,
        cursor: "pointer",
        fontSize: 12,
        color: "var(--text-secondary)",
      }}
    >
      <Download size={14} /> Export
    </button>
  );
}

function DeleteButton({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      title="Delete this custom theme"
      style={{
        ...BUTTON,
        background: "transparent",
        border: "1px solid var(--border-color)",
        borderRadius: 4,
        cursor: busy ? "not-allowed" : "pointer",
        fontSize: 12,
        color: "#ef5350",
        marginLeft: "auto",
      }}
    >
      <Trash2 size={14} /> Delete
    </button>
  );
}

export function FileActionBar({ isCustom, isDirty, busy, name, actions }: FileActionBarProps) {
  return (
    <div
      style={{
        padding: "8px 10px",
        background: "var(--bg-surface)",
        borderBottom: "1px solid var(--border-color)",
        display: "flex",
        gap: 6,
        flexWrap: "wrap",
        alignItems: "center",
      }}
    >
      {isCustom ? <SaveChangesButton isDirty={isDirty} busy={busy} onClick={actions.saveChanges} /> : <ReadOnlyNote />}
      <SaveAsButton isCustom={isCustom} busy={busy} name={name} onClick={actions.saveAsCustom} />
      <DiscardButton isDirty={isDirty} busy={busy} onClick={actions.discard} />
      <ExportButton onClick={actions.exportFile} />
      {isCustom && <DeleteButton busy={busy} onClick={actions.askDelete} />}
    </div>
  );
}

/** The line under the bar: what the last file action did, or why it failed. */
export function FileActionLine({ line }: { line: StatusLine }) {
  return (
    <div
      style={{
        padding: "6px 12px",
        fontSize: 11,
        color: line.kind === "error" ? "#ef5350" : "#66bb6a",
        background: "var(--bg-surface)",
        borderBottom: "1px solid var(--border-color)",
      }}
    >
      {line.text}
    </div>
  );
}
