/**
 * The Theme Studio's file buttons, above the editor's sections, and the line
 * under them that says how the last file action went (`statusLine.ts` runs the
 * actions and keeps the line). A custom theme offers Save Changes, Save as Copy,
 * Discard, Export and Delete; a built-in one says it is read-only in Save
 * Changes' place, offers Save as Custom instead of Save as Copy, and cannot be
 * deleted.
 *
 * Each button is held by one rule, and a held button always looks it (a
 * not-allowed cursor, faded): Save Changes and Discard while an action runs or
 * nothing is unsaved; Save as Copy / Save as Custom while an action runs or the
 * name is blank, which its tooltip then says; Delete while an action runs.
 * Export is never held.
 */
import type { CSSProperties, ReactNode } from "react";
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

const BUTTON: CSSProperties = { display: "flex", alignItems: "center", gap: 4, padding: "6px 12px", borderRadius: 4, fontSize: 12 };
/** How a button the bar is not drawing as the main one is coloured. */
const PLAIN: CSSProperties = { background: "var(--bg-hover)", border: "1px solid var(--border-color)", color: "var(--text-secondary)" };

interface FileButtonProps {
  /** It cannot be pressed now: disabled, a not-allowed cursor and faded, all from this one flag. */
  held?: boolean;
  /** How faded it is while held. */
  fade?: number;
  title: string;
  look: CSSProperties;
  onClick: () => void;
  children: ReactNode;
}

function FileButton({ held = false, fade = 0.55, title, look, onClick, children }: FileButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={held}
      title={title}
      style={{ ...BUTTON, ...look, cursor: held ? "not-allowed" : "pointer", opacity: held ? fade : 1 }}
    >
      {children}
    </button>
  );
}

function SaveChangesButton({ isDirty, busy, onClick }: { isDirty: boolean; busy: boolean; onClick: () => void }) {
  return (
    <FileButton
      held={busy || !isDirty}
      title={isDirty ? "Save changes to this theme file" : "No changes to save"}
      look={{ background: "var(--accent-bg)", color: "#fff", border: "none", fontWeight: 600 }}
      onClick={onClick}
    >
      <Save size={14} /> Save Changes
    </FileButton>
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
  const unnamed = !name.trim();
  return (
    <FileButton
      held={busy || unnamed}
      title={unnamed ? "Enter a theme name" : title}
      look={{ background, color, border }}
      onClick={onClick}
    >
      <Icon size={14} />
      {label}
    </FileButton>
  );
}

function DiscardButton({ isDirty, busy, onClick }: { isDirty: boolean; busy: boolean; onClick: () => void }) {
  return (
    <FileButton
      held={busy || !isDirty}
      fade={0.5}
      title={isDirty ? "Reload theme and discard your edits" : "No edits to discard"}
      look={PLAIN}
      onClick={onClick}
    >
      <RotateCcw size={14} /> Discard
    </FileButton>
  );
}

function ExportButton({ onClick }: { onClick: () => void }) {
  return (
    <FileButton title="Download theme JSON" look={PLAIN} onClick={onClick}>
      <Download size={14} /> Export
    </FileButton>
  );
}

function DeleteButton({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <FileButton
      held={busy}
      title="Delete this custom theme"
      look={{ background: "transparent", border: "1px solid var(--border-color)", color: "#ef5350", marginLeft: "auto" }}
      onClick={onClick}
    >
      <Trash2 size={14} /> Delete
    </FileButton>
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
