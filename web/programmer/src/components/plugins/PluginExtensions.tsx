/**
 * Plugin extension renderers — StateTableRenderer, PluginLogRenderer,
 * StatusCardSlot, DevicePanelSlot, ContextActionRenderer.
 *
 * These components are used across multiple views (Dashboard, Devices, etc.)
 * to render plugin-contributed UI content.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { Activity, Zap } from "lucide-react";
import { useConnectionStore } from "../../store/connectionStore";
import { showError } from "../../store/toastStore";
import { usePluginStore } from "../../store/pluginStore";
import { useLogStore } from "../../store/logStore";
import type { PluginExtension } from "../../api/restClient";
import * as api from "../../api/restClient";
import { SurfaceConfigurator } from "./SurfaceConfigurator";

// ──── State Table Renderer ────
// Shows a read-only table of state keys matching a glob pattern with live updates.

export function StateTableRenderer({
  statePattern,
  title,
}: {
  statePattern: string;
  title?: string;
}) {
  const liveState = useConnectionStore((s) => s.liveState);

  // Filter state keys matching the glob pattern (simple startsWith for now)
  const prefix = statePattern.replace(/\*.*$/, "");
  const entries = Object.entries(liveState).filter(([key]) =>
    key.startsWith(prefix)
  );

  return (
    <div>
      {title && (
        <h4
          style={{
            fontSize: "var(--font-size-sm)",
            fontWeight: 600,
            color: "var(--text-secondary)",
            marginBottom: "var(--space-sm)",
          }}
        >
          {title}
        </h4>
      )}
      {entries.length === 0 ? (
        <div
          style={{
            padding: "var(--space-md)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            textAlign: "center",
          }}
        >
          No state data
        </div>
      ) : (
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "var(--font-size-sm)",
          }}
        >
          <thead>
            <tr>
              <th
                style={{
                  textAlign: "left",
                  padding: "var(--space-xs) var(--space-sm)",
                  borderBottom: "1px solid var(--border-color)",
                  color: "var(--text-muted)",
                  fontWeight: 500,
                }}
              >
                Key
              </th>
              <th
                style={{
                  textAlign: "left",
                  padding: "var(--space-xs) var(--space-sm)",
                  borderBottom: "1px solid var(--border-color)",
                  color: "var(--text-muted)",
                  fontWeight: 500,
                }}
              >
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, value]) => (
              <tr key={key}>
                <td
                  style={{
                    padding: "var(--space-xs) var(--space-sm)",
                    borderBottom: "1px solid var(--border-color)",
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-secondary)",
                  }}
                >
                  {key}
                </td>
                <td
                  style={{
                    padding: "var(--space-xs) var(--space-sm)",
                    borderBottom: "1px solid var(--border-color)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {formatValue(value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

// ──── Plugin Log Renderer ────
// Shows log entries filtered to a specific plugin.

export function PluginLogRenderer({ pluginId }: { pluginId: string }) {
  const logEntries = useLogStore((s) => s.logEntries);
  const filtered = logEntries.filter(
    (e: { source: string; message: string }) =>
      e.source === `server.core.plugin_loader` ||
      e.message.includes(`[Plugin:${pluginId}]`)
  );

  const recent = filtered.slice(-50);

  return (
    <div
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        lineHeight: 1.6,
        maxHeight: 300,
        overflow: "auto",
        padding: "var(--space-sm)",
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
      }}
    >
      {recent.length === 0 ? (
        <div style={{ color: "var(--text-muted)", padding: "var(--space-md)", textAlign: "center" }}>
          No log entries for this plugin
        </div>
      ) : (
        recent.map((entry: { timestamp: number; level: string; message: string }, i: number) => (
          <div key={i} style={{ color: logColor(entry.level) }}>
            <span style={{ color: "var(--text-muted)" }}>
              {new Date(entry.timestamp * 1000).toLocaleTimeString()}
            </span>{" "}
            {entry.message}
          </div>
        ))
      )}
    </div>
  );
}

function logColor(level: string): string {
  switch (level.toUpperCase()) {
    case "ERROR":
      return "var(--color-error)";
    case "WARNING":
      return "var(--color-warning)";
    case "DEBUG":
      return "var(--text-muted)";
    default:
      return "var(--text-primary)";
  }
}

// ──── Status Card Slot ────
// Renders status cards from plugin extensions on the Dashboard.

export function StatusCardSlot() {
  const statusCards = usePluginStore((s) => s.extensions.status_cards);
  const liveState = useConnectionStore((s) => s.liveState);

  if (statusCards.length === 0) return null;

  return (
    <>
      {statusCards.map((card) => (
        <div
          key={`${card.plugin_id}.${card.id}`}
          style={{
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-color)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-sm)",
              marginBottom: "var(--space-sm)",
              fontWeight: 600,
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Activity size={14} style={{ color: "var(--accent)" }} />
            {card.label}
          </div>
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              marginBottom: "var(--space-sm)",
            }}
          >
            {card.plugin_name}
          </div>
          {card.metrics && (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
              {card.metrics.map((metric) => (
                <div
                  key={metric.key}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: "var(--font-size-sm)",
                  }}
                >
                  <span style={{ color: "var(--text-secondary)" }}>{metric.label}</span>
                  <span style={{ fontFamily: "var(--font-mono)" }}>
                    {formatMetric(liveState[metric.key], metric.format)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </>
  );
}

function formatMetric(value: unknown, format: string): string {
  if (value === null || value === undefined) return "—";
  if (format === "boolean") return value ? "Yes" : "No";
  return String(value);
}

// ──── Device Panel Slot ────
// Renders matching device_panel extensions on the device detail page.

export function DevicePanelSlot({
  deviceId,
  driverId,
  transport,
  category,
}: {
  deviceId: string;
  driverId: string;
  transport?: string;
  category?: string;
}) {
  const devicePanels = usePluginStore((s) => s.extensions.device_panels);
  const liveState = useConnectionStore((s) => s.liveState);

  // Filter panels that match this device
  const matching = devicePanels.filter((panel) => {
    const match = panel.match as Record<string, unknown> | undefined;
    if (!match) return true;

    if (match.driver_id) {
      const pattern = String(match.driver_id);
      if (pattern.includes("*")) {
        const prefix = pattern.replace("*", "");
        if (!driverId.startsWith(prefix)) return false;
      } else {
        if (driverId !== pattern) return false;
      }
    }
    if (match.transport && transport !== match.transport) return false;
    if (match.category && category !== match.category) return false;
    return true;
  });

  if (matching.length === 0) return null;

  return (
    <>
      {matching.map((panel) => {
        const resolvedPattern = panel.state_pattern?.replace(
          "{device_id}",
          deviceId
        );

        return (
          <div
            key={`${panel.plugin_id}.${panel.id}`}
            style={{
              marginTop: "var(--space-lg)",
              padding: "var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-surface)",
              border: "1px solid var(--border-color)",
            }}
          >
            <h3
              style={{
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                marginBottom: "var(--space-sm)",
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
              }}
            >
              {panel.label}
              <span
                style={{
                  fontSize: 10,
                  color: "var(--text-muted)",
                  fontWeight: 400,
                }}
              >
                via {panel.plugin_name}
              </span>
            </h3>
            {panel.renderer === "state_table" && resolvedPattern && (
              <StateTableRenderer statePattern={resolvedPattern} />
            )}
            {panel.renderer === "log" && (
              <PluginLogRenderer pluginId={panel.plugin_id} />
            )}
          </div>
        );
      })}
    </>
  );
}

// ──── Context Action Renderer ────
// Renders context action buttons from plugin extensions.

export function ContextActionRenderer({
  context,
  deviceId,
  driverId,
}: {
  context: "global" | "device" | "plugin";
  deviceId?: string;
  driverId?: string;
}) {
  const contextActions = usePluginStore((s) => s.extensions.context_actions);
  const [loading, setLoading] = useState<string | null>(null);

  const matching = contextActions.filter((action) => {
    if (action.context !== context) return false;
    if (context === "device" && action.match) {
      const match = action.match as Record<string, unknown>;
      if (match.driver_id && driverId) {
        const pattern = String(match.driver_id);
        if (pattern.includes("*")) {
          if (!driverId.startsWith(pattern.replace("*", ""))) return false;
        } else {
          if (driverId !== pattern) return false;
        }
      }
    }
    return true;
  });

  const handleClick = useCallback(
    async (action: PluginExtension) => {
      if (!action.event) return;
      setLoading(action.id);
      try {
        const actionId = action.event.replace("action.", "");
        const payload: Record<string, unknown> = {};
        if (deviceId) payload.device_id = deviceId;
        await api.emitContextAction(action.plugin_id, actionId, payload);
      } catch (e) {
        console.error("Context action failed:", e);
      }
      setLoading(null);
    },
    [deviceId]
  );

  if (matching.length === 0) return null;

  return (
    <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
      {matching.map((action) => (
        <button
          key={`${action.plugin_id}.${action.id}`}
          onClick={() => handleClick(action)}
          disabled={loading === action.id}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
            opacity: loading === action.id ? 0.5 : 1,
          }}
          title={`${action.label} (${action.plugin_name})`}
        >
          <Zap size={12} />
          {action.label}
        </button>
      ))}
    </div>
  );
}

// ──── Plugin View Renderer ────
// Dispatches to the appropriate renderer for a plugin view.

export function PluginViewRenderer({ ext }: { ext: PluginExtension }) {
  switch (ext.renderer) {
    case "surface":
      return <SurfaceViewRenderer ext={ext} />;
    case "state_table":
      return (
        <div style={{ padding: "var(--space-lg)" }}>
          <StateTableRenderer
            statePattern={ext.state_pattern ?? `plugin.${ext.plugin_id}.*`}
            title={ext.label}
          />
        </div>
      );
    case "log":
      return (
        <div style={{ padding: "var(--space-lg)" }}>
          <PluginLogRenderer pluginId={ext.plugin_id} />
        </div>
      );
    default:
      return (
        <div
          style={{
            padding: "var(--space-lg)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          Renderer type "{ext.renderer}" is not yet supported.
        </div>
      );
  }
}

function SurfaceViewRenderer({ ext }: { ext: PluginExtension }) {
  const [pluginDetail, setPluginDetail] = useState<Record<string, unknown> | null>(null);
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const updateConfig = usePluginStore((s) => s.updateConfig);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    api.getPlugin(ext.plugin_id).then((detail) => {
      setPluginDetail(detail as unknown as Record<string, unknown>);
    }).catch(() => showError(`Failed to load plugin details for '${ext.plugin_id}'`));

    api.getPluginConfig(ext.plugin_id).then((r) => {
      setConfig(r.config);
    }).catch(() => showError(`Failed to load config for plugin '${ext.plugin_id}'`));
  }, [ext.plugin_id]);

  const handleConfigChange = useCallback(
    (newConfig: Record<string, unknown>) => {
      setConfig(newConfig);
      clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        updateConfig(ext.plugin_id, newConfig);
      }, 1500);
    },
    [ext.plugin_id, updateConfig]
  );

  const surfaceLayout = pluginDetail?.surface_layout as Record<string, unknown> | undefined;
  if (!surfaceLayout) {
    return (
      <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
        Loading surface layout...
      </div>
    );
  }

  return (
    <div style={{ padding: "var(--space-lg)" }}>
      <SurfaceConfigurator
        layout={surfaceLayout as any}
        pluginId={ext.plugin_id}
        config={config}
        onConfigChange={handleConfigChange}
      />
    </div>
  );
}
