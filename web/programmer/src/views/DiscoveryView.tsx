import { useState, useEffect, useCallback, useMemo } from "react";
import {
  Radar,
  Play,
  Square,
  Trash2,
  Settings,
  ChevronDown,
  ChevronRight,
  Plus,
  Wifi,
  WifiOff,
  X,
  Download,
} from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { DeviceSettingsSetupDialog, hasDriverSetupSettings } from "../components/shared/DeviceSettingsSetupDialog";
import { useDiscoveryStore } from "../store/discoveryStore";
import { useProjectStore } from "../store/projectStore";
import { useNavigationStore } from "../store/navigationStore";
import * as api from "../api/restClient";
import type { DriverInfo } from "../api/types";
import { showError } from "../store/toastStore";


const PORT_LABELS: Record<number, string> = {
  23: "Telnet",
  80: "HTTP",
  443: "HTTPS",
  1515: "Samsung MDC",
  1688: "Crestron CIP",
  3088: "Crestron XIO",
  4352: "PJLink",
  5000: "Kramer/QSC",
  5900: "VNC",
  7142: "AMX ICSP",
  8080: "HTTP alt",
  9090: "HTTP alt",
  10500: "VISCA",
  41794: "Crestron CTP",
  49152: "Biamp",
  52000: "Q-SYS",
  61000: "Shure",
};

function confidenceStars(c: number): string {
  if (c >= 0.6) return "\u2605\u2605\u2605";
  if (c >= 0.3) return "\u2605\u2605\u2606";
  if (c >= 0.1) return "\u2605\u2606\u2606";
  return "\u2606\u2606\u2606";
}

function categoryLabel(cat: string | null): string {
  if (!cat) return "Unknown";
  return cat.charAt(0).toUpperCase() + cat.slice(1);
}

function confidenceBadge(confidence: number): { text: string; color: string } {
  if (confidence >= 0.28) return { text: "Protocol verified", color: "var(--success)" };
  if (confidence >= 0.18) return { text: "Strong match", color: "var(--accent)" };
  return { text: "Possible match", color: "var(--warning, #e6a700)" };
}

type SortKey = "confidence" | "ip" | "manufacturer" | "category";
type FilterCategory = "all" | "projector" | "display" | "audio" | "camera" | "switcher" | "control" | "other";

/** Standalone view with ViewContainer header. Used when Discovery has its own sidebar tab. */
export function DiscoveryView() {
  return (
    <ViewContainer title="Discovery">
      <DiscoveryPanel />
    </ViewContainer>
  );
}

