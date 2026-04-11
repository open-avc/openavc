import { useState } from "react";
import { Plus, Trash2, FileText, AlertTriangle, Cpu, ChevronDown, ChevronRight } from "lucide-react";
import type { ScriptConfig, PythonDriverInfo } from "../../api/types";
import { CopyButton } from "../shared/CopyButton";

interface ScriptFileTreeProps {
  scripts: ScriptConfig[];
  drivers: PythonDriverInfo[];
  selectedId: string | null;
  selectedType: "script" | "driver" | null;
  loadErrors?: Record<string, string>;
  onSelectScript: (id: string) => void;
  onSelectDriver: (id: string) => void;
  onCreateScript: (id: string, file: string, description: string) => void;
  onCreateDriver: () => void;
  onDeleteScript: (id: string) => void;
  onDeleteDriver: (id: string) => void;
}

export function ScriptFileTree({
  scripts,
  drivers,
  selectedId,
  selectedType,
  loadErrors = {},
  onSelectScript,
  onSelectDriver,
  onCreateScript,
  onCreateDriver,
  onDeleteScript,
  onDeleteDriver,
}: ScriptFileTreeProps) {
  const [showCreate, setShowCreate] = useState(false);
  const [newId, setNewId] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [search, setSearch] = useState("");
  const [scriptsCollapsed, setScriptsCollapsed] = useState(false);
  const [driversCollapsed, setDriversCollapsed] = useState(false);

  const filteredScripts = scripts.filter(s =>
    !search ||
    s.id.toLowerCase().includes(search.toLowerCase()) ||
    (s.description && s.description.toLowerCase().includes(search.toLowerCase())) ||
    s.file.toLowerCase().includes(search.toLowerCase())
  );

  const filteredDrivers = drivers.filter(d =>
    !search ||
    d.id.toLowerCase().includes(search.toLowerCase()) ||
    d.name.toLowerCase().includes(search.toLowerCase()) ||
    d.manufacturer.toLowerCase().includes(search.toLowerCase())
  );

  const handleCreateScript = () => {
    if (!newId.trim()) return;
    const safeId = newId.trim().replace(/[^a-zA-Z0-9_-]/g, "_");
    const file = `${safeId}.py`;
    onCreateScript(safeId, file, newDesc.trim());
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
      {/* Search */}
      <div style={{ padding: "var(--space-xs) var(--space-sm)", borderBottom: "1px solid var(--border-color)" }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search..."
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

      <div style={{ flex: 1, overflow: "auto" }}>
        {/* === SCRIPTS SECTION === */}
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "var(--space-sm) var(--space-md)",
              borderBottom: "1px solid var(--border-color)",
              cursor: "pointer",
              userSelect: "none",
            }}
            onClick={() => setScriptsCollapsed(!scriptsCollapsed)}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {scriptsCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
              <span style={sectionHeaderStyle}>
                Scripts ({scripts.length})
              </span>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowCreate(!showCreate);
              }}
              style={addBtnStyle}
              title="New script"
            >
              <Plus size={14} />
            </button>
          </div>

          {/* Create script form */}
          {showCreate && !scriptsCollapsed && (
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
                onKeyDown={(e) => e.key === "Enter" && handleCreateScript()}
              />
              <input
                type="text"
                placeholder="Description (optional)"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                style={inputStyle}
                onKeyDown={(e) => e.key === "Enter" && handleCreateScript()}
              />
              <div style={{ display: "flex", gap: "var(--space-xs)" }}>
                <button onClick={handleCreateScript} style={createBtnStyle}>
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

          {/* Script list */}
          {!scriptsCollapsed && (
            <>
              {filteredScripts.length === 0 && scripts.length === 0 ? (
                <div style={emptyStyle}>
                  No scripts yet. Click <strong>+</strong> to create one.
                </div>
              ) : (
                filteredScripts.map((s) => (
                  <div
                    key={`script-${s.id}`}
                    onClick={() => onSelectScript(s.id)}
                    style={{
                      ...itemStyle,
                      background:
                        selectedId === s.id && selectedType === "script"
                          ? "var(--bg-hover)"
                          : "transparent",
                    }}
                    onMouseEnter={(e) => {
                      if (!(selectedId === s.id && selectedType === "script"))
                        (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
                    }}
                    onMouseLeave={(e) => {
                      if (!(selectedId === s.id && selectedType === "script"))
                        (e.currentTarget as HTMLElement).style.background = "transparent";
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", minWidth: 0 }}>
                      {loadErrors[s.id] ? (
                        <span title={`Load error: ${loadErrors[s.id]}`}>
                          <AlertTriangle size={14} style={{ color: "var(--danger, #ef4444)", flexShrink: 0 }} />
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
                        <div style={itemNameStyle(loadErrors[s.id], s.enabled, selectedId === s.id && selectedType === "script")}>
                          {s.file}
                        </div>
                        {loadErrors[s.id] ? (
                          <div style={errorDescStyle} title={loadErrors[s.id]}>
                            {loadErrors[s.id].length > 60 ? loadErrors[s.id].slice(0, 60) + "..." : loadErrors[s.id]}
                          </div>
                        ) : s.description ? (
                          <div style={descStyle}>{s.description}</div>
                        ) : null}
                        <div style={{ display: "flex", alignItems: "center", gap: 2, marginTop: 1 }}>
                          <code style={idStyle}>{s.id}</code>
                          <CopyButton value={s.id} size={10} title="Copy script ID" />
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); onDeleteScript(s.id); }}
                      style={deleteBtnStyle}
                      title="Delete script"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))
              )}
            </>
          )}
        </div>

        {/* === PYTHON DRIVERS SECTION === */}
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "var(--space-sm) var(--space-md)",
              borderBottom: "1px solid var(--border-color)",
              borderTop: "1px solid var(--border-color)",
              cursor: "pointer",
              userSelect: "none",
            }}
            onClick={() => setDriversCollapsed(!driversCollapsed)}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {driversCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
              <span style={sectionHeaderStyle}>
                Python Drivers ({drivers.length})
              </span>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); onCreateDriver(); }}
              style={addBtnStyle}
              title="New Python driver"
            >
              <Plus size={14} />
            </button>
          </div>

          {/* Driver list */}
          {!driversCollapsed && (
            <>
              {filteredDrivers.length === 0 && drivers.length === 0 ? (
                <div style={emptyStyle}>
                  No Python drivers yet.
                  <br />
                  Click <strong>+</strong> to create one, or use the{" "}
                  <strong>Driver Builder</strong> for YAML drivers.
                </div>
              ) : (
                filteredDrivers.map((d) => (
                  <div
                    key={`driver-${d.id}`}
                    onClick={() => onSelectDriver(d.id)}
                    style={{
                      ...itemStyle,
                      background:
                        selectedId === d.id && selectedType === "driver"
                          ? "var(--bg-hover)"
                          : "transparent",
                    }}
                    onMouseEnter={(e) => {
                      if (!(selectedId === d.id && selectedType === "driver"))
                        (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
                    }}
                    onMouseLeave={(e) => {
                      if (!(selectedId === d.id && selectedType === "driver"))
                        (e.currentTarget as HTMLElement).style.background = "transparent";
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", minWidth: 0 }}>
                      {d.load_error ? (
                        <span title={`Load error: ${d.load_error}`}>
                          <AlertTriangle size={14} style={{ color: "var(--danger, #ef4444)", flexShrink: 0 }} />
                        </span>
                      ) : (
                        <Cpu
                          size={14}
                          style={{
                            color: d.loaded ? "var(--accent)" : "var(--text-muted)",
                            flexShrink: 0,
                          }}
                        />
                      )}
                      <div style={{ minWidth: 0 }}>
                        <div style={itemNameStyle(d.load_error, d.loaded, selectedId === d.id && selectedType === "driver")}>
                          {d.name}
                        </div>
                        {d.manufacturer && (
                          <div style={descStyle}>{d.manufacturer}</div>
                        )}
                        <div style={{ display: "flex", alignItems: "center", gap: 2, marginTop: 1 }}>
                          <code style={idStyle}>{d.id}</code>
                          <CopyButton value={d.id} size={10} title="Copy driver ID" />
                          {d.devices_using.length > 0 && (
                            <span
                              style={{
                                marginLeft: 4,
                                fontSize: 10,
                                color: "var(--text-muted)",
                                background: "var(--bg-hover)",
                                padding: "0 4px",
                                borderRadius: 3,
                              }}
                              title={`Used by: ${d.devices_using.join(", ")}`}
                            >
                              {d.devices_using.length} device{d.devices_using.length !== 1 ? "s" : ""}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); onDeleteDriver(d.id); }}
                      disabled={d.devices_using.length > 0}
                      style={{
                        ...deleteBtnStyle,
                        opacity: d.devices_using.length > 0 ? 0.3 : 1,
                        cursor: d.devices_using.length > 0 ? "not-allowed" : "pointer",
                      }}
                      title={d.devices_using.length > 0 ? `Cannot delete: used by ${d.devices_using.join(", ")}` : "Delete driver"}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const sectionHeaderStyle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  fontWeight: 600,
};

const addBtnStyle: React.CSSProperties = {
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
};

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

const itemStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "var(--space-sm) var(--space-md)",
  cursor: "pointer",
  borderBottom: "1px solid var(--border-color)",
};

const emptyStyle: React.CSSProperties = {
  padding: "var(--space-md)",
  textAlign: "center",
  color: "var(--text-muted)",
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.5,
};

function itemNameStyle(error: string | null | undefined, active: boolean, selected: boolean): React.CSSProperties {
  return {
    fontSize: "var(--font-size-sm)",
    color: error ? "var(--danger, #ef4444)" : active ? "var(--text-primary)" : "var(--text-muted)",
    fontWeight: selected ? 600 : 400,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
}

const descStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const errorDescStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--danger, #ef4444)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  maxWidth: 180,
};

const idStyle: React.CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  fontFamily: "var(--font-mono)",
  opacity: 0.7,
};

const deleteBtnStyle: React.CSSProperties = {
  display: "flex",
  padding: 4,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
  flexShrink: 0,
};
