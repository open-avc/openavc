/**
 * The buttons the UI Builder docks at the end of the toolbar's pages row:
 * one each for the dialogs the page opens (Theme Studio, the project
 * stylesheet, Panel Settings), drawn alike, and the keyboard shortcuts list,
 * which is the IDE's own (Ctrl+/) and a small square of its own.
 */
import type { ReactNode } from "react";
import { FileCode, Palette, Settings } from "lucide-react";

export interface ToolbarActionsProps {
  onTheme: () => void;
  onStylesheet: () => void;
  onSettings: () => void;
}

function DialogButton({ title, icon, label, onClick }: { title: string; icon: ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-xs)",
        padding: "3px var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: "var(--bg-hover)",
        fontSize: "var(--font-size-sm)",
        border: "none",
        cursor: "pointer",
        color: "var(--text-secondary)",
      }}
    >
      {icon} {label}
    </button>
  );
}

/** Opens the IDE's shortcuts list the way its own Ctrl+/ does. */
function openShortcuts() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "/", ctrlKey: true, bubbles: true }));
}

export function ToolbarActions({ onTheme, onStylesheet, onSettings }: ToolbarActionsProps) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexShrink: 0 }}>
      <DialogButton title="Open Theme Studio" icon={<Palette size={14} />} label="Theme" onClick={onTheme} />
      <DialogButton
        title="Edit the project stylesheet (custom CSS)"
        icon={<FileCode size={14} />}
        label="Stylesheet"
        onClick={onStylesheet}
      />
      <DialogButton title="Panel Settings" icon={<Settings size={14} />} label="Settings" onClick={onSettings} />
      <button
        onClick={openShortcuts}
        title="Keyboard Shortcuts"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 24,
          height: 24,
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          fontSize: "var(--font-size-sm)",
          fontWeight: 700,
          border: "none",
          cursor: "pointer",
          color: "var(--text-secondary)",
        }}
      >
        ?
      </button>
    </div>
  );
}
