import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Trash2, X, Image } from "lucide-react";
import * as api from "../../api/restClient";
import { useProjectStore } from "../../store/projectStore";
import { ConfirmDialog } from "../shared/ConfirmDialog";

interface AssetPickerProps {
  value: string;
  onChange: (ref: string) => void;
}

export function AssetPicker({ value, onChange }: AssetPickerProps) {
  const [open, setOpen] = useState(false);
  const currentName = value?.replace("assets://", "") || "";

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        {currentName ? (
          <img
            src={api.getAssetUrl(currentName)}
            alt={currentName}
            style={{
              width: 32,
              height: 32,
              objectFit: "cover",
              borderRadius: 4,
              border: "1px solid var(--border-color)",
            }}
          />
        ) : (
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 4,
              border: "1px dashed var(--border-color)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-muted)",
            }}
          >
            <Image size={16} />
          </div>
        )}
        <button
          onClick={() => setOpen(true)}
          style={{
            padding: "3px 8px",
            borderRadius: 3,
            fontSize: "var(--font-size-sm)",
            color: "var(--accent)",
            background: "var(--bg-base)",
            border: "1px solid var(--border-color)",
          }}
        >
          {currentName ? "Change" : "Choose Image"}
        </button>
        {currentName && (
          <button
            onClick={() => onChange("")}
            style={{
              padding: "2px 4px",
              fontSize: 10,
              color: "var(--text-muted)",
              borderRadius: 3,
            }}
          >
            Clear
          </button>
        )}
      </div>
      {open && (
        <AssetBrowserModal
          currentValue={value}
          onSelect={(ref) => {
            onChange(ref);
            setOpen(false);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

function AssetBrowserModal({
  currentValue,
  onSelect,
  onClose,
}: {
  currentValue: string;
  onSelect: (ref: string) => void;
  onClose: () => void;
}) {
  const [assets, setAssets] = useState<api.AssetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [pendingLargeUpload, setPendingLargeUpload] = useState<{ files: File[]; largeNames: string } | null>(null);
  const [pendingDeleteAsset, setPendingDeleteAsset] = useState<string | null>(null);

  // Build set of referenced asset names (12.10)
  const project = useProjectStore((s) => s.project);
  const usedAssets = useMemo(() => {
    const used = new Set<string>();
    if (!project) return used;
    for (const page of project.ui.pages) {
      for (const el of page.elements) {
        if (el.src?.startsWith("assets://")) used.add(el.src.replace("assets://", ""));
        if (el.button_image?.startsWith("assets://")) used.add(el.button_image.replace("assets://", ""));
        // Per-state images (multi-state feedback)
        const fb = (el.bindings as { feedback?: { states?: Record<string, { button_image?: string }>; style_active?: { button_image?: string }; style_inactive?: { button_image?: string } } })?.feedback;
        if (fb?.states) {
          for (const state of Object.values(fb.states)) {
            if (state?.button_image?.startsWith("assets://")) used.add(state.button_image.replace("assets://", ""));
          }
        }
        if (fb?.style_active?.button_image?.startsWith("assets://")) used.add(fb.style_active.button_image.replace("assets://", ""));
        if (fb?.style_inactive?.button_image?.startsWith("assets://")) used.add(fb.style_inactive.button_image.replace("assets://", ""));
        const bg = page.background;
        if (bg && typeof bg === "object" && (bg as any).image?.startsWith("assets://")) used.add((bg as any).image.replace("assets://", ""));
      }
    }
    return used;
  }, [project]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  const loadAssets = useCallback(async () => {
    try {
      const data = await api.listAssets();
      setAssets(data.assets);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAssets();
  }, [loadAssets]);

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const allFiles = Array.from(files);
    const largeFiles = allFiles.filter((f) => f.size > 500 * 1024);
    if (largeFiles.length > 0) {
      const names = largeFiles.map((f) => `${f.name} (${(f.size / 1024).toFixed(0)} KB)`).join(", ");
      setPendingLargeUpload({ files: allFiles, largeNames: names });
      return;
    }
    await doUpload(allFiles);
  };

  const doUpload = async (files: File[]) => {
    setUploading(true);
    setError("");
    try {
      for (const file of files) {
        await api.uploadAsset(file);
      }
      await loadAssets();
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = (name: string) => {
    setPendingDeleteAsset(name);
  };

  const confirmDeleteAsset = async () => {
    if (!pendingDeleteAsset) return;
    const name = pendingDeleteAsset;
    setPendingDeleteAsset(null);
    try {
      await api.deleteAsset(name);
      await loadAssets();
    } catch (e) {
      setError(String(e));
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    handleUpload(e.dataTransfer.files);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Project Assets"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 10000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.6)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)",
          width: 560,
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 16px",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <span style={{ fontWeight: 600, fontSize: 14 }}>Project Assets</span>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              padding: 4,
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Drop zone + upload */}
        <div
          ref={dropRef}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          style={{
            margin: "12px 16px",
            padding: "16px",
            border: "2px dashed var(--border-color)",
            borderRadius: 8,
            textAlign: "center",
            color: "var(--text-muted)",
            fontSize: 13,
          }}
        >
          {uploading ? (
            "Uploading..."
          ) : (
            <>
              Drop images here or{" "}
              <button
                onClick={() => fileInputRef.current?.click()}
                style={{
                  color: "var(--accent)",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  textDecoration: "underline",
                  fontSize: 13,
                }}
              >
                browse
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*,.svg"
                multiple
                style={{ display: "none" }}
                onChange={(e) => handleUpload(e.target.files)}
              />
            </>
          )}
        </div>

        {error && (
          <div
            style={{
              margin: "0 16px 8px",
              padding: "6px 10px",
              background: "rgba(244,67,54,0.1)",
              color: "#ef5350",
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}

        {/* Asset search (12.8) */}
        <div style={{ padding: "0 16px 8px" }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search assets..."
            style={{
              width: "100%", padding: "4px 8px", fontSize: 12,
              borderRadius: 4, border: "1px solid var(--border-color)",
              background: "var(--bg-primary)", color: "var(--text-primary)",
            }}
          />
        </div>

        {/* Asset grid */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "0 16px 16px",
          }}
        >
          {loading ? (
            <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 16, textAlign: "center" }}>
              Loading...
            </div>
          ) : assets.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 16, textAlign: "center" }}>
              No assets uploaded yet
            </div>
          ) : (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(100px, 1fr))",
                gap: 8,
              }}
            >
              {assets.filter((a) => !search || a.name.toLowerCase().includes(search.toLowerCase())).map((asset) => {
                const isSelected = currentValue === `assets://${asset.name}`;
                return (
                  <div
                    key={asset.name}
                    onClick={() => onSelect(`assets://${asset.name}`)}
                    style={{
                      border: isSelected
                        ? "2px solid var(--accent)"
                        : "1px solid var(--border-color)",
                      borderRadius: 6,
                      padding: 4,
                      cursor: "pointer",
                      background: isSelected
                        ? "var(--accent-dim)"
                        : "var(--bg-base)",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    <img
                      src={api.getAssetUrl(asset.name)}
                      alt={asset.name}
                      style={{
                        width: "100%",
                        height: 64,
                        objectFit: "contain",
                        borderRadius: 4,
                      }}
                    />
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--text-secondary)",
                        textAlign: "center",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        width: "100%",
                      }}
                    >
                      {asset.name}
                    </div>
                    <div style={{ fontSize: 9, color: "var(--text-muted)", textAlign: "center" }}>
                      {asset.size < 1024
                        ? `${asset.size} B`
                        : asset.size < 1048576
                          ? `${(asset.size / 1024).toFixed(1)} KB`
                          : `${(asset.size / 1048576).toFixed(1)} MB`}
                      {!usedAssets.has(asset.name) && (
                        <span style={{ marginLeft: 4, color: "#f59e0b", fontWeight: 500 }} title="Not referenced by any element">unused</span>
                      )}
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(asset.name);
                      }}
                      style={{
                        padding: "2px 4px",
                        fontSize: 10,
                        color: "var(--text-muted)",
                        borderRadius: 3,
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                      }}
                      title="Delete"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {pendingLargeUpload && (
        <ConfirmDialog
          title="Large Files"
          message={<>
            <div>The following files are larger than 500 KB:</div>
            <div style={{ margin: "8px 0", fontFamily: "monospace", fontSize: "var(--font-size-sm)" }}>{pendingLargeUpload.largeNames}</div>
            <div>Large images slow down panel loading. Consider compressing them before uploading.</div>
          </>}
          confirmLabel="Upload Anyway"
          onConfirm={() => {
            const files = pendingLargeUpload.files;
            setPendingLargeUpload(null);
            doUpload(files);
          }}
          onCancel={() => setPendingLargeUpload(null)}
        />
      )}

      {pendingDeleteAsset && (
        <ConfirmDialog
          title="Delete Asset"
          message={`Delete "${pendingDeleteAsset}"? This cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={confirmDeleteAsset}
          onCancel={() => setPendingDeleteAsset(null)}
        />
      )}
    </div>
  );
}
