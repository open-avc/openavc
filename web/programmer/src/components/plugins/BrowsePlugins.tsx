/**
 * Browse Plugins — community plugin browser with search, category filter,
 * install/uninstall. Mirrors CommunityBrowser for drivers.
 */
import { useState, useEffect } from "react";
import { Search, Download, Trash2, CheckCircle, Shield, Loader2, RefreshCw, AlertTriangle } from "lucide-react";
import { usePluginStore } from "../../store/pluginStore";
import type { CommunityPlugin } from "../../api/restClient";

const COMMUNITY_BASE_URL =
  "https://raw.githubusercontent.com/open-avc/openavc-plugins/main/";

const CATEGORIES = [
  "All",
  "Control Surface",
  "Integration",
  "Sensor",
  "Utility",
];

const categoryMap: Record<string, string> = {
  control_surface: "Control Surface",
  integration: "Integration",
  sensor: "Sensor",
  utility: "Utility",
};

export function BrowsePlugins() {
  const communityPlugins = usePluginStore((s) => s.communityPlugins);
  const installedPlugins = usePluginStore((s) => s.installedPlugins);
  const communityLoading = usePluginStore((s) => s.communityLoading);
  const communityError = usePluginStore((s) => s.communityError);
  const installingIds = usePluginStore((s) => s.installingIds);
  const loadCommunity = usePluginStore((s) => s.loadCommunity);
  const installCommunityPlugin = usePluginStore((s) => s.installCommunityPlugin);
  const uninstallPlugin = usePluginStore((s) => s.uninstallPlugin);

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("All");
  const [installError, setInstallError] = useState<Record<string, string>>({});

  useEffect(() => {
    loadCommunity();
  }, [loadCommunity]);

  const installedIds = new Set(installedPlugins.map((p) => p.id));

  const filtered = communityPlugins.filter((p) => {
    if (category !== "All" && (categoryMap[p.category] ?? p.category) !== category) {
      return false;
    }
    if (search) {
      const q = search.toLowerCase();
      return (
        p.name.toLowerCase().includes(q) ||
        p.id.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q) ||
        (p.author ?? "").toLowerCase().includes(q)
      );
    }
    return true;
  });

  const handleInstall = async (plugin: CommunityPlugin) => {
    setInstallError((prev) => {
      const next = { ...prev };
      delete next[plugin.id];
      return next;
    });
    try {
      const fileUrl = `${COMMUNITY_BASE_URL}${plugin.file}`;
      await installCommunityPlugin(plugin.id, fileUrl);
    } catch (e) {
      setInstallError((prev) => ({ ...prev, [plugin.id]: String(e) }));
    }
  };

  const handleUninstall = async (pluginId: string) => {
    try {
      await uninstallPlugin(pluginId);
    } catch (e) {
      setInstallError((prev) => ({ ...prev, [pluginId]: String(e) }));
    }
  };

  const installedCount = communityPlugins.filter((p) => installedIds.has(p.id)).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div
        style={{
          padding: "var(--space-md)",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-sm)",
        }}
      >
        {/* Search + Refresh */}
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              gap: "var(--space-sm)",
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-surface)",
              border: "1px solid var(--border-color)",
            }}
          >
            <Search size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
            <input
              type="text"
              placeholder="Search plugins..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{
                flex: 1,
                background: "transparent",
                border: "none",
                outline: "none",
                color: "var(--text-primary)",
                fontSize: "var(--font-size-sm)",
              }}
            />
          </div>
          <button
            onClick={() => loadCommunity()}
            style={{
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              display: "flex",
              alignItems: "center",
            }}
            title="Refresh"
          >
            <RefreshCw size={14} />
          </button>
        </div>

        {/* Category pills */}
        <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              style={{
                padding: "2px var(--space-sm)",
                borderRadius: 12,
                background: category === cat ? "var(--accent)" : "var(--bg-hover)",
                color: category === cat ? "var(--text-on-accent)" : "var(--text-secondary)",
                fontSize: 11,
                fontWeight: category === cat ? 600 : 400,
                transition: "all var(--transition-fast)",
              }}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-md)" }}>
        {communityLoading && communityPlugins.length === 0 && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "var(--space-2xl)", color: "var(--text-muted)" }}>
            <Loader2 size={20} style={{ animation: "spin 1s linear infinite", marginRight: "var(--space-sm)" }} />
            Loading community plugins...
          </div>
        )}

        {communityError && communityPlugins.length === 0 && (
          <div
            style={{
              padding: "var(--space-lg)",
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <AlertTriangle size={24} style={{ color: "var(--color-warning)", marginBottom: "var(--space-sm)" }} />
            <div>Could not load community plugins.</div>
            <div style={{ fontSize: 11, marginTop: "var(--space-xs)" }}>{communityError}</div>
          </div>
        )}

        {!communityLoading && !communityError && filtered.length === 0 && (
          <div style={{ padding: "var(--space-lg)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
            {communityPlugins.length === 0
              ? "No community plugins available yet."
              : "No matching plugins."}
          </div>
        )}

        {/* Plugin Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: "var(--space-md)",
          }}
        >
          {filtered.map((plugin) => (
            <PluginCard
              key={plugin.id}
              plugin={plugin}
              installed={installedIds.has(plugin.id)}
              installing={installingIds.has(plugin.id)}
              error={installError[plugin.id]}
              onInstall={() => handleInstall(plugin)}
              onUninstall={() => handleUninstall(plugin.id)}
            />
          ))}
        </div>
      </div>

      {/* Footer */}
      <div
        style={{
          padding: "var(--space-sm) var(--space-md)",
          borderTop: "1px solid var(--border-color)",
          fontSize: 11,
          color: "var(--text-muted)",
        }}
      >
        {filtered.length} of {communityPlugins.length} plugins &middot; {installedCount} installed
      </div>
    </div>
  );
}

// ──── Plugin Card ────

function PluginCard({
  plugin,
  installed,
  installing,
  error,
  onInstall,
  onUninstall,
}: {
  plugin: CommunityPlugin;
  installed: boolean;
  installing: boolean;
  error?: string;
  onInstall: () => void;
  onUninstall: () => void;
}) {
  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
            <span style={{ fontWeight: 600, fontSize: "var(--font-size-base)" }}>{plugin.name}</span>
            {plugin.verified && (
              <span title="Verified"><Shield size={12} style={{ color: "var(--accent)", flexShrink: 0 }} /></span>
            )}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            by {plugin.author} &middot; v{plugin.version}
          </div>
        </div>
      </div>

      {/* Badges */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)" }}>
        <span
          style={{
            padding: "1px var(--space-xs)",
            borderRadius: 4,
            background: "var(--bg-hover)",
            fontSize: 10,
            color: "var(--text-muted)",
          }}
        >
          {categoryMap[plugin.category] ?? plugin.category}
        </span>
        {plugin.platforms && !plugin.platforms.includes("all") && (
          <span
            style={{
              padding: "1px var(--space-xs)",
              borderRadius: 4,
              background: "var(--bg-hover)",
              fontSize: 10,
              color: "var(--text-muted)",
            }}
          >
            {plugin.platforms.join(", ")}
          </span>
        )}
      </div>

      {/* Description */}
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          lineHeight: 1.4,
          overflow: "hidden",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
        }}
      >
        {plugin.description}
      </div>

      {/* Error */}
      {error && (
        <div style={{ fontSize: 11, color: "var(--color-error)", padding: "var(--space-xs)", background: "rgba(244, 67, 54, 0.1)", borderRadius: 4 }}>
          {error}
        </div>
      )}

      {/* Action */}
      <div style={{ marginTop: "auto" }}>
        {installed ? (
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                fontSize: "var(--font-size-sm)",
                color: "var(--color-success)",
              }}
            >
              <CheckCircle size={14} />
              Installed
            </span>
            <button
              onClick={onUninstall}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "transparent",
                border: "1px solid var(--border-color)",
                color: "var(--text-muted)",
                fontSize: 11,
                cursor: "pointer",
              }}
              title="Uninstall"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ) : (
          <button
            onClick={onInstall}
            disabled={installing}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "var(--space-xs)",
              width: "100%",
              padding: "var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: installing ? "var(--bg-hover)" : "var(--accent)",
              color: installing ? "var(--text-muted)" : "var(--text-on-accent)",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
              cursor: installing ? "default" : "pointer",
              opacity: installing ? 0.7 : 1,
            }}
          >
            {installing ? (
              <>
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
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
