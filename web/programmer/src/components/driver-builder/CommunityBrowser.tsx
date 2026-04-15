import { useEffect, useState, useCallback } from "react";
import { Search, CheckCircle, Download, RefreshCw, AlertTriangle, Shield, X, PlayCircle, ArrowUpCircle, Loader2 } from "lucide-react";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import { hasUpdate } from "../../api/types";
import type { CommunityDriver } from "../../api/types";

const COMMUNITY_BASE_URL =
  "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/";

const CATEGORIES = [
  "All",
  "Projector",
  "Display",
  "Switcher",
  "Audio",
  "Camera",
  "Lighting",
  "Utility",
] as const;

const CATEGORY_COLORS: Record<string, string> = {
  projector: "#e67e22",
  display: "#3498db",
  switcher: "#9b59b6",
  audio: "#2ecc71",
  camera: "#e74c3c",
  lighting: "#f1c40f",
  utility: "#95a5a6",
};

const TRANSPORT_COLORS: Record<string, string> = {
  tcp: "#007acc",
  serial: "#e67e22",
  udp: "#2ecc71",
};

export function CommunityBrowser() {
  const communityDrivers = useDriverBuilderStore((s) => s.communityDrivers);
  const installedDrivers = useDriverBuilderStore((s) => s.installedDrivers);
  const communityLoading = useDriverBuilderStore((s) => s.communityLoading);
  const communityError = useDriverBuilderStore((s) => s.communityError);
  const loadCommunityDrivers = useDriverBuilderStore((s) => s.loadCommunityDrivers);
  const loadInstalledDrivers = useDriverBuilderStore((s) => s.loadInstalledDrivers);

  const [searchQuery, setSearchQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState("All");
  const [installingIds, setInstallingIds] = useState<Set<string>>(new Set());
  const [installErrors, setInstallErrors] = useState<Record<string, string>>({});
  const [selectedDriver, setSelectedDriver] = useState<CommunityDriver | null>(null);

  useEffect(() => {
    loadCommunityDrivers();
    loadInstalledDrivers();
  }, [loadCommunityDrivers, loadInstalledDrivers]);

  const installedIdSet = new Set(installedDrivers.map((d) => d.id));
  const installedVersions = new Map(installedDrivers.map((d) => [d.id, d.version ?? ""]));

  const filteredDrivers = communityDrivers.filter((driver) => {
    // Category filter
    if (activeCategory !== "All") {
      if (driver.category.toLowerCase() !== activeCategory.toLowerCase()) {
        return false;
      }
    }
    // Search filter
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      return (
        driver.name.toLowerCase().includes(q) ||
        driver.manufacturer.toLowerCase().includes(q) ||
        driver.description.toLowerCase().includes(q) ||
        driver.id.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const handleInstall = useCallback(
    async (driver: CommunityDriver) => {
      const fileUrl = `${COMMUNITY_BASE_URL}${driver.file}`;
      setInstallingIds((prev) => new Set(prev).add(driver.id));
      setInstallErrors((prev) => {
        const next = { ...prev };
        delete next[driver.id];
        return next;
      });
      try {
        await useDriverBuilderStore.getState().installDriver(driver.id, fileUrl, driver.min_platform_version);
      } catch (e) {
        setInstallErrors((prev) => ({ ...prev, [driver.id]: String(e) }));
      } finally {
        setInstallingIds((prev) => {
          const next = new Set(prev);
          next.delete(driver.id);
          return next;
        });
      }
    },
    []
  );

  const handleUpdate = useCallback(
    async (driver: CommunityDriver) => {
      const fileUrl = `${COMMUNITY_BASE_URL}${driver.file}`;
      setInstallingIds((prev) => new Set(prev).add(driver.id));
      setInstallErrors((prev) => {
        const next = { ...prev };
        delete next[driver.id];
        return next;
      });
      try {
        await useDriverBuilderStore.getState().updateDriver(driver.id, fileUrl, driver.min_platform_version);
      } catch (e) {
        setInstallErrors((prev) => ({ ...prev, [driver.id]: String(e) }));
      } finally {
        setInstallingIds((prev) => {
          const next = new Set(prev);
          next.delete(driver.id);
          return next;
        });
      }
    },
    []
  );

  const handleRetry = useCallback(() => {
    loadCommunityDrivers();
    loadInstalledDrivers();
  }, [loadCommunityDrivers, loadInstalledDrivers]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Search bar */}
      <div
        style={{
          padding: "var(--space-md) var(--space-lg)",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-sm)",
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <Search size={16} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search drivers by name, manufacturer, or description..."
            style={{
              flex: 1,
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-surface)",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-primary)",
              outline: "none",
            }}
          />
          <button
            onClick={handleRetry}
            title="Refresh driver list"
            style={{
              padding: "var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              color: "var(--text-muted)",
              border: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              flexShrink: 0,
            }}
          >
            <RefreshCw size={16} />
          </button>
        </div>

        {/* Category filter pills */}
        <div
          style={{
            display: "flex",
            gap: "var(--space-xs)",
            flexWrap: "wrap",
          }}
        >
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              style={{
                padding: "2px 10px",
                borderRadius: "12px",
                border: "1px solid",
                borderColor:
                  activeCategory === cat ? "var(--accent)" : "var(--border-color)",
                background:
                  activeCategory === cat ? "var(--accent)" : "transparent",
                color: activeCategory === cat ? "#fff" : "var(--text-muted)",
                fontSize: "12px",
                cursor: "pointer",
                fontWeight: activeCategory === cat ? 600 : 400,
              }}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Content area */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "var(--space-md) var(--space-lg)",
        }}
      >
        {communityLoading ? (
          <LoadingState />
        ) : communityError ? (
          <ErrorState error={communityError} onRetry={handleRetry} />
        ) : filteredDrivers.length === 0 ? (
          <EmptyFilterState
            hasDrivers={communityDrivers.length > 0}
            searchQuery={searchQuery}
            category={activeCategory}
          />
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
              gap: "var(--space-md)",
            }}
          >
            {filteredDrivers.map((driver) => (
              <DriverCard
                key={driver.id}
                driver={driver}
                installed={installedIdSet.has(driver.id)}
                installing={installingIds.has(driver.id)}
                installError={installErrors[driver.id] || null}
                updateAvailable={hasUpdate(installedVersions.get(driver.id) ?? "", driver.version)}
                onInstall={handleInstall}
                onUpdate={handleUpdate}
                onSelect={setSelectedDriver}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer with count */}
      {!communityLoading && !communityError && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-lg)",
            borderTop: "1px solid var(--border-color)",
            fontSize: "12px",
            color: "var(--text-muted)",
            flexShrink: 0,
          }}
        >
          {filteredDrivers.length} of {communityDrivers.length} drivers
          {installedDrivers.length > 0 &&
            ` · ${installedDrivers.length} installed`}
        </div>
      )}

      {/* Detail modal */}
      {selectedDriver && (
        <CommunityDriverDetail
          driver={selectedDriver}
          installed={installedIdSet.has(selectedDriver.id)}
          installing={installingIds.has(selectedDriver.id)}
          installError={installErrors[selectedDriver.id] || null}
          updateAvailable={hasUpdate(installedVersions.get(selectedDriver.id) ?? "", selectedDriver.version)}
          onInstall={handleInstall}
          onUpdate={handleUpdate}
          onClose={() => setSelectedDriver(null)}
        />
      )}
    </div>
  );
}

