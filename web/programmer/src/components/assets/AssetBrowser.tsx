import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Trash2, X, Image as ImageIcon, Music, FolderOpen } from "lucide-react";
import * as api from "../../api/restClient";
import { useProjectStore } from "../../store/projectStore";
import { ConfirmDialog } from "../shared/ConfirmDialog";

export type AssetFilter = "all" | "image" | "audio";
export type AssetSelectMode = "pick" | "manage";

interface AssetBrowserProps {
  /** Restrict the visible/uploadable asset types. */
  filter: AssetFilter;
  /** "pick" = clicking a card calls onSelect; "manage" = browse-only, no selection. */
  selectMode: AssetSelectMode;
  /** Currently-selected asset reference (for highlighting). Picker mode only. */
  currentValue?: string;
  /** Called when a card is clicked in pick mode. */
  onSelect?: (ref: string) => void;
  /** Show filter chips for switching between All / Images / Audio. */
  showFilterChips?: boolean;
  /** Called when the user changes the active filter via chips. */
  onFilterChange?: (filter: AssetFilter) => void;
}

const ACCEPT_BY_FILTER: Record<AssetFilter, string> = {
  all: "image/*,.svg,audio/*,.mp3,.wav,.ogg,.m4a",
  image: "image/*,.svg",
  audio: "audio/*,.mp3,.wav,.ogg,.m4a",
};

const LARGE_WARN_BYTES_BY_TYPE: Record<api.AssetType, number> = {
  image: 500 * 1024,        // 500 KB — large for a panel image
  audio: 5 * 1024 * 1024,   // 5 MB — large for a notification chime
};

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function classifyByExt(file: File): api.AssetType {
  const name = file.name.toLowerCase();
  if (name.endsWith(".mp3") || name.endsWith(".wav") || name.endsWith(".ogg") || name.endsWith(".m4a")) {
    return "audio";
  }
  return "image";
}

/**
 * Reusable asset list + upload + delete UI.
 * Used standalone in the Assets view, and wrapped by AssetBrowserModal for pickers.
 */
