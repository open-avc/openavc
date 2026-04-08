import { useEffect, useState, useRef, useCallback } from "react";
import yaml from "js-yaml";
import { useDriverBuilderStore } from "../store/driverBuilderStore";
import { DriverList } from "../components/driver-builder/DriverList";
import { DriverEditor } from "../components/driver-builder/DriverEditor";
import { CommunityBrowser } from "../components/driver-builder/CommunityBrowser";
import { InstalledDriversView } from "../components/driver-builder/InstalledDriversView";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import type { DriverDefinition } from "../api/types";

/** Parse a driver definition from text — supports both JSON and YAML. */
function parseDriverDefinition(text: string): DriverDefinition {
  // Try JSON first (faster, more common from our own exports)
  try {
    return JSON.parse(text) as DriverDefinition;
  } catch {
    // Fall through to YAML
  }
  // Try YAML (community drivers are YAML)
  const parsed = yaml.load(text);
  if (parsed && typeof parsed === "object") {
    return parsed as DriverDefinition;
  }
  throw new SyntaxError("File is not valid JSON or YAML");
}

type ViewTab = "installed" | "create" | "browse-community";

/** Standalone view — kept for backward compatibility if route is hit directly. */
export function DriverBuilderView() {
  return <DriverPanel />;
}

/** Embeddable driver management panel — used inside DeviceView sub-tabs. */
export function DriverPanel() {
  const [viewTab, setViewTab] = useState<ViewTab>("installed");
  const {
    definitions,
    selectedId,
    draft,
    dirty,
    saving,
    loading,
    error,
    loadDefinitions,
    selectDriver,
    newDriver,
    updateDraft,
    save,
    deleteDriver,
    importDriver,
    exportDriver,
  } = useDriverBuilderStore();

  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadDefinitions();
  }, [loadDefinitions]);

  const handleDelete = async (id: string) => {
    setDeleteConfirm(id);
  };

  const confirmDelete = async () => {
    if (deleteConfirm) {
      await deleteDriver(deleteConfirm);
      setDeleteConfirm(null);
    }
  };

  const handleImportClick = () => {
    setShowImportDialog(true);
  };

  const handleImportFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const definition = parseDriverDefinition(text);
        await importDriver(definition);
        setShowImportDialog(false);
      } catch (err) {
        useDriverBuilderStore.setState({
          error: `Failed to import: ${err instanceof SyntaxError ? "Invalid file (not JSON or YAML)" : String(err)}`,
        });
      }
      // Reset the input so the same file can be re-selected
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [importDriver]
  );

  const handleExport = (id: string) => {
    exportDriver(id);
  };

  const isNew = selectedId === null && dirty;
  const showEditor = selectedId !== null || isNew;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "var(--space-sm) 0",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          flexShrink: 0,
        }}
      >
        <ViewTabButton
          label="Installed"
          active={viewTab === "installed"}
          onClick={() => setViewTab("installed")}
        />
        <ViewTabButton
          label="Create"
          active={viewTab === "create"}
          onClick={() => setViewTab("create")}
        />
        <ViewTabButton
          label="Browse Community"
          active={viewTab === "browse-community"}
          onClick={() => setViewTab("browse-community")}
        />
        {loading && viewTab === "create" && (
          <span
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-muted)",
            }}
          >
            Loading...
          </span>
        )}
      </div>

      {viewTab === "installed" ? (
        <InstalledDriversView />
      ) : viewTab === "create" ? (
        <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
          <DriverList
            definitions={definitions}
            selectedId={selectedId}
            onSelect={selectDriver}
            onNew={newDriver}
            onImport={handleImportClick}
            onExport={handleExport}
            onDelete={handleDelete}
          />

          <div style={{ flex: 1, overflow: "hidden" }}>
            {showEditor ? (
              <DriverEditor
                draft={draft}
                dirty={dirty}
                saving={saving}
                error={error}
                isNew={isNew}
                onUpdate={updateDraft}
                onSave={save}
                onExport={() => selectedId && handleExport(selectedId)}
              />
            ) : (
              <EmptyState />
            )}
          </div>
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          <CommunityBrowser />
        </div>
      )}

      {/* Hidden file input for import */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".avcdriver,.json,.yaml,.yml"
        style={{ display: "none" }}
        onChange={handleImportFile}
      />

      {showImportDialog && (
        <ImportDialog
          onFile={() => fileInputRef.current?.click()}
          onPaste={async (text) => {
            try {
              const definition = parseDriverDefinition(text);
              await importDriver(definition);
              setShowImportDialog(false);
            } catch (err) {
              useDriverBuilderStore.setState({
                error: `Failed to import: ${err instanceof SyntaxError ? "Invalid JSON or YAML" : String(err)}`,
              });
            }
          }}
          onClose={() => setShowImportDialog(false)}
          error={error}
        />
      )}

      {deleteConfirm && (
        <ConfirmDialog
          title="Delete Driver"
          message={`Are you sure you want to delete the driver "${deleteConfirm}"? This cannot be undone.`}
          confirmLabel="Delete"
          onConfirm={confirmDelete}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </div>
  );
}

// --- View Tab Button ---

function ViewTabButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "var(--space-xs) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: active ? "var(--accent)" : "var(--bg-hover)",
        color: active ? "#fff" : "var(--text-primary)",
        fontSize: "var(--font-size-sm)",
        fontWeight: active ? 600 : 400,
        border: "none",
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}