// --- Driver Card ---

function DriverCard({
  driver,
  installed,
  installing,
  installError,
  updateAvailable,
  onInstall,
  onUpdate,
  onSelect,
}: {
  driver: CommunityDriver;
  installed: boolean;
  installing: boolean;
  installError: string | null;
  updateAvailable: boolean;
  onInstall: (driver: CommunityDriver) => void;
  onUpdate: (driver: CommunityDriver) => void;
  onSelect: (driver: CommunityDriver) => void;
}) {
  const [hovered, setHovered] = useState(false);

  const catColor = CATEGORY_COLORS[driver.category.toLowerCase()] || "#888";
  const transportColor = TRANSPORT_COLORS[driver.transport.toLowerCase()] || "#888";

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => onSelect(driver)}
      style={{
        background: hovered ? "#3d3d3d" : "#2d2d2d",
        borderRadius: "4px",
        padding: "var(--space-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
        border: "1px solid var(--border-color)",
        transition: "background 0.15s",
        cursor: "pointer",
      }}
    >
      {/* Header row: name + badges */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-sm)" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
            }}
          >
            <span
              style={{
                fontWeight: 600,
                fontSize: "var(--font-size-sm)",
                color: "#ccc",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {driver.name}
            </span>
            {driver.verified && (
              <span title="Verified driver" style={{ display: "flex", flexShrink: 0 }}>
                <Shield size={14} style={{ color: "var(--color-success)" }} />
              </span>
            )}
            {driver.simulated && (
              <span title="Simulator available" style={{ display: "flex", flexShrink: 0 }}>
                <PlayCircle size={14} style={{ color: "var(--accent)" }} />
              </span>
            )}
          </div>
          <div
            style={{
              fontSize: "12px",
              color: "#888",
              marginTop: "2px",
            }}
          >
            {driver.manufacturer} · by {driver.author}
          </div>
        </div>
      </div>

      {/* Badges */}
      <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
        <span
          style={{
            padding: "1px 8px",
            borderRadius: "3px",
            fontSize: "11px",
            fontWeight: 500,
            background: `${catColor}22`,
            color: catColor,
            border: `1px solid ${catColor}44`,
          }}
        >
          {driver.category}
        </span>
        <span
          style={{
            padding: "1px 8px",
            borderRadius: "3px",
            fontSize: "11px",
            fontWeight: 500,
            background: `${transportColor}22`,
            color: transportColor,
            border: `1px solid ${transportColor}44`,
          }}
        >
          {driver.transport.toUpperCase()}
        </span>
        <span
          style={{
            padding: "1px 8px",
            borderRadius: "3px",
            fontSize: "11px",
            color: "#888",
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.1)",
          }}
        >
          v{driver.version}
        </span>
      </div>

      {/* Description */}
      <div
        style={{
          fontSize: "12px",
          color: "#888",
          lineHeight: 1.5,
          overflow: "hidden",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          flex: 1,
        }}
      >
        {driver.description}
      </div>

      {/* Install error */}
      {installError && (
        <div
          style={{
            fontSize: "11px",
            color: "var(--color-error)",
            background: "rgba(239,68,68,0.1)",
            padding: "var(--space-xs) var(--space-sm)",
            borderRadius: "3px",
          }}
        >
          Install failed: {installError.replace(/^Error:\s*/i, "").slice(0, 100)}
        </div>
      )}

      {/* Action button */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        {installed ? (
          updateAvailable && !installing ? (
            <button
              onClick={(e) => { e.stopPropagation(); onUpdate(driver); }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "4px",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "4px",
                fontSize: "var(--font-size-sm)",
                background: "#007acc",
                color: "#fff",
                border: "none",
                cursor: "pointer",
                fontWeight: 500,
              }}
            >
              <ArrowUpCircle size={14} />
              Update
            </button>
          ) : installing ? (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: "4px",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "4px",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-muted)",
                background: "var(--bg-hover)",
                fontWeight: 500,
              }}
            >
              <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
              Updating...
            </span>
          ) : (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: "4px",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "4px",
                fontSize: "var(--font-size-sm)",
                color: "var(--color-success)",
                background: "rgba(76,175,80,0.12)",
                fontWeight: 500,
              }}
            >
              <CheckCircle size={14} />
              Installed
            </span>
          )
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); onInstall(driver); }}
            disabled={installing}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "4px",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "4px",
              fontSize: "var(--font-size-sm)",
              background: installing ? "var(--bg-hover)" : "#007acc",
              color: installing ? "var(--text-muted)" : "#fff",
              border: "none",
              cursor: installing ? "default" : "pointer",
              fontWeight: 500,
            }}
          >
            {installing ? (
              <>
                <RefreshCw
                  size={14}
                  style={{
                    animation: "spin 1s linear infinite",
                  }}
                />
                Installing...
              </>
            ) : (
              <>
                <Download size={14} />
                Install
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}

// --- Community Driver Detail Modal ---

function CommunityDriverDetail({
  driver,
  installed,
  installing,
  installError,
  updateAvailable,
  onInstall,
  onUpdate,
  onClose,
}: {
  driver: CommunityDriver;
  installed: boolean;
  installing: boolean;
  installError: string | null;
  updateAvailable: boolean;
  onInstall: (driver: CommunityDriver) => void;
  onUpdate: (driver: CommunityDriver) => void;
  onClose: () => void;
}) {
  const catColor = CATEGORY_COLORS[driver.category.toLowerCase()] || "#888";
  const transportColor = TRANSPORT_COLORS[driver.transport.toLowerCase()] || "#888";

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-surface, #2d2d2d)",
          borderRadius: "8px",
          border: "1px solid var(--border-color)",
          width: "min(560px, 90vw)",
          maxHeight: "80vh",
          overflow: "auto",
          padding: "var(--space-lg)",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <h2 style={{ margin: 0, fontSize: "1.25rem" }}>{driver.name}</h2>
              {driver.verified && (
                <span title="Verified driver"><Shield size={16} style={{ color: "var(--color-success)" }} /></span>
              )}
              {driver.simulated && (
                <span title="Simulator available"><PlayCircle size={16} style={{ color: "var(--accent)" }} /></span>
              )}
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginTop: 4 }}>
              {driver.manufacturer} &middot; by {driver.author}
            </div>
          </div>
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
            <X size={18} />
          </button>
        </div>

        {/* Badges */}
        <div style={{ display: "flex", gap: "var(--space-xs)", marginTop: "var(--space-md)", flexWrap: "wrap" }}>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: "3px",
              fontSize: "11px",
              fontWeight: 500,
              background: `${catColor}22`,
              color: catColor,
              border: `1px solid ${catColor}44`,
            }}
          >
            {driver.category}
          </span>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: "3px",
              fontSize: "11px",
              fontWeight: 500,
              background: `${transportColor}22`,
              color: transportColor,
              border: `1px solid ${transportColor}44`,
            }}
          >
            {driver.transport.toUpperCase()}
          </span>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: "3px",
              fontSize: "11px",
              color: "#888",
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(255,255,255,0.1)",
            }}
          >
            v{driver.version}
          </span>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: "3px",
              fontSize: "11px",
              color: "#888",
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(255,255,255,0.1)",
            }}
          >
            {driver.format === "python" ? "Python" : "YAML"}
          </span>
        </div>

        {/* Full description */}
        <div style={{ marginTop: "var(--space-lg)", lineHeight: 1.6, fontSize: "var(--font-size-sm)" }}>
          {driver.description}
        </div>

        {/* Protocols */}
        {driver.protocols && driver.protocols.length > 0 && (
          <div style={{ marginTop: "var(--space-md)" }}>
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: 4, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Protocols
            </div>
            <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
              {driver.protocols.map((p) => (
                <span
                  key={p}
                  style={{
                    padding: "2px 8px",
                    borderRadius: "3px",
                    fontSize: "11px",
                    background: "rgba(59,130,246,0.15)",
                    color: "#60a5fa",
                    border: "1px solid rgba(59,130,246,0.3)",
                  }}
                >
                  {p}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Ports */}
        {driver.ports && driver.ports.length > 0 && (
          <div style={{ marginTop: "var(--space-md)" }}>
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: 4, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Default Ports
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              {driver.ports.join(", ")}
            </div>
          </div>
        )}

        {/* Note about help */}
        <div
          style={{
            marginTop: "var(--space-lg)",
            padding: "var(--space-sm) var(--space-md)",
            background: "rgba(255,255,255,0.03)",
            borderRadius: "var(--radius)",
            border: "1px solid var(--border-color)",
            fontSize: "var(--font-size-xs)",
            color: "var(--text-muted)",
            fontStyle: "italic",
          }}
        >
          Install this driver to see setup instructions, configuration details, and available commands.
        </div>

        {/* Install error */}
        {installError && (
          <div
            style={{
              marginTop: "var(--space-sm)",
              fontSize: "11px",
              color: "var(--color-error)",
              background: "rgba(239,68,68,0.1)",
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "3px",
            }}
          >
            Install failed: {installError.replace(/^Error:\s*/i, "").slice(0, 200)}
          </div>
        )}

        {/* Action */}
        <div style={{ marginTop: "var(--space-lg)", display: "flex", justifyContent: "flex-end" }}>
          {installed ? (
            updateAvailable && !installing ? (
              <button
                onClick={() => onUpdate(driver)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                  padding: "var(--space-sm) var(--space-lg)",
                  borderRadius: "4px",
                  fontSize: "var(--font-size-sm)",
                  background: "#007acc",
                  color: "#fff",
                  border: "none",
                  cursor: "pointer",
                  fontWeight: 500,
                }}
              >
                <ArrowUpCircle size={14} />
                Update
              </button>
            ) : installing ? (
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                  padding: "var(--space-sm) var(--space-lg)",
                  borderRadius: "4px",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-muted)",
                  background: "var(--bg-hover)",
                  fontWeight: 500,
                }}
              >
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
                Updating...
              </span>
            ) : (
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                  padding: "var(--space-sm) var(--space-lg)",
                  borderRadius: "4px",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-success)",
                  background: "rgba(76,175,80,0.12)",
                  fontWeight: 500,
                }}
              >
                <CheckCircle size={14} />
                Installed
              </span>
            )
          ) : (
            <button
              onClick={() => onInstall(driver)}
              disabled={installing}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "4px",
                padding: "var(--space-sm) var(--space-lg)",
                borderRadius: "4px",
                fontSize: "var(--font-size-sm)",
                background: installing ? "var(--bg-hover)" : "#007acc",
                color: installing ? "var(--text-muted)" : "#fff",
                border: "none",
                cursor: installing ? "default" : "pointer",
                fontWeight: 500,
              }}
            >
              {installing ? (
                <>
                  <RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />
                  Installing...
                </>
              ) : (
                <>
                  <Download size={14} />
                  Install Driver
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}


// --- Loading State ---

function LoadingState() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: "var(--space-md)",
        color: "var(--text-muted)",
      }}
    >
      <RefreshCw
        size={32}
        style={{
          animation: "spin 1s linear infinite",
          opacity: 0.5,
        }}
      />
      <span style={{ fontSize: "var(--font-size-sm)" }}>
        Loading community drivers...
      </span>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

