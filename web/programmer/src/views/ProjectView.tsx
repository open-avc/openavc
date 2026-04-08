import { useState, useEffect, useCallback } from "react";
import {
  Save,
  Download,
  Upload,
  Plus,
  FilePlus,
  Trash2,
  Copy,
  FolderOpen,
  MoreHorizontal,
} from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { Dialog } from "../components/shared/Dialog";
import { useProjectStore } from "../store/projectStore";
import * as api from "../api/restClient";
import type { LibraryProject } from "../api/types";
import { showError, showInfo, showSuccess } from "../store/toastStore";

export function ProjectView() {
  const project = useProjectStore((s) => s.project);
  const dirty = useProjectStore((s) => s.dirty);
  const saving = useProjectStore((s) => s.saving);
  const save = useProjectStore((s) => s.save);
  const updateProject = useProjectStore((s) => s.updateProject);
  const setProject = useProjectStore((s) => s.setProject);
  const loadProject = useProjectStore((s) => s.load);

  // Library state
  const [library, setLibrary] = useState<LibraryProject[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  // Dialog states
  const [showSaveAs, setShowSaveAs] = useState(false);
  const [showBlank, setShowBlank] = useState(false);
  const [showOpen, setShowOpen] = useState<string | null>(null);
  const [showDuplicate, setShowDuplicate] = useState<string | null>(null);
  const [showDelete, setShowDelete] = useState<string | null>(null);
  const [openMenu, setOpenMenu] = useState<string | null>(null);

  // Backup state
  const [backups, setBackups] = useState<api.BackupInfo[]>([]);
  const [backupsLoading, setBackupsLoading] = useState(false);
  const [restoreConfirm, setRestoreConfirm] = useState<string | null>(null);
  const [creatingBackup, setCreatingBackup] = useState(false);

  // Form states
  const [saveAsId, setSaveAsId] = useState("");
  const [saveAsName, setSaveAsName] = useState("");
  const [saveAsDesc, setSaveAsDesc] = useState("");
  const [blankName, setBlankName] = useState("New Room");
  const [blankId, setBlankId] = useState("");
  const [openName, setOpenName] = useState("");
  const [openId, setOpenId] = useState("");
  const [dupId, setDupId] = useState("");
  const [dupName, setDupName] = useState("");

  const refreshLibrary = useCallback(async () => {
    try {
      setLibraryLoading(true);
      setLibrary(await api.listLibrary());
    } catch {
      // Silently fail — library section just shows empty
    } finally {
      setLibraryLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshLibrary();
  }, [refreshLibrary]);

  const refreshBackups = useCallback(async () => {
    try {
      setBackupsLoading(true);
      setBackups(await api.listBackups());
    } catch {
      // Silently fail
    } finally {
      setBackupsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshBackups();
  }, [refreshBackups]);

  // Close overflow menu on outside click
  useEffect(() => {
    if (!openMenu) return;
    const close = () => setOpenMenu(null);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [openMenu]);

  // --- Handlers ---

  const handleExportCurrent = useCallback(() => {
    if (!project) return;
    const blob = new Blob([JSON.stringify(project, null, 4)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${project.project.id}.avc`;
    a.click();
    URL.revokeObjectURL(url);
  }, [project]);

  const handleImportCurrent = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".avc,.json";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const parsed = JSON.parse(text);
        setProject(parsed);
        // Save to server and reload engine so imported project is active
        const store = useProjectStore.getState();
        store.update(parsed);
        await store.save();
        await api.reloadProject();
      } catch {
        showError("Invalid project file");
      }
    };
    input.click();
  }, [setProject]);

  const handleSaveAs = async () => {
    if (!saveAsId.trim() || !saveAsName.trim()) return;
    setBusy(true);
    try {
      await api.saveToLibrary({ id: saveAsId.trim(), name: saveAsName.trim(), description: saveAsDesc.trim() });
      setShowSaveAs(false);
      await refreshLibrary();
    } catch (e) {
      showError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleBlank = async () => {
    if (!blankName.trim()) return;
    setBusy(true);
    try {
      await api.createBlankProject(blankName.trim(), blankId.trim() || undefined);
      setShowBlank(false);
      setBlankName("New Room");
      setBlankId("");
      await loadProject();
    } catch (e) {
      showError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleOpen = async () => {
    if (!showOpen || !openName.trim()) return;
    setBusy(true);
    try {
      await api.openFromLibrary(showOpen, openName.trim(), openId.trim() || undefined);
      setShowOpen(null);
      await loadProject();
    } catch (e) {
      showError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDuplicate = async () => {
    if (!showDuplicate || !dupId.trim() || !dupName.trim()) return;
    setBusy(true);
    try {
      await api.duplicateLibraryProject(showDuplicate, dupId.trim(), dupName.trim());
      setShowDuplicate(null);
      await refreshLibrary();
    } catch (e) {
      showError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!showDelete) return;
    setBusy(true);
    try {
      await api.deleteLibraryProject(showDelete);
      setShowDelete(null);
      await refreshLibrary();
    } catch (e) {
      showError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleExportLib = async (id: string) => {
    try {
      await api.exportLibraryProject(id);
    } catch (e) {
      showError(String(e));
    }
  };

  const handleImportLib = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".avc,.zip";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setBusy(true);
      showInfo("Importing project...");
      try {
        const result = await api.importToLibrary(file);
        await refreshLibrary();
        // Show driver installation/warning info
        const msgs: string[] = [];
        if (result.installed_drivers && result.installed_drivers.length > 0) {
          msgs.push(`Installed ${result.installed_drivers.length} bundled driver(s): ${result.installed_drivers.join(", ")}`);
        }
        if (result.warnings && result.warnings.length > 0) {
          msgs.push("", "Warnings:", ...result.warnings,
            "", "Devices using missing drivers will appear as orphaned. Install drivers from Community Drivers to activate them.");
        }
        if (msgs.length > 0) {
          showInfo("Project imported. " + msgs.join(" "));
        }
      } catch (e) {
        showError(String(e));
      } finally {
        setBusy(false);
      }
    };
    input.click();
  };

  const handleRestore = async (filename: string) => {
    setBusy(true);
    try {
      await api.restoreBackup(filename);
      await loadProject();
      showSuccess("Project restored successfully.");
      await refreshBackups();
    } catch (e) {
      showError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const openSaveAs = () => {
    if (!project) return;
    setSaveAsId(project.project.id);
    setSaveAsName(project.project.name);
    setSaveAsDesc(project.project.description);
    setShowSaveAs(true);
  };

  const openOpenDialog = (lib: LibraryProject) => {
    setOpenName(lib.name);
    setOpenId("");
    setShowOpen(lib.id);
    setOpenMenu(null);
  };

  const openDuplicateDialog = (lib: LibraryProject) => {
    setDupId(lib.id + "_copy");
    setDupName(lib.name + " (Copy)");
    setShowDuplicate(lib.id);
    setOpenMenu(null);
  };

  // --- Styles ---

  const btnStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-xs)",
    padding: "var(--space-xs) var(--space-md)",
    borderRadius: "var(--border-radius)",
    background: "var(--bg-hover)",
    fontSize: "var(--font-size-sm)",
    cursor: "pointer",
  };

  const accentBtnStyle: React.CSSProperties = {
    ...btnStyle,
    background: dirty ? "var(--accent)" : "var(--bg-hover)",
    color: dirty ? "var(--text-on-accent)" : "var(--text-muted)",
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  };

  const inputStyle: React.CSSProperties = { width: "100%", maxWidth: 480 };
  const fieldStyle: React.CSSProperties = { marginBottom: "var(--space-lg)" };
  const dialogInputStyle: React.CSSProperties = { width: "100%", marginBottom: "var(--space-md)" };

  if (!project) {
    return (
      <ViewContainer title="Program">
        <p style={{ color: "var(--text-secondary)" }}>Loading project...</p>
      </ViewContainer>
    );
  }

  const deleteTarget = library.find((l) => l.id === showDelete);

  return (
    <ViewContainer
      title="Program"
      actions={
        <>
          <button onClick={() => setShowBlank(true)} style={btnStyle}>
            <FilePlus size={14} /> New
          </button>
          <button onClick={handleImportCurrent} style={btnStyle}>
            <Upload size={14} /> Import
          </button>
          <button onClick={handleExportCurrent} style={btnStyle}>
            <Download size={14} /> Export
          </button>
          <button onClick={openSaveAs} style={btnStyle}>
            <Plus size={14} /> Save As
          </button>
          <button
            onClick={() => save()}
            disabled={!dirty || saving}
            style={{ ...accentBtnStyle, opacity: saving ? 0.6 : 1 }}
          >
            <Save size={14} /> {saving ? "Saving..." : "Save"}
          </button>
        </>
      }
    >
      {/* Current Project */}
      <div style={{ maxWidth: 600 }}>
        <div style={fieldStyle}>
          <label style={labelStyle}>Project Name</label>
          <input
            style={inputStyle}
            value={project.project.name}
            onChange={(e) => updateProject({ name: e.target.value })}
          />
        </div>
        <div style={fieldStyle}>
          <label style={labelStyle}>Description</label>
          <textarea
            style={{ ...inputStyle, minHeight: 80, resize: "vertical" }}
            value={project.project.description}
            onChange={(e) => updateProject({ description: e.target.value })}
          />
        </div>
        <div style={fieldStyle}>
          <label style={labelStyle}>Project ID</label>
          <input style={inputStyle} value={project.project.id} readOnly />
        </div>
        <div style={fieldStyle}>
          <label style={labelStyle}>Project Format</label>
          <input style={inputStyle} value={"v" + project.openavc_version} readOnly />
        </div>
        {project.project.created && (
          <div style={fieldStyle}>
            <label style={labelStyle}>Created</label>
            <input style={inputStyle} value={new Date(project.project.created).toLocaleString()} readOnly />
          </div>
        )}
        {project.project.modified && (
          <div style={fieldStyle}>
            <label style={labelStyle}>Last Modified</label>
            <input style={inputStyle} value={new Date(project.project.modified).toLocaleString()} readOnly />
          </div>
        )}

      </div>

      {/* Project Library */}
      <div style={{ marginTop: "var(--space-2xl)", maxWidth: 600 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "var(--space-md)",
          }}
        >
          <h3
            style={{
              fontSize: "var(--font-size-base)",
              color: "var(--text-secondary)",
            }}
          >
            Project Library
          </h3>
          <button onClick={handleImportLib} style={btnStyle} disabled={busy}>
            <Upload size={14} /> Import to Library
          </button>
        </div>

        <div
          style={{
            background: "var(--bg-surface)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            overflow: "hidden",
          }}
        >
          {libraryLoading ? (
            <div style={{ padding: "var(--space-xl)", color: "var(--text-muted)", textAlign: "center" }}>
              Loading...
            </div>
          ) : library.length === 0 ? (
            <div style={{ padding: "var(--space-xl)", color: "var(--text-muted)", textAlign: "center" }}>
              No saved projects yet. Use <strong>Save As</strong> to add the current project,
              or <strong>Import</strong> a .avc file.
            </div>
          ) : (
            library.map((lib, i) => (
              <div
                key={lib.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "var(--space-md) var(--space-lg)",
                  borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                  cursor: "pointer",
                  transition: "background var(--transition-fast)",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "")}
                onClick={() => openOpenDialog(lib)}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {lib.name}
                  </div>
                  <div style={{ fontSize: "var(--font-size-xs, 11px)", color: "var(--text-muted)", marginTop: 2 }}>
                    {lib.device_count} device{lib.device_count !== 1 ? "s" : ""} · {lib.page_count} page
                    {lib.page_count !== 1 ? "s" : ""} · {lib.macro_count} macro
                    {lib.macro_count !== 1 ? "s" : ""}
                    {lib.script_count > 0 && ` · ${lib.script_count} script${lib.script_count !== 1 ? "s" : ""}`}
                  </div>
                  {lib.required_drivers && lib.required_drivers.length > 0 && (
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
                      {lib.required_drivers.map((d: string) => (
                        <span
                          key={d}
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 3,
                            background: "var(--bg-hover)",
                            color: "var(--text-muted)",
                          }}
                        >
                          {d}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div style={{ position: "relative", flexShrink: 0 }}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpenMenu(openMenu === lib.id ? null : lib.id);
                    }}
                    style={{
                      padding: "var(--space-xs)",
                      borderRadius: "var(--border-radius)",
                      background: "transparent",
                      cursor: "pointer",
                      display: "flex",
                    }}
                  >
                    <MoreHorizontal size={16} />
                  </button>
                  {openMenu === lib.id && (
                    <div
                      style={{
                        position: "absolute",
                        right: 0,
                        top: "100%",
                        zIndex: 100,
                        background: "var(--bg-elevated)",
                        borderRadius: "var(--border-radius)",
                        border: "1px solid var(--border-color)",
                        boxShadow: "var(--shadow-lg)",
                        minWidth: 140,
                        overflow: "hidden",
                      }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MenuBtn icon={<FolderOpen size={14} />} label="Open" onClick={() => openOpenDialog(lib)} />
                      <MenuBtn icon={<Copy size={14} />} label="Duplicate" onClick={() => openDuplicateDialog(lib)} />
                      <MenuBtn icon={<Download size={14} />} label="Export" onClick={() => { handleExportLib(lib.id); setOpenMenu(null); }} />
                      <MenuBtn
                        icon={<Trash2 size={14} />}
                        label="Delete"
                        danger
                        onClick={() => { setShowDelete(lib.id); setOpenMenu(null); }}
                      />
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        <p
          style={{
            marginTop: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          Click a project to open it. Opening a project replaces the running one — a backup is created automatically.
        </p>
      </div>

      {/* Backups */}
      <div style={{ marginTop: "var(--space-2xl)", maxWidth: 600 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-md)" }}>
          <h3 style={{ fontSize: "var(--font-size-base)", color: "var(--text-secondary)", margin: 0 }}>
            Backups
          </h3>
          <button
            onClick={async () => {
              setCreatingBackup(true);
              try {
                await api.createBackup();
                showSuccess("Backup created.");
                await refreshBackups();
              } catch {
                showError("Failed to create backup.");
              } finally {
                setCreatingBackup(false);
              }
            }}
            disabled={busy || creatingBackup}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "var(--accent-text)",
              fontSize: "var(--font-size-sm)",
              cursor: "pointer",
              opacity: creatingBackup ? 0.6 : 1,
            }}
          >
            {creatingBackup ? "Creating..." : "Create Backup"}
          </button>
        </div>
        <div style={{
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          overflow: "hidden",
        }}>
          {backupsLoading ? (
            <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", textAlign: "center" }}>
              Loading...
            </div>
          ) : backups.length === 0 ? (
            <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", textAlign: "center" }}>
              No backups yet. Backups are created automatically before important operations, or click Create Backup.
            </div>
          ) : (
            backups.map((b, i) => (
              <div
                key={b.filename}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "var(--space-sm) var(--space-lg)",
                  borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                }}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>
                    {b.reason}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {b.timestamp ? new Date(b.timestamp).toLocaleString() : "Unknown"} · {Math.round(b.size / 1024)} KB{b.format === "legacy" ? " · Legacy" : ""}
                  </div>
                </div>
                <button
                  onClick={() => setRestoreConfirm(b.filename)}
                  disabled={busy}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                    cursor: "pointer",
                  }}
                >
                  Restore
                </button>
              </div>
            ))
          )}
        </div>
        <p style={{
          marginTop: "var(--space-md)",
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          lineHeight: 1.5,
        }}>
          Backups are created automatically before project replacement, AI changes, and cloud updates. Restoring replaces the current project and reloads.
        </p>
      </div>

      {/* --- Dialogs --- */}

      {showSaveAs && (
        <Dialog title="Save to Library" onClose={() => setShowSaveAs(false)}>
          <label style={labelStyle}>Project ID</label>
          <input style={dialogInputStyle} value={saveAsId} onChange={(e) => setSaveAsId(e.target.value)} placeholder="e.g. my_boardroom" />
          <label style={labelStyle}>Name</label>
          <input style={dialogInputStyle} value={saveAsName} onChange={(e) => setSaveAsName(e.target.value)} placeholder="e.g. Board Room Setup" />
          <label style={labelStyle}>Description</label>
          <textarea style={{ ...dialogInputStyle, minHeight: 60, resize: "vertical" }} value={saveAsDesc} onChange={(e) => setSaveAsDesc(e.target.value)} placeholder="Optional" />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
            <button onClick={() => setShowSaveAs(false)} style={btnStyle}>Cancel</button>
            <button onClick={handleSaveAs} style={{ ...btnStyle, background: "var(--accent)", color: "var(--text-on-accent)" }} disabled={busy || !saveAsId.trim() || !saveAsName.trim()}>
              {busy ? "Saving..." : "Save"}
            </button>
          </div>
        </Dialog>
      )}

      {showBlank && (
        <Dialog title="New Blank Project" onClose={() => setShowBlank(false)}>
          <p style={{ color: "var(--text-secondary)", marginBottom: "var(--space-lg)", fontSize: "var(--font-size-sm)" }}>
            This replaces the running project with an empty one. A backup is created automatically.
          </p>
          <label style={labelStyle}>Project Name</label>
          <input style={dialogInputStyle} value={blankName} onChange={(e) => setBlankName(e.target.value)} placeholder="e.g. New Room" />
          <label style={labelStyle}>Project ID (optional)</label>
          <input style={dialogInputStyle} value={blankId} onChange={(e) => setBlankId(e.target.value)} placeholder="Auto-generated from name" />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
            <button onClick={() => setShowBlank(false)} style={btnStyle}>Cancel</button>
            <button onClick={handleBlank} style={{ ...btnStyle, background: "var(--accent)", color: "var(--text-on-accent)" }} disabled={busy || !blankName.trim()}>
              {busy ? "Creating..." : "Create"}
            </button>
          </div>
        </Dialog>
      )}

      {showOpen && (
        <Dialog title="Open Project" onClose={() => setShowOpen(null)}>
          <p style={{ color: "var(--text-secondary)", marginBottom: "var(--space-lg)", fontSize: "var(--font-size-sm)" }}>
            This replaces the running project. A backup is created automatically.
          </p>
          <label style={labelStyle}>Project Name</label>
          <input style={dialogInputStyle} value={openName} onChange={(e) => setOpenName(e.target.value)} />
          <label style={labelStyle}>Project ID (optional)</label>
          <input style={dialogInputStyle} value={openId} onChange={(e) => setOpenId(e.target.value)} placeholder="Auto-generated from name" />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
            <button onClick={() => setShowOpen(null)} style={btnStyle}>Cancel</button>
            <button onClick={handleOpen} style={{ ...btnStyle, background: "var(--accent)", color: "var(--text-on-accent)" }} disabled={busy || !openName.trim()}>
              {busy ? "Opening..." : "Open"}
            </button>
          </div>
        </Dialog>
      )}

      {showDuplicate && (
        <Dialog title="Duplicate Project" onClose={() => setShowDuplicate(null)}>
          <label style={labelStyle}>New Project ID</label>
          <input style={dialogInputStyle} value={dupId} onChange={(e) => setDupId(e.target.value)} />
          <label style={labelStyle}>New Name</label>
          <input style={dialogInputStyle} value={dupName} onChange={(e) => setDupName(e.target.value)} />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
            <button onClick={() => setShowDuplicate(null)} style={btnStyle}>Cancel</button>
            <button onClick={handleDuplicate} style={{ ...btnStyle, background: "var(--accent)", color: "var(--text-on-accent)" }} disabled={busy || !dupId.trim() || !dupName.trim()}>
              {busy ? "Duplicating..." : "Duplicate"}
            </button>
          </div>
        </Dialog>
      )}

      {showDelete && deleteTarget && (
        <ConfirmDialog
          title={`Delete "${deleteTarget.name}"?`}
          message="This permanently removes this project from the library. This cannot be undone."
          confirmLabel="Delete"
          onConfirm={handleDelete}
          onCancel={() => setShowDelete(null)}
        />
      )}
      {restoreConfirm && (
        <ConfirmDialog
          title="Restore Backup"
          message={`Restore from "${restoreConfirm}"? This will replace the current project.`}
          confirmLabel="Restore"
          onConfirm={() => { const f = restoreConfirm; setRestoreConfirm(null); handleRestore(f); }}
          onCancel={() => setRestoreConfirm(null)}
        />
      )}
    </ViewContainer>
  );
}

// --- Overflow menu button ---

function MenuBtn({
  icon,
  label,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        width: "100%",
        padding: "var(--space-sm) var(--space-md)",
        background: "transparent",
        color: danger ? "var(--color-error)" : "var(--text-primary)",
        fontSize: "var(--font-size-sm)",
        cursor: "pointer",
        textAlign: "left",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {icon} {label}
    </button>
  );
}
