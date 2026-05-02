import { useState, useEffect } from "react";
import { Search, Trash2, Pencil, Copy, Code } from "lucide-react";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import { useNavigationStore } from "../../store/navigationStore";
import { parseApiError } from "../../api/errors";
import type { DriverInfo, InstalledDriver } from "../../api/types";

const GENERIC_IDS = new Set(["generic_tcp", "generic_serial", "generic_http"]);

const headerBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius, var(--radius))",
  background: "var(--bg-hover)",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
};

const CATEGORY_COLORS: Record<string, string> = {
  projector: "#e74c3c",
  display: "#3498db",
  switcher: "#9b59b6",
  audio: "#2ecc71",
  camera: "#e67e22",
  lighting: "#f1c40f",
  video: "#1abc9c",
  utility: "#95a5a6",
};

interface InstalledDriversViewProps {
  /** Switch DriverPanel viewTab to "create" with this driver loaded in the editor. */
  onOpenInBuilder?: (driverId: string) => void;
  /** Duplicate a built-in driver into the user repo and switch to the editor. */
  onCustomizeCopy?: (driverId: string) => Promise<void> | void;
}

export function InstalledDriversView({
  onOpenInBuilder,
  onCustomizeCopy,
}: InstalledDriversViewProps = {}) {
  const registeredDrivers = useDriverBuilderStore((s) => s.registeredDrivers);
  const installedDrivers = useDriverBuilderStore((s) => s.installedDrivers);
  const definitions = useDriverBuilderStore((s) => s.definitions);
  const loadRegisteredDrivers = useDriverBuilderStore((s) => s.loadRegisteredDrivers);
  const loadInstalledDrivers = useDriverBuilderStore((s) => s.loadInstalledDrivers);
  const loadDefinitions = useDriverBuilderStore((s) => s.loadDefinitions);
  const uninstallDriver = useDriverBuilderStore((s) => s.uninstallDriver);
  const project = useProjectStore((s) => s.project);
  const navigateTo = useNavigationStore((s) => s.navigateTo);
  const selectedId = useDriverBuilderStore((s) => s.installedDriverId);
  const setSelectedId = useDriverBuilderStore((s) => s.setInstalledDriverId);

  const [searchQuery, setSearchQuery] = useState("");
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [uninstalling, setUninstalling] = useState(false);
  const [uninstallError, setUninstallError] = useState<string | null>(null);

  useEffect(() => {
    loadRegisteredDrivers();
    loadInstalledDrivers();
    loadDefinitions();
  }, [loadRegisteredDrivers, loadInstalledDrivers, loadDefinitions]);

  // Reset confirm/error state when selection changes
  useEffect(() => {
    setConfirmUninstall(false);
    setUninstallError(null);
  }, [selectedId]);

  const installedIdSet = new Set(installedDrivers.map((d) => d.id));
  const installedById = new Map<string, InstalledDriver>(
    installedDrivers.map((d) => [d.id, d])
  );
  const definitionById = new Map(definitions.map((d) => [d.id, d]));

  const filteredDrivers = registeredDrivers.filter((d) => {
    if (GENERIC_IDS.has(d.id)) return false;
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      d.name.toLowerCase().includes(q) ||
      d.manufacturer.toLowerCase().includes(q) ||
      d.category.toLowerCase().includes(q)
    );
  });

  const selectedDriver = registeredDrivers.find((d) => d.id === selectedId) || null;
  const canUninstall = selectedId ? installedIdSet.has(selectedId) : false;
  const selectedInstalled = selectedId ? installedById.get(selectedId) ?? null : null;
  const selectedDefinition = selectedId ? definitionById.get(selectedId) ?? null : null;
  const isPython = selectedInstalled?.format === "python";
  const isBuiltin = selectedDefinition?.source === "builtin"
    || (!selectedInstalled && !!selectedDefinition);

  const devicesUsingDriver = selectedDriver && project
    ? project.devices.filter((d) => d.driver === selectedDriver.id)
    : [];

  const handleOpenInCodeEditor = (driverId: string, filename?: string) => {
    navigateTo("scripts", {
      type: "python_driver",
      id: driverId,
      detail: filename ? `file:${filename}` : undefined,
    });
  };

  const handleUninstall = async () => {
    if (!selectedDriver) return;
    setUninstalling(true);
    setUninstallError(null);
    try {
      await uninstallDriver(selectedDriver.id);
      setSelectedId(null);
      setConfirmUninstall(false);
    } catch (e) {
      setUninstallError(parseApiError(e));
      setConfirmUninstall(false);
    } finally {
      setUninstalling(false);
    }
  };

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      {/* Left sidebar */}
      <div
        style={{
          width: 260,
          borderRight: "1px solid var(--border-color)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Search */}
        <div style={{ padding: "var(--space-sm)", borderBottom: "1px solid var(--border-color)" }}>
          <div style={{ position: "relative" }}>
            <Search
              size={14}
              style={{
                position: "absolute",
                left: 8,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--text-muted)",
              }}
            />
            <input
              type="text"
              placeholder="Search drivers..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{ width: "100%", paddingLeft: 28, fontSize: "var(--font-size-sm)" }}
            />
          </div>
        </div>

        {/* Driver list */}
        <div style={{ flex: 1, overflowY: "auto" }}>
          {filteredDrivers.length === 0 ? (
            <div
              style={{
                padding: "var(--space-lg)",
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              {searchQuery ? "No drivers match your search." : "No drivers installed."}
            </div>
          ) : (
            filteredDrivers.map((d) => {
              const installedRow = installedById.get(d.id);
              const formatBadge = installedRow?.format === "python" ? "PY" : null;
              return (
                <div
                  key={d.id}
                  onClick={() => setSelectedId(d.id)}
                  style={{
                    padding: "var(--space-sm) var(--space-md)",
                    cursor: "pointer",
                    borderBottom: "1px solid var(--border-color)",
                    background:
                      selectedId === d.id ? "var(--accent-dim, rgba(59,130,246,0.1))" : "transparent",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      fontWeight: 500,
                      fontSize: "var(--font-size-sm)",
                    }}
                  >
                    <span
                      style={{
                        flex: 1,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {d.name}
                    </span>
                    {formatBadge && (
                      <span
                        title="Python driver"
                        style={{
                          fontSize: 10,
                          padding: "1px 5px",
                          borderRadius: 3,
                          background: "var(--bg-hover)",
                          color: "var(--text-muted)",
                          fontFamily: "var(--font-mono)",
                          fontWeight: 600,
                          flexShrink: 0,
                        }}
                      >
                        {formatBadge}
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: "var(--font-size-xs)",
                      color: "var(--text-muted)",
                      marginTop: 2,
                    }}
                  >
                    {d.manufacturer} &middot; {d.category || "Other"}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div
          style={{
            padding: "var(--space-sm)",
            borderTop: "1px solid var(--border-color)",
            fontSize: "var(--font-size-xs)",
            color: "var(--text-muted)",
            textAlign: "center",
          }}
        >
          {filteredDrivers.length} driver{filteredDrivers.length !== 1 ? "s" : ""}
        </div>
      </div>

      {/* Right detail panel */}
      <div style={{ flex: 1, overflowY: "auto", padding: "var(--space-lg)" }}>
        {selectedDriver ? (
          <DriverDetailPanel
            driver={selectedDriver}
            installed={selectedInstalled}
            isPython={isPython}
            isBuiltin={isBuiltin}
            canUninstall={canUninstall}
            devicesUsingDriver={devicesUsingDriver.map((d) => d.name || d.id)}
            confirmUninstall={confirmUninstall}
            uninstalling={uninstalling}
            uninstallError={uninstallError}
            canOpenInBuilder={!!onOpenInBuilder && !isPython && !!selectedDefinition}
            onOpenInBuilder={
              onOpenInBuilder ? () => onOpenInBuilder(selectedDriver.id) : undefined
            }
            onCustomizeCopy={
              onCustomizeCopy ? () => onCustomizeCopy(selectedDriver.id) : undefined
            }
            onOpenInCodeEditor={
              isPython
                ? () => handleOpenInCodeEditor(selectedDriver.id, selectedInstalled?.filename)
                : undefined
            }
            onRequestUninstall={() => {
              setUninstallError(null);
              setConfirmUninstall(true);
            }}
            onConfirmUninstall={handleUninstall}
            onCancelUninstall={() => setConfirmUninstall(false)}
            onDismissUninstallError={() => setUninstallError(null)}
          />
        ) : (
          <div
            style={{
              textAlign: "center",
              padding: "var(--space-xl)",
              color: "var(--text-muted)",
            }}
          >
            <p>Select a driver from the list to view its details.</p>
            <p style={{ fontSize: "var(--font-size-sm)", marginTop: "var(--space-sm)" }}>
              You can see help text, configuration properties, commands, and uninstall drivers you no longer need.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}


function DriverDetailPanel({
  driver,
  installed,
  isPython,
  isBuiltin,
  canUninstall,
  devicesUsingDriver,
  confirmUninstall,
  uninstalling,
  uninstallError,
  canOpenInBuilder,
  onOpenInBuilder,
  onCustomizeCopy,
  onOpenInCodeEditor,
  onRequestUninstall,
  onConfirmUninstall,
  onCancelUninstall,
  onDismissUninstallError,
}: {
  driver: DriverInfo;
  installed: InstalledDriver | null;
  isPython: boolean;
  isBuiltin: boolean;
  canUninstall: boolean;
  devicesUsingDriver: string[];
  confirmUninstall: boolean;
  uninstalling: boolean;
  uninstallError: string | null;
  canOpenInBuilder: boolean;
  onOpenInBuilder?: () => void;
  onCustomizeCopy?: () => void;
  onOpenInCodeEditor?: () => void;
  onRequestUninstall: () => void;
  onConfirmUninstall: () => void;
  onCancelUninstall: () => void;
  onDismissUninstallError: () => void;
}) {
  const help = driver.help;
  const configSchema = driver.config_schema || {};
  const commands = driver.commands || {};
  const stateVars = driver.state_variables || {};
  const inUse = devicesUsingDriver.length > 0;

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "var(--space-lg)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-md)",
            flexWrap: "wrap",
          }}
        >
          <h2 style={{ margin: 0, fontSize: "1.25rem", flex: 1 }}>{driver.name}</h2>
          {/* Edit / Customize / Code editor actions */}
          {!confirmUninstall && (
            <>
              {isPython && onOpenInCodeEditor && (
                <button
                  onClick={onOpenInCodeEditor}
                  title="Open this Python driver in the Code Editor"
                  style={headerBtnStyle}
                >
                  <Code size={14} /> Open in Code Editor
                </button>
              )}
              {!isPython && canOpenInBuilder && !isBuiltin && onOpenInBuilder && (
                <button
                  onClick={onOpenInBuilder}
                  title="Open this driver in the Driver Builder"
                  style={headerBtnStyle}
                >
                  <Pencil size={14} /> Open in Builder
                </button>
              )}
              {!isPython && isBuiltin && onCustomizeCopy && (
                <button
                  onClick={onCustomizeCopy}
                  title="Built-in drivers can't be edited in place. Clones to your library so you can customize."
                  style={headerBtnStyle}
                >
                  <Copy size={14} /> Customize a Copy
                </button>
              )}
            </>
          )}
          {canUninstall && (
            confirmUninstall ? (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error, var(--danger))" }}>
                  Uninstall this driver?
                </span>
                <button
                  onClick={onConfirmUninstall}
                  disabled={uninstalling}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius, var(--radius))",
                    background: "var(--color-error, var(--danger))",
                    color: "#fff",
                    fontSize: "var(--font-size-sm)",
                    opacity: uninstalling ? 0.6 : 1,
                  }}
                >
                  {uninstalling ? "Uninstalling..." : "Yes, Uninstall"}
                </button>
                <button
                  onClick={onCancelUninstall}
                  disabled={uninstalling}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius, var(--radius))",
                    background: "var(--bg-hover)",
                    fontSize: "var(--font-size-sm)",
                  }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={onRequestUninstall}
                disabled={inUse}
                title={
                  inUse
                    ? `In use by ${devicesUsingDriver.length} device(s). Remove or reassign them before uninstalling.`
                    : "Removes the driver file. You can reinstall from Browse Community."
                }
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius, var(--radius))",
                  background: "var(--bg-hover)",
                  color: inUse ? "var(--text-muted)" : "var(--color-error, var(--danger))",
                  fontSize: "var(--font-size-sm)",
                  cursor: inUse ? "not-allowed" : "pointer",
                  opacity: inUse ? 0.6 : 1,
                }}
              >
                <Trash2 size={14} /> Uninstall
              </button>
            )
          )}
        </div>
        <div
          style={{
            display: "flex",
            gap: "var(--space-sm)",
            alignItems: "center",
            marginTop: "var(--space-xs)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
          }}
        >
          <span>{driver.manufacturer}</span>
          {driver.version && <span>&middot; v{driver.version}</span>}
          {driver.author && <span>&middot; {driver.author}</span>}
          {installed?.filename && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
              &middot; {installed.filename}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: "var(--space-xs)", marginTop: "var(--space-sm)" }}>
          {driver.category && (
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                padding: "2px 8px",
                borderRadius: "var(--radius)",
                background: CATEGORY_COLORS[driver.category] || "#95a5a6",
                color: "#fff",
              }}
            >
              {driver.category}
            </span>
          )}
        </div>

        {/* Uninstall error / in-use warning live next to the action */}
        {uninstallError && (
          <div
            style={{
              marginTop: "var(--space-sm)",
              padding: "var(--space-sm) var(--space-md)",
              background: "var(--danger-dim, rgba(220,38,38,0.1))",
              border: "1px solid var(--danger)",
              borderRadius: "var(--radius)",
              color: "var(--danger)",
              fontSize: "var(--font-size-sm)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-start",
              gap: "var(--space-sm)",
            }}
          >
            <span style={{ whiteSpace: "pre-wrap" }}>{uninstallError}</span>
            <button
              onClick={onDismissUninstallError}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--danger)",
                cursor: "pointer",
                fontSize: "var(--font-size-sm)",
                padding: 0,
                lineHeight: 1,
              }}
              aria-label="Dismiss error"
            >
              ✕
            </button>
          </div>
        )}
        {canUninstall && inUse && !uninstallError && (
          <div
            style={{
              marginTop: "var(--space-sm)",
              padding: "var(--space-sm) var(--space-md)",
              background: "rgba(244,67,54,0.08)",
              borderRadius: "var(--radius)",
              fontSize: 12,
              color: "var(--text-secondary)",
            }}
          >
            <strong>In use:</strong> {devicesUsingDriver.length} device(s) reference this driver:
            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
              {devicesUsingDriver.slice(0, 5).map((name, i) => (
                <li key={i}>{name}</li>
              ))}
              {devicesUsingDriver.length > 5 && (
                <li>...and {devicesUsingDriver.length - 5} more</li>
              )}
            </ul>
            <div style={{ marginTop: 4 }}>
              Remove or reassign these devices before uninstalling.
            </div>
          </div>
        )}
      </div>

      {/* Overview */}
      {(help?.overview || driver.description) && (
        <Section title="Overview">
          <p style={{ margin: 0, lineHeight: 1.6 }}>
            {help?.overview || driver.description}
          </p>
        </Section>
      )}

      {/* Setup instructions */}
      {help?.setup && (
        <Section title="Setup Instructions">
          <pre
            style={{
              margin: 0,
              whiteSpace: "pre-wrap",
              fontFamily: "inherit",
              lineHeight: 1.6,
              color: "var(--text-muted)",
            }}
          >
            {help.setup}
          </pre>
        </Section>
      )}

      {/* Configuration */}
      {Object.keys(configSchema).length > 0 && (
        <Section title="Configuration">
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th style={{ textAlign: "left", padding: "4px 8px" }}>Property</th>
                <th style={{ textAlign: "left", padding: "4px 8px" }}>Type</th>
                <th style={{ textAlign: "left", padding: "4px 8px" }}>Default</th>
                <th style={{ textAlign: "left", padding: "4px 8px" }}>Required</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(configSchema).map(([key, schema]) => {
                const s = schema as Record<string, unknown>;
                return (
                  <tr key={key} style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <td style={{ padding: "4px 8px", fontWeight: 500 }}>
                      {(s.label as string) || key}
                    </td>
                    <td style={{ padding: "4px 8px", color: "var(--text-muted)" }}>
                      {(s.type as string) || "string"}
                    </td>
                    <td style={{ padding: "4px 8px", color: "var(--text-muted)" }}>
                      {s.default !== undefined ? String(s.default) : "\u2014"}
                    </td>
                    <td style={{ padding: "4px 8px", color: "var(--text-muted)" }}>
                      {s.required ? "Yes" : "No"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Section>
      )}

      {/* Commands */}
      {Object.keys(commands).length > 0 && (
        <Section title="Commands">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {Object.entries(commands).map(([key, cmd]) => {
              const c = cmd as Record<string, unknown>;
              return (
                <div key={key} style={{ fontSize: "var(--font-size-sm)" }}>
                  <span style={{ fontWeight: 500 }}>{(c.label as string) || key}</span>
                  {typeof c.help === "string" && (
                    <span style={{ color: "var(--text-muted)", marginLeft: "var(--space-sm)" }}>
                      {c.help}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* State Variables */}
      {Object.keys(stateVars).length > 0 && (
        <Section title="State Variables">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {Object.entries(stateVars).map(([key, sv]) => {
              const s = sv as Record<string, unknown>;
              return (
                <div key={key} style={{ fontSize: "var(--font-size-sm)" }}>
                  <span style={{ fontWeight: 500 }}>{(s.label as string) || key}</span>
                  <span style={{ color: "var(--text-muted)", marginLeft: "var(--space-sm)" }}>
                    ({(s.type as string) || "string"})
                  </span>
                </div>
              );
            })}
          </div>
        </Section>
      )}

    </div>
  );
}


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "var(--space-lg)" }}>
      <h3
        style={{
          margin: "0 0 var(--space-sm) 0",
          fontSize: "var(--font-size-sm)",
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: "var(--text-muted)",
        }}
      >
        {title}
      </h3>
      {children}
    </div>
  );
}
