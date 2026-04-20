/**
 * Keyboard shortcuts reference panel — toggled with Ctrl+/
 */

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
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10000,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)",
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
      </div>
    </div>
  );
}
