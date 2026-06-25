import { useEffect, useState, useCallback, useRef } from "react";
import { Search, Plug, AlertTriangle, RefreshCw, Power, PowerOff, Trash2, ArrowRight } from "lucide-react";
import { CopyButton } from "../components/shared/CopyButton";
import { ViewContainer } from "../components/layout/ViewContainer";
import { usePluginStore } from "../store/pluginStore";
import { useNavigationStore } from "../store/navigationStore";
import * as api from "../api/restClient";
import type { PluginDataInfo } from "../api/pluginClient";
import { parseApiError } from "../api/errors";
import { InlineError } from "../components/shared/InlineError";
import type { PluginInfo } from "../api/types";
import { SurfaceConfigurator } from "../components/plugins/SurfaceConfigurator";
import { SchemaFormRenderer } from "../components/plugins/PluginConfigForm";
import { CollapsibleSection } from "../components/driver-builder/CollapsibleSection";
import { BrowsePlugins } from "../components/plugins/BrowsePlugins";
import { MarkdownContent } from "../components/ai/MarkdownContent";
import { isPluginIncompatible } from "./pluginsView.helpers";

// ──── Helpers ────

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// ──── Status Dot ────

function PluginStatusDot({
  status,
  incompatible = false,
  size = 10,
}: {
  status: string;
  incompatible?: boolean;
  size?: number;
}) {
  // `incompatible` is derived from the backend's truthful `compatible` flag by
  // the caller; a plugin can be incompatible without its status literally
  // being "incompatible" (it's only set so for started project plugins).
  const isIncompat = incompatible || status === "incompatible";
  const isTriangle = status === "missing" || isIncompat;
  const color =
    status === "running"
      ? "var(--color-success)"
      : status === "error"
        ? "var(--color-error)"
        : status === "missing"
          ? "var(--color-warning, #f59e0b)"
          : isIncompat
            ? "#f97316"
            : "var(--text-muted)";

  const title =
    status === "running"
      ? "Running"
      : status === "error"
        ? "Error"
        : status === "missing"
          ? "Not installed"
          : isIncompat
            ? "Incompatible platform"
            : "Stopped";

  if (isTriangle) {
    return (
      <span
        style={{
          display: "inline-block",
          flexShrink: 0,
          width: 0,
          height: 0,
          borderLeft: `${size / 2}px solid transparent`,
          borderRight: `${size / 2}px solid transparent`,
          borderBottom: `${size}px solid ${color}`,
          backgroundColor: "transparent",
        }}
        title={title}
      />
    );
  }

  return (
    <span
      style={{
        display: "inline-block",
        flexShrink: 0,
        width: size,
        height: size,
        borderRadius: "50%",
        backgroundColor: color,
      }}
      title={title}
    />
  );
}

// ──── Plugin List Item ────

function PluginListItem({
  plugin,
  selected,
  onClick,
}: {
  plugin: PluginInfo;
  selected: boolean;
  onClick: () => void;
}) {
  const incompatible = isPluginIncompatible(plugin);
  const suffix =
    plugin.status === "missing"
      ? " (not installed)"
      : incompatible
        ? " (incompatible)"
        : "";

  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-md)",
        width: "100%",
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: selected ? "var(--accent-dim)" : "transparent",
        textAlign: "left",
        marginBottom: "var(--space-xs)",
        transition: "background var(--transition-fast)",
        opacity: plugin.status === "running" ? 1 : 0.6,
      }}
    >
      <PluginStatusDot status={plugin.status} incompatible={incompatible} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {plugin.name}
        </div>
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color:
              plugin.status === "missing" || incompatible
                ? "var(--color-warning, #f59e0b)"
                : "var(--text-muted)",
          }}
        >
          {plugin.version ? `v${plugin.version}` : plugin.plugin_id}
          {suffix}
        </div>
      </div>
    </button>
  );
}

// ──── Missing Plugin Banner ────

