/**
 * Keyboard shortcuts reference panel — toggled with Ctrl+/
 */
import { Modal } from "./Modal";

interface ShortcutGroup {
  title: string;
  shortcuts: { keys: string; description: string }[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: "Global",
    shortcuts: [
      { keys: "Ctrl+Z", description: "Undo" },
      { keys: "Ctrl+Shift+Z", description: "Redo" },
      { keys: "Ctrl+/", description: "Toggle this shortcuts panel" },
    ],
  },
  {
    title: "UI Builder",
    shortcuts: [
      { keys: "Ctrl+S", description: "Save project" },
      { keys: "Ctrl+P", description: "Toggle preview mode" },
      { keys: "Ctrl+E", description: "Toggle element palette" },
      { keys: "Ctrl+C", description: "Copy selected element" },
      { keys: "Ctrl+V", description: "Paste element" },
      { keys: "Ctrl+D", description: "Duplicate selected element" },
      { keys: "Delete / Backspace", description: "Delete selected element(s)" },
      { keys: "Arrow keys", description: "Move selected element(s)" },
      { keys: "Escape", description: "Deselect all" },
      { keys: "Shift+Click", description: "Add/remove from multi-select" },
    ],
  },
  {
    title: "Script Editor",
    shortcuts: [
      { keys: "Ctrl+Shift+R", description: "Save and reload scripts" },
      { keys: "Ctrl+S", description: "Save current script" },
    ],
  },
];

export function ShortcutsPanel({ onClose }: { onClose: () => void }) {
  return (
    <Modal
      onClose={onClose}
      label="Keyboard Shortcuts"
      overlayStyle={{ background: "rgba(0,0,0,0.5)" }}
      panelStyle={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-lg)",
        width: "min(480px, 90vw)",
        maxHeight: "70vh",
        overflow: "auto",
        boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
      }}
    >
        <div style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "var(--space-md)",
        }}>
          <h2 style={{ fontSize: "var(--font-size-lg)", color: "var(--text-primary)", margin: 0 }}>
            Keyboard Shortcuts
          </h2>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              fontSize: "var(--font-size-xl)",
              padding: "var(--space-xs)",
            }}
          >
            &times;
          </button>
        </div>

        {SHORTCUT_GROUPS.map((group) => (
          <div key={group.title} style={{ marginBottom: "var(--space-lg)" }}>
            <div style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "var(--tracking-wide)",
              fontWeight: "var(--font-weight-semibold)",
              marginBottom: "var(--space-sm)",
            }}>
              {group.title}
            </div>
            {group.shortcuts.map((s) => (
              <div
                key={s.keys}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "var(--space-xs) 0",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <span style={{ color: "var(--text-secondary)" }}>{s.description}</span>
                <kbd style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-xs)",
                  padding: "var(--space-2xs) var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  border: "1px solid var(--border-color)",
                  color: "var(--text-primary)",
                }}>
                  {s.keys}
                </kbd>
              </div>
            ))}
          </div>
        ))}

        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", textAlign: "center", marginTop: "var(--space-sm)" }}>
          Press <kbd style={{ fontFamily: "var(--font-mono)", padding: "0 var(--space-xs)", borderRadius: "var(--radius-sm)", background: "var(--bg-hover)", border: "1px solid var(--border-color)" }}>Ctrl+/</kbd> or <kbd style={{ fontFamily: "var(--font-mono)", padding: "0 var(--space-xs)", borderRadius: "var(--radius-sm)", background: "var(--bg-hover)", border: "1px solid var(--border-color)" }}>Escape</kbd> to close
        </div>
    </Modal>
  );
}
