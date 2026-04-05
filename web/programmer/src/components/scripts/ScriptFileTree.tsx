import { useState } from "react";
import { Plus, Trash2, FileText, AlertTriangle } from "lucide-react";
import type { ScriptConfig } from "../../api/types";
import { CopyButton } from "../shared/CopyButton";

interface ScriptFileTreeProps {
  scripts: ScriptConfig[];
  selectedId: string | null;
  loadErrors?: Record<string, string>;
  onSelect: (id: string) => void;
  onCreate: (id: string, file: string, description: string) => void;
  onDelete: (id: string) => void;
}

export function ScriptFileTree({
  scripts,
  selectedId,
  loadErrors = {},
  onSelect,
  onCreate,
  onDelete,
}: ScriptFileTreeProps) {
  const [showCreate, setShowCreate] = useState(false);
  const [newId, setNewId] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [search, setSearch] = useState("");

  const filteredScripts = scripts.filter(s =>
    !search ||
    s.id.toLowerCase().includes(search.toLowerCase()) ||
    (s.description && s.description.toLowerCase().includes(search.toLowerCase())) ||
    s.file.toLowerCase().includes(search.toLowerCase())
  );

  const handleCreate = () => {
    if (!newId.trim()) return;
    const safeId = newId.trim().replace(/[^a-zA-Z0-9_-]/g, "_");
    const file = `${safeId}.py`;
    onCreate(safeId, file, newDesc.trim());
    setNewId("");
    setNewDesc("");
    setShowCreate(false);
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--space-sm) var(--space-md)",
          borderBottom: "1px solid var(--border-color)",
        }}
      >
        <span
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
          }}
        >
          Scripts ({scripts.length})
        </span>
        <button
          onClick={() => setShowCreate(!showCreate)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 8px",
            borderRadius: "var(--border-radius)",
            background: "var(--accent)",
            color: "#fff",
            fontSize: "var(--font-size-sm)",
            border: "none",
            cursor: "pointer",
          }}
          title="New script"
        >
          <Plus size={14} />
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-md)",
            borderBottom: "1px solid var(--border-color)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-xs)",
          }}
        >
          <input
            type="text"
            placeholder="Script ID (e.g. room_logic)"
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
            style={inputStyle}
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <input
            type="text"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            style={inputStyle}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <div style={{ display: "flex", gap: "var(--space-xs)" }}>
            <button onClick={handleCreate} style={createBtnStyle}>
              Create
            </button>
            <button
              onClick={() => setShowCreate(false)}
              style={{ ...createBtnStyle, background: "var(--bg-hover)" }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Search */}
      <div style={{ padding: "var(--space-xs) var(--space-sm)", borderBottom: "1px solid var(--border-color)" }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search scripts..."
          style={{
            width: "100%",
            padding: "var(--space-xs) var(--space-sm)",
            fontSize: "var(--font-size-sm)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
          }}
        />
      </div>

      {/* Script list */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {filteredScripts.length === 0 ? (
          <div
            style={{
              padding: "var(--space-lg)",
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
              lineHeight: 1.5,
            }}
          >
            {scripts.length === 0 ? (
              <>
                No scripts yet.
                <br />
                Click <strong>+</strong> to create one, or use a <strong>template</strong> to get started.
              </>
            ) : (
              <>No scripts match "{search}".</>
            )}
          </div>
        ) : (
          filteredScripts.map((s) => (
            <div
              key={s.id}
              onClick={() => onSelect(s.id)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "var(--space-sm) var(--space-md)",
                cursor: "pointer",
                background:
                  selectedId === s.id ? "var(--bg-hover)" : "transparent",
                borderBottom: "1px solid var(--border-color)",
              }}
              onMouseEnter={(e) => {
                if (selectedId !== s.id)
                  (e.currentTarget as HTMLElement).style.background =
                    "var(--bg-hover)";
              }}
              onMouseLeave={(e) => {
                if (selectedId !== s.id)
                  (e.currentTarget as HTMLElement).style.background =
                    "transparent";
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", minWidth: 0 }}>
                {loadErrors[s.id] ? (
                  <span title={`Load error: ${loadErrors[s.id]}`}>
                    <AlertTriangle
                      size={14}
                      style={{ color: "var(--danger, #ef4444)", flexShrink: 0 }}
                    />
                  </span>
                ) : (
                  <FileText
                    size={14}
                    style={{
                      color: s.enabled ? "var(--accent)" : "var(--text-muted)",
                      flexShrink: 0,
                    }}
                  />
                )}
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: "var(--font-size-sm)",
                      color: loadErrors[s.id]
                        ? "var(--danger, #ef4444)"
                        : s.enabled
                          ? "var(--text-primary)"
                          : "var(--text-muted)",
                      fontWeight: selectedId === s.id ? 600 : 400,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {s.file}
                  </div>
                  {loadErrors[s.id] ? (
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--danger, #ef4444)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        maxWidth: 180,
                      }}
                      title={loadErrors[s.id]}
                    >
                      {loadErrors[s.id].length > 60
                        ? loadErrors[s.id].slice(0, 60) + "..."
                        : loadErrors[s.id]}
                    </div>
                  ) : s.description ? (
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.description}
                    </div>
                  ) : null}
                  <div style={{ display: "flex", alignItems: "center", gap: 2, marginTop: 1 }}>
                    <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", opacity: 0.7 }}>
                      {s.id}
                    </code>
                    <CopyButton value={s.id} size={10} title="Copy script ID" />
                  </div>
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s.id);
                }}
                style={{
                  display: "flex",
                  padding: 4,
                  borderRadius: "var(--border-radius)",
                  background: "transparent",
                  color: "var(--text-muted)",
                  border: "none",
                  cursor: "pointer",
                  flexShrink: 0,
                }}
                title="Delete script"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};

const createBtnStyle: React.CSSProperties = {
  padding: "4px 12px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};