function MissingPluginBanner({ plugin }: { plugin: PluginInfo }) {
  const navigateTo = useNavigationStore((s) => s.navigateTo);
  const isMissing = plugin.status === "missing";
  const isIncompat = isPluginIncompatible(plugin);

  if (!isMissing && !isIncompat) return null;

  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.12)",
        border: "1px solid rgba(245, 158, 11, 0.3)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          fontWeight: 600,
          marginBottom: "var(--space-sm)",
          color: "var(--color-warning, #f59e0b)",
        }}
      >
        <AlertTriangle size={16} />
        {isMissing ? "Plugin Required" : "Platform Incompatible"}
      </div>
      <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
        {isMissing
          ? `This project uses the plugin "${plugin.name || plugin.plugin_id}" which is not installed.`
          : `Plugin "${plugin.name || plugin.plugin_id}" is not compatible with the current platform.`}
      </div>
      {isMissing && (
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button
            onClick={() => navigateTo("plugins")}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--color-warning, #f59e0b)",
              color: "#000",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
            }}
          >
            Install from Community
          </button>
        </div>
      )}
    </div>
  );
}

// ──── Plugin Detail View ────

function PluginDetail({ plugin }: { plugin: PluginInfo }) {
  const enablePlugin = usePluginStore((s) => s.enablePlugin);
  const disablePlugin = usePluginStore((s) => s.disablePlugin);
  const updateConfig = usePluginStore((s) => s.updateConfig);
  const activatePlugin = usePluginStore((s) => s.activatePlugin);
  const load = usePluginStore((s) => s.load);
  const setSelectedId = usePluginStore((s) => s.setSelectedId);
  const pluginViewExts = usePluginStore((s) => s.extensions.views);
  const navigateTo = useNavigationStore((s) => s.navigateTo);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [detailInfo, setDetailInfo] = useState<PluginInfo | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [uninstallError, setUninstallError] = useState<string | null>(null);
  const [dataInfo, setDataInfo] = useState<PluginDataInfo | null>(null);
  const [discardData, setDiscardData] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // When the uninstall confirm opens, fetch plugin-data size so we can
  // show "Also discard X MB" — only if there's actually data to discard.
  useEffect(() => {
    if (!confirmUninstall) {
      setDataInfo(null);
      setDiscardData(false);
      return;
    }
    let cancelled = false;
    api
      .getPluginDataInfo(plugin.plugin_id)
      .then((info) => {
        if (!cancelled) setDataInfo(info);
      })
      .catch(() => {
        // Best-effort: if the size lookup fails we just hide the checkbox.
        if (!cancelled) setDataInfo(null);
      });
    return () => {
      cancelled = true;
    };
  }, [confirmUninstall, plugin.plugin_id]);

  // Fetch full detail (including config_schema) on mount
  useEffect(() => {
    api.getPlugin(plugin.plugin_id).then(setDetailInfo).catch(console.error);
  }, [plugin.plugin_id, plugin.status]);

  // Load config values
  useEffect(() => {
    if (plugin.status !== "missing" && plugin.status !== "incompatible") {
      api
        .getPluginConfig(plugin.plugin_id)
        .then((r) => setConfigValues(r.config))
        .catch(console.error);
    }
  }, [plugin.plugin_id, plugin.status]);

  const handleConfigChange = useCallback(
    (key: string, value: unknown) => {
      setConfigValues((prev) => {
        const next = { ...prev, [key]: value };
        clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(async () => {
          setSaving(true);
          await updateConfig(plugin.plugin_id, next);
          setSaving(false);
        }, 1500);
        return next;
      });
    },
    [plugin.plugin_id, updateConfig]
  );

  // Cancel any pending debounced config save when this panel unmounts — which
  // now includes switching plugins, since PluginDetail is keyed by plugin_id.
  // Without it, a write the user navigated away from still fires later,
  // persisting abandoned config and restarting the plugin.
  useEffect(() => () => clearTimeout(saveTimer.current), []);

  const info = detailInfo ?? plugin;
  const isRunning = info.status === "running";
  const isMissing = info.status === "missing";
  const isIncompat = isPluginIncompatible(info);

  // A running plugin can contribute its own full-page view for its control
  // surface (e.g. a Stream Deck view). When it does, that view is the one
  // home for surface authoring and this page just points there.
  const surfaceView = pluginViewExts.find(
    (v) => v.plugin_id === plugin.plugin_id && v.renderer === "surface"
  );

  const categoryLabels: Record<string, string> = {
    control_surface: "Control Surface",
    integration: "Integration",
    sensor: "Sensor",
    utility: "Utility",
  };

  return (
    <div
      style={{
        flex: 1,
        overflow: "auto",
        padding: "var(--space-lg)",
      }}
    >
      {/* Banner for missing/incompatible */}
      <MissingPluginBanner plugin={info} />

      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: "var(--space-lg)",
        }}
      >
        <div>
          <h2 style={{ fontSize: "var(--font-size-xl)", fontWeight: 600, marginBottom: "var(--space-xs)" }}>
            {info.name}
          </h2>
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            {info.version && `v${info.version}`}
            {info.author && ` by ${info.author}`}
            {info.category && ` · ${categoryLabels[info.category] ?? info.category}`}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
            <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {plugin.plugin_id}
            </code>
            <CopyButton value={plugin.plugin_id} title="Copy plugin ID" />
          </div>
        </div>
        {!isMissing && !isIncompat && (
          <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center", flexWrap: "wrap" }}>
            {isRunning ? (
              <button
                onClick={() => disablePlugin(plugin.plugin_id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <PowerOff size={14} />
                Disable
              </button>
            ) : (
              <button
                onClick={() => enablePlugin(plugin.plugin_id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--accent-bg)",
                  color: "var(--text-on-accent)",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 500,
                }}
              >
                <Power size={14} />
                Enable
              </button>
            )}
            {confirmUninstall ? (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-xs)",
                  alignItems: "flex-end",
                }}
              >
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>
                  Uninstall this plugin?
                </span>
                {dataInfo?.exists && dataInfo.size_bytes > 0 && (
                  <label
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-xs)",
                      fontSize: "var(--font-size-sm)",
                      color: "var(--text-muted)",
                      cursor: "pointer",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={discardData}
                      onChange={(e) => setDiscardData(e.target.checked)}
                    />
                    Also discard {formatBytes(dataInfo.size_bytes)} of plugin data
                  </label>
                )}
                <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                  <button
                    onClick={async () => {
                      try {
                        await api.uninstallPlugin(plugin.plugin_id, {
                          removeData: discardData,
                        });
                        setConfirmUninstall(false);
                        setSelectedId(null);
                        load();
                      } catch (e) {
                        setUninstallError(parseApiError(e));
                        setConfirmUninstall(false);
                      }
                    }}
                    style={{
                      padding: "var(--space-xs) var(--space-md)",
                      borderRadius: "var(--border-radius)",
                      background: "var(--color-error, #dc2626)",
                      color: "#fff",
                      fontSize: "var(--font-size-sm)",
                    }}
                  >
                    Yes, Uninstall
                  </button>
                  <button
                    onClick={() => setConfirmUninstall(false)}
                    style={{
                      padding: "var(--space-xs) var(--space-md)",
                      borderRadius: "var(--border-radius)",
                      background: "var(--bg-hover)",
                      fontSize: "var(--font-size-sm)",
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => {
                  if (isRunning) {
                    setUninstallError("Disable the plugin before uninstalling.");
                    return;
                  }
                  setUninstallError(null);
                  setConfirmUninstall(true);
                }}
                title={
                  isRunning
                    ? "Disable the plugin before uninstalling."
                    : "Removes the plugin files. You can reinstall from Browse."
                }
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  color: isRunning ? "var(--text-muted)" : "var(--color-error, #dc2626)",
                  fontSize: "var(--font-size-sm)",
                  cursor: "pointer",
                  opacity: isRunning ? 0.6 : 1,
                }}
              >
                <Trash2 size={14} /> Uninstall
              </button>
            )}
          </div>
        )}
        {isMissing && (
          <button
            onClick={() => activatePlugin(plugin.plugin_id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
            }}
          >
            <RefreshCw size={14} />
            Activate
          </button>
        )}
      </div>

      {/* Uninstall error / status, next to the action */}
      <InlineError
        message={uninstallError}
        onDismiss={() => setUninstallError(null)}
        style={{ marginBottom: "var(--space-md)" }}
      />

      {/* Description */}
      {info.description && (
        <p style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: "var(--space-lg)" }}>
          {info.description}
        </p>
      )}

      {/* Usage / How to Use — markdown from PLUGIN_INFO.usage */}
      {info.usage && (
        <div
          style={{
            marginBottom: "var(--space-lg)",
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-color)",
          }}
        >
          <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: "var(--space-sm)", color: "var(--text-secondary)" }}>
            How to Use
          </h3>
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)" }}>
            <MarkdownContent content={info.usage} />
          </div>
        </div>
      )}

      {/* Error message */}
      {info.status === "error" && info.error && (
        <div
          style={{
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            background: "rgba(244, 67, 54, 0.12)",
            border: "1px solid rgba(244, 67, 54, 0.3)",
            fontSize: "var(--font-size-sm)",
            color: "var(--color-error)",
          }}
        >
          <strong>Error:</strong> {info.error}
        </div>
      )}

      {/* Control surface authoring. A plugin with its own surface view gets
          a pointer (one home for the editor); plugins without one keep the
          embedded configurator. */}
      {(detailInfo as any)?.surface_layout && (
        <div style={{ marginBottom: "var(--space-lg)" }}>
          {surfaceView ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "var(--space-md)",
                flexWrap: "wrap",
                padding: "var(--space-md)",
                borderRadius: "var(--border-radius)",
                border: "1px solid var(--border-color)",
                background: "var(--bg-surface)",
              }}
            >
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
                Button assignments, pages, and hardware options live in the{" "}
                <strong style={{ color: "var(--text-primary)" }}>{surfaceView.label}</strong> view.
              </div>
              <button
                onClick={() =>
                  navigateTo(`plugin-view:${plugin.plugin_id}.${surfaceView.id}`)
                }
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--accent-bg)",
                  color: "var(--text-on-accent)",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 500,
                }}
              >
                Open {surfaceView.label}
                <ArrowRight size={14} />
              </button>
            </div>
          ) : isRunning ? (
            <>
              <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "var(--space-md)" }}>
                Surface Layout
              </h3>
              <SurfaceConfigurator
                layout={(detailInfo as any).surface_layout}
                pluginId={plugin.plugin_id}
                config={configValues}
                onConfigChange={(newConfig) => {
                  setConfigValues(newConfig);
                  clearTimeout(saveTimer.current);
                  saveTimer.current = setTimeout(async () => {
                    setSaving(true);
                    await updateConfig(plugin.plugin_id, newConfig);
                    setSaving(false);
                  }, 1500);
                }}
                onRequestConfigRefresh={async () => {
                  try {
                    const r = await api.getPluginConfig(plugin.plugin_id);
                    setConfigValues(r.config);
                  } catch (e) {
                    console.error("Failed to refresh config:", e);
                  }
                }}
              />
            </>
          ) : (
            <div
              style={{
                padding: "var(--space-md)",
                borderRadius: "var(--border-radius)",
                border: "1px dashed var(--border-color)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              Enable the plugin to set up its control surface.
            </div>
          )}
        </div>
      )}

      {/* Configuration */}
      {detailInfo?.config_schema && Object.keys(detailInfo.config_schema).length > 0 && (
        <div style={{ marginBottom: "var(--space-lg)" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-md)" }}>
            <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, color: "var(--text-secondary)" }}>
              Configuration
            </h3>
            {saving && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Saving...</span>
            )}
          </div>
          <SchemaFormRenderer
            schema={detailInfo.config_schema}
            values={configValues}
            onChange={handleConfigChange}
          />
        </div>
      )}

      {/* Developer-facing metadata, tucked away */}
      {((info.capabilities && info.capabilities.length > 0) ||
        (info.platforms && info.platforms.length > 0)) && (
        <CollapsibleSection
          title="Plugin Details"
          subtitle="Granted capabilities and supported platforms"
          defaultOpen={false}
        >
          {info.capabilities && info.capabilities.length > 0 && (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                Capabilities
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)" }}>
                {info.capabilities.map((cap) => (
                  <span
                    key={cap}
                    style={{
                      padding: "2px var(--space-sm)",
                      borderRadius: "var(--border-radius)",
                      background: "var(--bg-hover)",
                      fontSize: 11,
                      color: "var(--text-muted)",
                    }}
                  >
                    {cap}
                  </span>
                ))}
              </div>
            </div>
          )}
          {info.platforms && info.platforms.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                Platforms
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)" }}>
                {info.platforms.map((p) => (
                  <span
                    key={p}
                    style={{
                      padding: "2px var(--space-sm)",
                      borderRadius: "var(--border-radius)",
                      background: "var(--bg-hover)",
                      fontSize: 11,
                      color: "var(--text-muted)",
                    }}
                  >
                    {p}
                  </span>
                ))}
              </div>
            </div>
          )}
        </CollapsibleSection>
      )}

    </div>
  );
}