// --- Error State ---

function ErrorState({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: "var(--space-md)",
      }}
    >
      <AlertTriangle size={32} style={{ color: "var(--color-error)", opacity: 0.7 }} />
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          textAlign: "center",
          maxWidth: 400,
        }}
      >
        Failed to load community drivers.
        <br />
        <span style={{ fontSize: "12px", color: "#888" }}>
          {error.replace(/^Error:\s*/i, "").slice(0, 200)}
        </span>
      </div>
      <button
        onClick={onRetry}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-lg)",
          borderRadius: "var(--border-radius)",
          background: "var(--accent)",
          color: "#fff",
          border: "none",
          cursor: "pointer",
          fontSize: "var(--font-size-sm)",
        }}
      >
        <RefreshCw size={14} />
        Retry
      </button>
    </div>
  );
}

// --- Empty Filter State ---

function EmptyFilterState({
  hasDrivers,
  searchQuery,
  category,
}: {
  hasDrivers: boolean;
  searchQuery: string;
  category: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: "var(--space-sm)",
        color: "var(--text-muted)",
      }}
    >
      <Search size={32} style={{ opacity: 0.3 }} />
      <span style={{ fontSize: "var(--font-size-sm)" }}>
        {hasDrivers
          ? `No drivers match "${searchQuery}"${category !== "All" ? ` in ${category}` : ""}`
          : "No community drivers available yet."}
      </span>
      {hasDrivers && (
        <span style={{ fontSize: "12px", color: "#888" }}>
          Try a different search term or category.
        </span>
      )}
    </div>
  );
}