// --- Import Dialog ---

function ImportDialog({
  onFile,
  onPaste,
  onClose,
  error,
}: {
  onFile: () => void;
  onPaste: (json: string) => void;
  onClose: () => void;
  error: string | null;
}) {
  const [pasteText, setPasteText] = useState("");

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xl)",
          width: 520,
          maxHeight: "80vh",
          overflow: "auto",
          boxShadow: "var(--shadow-lg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3
          style={{
            fontSize: "var(--font-size-lg)",
            marginBottom: "var(--space-lg)",
          }}
        >
          Import Driver Definition
        </h3>

        <p
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            marginBottom: "var(--space-lg)",
          }}
        >
          Import a driver from an .avcdriver file or paste the definition
          directly. Legacy .json files are also accepted.
        </p>

        {error && (
          <div
            style={{
              background: "rgba(244,67,54,0.15)",
              color: "var(--color-error)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-md)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {error}
          </div>
        )}

        <button
          onClick={onFile}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "var(--space-sm)",
            width: "100%",
            padding: "var(--space-lg)",
            borderRadius: "var(--border-radius)",
            border: "2px dashed var(--border-color)",
            background: "var(--bg-surface)",
            fontSize: "var(--font-size-sm)",
            cursor: "pointer",
            marginBottom: "var(--space-lg)",
          }}
        >
          Choose a .avcdriver file...
        </button>

        <div
          style={{
            textAlign: "center",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            marginBottom: "var(--space-md)",
          }}
        >
          or paste JSON below
        </div>

        <textarea
          value={pasteText}
          onChange={(e) => setPasteText(e.target.value)}
          placeholder='{"id": "my_driver", "name": "My Driver", "transport": "tcp", ...}'
          rows={8}
          style={{
            width: "100%",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            resize: "vertical",
            marginBottom: "var(--space-lg)",
          }}
        />

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-sm)",
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => onPaste(pasteText)}
            disabled={!pasteText.trim()}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: pasteText.trim()
                ? "var(--accent)"
                : "var(--bg-hover)",
              color: pasteText.trim()
                ? "var(--text-on-accent)"
                : "var(--text-muted)",
            }}
          >
            Import
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Empty State ---

function EmptyState() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        padding: "var(--space-xl)",
        gap: "var(--space-lg)",
        overflow: "auto",
      }}
    >
      <div style={{ textAlign: "center", maxWidth: 480 }}>
        <div
          style={{
            fontSize: "var(--font-size-lg)",
            color: "var(--text-primary)",
            marginBottom: "var(--space-sm)",
          }}
        >
          No driver selected
        </div>
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          Select a driver from the list to edit it, or use "Create New Driver"
          to build one from scratch. You can also import an .avcdriver file
          shared by someone else, or use the "Browse Community" tab to find
          and install drivers from the community repository.
        </div>
      </div>
    </div>
  );
}