export function AssetBrowser({
  filter,
  selectMode,
  currentValue,
  onSelect,
  showFilterChips = false,
  onFilterChange,
}: AssetBrowserProps) {
  const [assets, setAssets] = useState<api.AssetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [pendingLargeUpload, setPendingLargeUpload] = useState<{ files: File[]; largeNames: string } | null>(null);
  const [pendingDeleteAsset, setPendingDeleteAsset] = useState<string | null>(null);

  // Track which image assets are referenced by UI elements so we can flag unused ones.
  // Audio reference tracking (macros, scripts) lands in later phases of the audio player plan.
  const pages = useProjectStore((s) => s.project?.ui.pages);
  const usedImageAssets = useMemo(() => {
    const used = new Set<string>();
    if (!pages) return used;
    for (const page of pages) {
      const bg = page.background;
      if (bg && typeof bg === "object" && (bg as { image?: string }).image?.startsWith("assets://")) {
        used.add((bg as { image: string }).image.replace("assets://", ""));
      }
      for (const el of page.elements) {
        if (el.src?.startsWith("assets://")) used.add(el.src.replace("assets://", ""));
        if (el.button_image?.startsWith("assets://")) used.add(el.button_image.replace("assets://", ""));
        const fb = (el.bindings as { feedback?: { states?: Record<string, { button_image?: string }>; style_active?: { button_image?: string }; style_inactive?: { button_image?: string } } })?.feedback;
        if (fb?.states) {
          for (const state of Object.values(fb.states)) {
            if (state?.button_image?.startsWith("assets://")) used.add(state.button_image.replace("assets://", ""));
          }
        }
        if (fb?.style_active?.button_image?.startsWith("assets://")) used.add(fb.style_active.button_image.replace("assets://", ""));
        if (fb?.style_inactive?.button_image?.startsWith("assets://")) used.add(fb.style_inactive.button_image.replace("assets://", ""));
      }
    }
    return used;
  }, [pages]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  const loadAssets = useCallback(async () => {
    setLoading(true);
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

  const visibleAssets = useMemo(() => {
    let list = assets;
    if (filter !== "all") list = list.filter((a) => a.type === filter);
    if (search) {
      const s = search.toLowerCase();
      list = list.filter((a) => a.name.toLowerCase().includes(s));
    }
    return list;
  }, [assets, filter, search]);

  const handleUpload = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const allFiles = Array.from(files);
    // Reject files of the wrong type early so the user gets a clear message
    // instead of a generic 400 from the server.
    if (filter !== "all") {
      const wrongType = allFiles.filter((f) => classifyByExt(f) !== filter);
      if (wrongType.length > 0) {
        setError(
          `Only ${filter} files can be uploaded here. Rejected: ${wrongType.map((f) => f.name).join(", ")}`,
        );
        return;
      }
    }
    const largeFiles = allFiles.filter(
      (f) => f.size > LARGE_WARN_BYTES_BY_TYPE[classifyByExt(f)],
    );
    if (largeFiles.length > 0) {
      const names = largeFiles
        .map((f) => `${f.name} (${fmtSize(f.size)})`)
        .join(", ");
      setPendingLargeUpload({ files: allFiles, largeNames: names });
      return;
    }
    void doUpload(allFiles);
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

  const dropLabel =
    filter === "audio" ? "Drop audio files here" :
    filter === "image" ? "Drop images here" :
    "Drop files here";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Filter chips */}
      {showFilterChips && (
        <div style={{ padding: "0 16px 8px", display: "flex", gap: 6 }}>
          {(["all", "image", "audio"] as AssetFilter[]).map((f) => {
            const active = filter === f;
            return (
              <button
                key={f}
                onClick={() => onFilterChange?.(f)}
                style={{
                  padding: "4px 10px",
                  borderRadius: 999,
                  fontSize: 12,
                  background: active ? "var(--accent-bg, var(--accent))" : "var(--bg-base)",
                  color: active ? "#fff" : "var(--text-secondary)",
                  border: `1px solid ${active ? "var(--accent)" : "var(--border-color)"}`,
                  cursor: "pointer",
                  textTransform: "capitalize",
                }}
              >
                {f === "all" ? "All" : f === "image" ? "Images" : "Audio"}
              </button>
            );
          })}
        </div>
      )}

      {/* Drop zone + upload */}
      <div
        ref={dropRef}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        style={{
          margin: "0 16px 12px",
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
            {dropLabel} or{" "}
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
              accept={ACCEPT_BY_FILTER[filter]}
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

      {/* Search */}
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
          minHeight: 0,
        }}
      >
        {loading ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 16, textAlign: "center" }}>
            Loading...
          </div>
        ) : visibleAssets.length === 0 ? (
          <EmptyState filter={filter} hasSearch={!!search} />
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
              gap: 8,
            }}
          >
            {visibleAssets.map((asset) => {
              const isSelected = currentValue === `assets://${asset.name}`;
              const cardClickable = selectMode === "pick";
              return (
                <div
                  key={asset.name}
                  onClick={cardClickable ? () => onSelect?.(`assets://${asset.name}`) : undefined}
                  style={{
                    border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
                    borderRadius: 6,
                    padding: 6,
                    cursor: cardClickable ? "pointer" : "default",
                    background: isSelected ? "var(--accent-dim)" : "var(--bg-base)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                    gridColumn: asset.type === "audio" ? "span 2" : undefined,
                    minWidth: 0,
                  }}
                >
                  {asset.type === "image" ? (
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
                  ) : (
                    <AudioPreview name={asset.name} />
                  )}
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--text-secondary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={asset.name}
                  >
                    {asset.name}
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      fontSize: 10,
                      color: "var(--text-muted)",
                    }}
                  >
                    <span>
                      {fmtSize(asset.size)}
                      {asset.type === "image" && !usedImageAssets.has(asset.name) && (
                        <span style={{ marginLeft: 4, color: "#f59e0b", fontWeight: 500 }} title="Not referenced by any element">
                          unused
                        </span>
                      )}
                    </span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(asset.name);
                      }}
                      style={{
                        padding: "2px 4px",
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
                </div>
              );
            })}
          </div>
        )}
      </div>

      {pendingLargeUpload && (
        <ConfirmDialog
          title="Large Files"
          message={
            <>
              <div>The following files are large:</div>
              <div style={{ margin: "8px 0", fontFamily: "monospace", fontSize: "var(--font-size-sm)" }}>
                {pendingLargeUpload.largeNames}
              </div>
              <div>Large assets slow down panel loading. Consider compressing them before uploading.</div>
            </>
          }
          confirmLabel="Upload Anyway"
          onConfirm={() => {
            const files = pendingLargeUpload.files;
            setPendingLargeUpload(null);
            void doUpload(files);
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

function AudioPreview({ name }: { name: string }) {
  return (
    <div
      style={{
        height: 64,
        borderRadius: 4,
        background: "var(--bg-primary)",
        border: "1px solid var(--border-color)",
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "0 8px",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <Music size={20} style={{ color: "var(--accent)", flexShrink: 0 }} />
      <audio
        src={api.getAssetUrl(name)}
        controls
        preload="none"
        style={{ flex: 1, minWidth: 0, height: 32 }}
      />
    </div>
  );
}

function EmptyState({ filter, hasSearch }: { filter: AssetFilter; hasSearch: boolean }) {
  if (hasSearch) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 32, textAlign: "center" }}>
        No matching assets.
      </div>
    );
  }
  const Icon = filter === "audio" ? Music : filter === "image" ? ImageIcon : FolderOpen;
  const label =
    filter === "audio" ? "No audio uploaded yet." :
    filter === "image" ? "No images uploaded yet." :
    "No assets uploaded yet.";
  return (
    <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 32, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
      <Icon size={32} style={{ opacity: 0.5 }} />
      <div>{label}</div>
    </div>
  );
}

interface AssetBrowserModalProps {
  filter: AssetFilter;
  currentValue?: string;
  onSelect: (ref: string) => void;
  onClose: () => void;
}

/** Modal wrapper used by pickers. Always pick mode. */
export function AssetBrowserModal({ filter, currentValue, onSelect, onClose }: AssetBrowserModalProps) {
  const title =
    filter === "audio" ? "Audio Assets" :
    filter === "image" ? "Image Assets" :
    "Project Assets";
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
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
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 16px",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <span style={{ fontWeight: 600, fontSize: 14 }}>{title}</span>
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
        <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", paddingTop: 12 }}>
          <AssetBrowser
            filter={filter}
            selectMode="pick"
            currentValue={currentValue}
            onSelect={onSelect}
          />
        </div>
      </div>
    </div>
  );
}
