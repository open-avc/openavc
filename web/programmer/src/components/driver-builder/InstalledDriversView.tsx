import { useState, useEffect } from "react";
import { Search, Trash2 } from "lucide-react";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import type { DriverInfo } from "../../api/types";

const GENERIC_IDS = new Set(["generic_tcp", "generic_serial", "generic_http"]);

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

export function InstalledDriversView() {
  const registeredDrivers = useDriverBuilderStore((s) => s.registeredDrivers);
  const installedDrivers = useDriverBuilderStore((s) => s.installedDrivers);
  const loadRegisteredDrivers = useDriverBuilderStore((s) => s.loadRegisteredDrivers);
  const loadInstalledDrivers = useDriverBuilderStore((s) => s.loadInstalledDrivers);
  const uninstallDriver = useDriverBuilderStore((s) => s.uninstallDriver);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [confirmUninstall, setConfirmUninstall] = useState<string | null>(null);
  const [uninstallError, setUninstallError] = useState<string | null>(null);

  useEffect(() => {
    loadRegisteredDrivers();
    loadInstalledDrivers();
  }, [loadRegisteredDrivers, loadInstalledDrivers]);

  const installedIdSet = new Set(installedDrivers.map((d) => d.id));

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

  const handleUninstall = async () => {
    if (!confirmUninstall) return;
    setUninstallError(null);
    try {
      await uninstallDriver(confirmUninstall);
      if (selectedId === confirmUninstall) setSelectedId(null);
      setConfirmUninstall(null);
    } catch (e) {
      setUninstallError(String(e));
      setConfirmUninstall(null);
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
            filteredDrivers.map((d) => (
              <div
                key={d.id}
                onClick={() => setSelectedId(d.id)}
                style={{
                  padding: "var(--space-sm) var(--space-md)",
                  cursor: "pointer",
                  borderBottom: "1px solid var(--border-color)",
                  background: selectedId === d.id ? "var(--accent-dim, rgba(59,130,246,0.1))" : "transparent",
                }}
              >
                <div
                  style={{
                    fontWeight: 500,
                    fontSize: "var(--font-size-sm)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {d.name}
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
            ))
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
        {uninstallError && (
          <div
            style={{
              padding: "var(--space-sm) var(--space-md)",
              marginBottom: "var(--space-md)",
              background: "var(--danger-dim, rgba(220,38,38,0.1))",
              border: "1px solid var(--danger)",
              borderRadius: "var(--radius)",
              color: "var(--danger)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {uninstallError}
          </div>
        )}

        {selectedDriver ? (
          <DriverDetailPanel
            driver={selectedDriver}
            canUninstall={canUninstall}
            onUninstall={() => setConfirmUninstall(selectedDriver.id)}
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

      {/* Uninstall confirmation */}
      {confirmUninstall && (
        <ConfirmDialog
          title="Uninstall Driver"
          message={`Are you sure you want to uninstall "${registeredDrivers.find((d) => d.id === confirmUninstall)?.name || confirmUninstall}"? You can reinstall it from the community repo later.`}
          confirmLabel="Uninstall"
          onConfirm={handleUninstall}
          onCancel={() => setConfirmUninstall(null)}
        />
      )}
    </div>
  );
}


function DriverDetailPanel({
  driver,
  canUninstall,
  onUninstall,
}: {
  driver: DriverInfo;
  canUninstall: boolean;
  onUninstall: () => void;
}) {
  const help = driver.help;
  const configSchema = driver.config_schema || {};
  const commands = driver.commands || {};
  const stateVars = driver.state_variables || {};

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "var(--space-lg)" }}>
        <h2 style={{ margin: 0, fontSize: "1.25rem" }}>{driver.name}</h2>
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

      {/* Uninstall */}
      {canUninstall && (
        <div style={{ marginTop: "var(--space-lg)", paddingTop: "var(--space-md)", borderTop: "1px solid var(--border-color)" }}>
          <button
            className="btn btn-sm btn-danger"
            onClick={onUninstall}
            style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}
          >
            <Trash2 size={14} /> Uninstall Driver
          </button>
          <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: 4 }}>
            Removes the driver file. You can reinstall from Browse Community.
          </div>
        </div>
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