// ──── Main Plugins View ────

export function PluginsView() {
  const plugins = usePluginStore((s) => s.plugins);
  const loading = usePluginStore((s) => s.loading);
  const selectedId = usePluginStore((s) => s.selectedId);
  const setSelectedId = usePluginStore((s) => s.setSelectedId);
  const load = usePluginStore((s) => s.load);

  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"installed" | "browse">("installed");

  // Load on mount + consume focus
  useEffect(() => {
    load();
    const focus = useNavigationStore.getState().consumeFocus();
    if (focus?.type === "plugin" && focus.id) {
      setSelectedId(focus.id);
    }
  }, [load, setSelectedId]);

  const filtered = plugins.filter(
    (p) =>
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.plugin_id.toLowerCase().includes(search.toLowerCase())
  );

  const selected = plugins.find((p) => p.plugin_id === selectedId) ?? null;

  return (
    <ViewContainer
      title="Plugins"
      actions={
        <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
          {/* Tab toggle */}
          <div style={{ display: "flex", borderRadius: "var(--border-radius)", overflow: "hidden", border: "1px solid var(--border-color)" }}>
            <button
              onClick={() => setTab("installed")}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                fontSize: "var(--font-size-sm)",
                background: tab === "installed" ? "var(--accent-bg)" : "transparent",
                color: tab === "installed" ? "var(--text-on-accent)" : "var(--text-secondary)",
                fontWeight: tab === "installed" ? 600 : 400,
              }}
            >
              Installed
            </button>
            <button
              onClick={() => setTab("browse")}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                fontSize: "var(--font-size-sm)",
                background: tab === "browse" ? "var(--accent-bg)" : "transparent",
                color: tab === "browse" ? "var(--text-on-accent)" : "var(--text-secondary)",
                fontWeight: tab === "browse" ? 600 : 400,
              }}
            >
              Browse
            </button>
          </div>
          <button
            onClick={() => load()}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
            title="Refresh"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      }
    >
      {tab === "browse" ? (
        <BrowsePlugins />
      ) : (
      <div style={{ display: "flex", height: "100%", minHeight: 0 }}>
        {/* Left: Plugin List */}
        <div
          style={{
            width: 280,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            borderRight: "1px solid var(--border-color)",
          }}
        >
          {/* Search */}
          <div style={{ padding: "var(--space-sm)", borderBottom: "1px solid var(--border-color)" }}>
            <div
              style={{
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
          </div>

          {/* Plugin List */}
          <div style={{ flex: 1, overflow: "auto", padding: "var(--space-sm)" }}>
            {loading && plugins.length === 0 && (
              <div style={{ padding: "var(--space-lg)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
                Loading...
              </div>
            )}
            {!loading && filtered.length === 0 && (
              <div style={{ padding: "var(--space-lg)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
                {plugins.length === 0
                  ? <>
                      No plugins installed. Click the <strong>Browse</strong> tab to find and install plugins.
                      <br /><br />
                      <a href="https://docs.openavc.com/plugins" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                        Learn about plugins
                      </a>
                    </>
                  : "No matching plugins."}
              </div>
            )}
            {filtered.map((p) => (
              <PluginListItem
                key={p.plugin_id}
                plugin={p}
                selected={selectedId === p.plugin_id}
                onClick={() => setSelectedId(p.plugin_id)}
              />
            ))}
          </div>

          {/* Count */}
          <div
            style={{
              padding: "var(--space-sm) var(--space-md)",
              borderTop: "1px solid var(--border-color)",
              fontSize: 11,
              color: "var(--text-muted)",
            }}
          >
            {plugins.length} plugin{plugins.length !== 1 ? "s" : ""}
          </div>
        </div>

        {/* Right: Detail or Empty */}
        {selected ? (
          <PluginDetail key={selected.plugin_id} plugin={selected} />
        ) : (
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-muted)",
              gap: "var(--space-md)",
            }}
          >
            <Plug size={48} strokeWidth={1} />
            <div style={{ fontSize: "var(--font-size-sm)" }}>
              {plugins.length === 0
                ? "No plugins installed"
                : "Select a plugin to view details"}
            </div>
          </div>
        )}
      </div>
      )}
    </ViewContainer>
  );
}