/** Embeddable discovery panel without ViewContainer — used inside DeviceView sub-tabs. */
export function DiscoveryPanel() {
  const devices = useDiscoveryStore((s) => s.devices);
  const status = useDiscoveryStore((s) => s.status);
  const phase = useDiscoveryStore((s) => s.phase);
  const progress = useDiscoveryStore((s) => s.progress);
  const message = useDiscoveryStore((s) => s.message);
  const setStatus = useDiscoveryStore((s) => s.setStatus);
  const setDevices = useDiscoveryStore((s) => s.setDevices);
  const portLabels = useDiscoveryStore((s) => s.portLabels);
  const setPortLabels = useDiscoveryStore((s) => s.setPortLabels);
  const clear = useDiscoveryStore((s) => s.clear);

  // Merge hardcoded PORT_LABELS with dynamic community driver ports
  const allPortLabels = useMemo(() => {
    const merged: Record<number, string> = { ...PORT_LABELS };
    for (const [k, v] of Object.entries(portLabels)) {
      const port = Number(k);
      if (!merged[port]) merged[port] = v;
    }
    return merged;
  }, [portLabels]);

  const [sortBy, setSortBy] = useState<SortKey>("confidence");
  const [filterCat, setFilterCat] = useState<FilterCategory>("all");
  const [avOnly, setAvOnly] = useState(true);
  const [expandedIp, setExpandedIp] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [subnets, setSubnets] = useState<string[]>([]);
  const [extraSubnet, setExtraSubnet] = useState(
    () => localStorage.getItem("openavc_discovery_extra_subnet") || ""
  );

  // Settings state
  const [snmpEnabled, setSnmpEnabled] = useState(true);
  const [snmpCommunity, setSnmpCommunity] = useState("public");
  const [gentleMode, setGentleMode] = useState(false);

  // Load subnets + config on mount
  useEffect(() => {
    api.discoveryGetSubnets().then((r) => setSubnets(r.subnets)).catch(console.error);
    api.discoveryGetConfig().then((c) => {
      setSnmpEnabled(c.snmp_enabled);
      setSnmpCommunity(c.snmp_community);
      setGentleMode(c.gentle_mode);
    }).catch(console.error);
    // If there are existing results, load them
    api.discoveryGetResults().then((r) => {
      if (r.devices.length > 0) {
        setDevices(r.devices);
        if (r.status === "running") setStatus("running");
        else if (r.status === "complete") setStatus("complete");
      }
      if (r.port_labels) setPortLabels(r.port_labels);
    }).catch(console.error);
  }, [setDevices, setStatus, setPortLabels]);

  const handleStartScan = useCallback(async () => {
    try {
      await api.discoveryStartScan({
        extra_subnets: extraSubnet ? [extraSubnet] : undefined,
        snmp_enabled: snmpEnabled,
        snmp_community: snmpCommunity,
        gentle_mode: gentleMode,
      });
      // Only set running after API confirms the scan started
      setStatus("running");
    } catch (e) {
      setStatus("idle");
      showError(String(e));
    }
  }, [extraSubnet, snmpEnabled, snmpCommunity, gentleMode, setStatus]);

  const handleStopScan = useCallback(async () => {
    await api.discoveryStopScan();
    setStatus("cancelled");
  }, [setStatus]);

  const handleClear = useCallback(async () => {
    await api.discoveryClearResults();
    clear();
  }, [clear]);

  const handleSaveSettings = useCallback(async () => {
    await api.discoveryUpdateConfig({ snmp_enabled: snmpEnabled, snmp_community: snmpCommunity, gentle_mode: gentleMode });
    setShowSettings(false);
  }, [snmpEnabled, snmpCommunity, gentleMode]);

  const handleExport = useCallback(async () => {
    try {
      const text = await api.discoveryExport();
      const blob = new Blob([text], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "discovery-report.txt";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      showError(`Export failed: ${e}`);
    }
  }, []);

  const totalDeviceCount = Object.keys(devices).length;

  // Filter & sort devices
  const deviceList = useMemo(() => {
    let list = Object.values(devices);

    // AV-only filter
    if (avOnly) {
      list = list.filter((d) => {
        if (d.category === "network") return false;
        // Show if: has AV port, has AV manufacturer, or has matched driver
        return (
          d.open_ports.some((p) => p in allPortLabels && p !== 80 && p !== 443) ||
          d.manufacturer !== null ||
          d.matched_drivers.length > 0
        );
      });
    }

    // Category filter
    if (filterCat !== "all") {
      list = list.filter((d) => d.category === filterCat);
    }

    // Sort
    list.sort((a, b) => {
      switch (sortBy) {
        case "confidence":
          return b.confidence - a.confidence;
        case "ip":
          return a.ip.split(".").map(Number).reduce((s, n, i) => s + n * (256 ** (3 - i)), 0)
            - b.ip.split(".").map(Number).reduce((s, n, i) => s + n * (256 ** (3 - i)), 0);
        case "manufacturer":
          return (a.manufacturer ?? "zzz").localeCompare(b.manufacturer ?? "zzz");
        case "category":
          return (a.category ?? "zzz").localeCompare(b.category ?? "zzz");
        default:
          return 0;
      }
    });

    return list;
  }, [devices, sortBy, filterCat, avOnly]);

  const isRunning = status === "running";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "var(--space-md)" }}>
      {/* Action bar */}
      <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center", flexShrink: 0 }}>
        {isRunning ? (
          <button className="btn btn-sm btn-danger" onClick={handleStopScan}>
            <Square size={14} /> Stop
          </button>
        ) : (
          <button className="btn btn-sm btn-primary" onClick={handleStartScan}>
            <Play size={14} /> Scan
          </button>
        )}
        <button
          className="btn btn-sm"
          onClick={handleExport}
          disabled={isRunning || Object.keys(devices).length === 0}
          title="Export results as text"
        >
          <Download size={14} />
        </button>
        <button
          className="btn btn-sm"
          onClick={handleClear}
          disabled={isRunning}
          title="Clear all results"
        >
          <Trash2 size={14} />
        </button>
        <button
          className="btn btn-sm"
          onClick={() => setShowSettings(!showSettings)}
          title="Discovery settings"
        >
          <Settings size={14} />
        </button>
      </div>
      {/* Settings panel */}
      {showSettings && (
        <div
          style={{
            background: "var(--bg-surface)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--radius)",
            padding: "var(--space-md)",
            marginBottom: "var(--space-md)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-sm)" }}>
            <strong>Discovery Settings</strong>
            <button className="btn btn-sm" onClick={() => setShowSettings(false)}>
              <X size={14} />
            </button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-sm)" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              <input type="checkbox" checked={snmpEnabled} onChange={(e) => setSnmpEnabled(e.target.checked)} />
              SNMP Enabled
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              <input type="checkbox" checked={gentleMode} onChange={(e) => setGentleMode(e.target.checked)} />
              Gentle Scan (slower, less network noise)
            </label>
            <label>
              SNMP Community
              <input
                type="text"
                value={snmpCommunity}
                onChange={(e) => setSnmpCommunity(e.target.value)}
                style={{ marginLeft: "var(--space-xs)", width: 120 }}
              />
            </label>
            <label>
              Extra Subnet
              <input
                type="text"
                value={extraSubnet}
                onChange={(e) => {
                  setExtraSubnet(e.target.value);
                  localStorage.setItem("openavc_discovery_extra_subnet", e.target.value);
                }}
                placeholder="e.g. 10.1.2.0/24"
                style={{ marginLeft: "var(--space-xs)", width: 160 }}
              />
            </label>
          </div>

          {subnets.length > 0 && (
            <div style={{ marginTop: "var(--space-sm)", fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              Auto-detected subnets: {subnets.join(", ")}
            </div>
          )}

          <button className="btn btn-sm btn-primary" onClick={handleSaveSettings} style={{ marginTop: "var(--space-sm)" }}>
            Save Settings
          </button>
        </div>
      )}

      {/* Scan progress */}
      {isRunning && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--font-size-sm)", marginBottom: 4 }}>
            <span>{message || phase}</span>
            <span>{Object.keys(devices).length} found</span>
          </div>
          <div
            style={{
              height: 6,
              background: "var(--bg-input)",
              borderRadius: 3,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${Math.round(progress * 100)}%`,
                background: "var(--accent)",
                transition: "width 0.3s ease",
              }}
            />
          </div>
        </div>
      )}

      {/* Filters */}
      <div
        style={{
          display: "flex",
          gap: "var(--space-md)",
          alignItems: "center",
          marginBottom: "var(--space-md)",
          fontSize: "var(--font-size-sm)",
        }}
      >
        <label>
          Filter:{" "}
          <select value={filterCat} onChange={(e) => setFilterCat(e.target.value as FilterCategory)}>
            <option value="all">All</option>
            <option value="projector">Projectors</option>
            <option value="display">Displays</option>
            <option value="audio">Audio</option>
            <option value="camera">Cameras</option>
            <option value="switcher">Switchers</option>
            <option value="control">Control</option>
          </select>
        </label>
        <label>
          Sort:{" "}
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value as SortKey)}>
            <option value="confidence">Confidence</option>
            <option value="ip">IP Address</option>
            <option value="manufacturer">Manufacturer</option>
            <option value="category">Category</option>
          </select>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
          <input type="checkbox" checked={avOnly} onChange={(e) => setAvOnly(e.target.checked)} />
          AV only
        </label>
        {status !== "idle" && (
          <span style={{ color: "var(--text-muted)", marginLeft: "auto" }}>
            {deviceList.length === totalDeviceCount
              ? `${deviceList.length} device${deviceList.length !== 1 ? "s" : ""}`
              : <>
                  {deviceList.length} of {totalDeviceCount} devices{" "}
                  <span
                    style={{ cursor: "pointer", textDecoration: "underline" }}
                    onClick={() => { setAvOnly(false); setFilterCat("all"); }}
                    title="Show all devices"
                  >
                    ({totalDeviceCount - deviceList.length} filtered)
                  </span>
                </>
            }
          </span>
        )}
      </div>

      {/* Device list */}
      {status === "idle" && Object.keys(devices).length === 0 ? (
        <div
          style={{
            textAlign: "center",
            padding: "var(--space-xl)",
            color: "var(--text-muted)",
          }}
        >
          <Radar size={48} style={{ marginBottom: "var(--space-md)", opacity: 0.3 }} />
          <p>Click <strong>Scan</strong> to discover AV devices on your network.</p>
          <p style={{ fontSize: "var(--font-size-sm)" }}>
            OpenAVC will scan your local subnet for projectors, displays, audio DSPs, cameras, switchers, and more.
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          {deviceList.map((device) => (
            <DeviceCard
              key={device.ip}
              device={device}
              expanded={expandedIp === device.ip}
              onToggle={() => setExpandedIp(expandedIp === device.ip ? null : device.ip)}
              portLabels={allPortLabels}
            />
          ))}
          {deviceList.length === 0 && status !== "running" && (
            <div style={{ textAlign: "center", padding: "var(--space-lg)", color: "var(--text-muted)" }}>
              No devices match the current filters.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Device Card ---

function DeviceCard({
  device,
  expanded,
  onToggle,
  portLabels,
}: {
  device: api.DiscoveredDevice;
  expanded: boolean;
  onToggle: () => void;
  portLabels: Record<number, string>;
}) {
  const upsertDevice = useDiscoveryStore((s) => s.upsertDevice);
  const [addedViaInstall, setAddedViaInstall] = useState<{ name: string; deviceId?: string } | null>(null);

  const displayName =
    device.model
      ? (device.manufacturer && !device.model.toLowerCase().includes(device.manufacturer.toLowerCase())
          ? `${device.manufacturer} ${device.model}`
          : device.model)
      : device.device_name ??
        (device.manufacturer ? `${device.manufacturer} Device` : "Unknown Device");

  const protocolTag = device.protocols.length > 0
    ? device.protocols[0].replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
    : null;

  const installedMatches = device.matched_drivers.filter((m) => m.source === "installed");
  const communityMatches = device.matched_drivers.filter((m) => m.source === "community");
  const hasInstalledMatch = installedMatches.length > 0;

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--radius)",
        overflow: "hidden",
      }}
    >
      {/* Summary row */}
      <div
        onClick={onToggle}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "var(--space-sm) var(--space-md)",
          cursor: "pointer",
        }}
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}

        <span style={{ fontSize: "var(--font-size-sm)", minWidth: 32 }} title={`Confidence: ${Math.round(device.confidence * 100)}%`}>
          {confidenceStars(device.confidence)}
        </span>

        <span style={{ fontFamily: "monospace", minWidth: 120, fontSize: "var(--font-size-sm)" }}>
          {device.ip}
        </span>

        <span style={{ flex: 1, fontWeight: 500 }}>
          {displayName}
        </span>

        {device.manufacturer && (
          <span
            style={{
              fontSize: "var(--font-size-xs)",
              padding: "2px 6px",
              borderRadius: "var(--radius)",
              background: "var(--bg-input)",
            }}
          >
            {device.manufacturer}
          </span>
        )}

        {device.category && (
          <span
            style={{
              fontSize: "var(--font-size-xs)",
              padding: "2px 6px",
              borderRadius: "var(--radius)",
              background: "var(--bg-input)",
            }}
          >
            {categoryLabel(device.category)}
          </span>
        )}

        {protocolTag && (
          <span
            style={{
              fontSize: "var(--font-size-xs)",
              padding: "2px 6px",
              borderRadius: "var(--radius)",
              background: "var(--accent)",
              color: "var(--bg-main)",
            }}
          >
            {protocolTag}
          </span>
        )}

        {device.alive ? (
          <Wifi size={14} style={{ color: "var(--success)" }} />
        ) : (
          <WifiOff size={14} style={{ color: "var(--text-muted)" }} />
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          style={{
            borderTop: "1px solid var(--border-color)",
            padding: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "var(--space-xs) var(--space-lg)",
          }}
        >
          <DetailRow label="IP Address" value={device.ip} />
          <DetailRow label="MAC Address" value={device.mac ?? "Unknown"} />
          {device.hostname && <DetailRow label="Hostname" value={device.hostname} />}
          <DetailRow label="Manufacturer" value={device.manufacturer ?? "Unknown"} />
          <DetailRow label="Model" value={device.model ?? "Unknown"} />
          <DetailRow label="Device Name" value={device.device_name ?? "None"} />
          <DetailRow label="Firmware" value={device.firmware ?? "Unknown"} />
          {device.serial_number && <DetailRow label="Serial Number" value={device.serial_number} />}
          <DetailRow label="Category" value={categoryLabel(device.category)} />
          <DetailRow label="Confidence" value={`${Math.round(device.confidence * 100)}%`} />
          <DetailRow
            label="Protocols"
            value={device.protocols.length > 0 ? device.protocols.join(", ") : "None identified"}
          />

          <div style={{ gridColumn: "1 / -1" }}>
            <strong>Open Ports:</strong>{" "}
            {device.open_ports.length > 0
              ? device.open_ports.map((p) => `${p} (${portLabels[p] ?? "unknown"})`).join(", ")
              : "None detected"}
          </div>

          {Object.keys(device.banners).length > 0 && (
            <div style={{ gridColumn: "1 / -1" }}>
              <strong>Banners:</strong>
              {Object.entries(device.banners).map(([port, banner]) => (
                <div key={port} style={{ fontFamily: "monospace", marginTop: 2, fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                  Port {port}: {banner.substring(0, 200)}
                </div>
              ))}
            </div>
          )}

          {device.snmp_info && Object.keys(device.snmp_info).length > 0 && (
            <div style={{ gridColumn: "1 / -1" }}>
              <strong>SNMP Info:</strong>
              {Object.entries(device.snmp_info as Record<string, string>).map(([key, val]) => (
                <div key={key} style={{ marginTop: 2, fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                  {key}: {String(val).substring(0, 200)}
                </div>
              ))}
            </div>
          )}

          {device.mdns_services.length > 0 && (
            <div style={{ gridColumn: "1 / -1" }}>
              <strong>mDNS Services:</strong> {device.mdns_services.join(", ")}
            </div>
          )}

          <div style={{ gridColumn: "1 / -1" }}>
            <strong>Discovery Sources:</strong> {device.sources.join(", ") || "None"}
          </div>

          {/* Driver matches section */}
          {addedViaInstall ? (
            <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--border-color)", paddingTop: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
              <div style={{ color: "var(--success)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
                <span>Added "{addedViaInstall.name}" to project.</span>
                {addedViaInstall.deviceId && (
                  <button className="btn btn-sm btn-primary" onClick={() => {
                    useNavigationStore.getState().navigateTo("devices", { type: "device", id: addedViaInstall.deviceId! });
                  }}>
                    Go to Device &rarr;
                  </button>
                )}
              </div>
            </div>
          ) : hasInstalledMatch ? (
            <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--border-color)", paddingTop: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
              <strong>Installed Driver Match:</strong>
              {installedMatches.map((m) => (
                <div key={m.driver_id} style={{ marginTop: 4 }}>
                  <span style={{ fontWeight: 500 }}>{m.driver_name}</span>{" "}
                  <span style={{ color: "var(--text-muted)" }}>
                    ({Math.round(m.confidence * 100)}% — {m.match_reasons.join(", ")})
                  </span>
                </div>
              ))}
              <AddToProjectSection device={device} driverMatches={installedMatches} />
            </div>
          ) : communityMatches.length > 0 ? (
            <CommunityMatchSection
              device={device}
              matches={communityMatches}
              onDeviceUpdated={upsertDevice}
              onDeviceAdded={setAddedViaInstall}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}


// --- Add to Project (for installed drivers) ---

function AddToProjectSection({
  device,
  driverMatches,
}: {
  device: api.DiscoveredDevice;
  driverMatches: api.DiscoveryDriverMatch[];
}) {
  const project = useProjectStore((s) => s.project);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string | null>(null);
  const [addedDeviceId, setAddedDeviceId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedDriver, setSelectedDriver] = useState(driverMatches[0]?.driver_id ?? "");
  const [driverInfoForSetup, setDriverInfoForSetup] = useState<DriverInfo | null>(null);
  // Reset selection when matches change (e.g., after installing a new driver)
  useEffect(() => {
    if (driverMatches.length > 0 && !driverMatches.some((m) => m.driver_id === selectedDriver)) {
      setSelectedDriver(driverMatches[0].driver_id);
    }
  }, [driverMatches, selectedDriver]);
  const [setupText, setSetupText] = useState<string | null>(null);
  const [showSetup, setShowSetup] = useState(false);

  const handleAdd = async () => {
    // Try to fetch setup instructions before adding
    if (!showSetup && !added) {
      try {
        const help = await api.getDriverHelp(selectedDriver);
        if (help.setup) {
          setSetupText(help.setup);
          setShowSetup(true);
          return; // Show setup first, user clicks again to confirm
        }
      } catch {
        // No help available — proceed directly
      }
    }

    setAdding(true);
    setError(null);
    try {
      const result = await api.discoveryAddDevice({
        ip: device.ip,
        driver_id: selectedDriver,
      });
      setAdded(result.name);
      setAddedDeviceId(result.device_id);

      // Check if this driver has setup settings
      try {
        const drivers = await api.listDrivers();
        const di = drivers.find((d) => d.id === selectedDriver);
        if (di && hasDriverSetupSettings(di)) {
          setDriverInfoForSetup(di);
        }
      } catch {
        // Couldn't fetch driver info — skip setup
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setAdding(false);
    }
  };

  if (added) {
    return (
      <>
        <div style={{ marginTop: "var(--space-sm)", color: "var(--success)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
          <span>Added "{added}" to project.</span>
          {addedDeviceId && (
            <button className="btn btn-sm btn-primary" onClick={() => {
              useNavigationStore.getState().navigateTo("devices", { type: "device", id: addedDeviceId });
            }}>
              Go to Device &rarr;
            </button>
          )}
        </div>
        {driverInfoForSetup && addedDeviceId && (
          <DeviceSettingsSetupDialog
            deviceId={addedDeviceId}
            driverInfo={driverInfoForSetup}
            existingDeviceIds={(project?.devices ?? []).map((d) => d.id)}
            onClose={() => setDriverInfoForSetup(null)}
          />
        )}
      </>
    );
  }

  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
        {driverMatches.length > 1 && (
          <select
            value={selectedDriver}
            onChange={(e) => {
              setSelectedDriver(e.target.value);
              setShowSetup(false);
              setSetupText(null);
            }}
          >
            {driverMatches.map((m) => (
              <option key={m.driver_id} value={m.driver_id}>
                {m.driver_name} ({Math.round(m.confidence * 100)}%)
              </option>
            ))}
          </select>
        )}
        <button className="btn btn-sm btn-primary" onClick={handleAdd} disabled={adding}>
          <Plus size={14} /> {adding ? "Adding..." : showSetup ? "Confirm & Add" : "Add to Project"}
        </button>
        {error && <span style={{ color: "var(--danger)", fontSize: "var(--font-size-xs)" }}>{error}</span>}
      </div>

      {showSetup && setupText && (
        <div
          style={{
            marginTop: "var(--space-sm)",
            padding: "var(--space-sm)",
            background: "var(--bg-input)",
            borderRadius: "var(--radius)",
            fontSize: "var(--font-size-xs)",
            whiteSpace: "pre-line",
          }}
        >
          <strong>Setup Instructions:</strong>
          <div style={{ marginTop: 4, color: "var(--text-muted)" }}>{setupText}</div>
        </div>
      )}

      {!showSetup && (
        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: 4 }}>
          Creates a device in your project using the selected driver and connects immediately.
        </div>
      )}
    </div>
  );
}


// --- Community Driver Match Section ---

function CommunityMatchSection({
  device,
  matches,
  onDeviceUpdated,
  onDeviceAdded,
}: {
  device: api.DiscoveredDevice;
  matches: api.DiscoveryDriverMatch[];
  onDeviceUpdated: (device: api.DiscoveredDevice) => void;
  onDeviceAdded: (info: { name: string; deviceId?: string }) => void;
}) {
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(false);

  const topMatch = matches[0];
  const otherMatches = matches.slice(1);
  const badge = confidenceBadge(topMatch.confidence);

  const COMMUNITY_BASE = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/";

  const handleInstallAndAdd = async (match: api.DiscoveryDriverMatch) => {
    setInstalling(true);
    setError(null);

    try {
      const resp = await api.fetchCommunityDrivers();
      const communityDriver = resp.find((d) => d.id === match.driver_id);
      if (!communityDriver) {
        setError("Driver not found in community index");
        setInstalling(false);
        return;
      }

      const result = await api.discoveryInstallAndMatch({
        ip: device.ip,
        driver_id: match.driver_id,
        file_url: `${COMMUNITY_BASE}${communityDriver.file}`,
      });

      if (result.device) {
        onDeviceUpdated(result.device);
      }

      if (result.status === "ok") {
        onDeviceAdded({ name: result.name || match.driver_name, deviceId: result.device_id });
      } else if (result.status === "installed_not_added") {
        setError(`Driver installed but could not add device: ${result.error}`);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--border-color)", paddingTop: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
      <strong>Community Driver Available:</strong>

      {/* Top match — expanded */}
      <div
        style={{
          marginTop: "var(--space-sm)",
          padding: "var(--space-sm)",
          background: "var(--bg-input)",
          borderRadius: "var(--radius)",
          fontSize: "var(--font-size-sm)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <span style={{ fontWeight: 500 }}>{topMatch.driver_name}</span>
          <span
            style={{
              fontSize: "var(--font-size-xs)",
              padding: "1px 6px",
              borderRadius: "var(--radius)",
              background: badge.color,
              color: "var(--bg-main)",
            }}
          >
            {badge.text}
          </span>
          <button
            className="btn btn-sm btn-primary"
            onClick={() => handleInstallAndAdd(topMatch)}
            disabled={installing}
            style={{ marginLeft: "auto" }}
          >
            <Plus size={14} /> {installing ? "Installing..." : "Install & Add to Project"}
          </button>
        </div>

        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: 4 }}>
          {topMatch.match_reasons.join(" + ")}
          {topMatch.description && ` \u2014 ${topMatch.description.substring(0, 120)}`}
        </div>

        {badge.text !== "Protocol verified" && (
          <div style={{ fontSize: "var(--font-size-xs)", color: badge.color, marginTop: 4, fontStyle: "italic" }}>
            This driver may work with your device. Install and verify with your equipment.
          </div>
        )}

        {error && (
          <div style={{ fontSize: "var(--font-size-xs)", color: "var(--danger)", marginTop: 4 }}>
            Install failed: {error}
          </div>
        )}
      </div>

      {/* Other matches — collapsed */}
      {otherMatches.length > 0 && (
        <div style={{ marginTop: "var(--space-xs)" }}>
          <button
            className="btn btn-sm"
            onClick={() => setShowMore(!showMore)}
            style={{ fontSize: "var(--font-size-xs)", padding: "2px 8px" }}
          >
            {showMore ? "Hide" : `${otherMatches.length} other driver${otherMatches.length > 1 ? "s" : ""} may also work`}
          </button>

          {showMore && otherMatches.map((m) => {
            const b = confidenceBadge(m.confidence);
            return (
              <div
                key={m.driver_id}
                style={{
                  marginTop: 4,
                  padding: "var(--space-xs) var(--space-sm)",
                  background: "var(--bg-input)",
                  borderRadius: "var(--radius)",
                  fontSize: "var(--font-size-xs)",
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-sm)",
                }}
              >
                <span style={{ fontWeight: 500 }}>{m.driver_name}</span>
                <span
                  style={{
                    padding: "1px 4px",
                    borderRadius: "var(--radius)",
                    background: b.color,
                    color: "var(--bg-main)",
                  }}
                >
                  {b.text}
                </span>
                <button
                  className="btn btn-sm"
                  onClick={() => handleInstallAndAdd(m)}
                  disabled={installing}
                  style={{ marginLeft: "auto", fontSize: "var(--font-size-xs)", padding: "2px 8px" }}
                >
                  Install & Add
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span style={{ color: "var(--text-muted)" }}>{label}:</span>{" "}
      <span>{value}</span>
    </div>
  );
}
