/**
 * Keyboard shortcuts reference panel — toggled with Ctrl+/
 */
import { BUILDER_SHORTCUTS, type ShortcutRow } from "../../views/ui-builder/builderShortcuts";
import { Modal } from "./Modal";

interface ShortcutGroup {
  title: string;
  shortcuts: readonly ShortcutRow[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: "Global",
    shortcuts: [
      { keys: "Ctrl+/", description: "Toggle this shortcuts panel" },
    ],
  },
  { title: "UI Builder", shortcuts: BUILDER_SHORTCUTS },
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
              fontSize: 18,
              padding: 4,
            }}
          >
            &times;
          </button>
        </div>

        {SHORTCUT_GROUPS.map((group) => (
          <div key={group.title} style={{ marginBottom: "var(--space-lg)" }}>
            <div style={{
              fontSize: 11,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
              fontWeight: 600,
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
                  padding: "4px 0",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <span style={{ color: "var(--text-secondary)" }}>{s.description}</span>
                <kbd style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  padding: "1px 6px",
                  borderRadius: 3,
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

        <div style={{ fontSize: 11, color: "var(--text-muted)", textAlign: "center", marginTop: "var(--space-sm)" }}>
          Press <kbd style={{ fontFamily: "var(--font-mono)", padding: "0 4px", borderRadius: 2, background: "var(--bg-hover)", border: "1px solid var(--border-color)" }}>Ctrl+/</kbd> or <kbd style={{ fontFamily: "var(--font-mono)", padding: "0 4px", borderRadius: 2, background: "var(--bg-hover)", border: "1px solid var(--border-color)" }}>Escape</kbd> to close
        </div>
    </Modal>
  );
}
