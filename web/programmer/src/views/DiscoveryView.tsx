import { useState, useEffect, useCallback, useMemo, useRef } from "react";
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
  EyeOff,
  Eye,
  HelpCircle,
} from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { CopyButton } from "../components/shared/CopyButton";
import { DeviceSettingsSetupDialog, hasDriverSetupSettings } from "../components/shared/DeviceSettingsSetupDialog";
import { useDiscoveryStore } from "../store/discoveryStore";
import { useProjectStore } from "../store/projectStore";
import { useNavigationStore } from "../store/navigationStore";
import * as api from "../api/restClient";
import type { DriverInfo, CommunityDriver } from "../api/types";
import type { DeviceState, DiscoveryEvidence } from "../api/discoveryClient";
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

const HIDDEN_KEY = "openavc_discovery_hidden_ips";

function loadHiddenIps(): Set<string> {
  try {
    const raw = localStorage.getItem(HIDDEN_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

function saveHiddenIps(ips: Set<string>): void {
  localStorage.setItem(HIDDEN_KEY, JSON.stringify([...ips]));
}

function categoryLabel(cat: string | null): string {
  if (!cat) return "Unknown";
  return cat.charAt(0).toUpperCase() + cat.slice(1);
}

/**
 * Whether a device's evidence_log carries any AV-specific signal. Used by the
 * "AV only" toggle to discriminate between unknown devices that look AV-like
 * (an open AV port, a curated OUI hit, an SNMP PEN match, an mDNS/SSDP
 * announcement, etc.) and unknown devices that are just LAN noise.
 *
 * The discovery engine only emits open-port evidence when the port matches
 * at least one driver's `open_ports:` hint, so any `open_port:*` record
 * counts. OUI evidence is emitted for every MAC, so we additionally require
 * `data.vendor` to be populated (i.e. the OUI was in the curated AV DB).
 * Hostname evidence is emitted for every alive host and is not on its own
 * AV-specific.
 */
function hasAvSignal(device: api.DiscoveredDevice): boolean {
  for (const ev of device.evidence_log) {
    const scheme = ev.source.split(":")[0];
    switch (scheme) {
      case "open_port":
      case "snmp_pen":
      case "snmp":
      case "mdns":
      case "ssdp":
      case "amx_ddp":
      case "broadcast":
      case "probe":
        return true;
      case "oui":
        if (ev.data && ev.data.vendor) return true;
        break;
    }
  }
  return false;
}

function stateTone(state: DeviceState): { bg: string; fg: string; label: string } {
  switch (state) {
    case "identified":
      return { bg: "rgba(16,185,129,0.15)", fg: "#10b981", label: "Identified" };
    case "possible":
      return { bg: "rgba(245,158,11,0.15)", fg: "#f59e0b", label: "Possible" };
    default:
      return { bg: "rgba(107,114,128,0.18)", fg: "#9ca3af", label: "Unknown" };
  }
}

/** Plain-English one-liner describing the deterministic signal that produced a match. */
function summarizeSource(source: string): string {
  if (!source) return "no signal";
  const [scheme, ...rest] = source.split(":");
  const tail = rest.join(":");
  switch (scheme) {
    case "mdns":
      return `mDNS announcement (${tail})`;
    case "ssdp":
      return `SSDP/UPnP announcement${tail ? ` (${tail})` : ""}`;
    case "amx_ddp":
      return `AMX DDP beacon${tail ? ` (${tail})` : ""}`;
    case "broadcast":
      return `${tail.replace(/_/g, " ")} broadcast probe`;
    case "probe":
      return `${tail.replace(/_/g, " ")} TCP probe`;
    case "snmp":
      return `SNMP ${tail}`;
    case "oui":
      return `MAC OUI ${tail}`;
    case "hostname":
      return `hostname pattern (${tail})`;
    case "soft":
      return "ambiguous soft signal";
    default:
      return source;
  }
}

type SortKey = "state" | "ip" | "manufacturer" | "category";
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
  const upsertDevice = useDiscoveryStore((s) => s.upsertDevice);

  // Merge hardcoded PORT_LABELS with dynamic community driver ports
  const allPortLabels = useMemo(() => {
    const merged: Record<number, string> = { ...PORT_LABELS };
    for (const [k, v] of Object.entries(portLabels)) {
      const port = Number(k);
      if (!merged[port]) merged[port] = v;
    }
    return merged;
  }, [portLabels]);

  const [sortBy, setSortBy] = useState<SortKey>("state");
  const [filterCat, setFilterCat] = useState<FilterCategory>("all");
  const [avOnly, setAvOnly] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [hiddenIps, setHiddenIps] = useState<Set<string>>(() => loadHiddenIps());
  const [expandedIp, setExpandedIp] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [subnets, setSubnets] = useState<string[]>([]);
  const [extraSubnet, setExtraSubnet] = useState(
    () => localStorage.getItem("openavc_discovery_extra_subnet") || ""
  );

  // Driver catalogs (resolved once, used to label candidates and to route Add → install vs add)
  const [installedDrivers, setInstalledDrivers] = useState<DriverInfo[]>([]);
  const [communityDrivers, setCommunityDrivers] = useState<CommunityDriver[]>([]);

  // Settings state
  const [snmpEnabled, setSnmpEnabled] = useState(true);
  const [snmpCommunity, setSnmpCommunity] = useState("public");
  const [gentleMode, setGentleMode] = useState(false);
  const [scanDepth, setScanDepth] = useState<api.ScanDepth>("standard");
  const [maxSubnetSize, setMaxSubnetSize] = useState(20);

  // Active control interface
  const [controlInterface, setControlInterface] = useState("");
  const [adapterLabel, setAdapterLabel] = useState("");

  // Load subnets + config + driver catalogs on mount
  useEffect(() => {
    api.discoveryGetSubnets().then((r) => setSubnets(r.subnets)).catch(console.error);
    api.discoveryGetConfig().then((c) => {
      setSnmpEnabled(c.snmp_enabled);
      setSnmpCommunity(c.snmp_community);
      setGentleMode(c.gentle_mode);
      if (c.scan_depth) setScanDepth(c.scan_depth);
      if (c.max_subnet_size) setMaxSubnetSize(c.max_subnet_size);
    }).catch(console.error);
    api.discoveryGetResults().then((r) => {
      if (r.devices.length > 0) {
        setDevices(r.devices);
        if (r.status === "running") setStatus("running");
        else if (r.status === "complete") setStatus("complete");
      }
      if (r.port_labels) setPortLabels(r.port_labels);
    }).catch(console.error);
    api.getSystemConfig().then((cfg) => {
      const ip = cfg.network?.control_interface || "";
      setControlInterface(ip);
      if (ip) {
        api.getNetworkAdapters().then((r) => {
          const match = r.adapters.find((a) => a.ip === ip);
          if (match) setAdapterLabel(`${match.name} (${match.ip}/${match.subnet.split("/")[1] || "24"})`);
          else setAdapterLabel(ip);
        }).catch(() => setAdapterLabel(ip));
      }
    }).catch(() => {});
    api.listDrivers().then(setInstalledDrivers).catch(console.error);
    api.fetchCommunityDrivers().then(setCommunityDrivers).catch(console.error);
  }, [setDevices, setStatus, setPortLabels]);

  const driverNameLookup = useMemo(() => {
    const map = new Map<string, { name: string; source: "installed" | "community"; community?: CommunityDriver }>();
    for (const d of installedDrivers) {
      map.set(d.id, { name: d.name, source: "installed" });
    }
    for (const c of communityDrivers) {
      if (!map.has(c.id)) {
        map.set(c.id, { name: c.name, source: "community", community: c });
      }
    }
    return map;
  }, [installedDrivers, communityDrivers]);

  const handleStartScan = useCallback(async () => {
    try {
      await api.discoveryStartScan({
        extra_subnets: extraSubnet ? [extraSubnet] : undefined,
        snmp_enabled: snmpEnabled,
        snmp_community: snmpCommunity,
        gentle_mode: gentleMode,
        scan_depth: scanDepth,
        max_subnet_size: maxSubnetSize,
      });
      setStatus("running");
    } catch (e) {
      setStatus("idle");
      showError(String(e));
    }
  }, [extraSubnet, snmpEnabled, snmpCommunity, gentleMode, scanDepth, maxSubnetSize, setStatus]);

  const handleStopScan = useCallback(async () => {
    await api.discoveryStopScan();
    setStatus("cancelled");
  }, [setStatus]);

  const handleClear = useCallback(async () => {
    await api.discoveryClearResults();
    clear();
  }, [clear]);

  const handleSaveSettings = useCallback(async () => {
    await api.discoveryUpdateConfig({ snmp_enabled: snmpEnabled, snmp_community: snmpCommunity, gentle_mode: gentleMode, scan_depth: scanDepth, max_subnet_size: maxSubnetSize });
    setShowSettings(false);
  }, [snmpEnabled, snmpCommunity, gentleMode, scanDepth, maxSubnetSize]);

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

  const handleHide = useCallback((ip: string) => {
    setHiddenIps((prev) => {
      const next = new Set(prev);
      next.add(ip);
      saveHiddenIps(next);
      return next;
    });
  }, []);

  const handleUnhide = useCallback((ip: string) => {
    setHiddenIps((prev) => {
      const next = new Set(prev);
      next.delete(ip);
      saveHiddenIps(next);
      return next;
    });
  }, []);

  const totalDeviceCount = Object.keys(devices).length;
  const hiddenCount = useMemo(
    () => Object.keys(devices).filter((ip) => hiddenIps.has(ip)).length,
    [devices, hiddenIps],
  );

  const deviceList = useMemo(() => {
    let list = Object.values(devices);

    // Hidden filter (toggle to show)
    if (!showHidden) {
      list = list.filter((d) => !hiddenIps.has(d.ip));
    }

    // "AV only" filter — hides unknowns that carry no AV-specific signal.
    // Identified and possible devices always pass; unknowns survive if their
    // evidence still suggests an AV device (open AV port, curated OUI, SNMP
    // PEN, mDNS/SSDP/active probe response).
    if (avOnly) {
      list = list.filter((d) => {
        const state = d.identification?.state ?? "unknown";
        if (state !== "unknown") return true;
        return hasAvSignal(d);
      });
    }

    if (filterCat !== "all") {
      list = list.filter((d) => d.category === filterCat);
    }

    const stateRank = (s: string | undefined) =>
      s === "identified" ? 0 : s === "possible" ? 1 : 2;
    const nameOf = (d: api.DiscoveredDevice) =>
      (d.model || d.device_name || d.manufacturer || d.ip).toLowerCase();

    list.sort((a, b) => {
      switch (sortBy) {
        case "state": {
          const sa = stateRank(a.identification?.state);
          const sb = stateRank(b.identification?.state);
          if (sa !== sb) return sa - sb;
          return nameOf(a).localeCompare(nameOf(b));
        }
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
  }, [devices, sortBy, filterCat, avOnly, showHidden, hiddenIps]);

  const isRunning = status === "running";
  const [scanCompletedAt, setScanCompletedAt] = useState<Date | null>(null);
  const prevStatusRef = useRef(status);
  useEffect(() => {
    if (prevStatusRef.current === "running" && status === "complete") {
      setScanCompletedAt(new Date());
    }
    prevStatusRef.current = status;
  }, [status]);

  // Smooth progress bar interpolation
  const [displayProgress, setDisplayProgress] = useState(0);
  useEffect(() => {
    if (!isRunning) {
      setDisplayProgress(status === "complete" ? 1 : 0);
      return;
    }
    const interval = setInterval(() => {
      setDisplayProgress((prev) => {
        const diff = progress - prev;
        if (Math.abs(diff) < 0.002) return progress;
        return prev + diff * 0.18;
      });
    }, 50);
    return () => clearInterval(interval);
  }, [progress, isRunning, status]);

  const phaseLabel = phase === "ping_sweep" ? "Scanning network..."
    : phase === "port_scan" ? "Probing ports..."
    : phase === "protocol_probe" ? "Identifying devices..."
    : phase === "passive_collect" ? (message || "Collecting passive results...")
    : phase === "finalize" ? "Matching drivers..."
    : phase === "driver_match" ? "Matching drivers..."
    : phase === "snmp_scan" ? "Querying SNMP..."
    : phase === "mdns_scan" ? "Listening for mDNS..."
    : phase === "ssdp_scan" ? "Listening for SSDP..."
    : message || phase || "Scanning...";

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
            <label style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              Scan Depth
              <select
                value={scanDepth}
                onChange={(e) => setScanDepth(e.target.value as api.ScanDepth)}
                style={{ flex: 1, maxWidth: 220 }}
              >
                <option value="quick">Quick (fast re-scan)</option>
                <option value="standard">Standard (recommended)</option>
                <option value="thorough">Thorough (extended scan)</option>
              </select>
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                {scanDepth === "quick" && "Basic port scan and protocol probes."}
                {scanDepth === "standard" && "Full scan with passive listeners and broadcast probes."}
                {scanDepth === "thorough" && "Extended ports, longer passive listen. Takes longer."}
              </span>
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              <input type="checkbox" checked={snmpEnabled} onChange={(e) => setSnmpEnabled(e.target.checked)} />
              SNMP Enabled
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              <input type="checkbox" checked={gentleMode} onChange={(e) => setGentleMode(e.target.checked)} />
              Reduce network load (slower scan, less traffic)
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
            <label>
              Max subnet size
              <select
                value={maxSubnetSize}
                onChange={(e) => setMaxSubnetSize(Number(e.target.value))}
                style={{ marginLeft: "var(--space-xs)", width: 160 }}
              >
                <option value={24}>/24 (254 hosts)</option>
                <option value={22}>/22 (~1K hosts)</option>
                <option value={20}>/20 (~4K hosts)</option>
                <option value={18}>/18 (~16K hosts)</option>
                <option value={16}>/16 (~65K hosts)</option>
              </select>
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

      {/* Active adapter indicator */}
      {!showSettings && (
        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
          <Wifi size={12} />
          <span>Scanning on: {controlInterface ? adapterLabel || controlInterface : "Auto (default route)"}</span>
          <span style={{ color: "var(--text-muted)" }}>&middot;</span>
          <button
            type="button"
            onClick={() => useNavigationStore.getState().navigateTo("settings")}
            style={{
              background: "none",
              border: "none",
              color: "var(--accent)",
              cursor: "pointer",
              padding: 0,
              fontSize: "inherit",
              textDecoration: "underline",
            }}
          >
            Change in Settings
          </button>
        </div>
      )}

      {/* Scan progress */}
      {isRunning && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--font-size-sm)", marginBottom: 4 }}>
            <span style={{ fontWeight: 500 }}>{phaseLabel}</span>
            <span>{Object.keys(devices).length} found &middot; {Math.round(displayProgress * 100)}%</span>
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
                width: `${Math.round(displayProgress * 100)}%`,
                background: "var(--accent-bg)",
                transition: "width 0.8s ease-out",
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
          flexWrap: "wrap",
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
            <option value="state">State (identified first)</option>
            <option value="ip">IP Address</option>
            <option value="manufacturer">Manufacturer</option>
            <option value="category">Category</option>
          </select>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }} title="Hide unknown devices unless they show an AV-specific signal (open AV port, curated OUI, SNMP PEN, or mDNS/SSDP announcement)">
          <input type="checkbox" checked={avOnly} onChange={(e) => setAvOnly(e.target.checked)} />
          AV only
        </label>
        {hiddenCount > 0 && (
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
            Show hidden ({hiddenCount})
          </label>
        )}
        {status !== "idle" && (
          <span style={{ color: "var(--text-muted)", marginLeft: "auto" }}>
            {deviceList.length === totalDeviceCount
              ? `${deviceList.length} device${deviceList.length !== 1 ? "s" : ""}`
              : <>
                  {deviceList.length} of {totalDeviceCount} devices{" "}
                  <span
                    style={{ cursor: "pointer", textDecoration: "underline" }}
                    onClick={() => { setAvOnly(false); setFilterCat("all"); setShowHidden(true); }}
                    title="Show all devices"
                  >
                    ({totalDeviceCount - deviceList.length} filtered)
                  </span>
                </>
            }
          </span>
        )}
      </div>

      {/* Results timestamp */}
      {!isRunning && scanCompletedAt && Object.keys(devices).length > 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-xs)" }}>
          Results from {scanCompletedAt.toLocaleTimeString()} ({Object.keys(devices).length} device{Object.keys(devices).length !== 1 ? "s" : ""})
        </div>
      )}

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
              driverNameLookup={driverNameLookup}
              installedDrivers={installedDrivers}
              hidden={hiddenIps.has(device.ip)}
              onHide={() => handleHide(device.ip)}
              onUnhide={() => handleUnhide(device.ip)}
              onDeviceUpdated={upsertDevice}
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

type DriverEntry = { name: string; source: "installed" | "community"; community?: CommunityDriver };

function DeviceCard({
  device,
  expanded,
  onToggle,
  portLabels,
  driverNameLookup,
  installedDrivers,
  hidden,
  onHide,
  onUnhide,
  onDeviceUpdated,
}: {
  device: api.DiscoveredDevice;
  expanded: boolean;
  onToggle: () => void;
  portLabels: Record<number, string>;
  driverNameLookup: Map<string, DriverEntry>;
  installedDrivers: DriverInfo[];
  hidden: boolean;
  onHide: () => void;
  onUnhide: () => void;
  onDeviceUpdated: (device: api.DiscoveredDevice) => void;
}) {
  const [showWhy, setShowWhy] = useState(false);
  const [addedDevice, setAddedDevice] = useState<{ name: string; deviceId?: string } | null>(null);

  const ident = device.identification;
  const state: DeviceState = ident?.state ?? "unknown";
  const tone = stateTone(state);

  const displayName = (() => {
    if (state === "identified" && ident?.driver_id) {
      const entry = driverNameLookup.get(ident.driver_id);
      if (entry) return entry.name;
    }
    return device.model
      ? (device.manufacturer && !device.model.toLowerCase().includes(device.manufacturer.toLowerCase())
          ? `${device.manufacturer} ${device.model}`
          : device.model)
      : device.device_name ??
        (device.manufacturer ? `${device.manufacturer} Device` : "Unknown Device");
  })();

  const protocolTag = device.protocols.length > 0
    ? device.protocols[0].replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
    : null;

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--radius)",
        overflow: "hidden",
        opacity: hidden ? 0.5 : 1,
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

        <span style={{
          fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 3,
          background: tone.bg, color: tone.fg, minWidth: 76, textAlign: "center",
          letterSpacing: 0.3,
        }}>
          {tone.label.toUpperCase()}
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
              background: "var(--accent-bg)",
              color: "var(--bg-main)",
            }}
          >
            {protocolTag}
          </span>
        )}

        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); hidden ? onUnhide() : onHide(); }}
          title={hidden ? "Unhide this device" : "Hide this device from results"}
          aria-label={hidden ? "Unhide device" : "Hide device"}
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--text-muted)", padding: 4, display: "inline-flex",
          }}
        >
          {hidden ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>

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
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-md)",
          }}
        >
          {/* Identification block (state-specific) */}
          {addedDevice ? (
            <div style={{ color: "var(--success)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
              <span>Added "{addedDevice.name}" to project.</span>
              {addedDevice.deviceId && (
                <button className="btn btn-sm btn-primary" onClick={() => {
                  useNavigationStore.getState().navigateTo("devices", { type: "device", id: addedDevice.deviceId! });
                }}>
                  Go to Device &rarr;
                </button>
              )}
            </div>
          ) : (
            <IdentificationSection
              device={device}
              installedDrivers={installedDrivers}
              driverNameLookup={driverNameLookup}
              onDeviceAdded={setAddedDevice}
              onDeviceUpdated={onDeviceUpdated}
            />
          )}

          {/* "Why?" reveal */}
          <div>
            <button
              type="button"
              onClick={() => setShowWhy(!showWhy)}
              style={{
                background: "none", border: "none", cursor: "pointer",
                color: "var(--text-muted)", padding: 0, display: "inline-flex",
                alignItems: "center", gap: 4, fontSize: "var(--font-size-xs)",
              }}
            >
              <HelpCircle size={12} /> {showWhy ? "Hide evidence" : "Why this match?"}
            </button>
            {showWhy && (
              <EvidenceList evidence={device.evidence_log} />
            )}
          </div>

          {/* Detail rows */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-xs) var(--space-lg)" }}>
            <DetailRow label="IP Address" value={device.ip} copyable />
            <DetailRow label="MAC Address" value={device.mac ?? "Unknown"} copyable={!!device.mac} />
            {device.hostname && <DetailRow label="Hostname" value={device.hostname} />}
            <DetailRow label="Manufacturer" value={device.manufacturer ?? "Unknown"} />
            <DetailRow label="Model" value={device.model ?? "Unknown"} />
            {device.device_name && <DetailRow label="Device Name" value={device.device_name} />}
            {device.firmware && <DetailRow label="Firmware" value={device.firmware} />}
            {device.serial_number && <DetailRow label="Serial Number" value={device.serial_number} />}
            <DetailRow label="Category" value={categoryLabel(device.category)} />
            <DetailRow
              label="Protocols"
              value={device.protocols.length > 0 ? device.protocols.join(", ") : "None identified"}
            />
          </div>

          <div>
            <strong>Open Ports:</strong>{" "}
            {device.open_ports.length > 0
              ? device.open_ports.map((p) => `${p} (${portLabels[p] ?? "unknown"})`).join(", ")
              : "None detected"}
          </div>

          {Object.keys(device.banners).length > 0 && (
            <div>
              <strong>Banners:</strong>
              {Object.entries(device.banners).map(([port, banner]) => (
                <div key={port} style={{ fontFamily: "monospace", marginTop: 2, fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                  Port {port}: {banner.substring(0, 200)}
                </div>
              ))}
            </div>
          )}

          {device.snmp_info && Object.keys(device.snmp_info).length > 0 && (
            <div>
              <strong>SNMP Info:</strong>
              {Object.entries(device.snmp_info as Record<string, unknown>).map(([key, val]) => (
                <div key={key} style={{ marginTop: 2, fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                  {key}: {String(val).substring(0, 200)}
                </div>
              ))}
            </div>
          )}

          {device.mdns_services.length > 0 && (
            <div>
              <strong>mDNS Services:</strong> {device.mdns_services.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// --- Identification section (state-specific add affordance) ---

function IdentificationSection({
  device,
  installedDrivers,
  driverNameLookup,
  onDeviceAdded,
  onDeviceUpdated,
}: {
  device: api.DiscoveredDevice;
  installedDrivers: DriverInfo[];
  driverNameLookup: Map<string, DriverEntry>;
  onDeviceAdded: (info: { name: string; deviceId?: string }) => void;
  onDeviceUpdated: (device: api.DiscoveredDevice) => void;
}) {
  const ident = device.identification;
  const state: DeviceState = ident?.state ?? "unknown";

  if (state === "identified" && ident?.driver_id) {
    return (
      <DriverAddRow
        device={device}
        driverId={ident.driver_id}
        installedDrivers={installedDrivers}
        driverNameLookup={driverNameLookup}
        sourceLabel={summarizeSource(ident.source)}
        onDeviceAdded={onDeviceAdded}
        onDeviceUpdated={onDeviceUpdated}
      />
    );
  }

  if (state === "possible" && ident?.candidates.length) {
    return (
      <PossibleCandidates
        device={device}
        candidates={ident.candidates}
        sourceLabel={summarizeSource(ident.source)}
        installedDrivers={installedDrivers}
        driverNameLookup={driverNameLookup}
        onDeviceAdded={onDeviceAdded}
        onDeviceUpdated={onDeviceUpdated}
      />
    );
  }

  return (
    <ManualDriverPicker
      device={device}
      installedDrivers={installedDrivers}
      driverNameLookup={driverNameLookup}
      onDeviceAdded={onDeviceAdded}
      onDeviceUpdated={onDeviceUpdated}
    />
  );
}


// --- Single-driver add (identified, or one-click possible candidate) ---

function DriverAddRow({
  device,
  driverId,
  installedDrivers,
  driverNameLookup,
  sourceLabel,
  onDeviceAdded,
  onDeviceUpdated,
}: {
  device: api.DiscoveredDevice;
  driverId: string;
  installedDrivers: DriverInfo[];
  driverNameLookup: Map<string, DriverEntry>;
  sourceLabel: string;
  onDeviceAdded: (info: { name: string; deviceId?: string }) => void;
  onDeviceUpdated: (device: api.DiscoveredDevice) => void;
}) {
  const entry = driverNameLookup.get(driverId);
  const driverName = entry?.name ?? driverId;
  const isCommunity = entry?.source === "community";

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [setupText, setSetupText] = useState<string | null>(null);
  const [showSetup, setShowSetup] = useState(false);
  const [driverInfoForSetup, setDriverInfoForSetup] = useState<DriverInfo | null>(null);
  const [addedDeviceId, setAddedDeviceId] = useState<string | null>(null);
  const project = useProjectStore((s) => s.project);

  const handleAdd = async () => {
    setError(null);

    if (isCommunity) {
      // Install + add — no setup preview for community drivers (driver isn't installed yet)
      setBusy(true);
      try {
        const community = entry?.community;
        if (!community) {
          setError("Community driver not found in catalog");
          return;
        }
        const fileUrl = `https://raw.githubusercontent.com/open-avc/openavc-drivers/main/${community.file}`;
        const result = await api.discoveryInstallAndMatch({
          ip: device.ip,
          driver_id: driverId,
          file_url: fileUrl,
        });
        if (result.device) onDeviceUpdated(result.device);
        if (result.status === "ok") {
          onDeviceAdded({ name: result.name || driverName, deviceId: result.device_id });
        } else if (result.status === "installed_not_added") {
          setError(`Driver installed but could not add device: ${result.error}`);
        }
      } catch (e) {
        setError(String(e));
      } finally {
        setBusy(false);
      }
      return;
    }

    // Installed driver — show setup help once before adding
    if (!showSetup) {
      try {
        const help = await api.getDriverHelp(driverId);
        if (help.setup) {
          setSetupText(help.setup);
          setShowSetup(true);
          return;
        }
      } catch {
        // No help — fall through and add directly
      }
    }

    setBusy(true);
    try {
      const result = await api.discoveryAddDevice({ ip: device.ip, driver_id: driverId });
      setAddedDeviceId(result.device_id);
      const di = installedDrivers.find((d) => d.id === driverId);
      if (di && hasDriverSetupSettings(di)) {
        setDriverInfoForSetup(di);
      } else {
        onDeviceAdded({ name: result.name, deviceId: result.device_id });
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        padding: "var(--space-sm)",
        background: "var(--bg-input)",
        borderRadius: "var(--radius)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 500 }}>{driverName}</span>
        {isCommunity && (
          <span style={{
            fontSize: "var(--font-size-xs)", padding: "1px 6px", borderRadius: 3,
            background: "rgba(59,130,246,0.15)", color: "#3b82f6",
          }}>
            Community
          </span>
        )}
        <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-xs)" }}>
          {sourceLabel}
        </span>
        <button
          className="btn btn-sm btn-primary"
          onClick={handleAdd}
          disabled={busy}
          style={{ marginLeft: "auto" }}
        >
          <Plus size={14} />{" "}
          {busy
            ? (isCommunity ? "Installing..." : "Adding...")
            : showSetup
              ? "Confirm & Add"
              : isCommunity
                ? "Install & Add"
                : "Add to Project"}
        </button>
      </div>

      {showSetup && setupText && (
        <div
          style={{
            marginTop: "var(--space-sm)",
            padding: "var(--space-sm)",
            background: "var(--bg-surface)",
            borderRadius: "var(--radius)",
            fontSize: "var(--font-size-xs)",
            whiteSpace: "pre-line",
          }}
        >
          <strong>Setup Instructions:</strong>
          <div style={{ marginTop: 4, color: "var(--text-muted)" }}>{setupText}</div>
        </div>
      )}

      {error && (
        <div style={{ color: "var(--danger)", fontSize: "var(--font-size-xs)", marginTop: 4 }}>
          {error}
        </div>
      )}

      {driverInfoForSetup && addedDeviceId && (
        <DeviceSettingsSetupDialog
          deviceId={addedDeviceId}
          driverInfo={driverInfoForSetup}
          existingDeviceIds={(project?.devices ?? []).map((d) => d.id)}
          onClose={() => {
            setDriverInfoForSetup(null);
            onDeviceAdded({ name: driverName, deviceId: addedDeviceId });
          }}
        />
      )}
    </div>
  );
}


// --- Possible-state candidates ---

function PossibleCandidates({
  device,
  candidates,
  sourceLabel,
  installedDrivers,
  driverNameLookup,
  onDeviceAdded,
  onDeviceUpdated,
}: {
  device: api.DiscoveredDevice;
  candidates: string[];
  sourceLabel: string;
  installedDrivers: DriverInfo[];
  driverNameLookup: Map<string, DriverEntry>;
  onDeviceAdded: (info: { name: string; deviceId?: string }) => void;
  onDeviceUpdated: (device: api.DiscoveredDevice) => void;
}) {
  return (
    <div>
      <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: 4 }}>
        Possible match from {sourceLabel}. Pick the right driver:
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
        {candidates.map((id) => (
          <DriverAddRow
            key={id}
            device={device}
            driverId={id}
            installedDrivers={installedDrivers}
            driverNameLookup={driverNameLookup}
            sourceLabel={sourceLabel}
            onDeviceAdded={onDeviceAdded}
            onDeviceUpdated={onDeviceUpdated}
          />
        ))}
      </div>
    </div>
  );
}


// --- Manual driver picker (unknown state) ---

function ManualDriverPicker({
  device,
  installedDrivers,
  driverNameLookup,
  onDeviceAdded,
  onDeviceUpdated,
}: {
  device: api.DiscoveredDevice;
  installedDrivers: DriverInfo[];
  driverNameLookup: Map<string, DriverEntry>;
  onDeviceAdded: (info: { name: string; deviceId?: string }) => void;
  onDeviceUpdated: (device: api.DiscoveredDevice) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [selected, setSelected] = useState<string>("");

  const sortedInstalled = useMemo(
    () => [...installedDrivers].sort((a, b) => a.name.localeCompare(b.name)),
    [installedDrivers],
  );

  if (!picking) {
    return (
      <div style={{
        padding: "var(--space-sm)", background: "var(--bg-input)",
        borderRadius: "var(--radius)", display: "flex", alignItems: "center",
        gap: "var(--space-sm)", flexWrap: "wrap",
      }}>
        <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
          No driver matched this device automatically.
        </span>
        <button
          className="btn btn-sm"
          onClick={() => setPicking(true)}
          style={{ marginLeft: "auto" }}
        >
          Pick driver manually
        </button>
      </div>
    );
  }

  return (
    <div style={{
      padding: "var(--space-sm)", background: "var(--bg-input)",
      borderRadius: "var(--radius)", display: "flex", flexDirection: "column",
      gap: "var(--space-sm)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          style={{ flex: 1, minWidth: 240 }}
        >
          <option value="">Select an installed driver...</option>
          {sortedInstalled.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} {d.manufacturer ? `(${d.manufacturer})` : ""}
            </option>
          ))}
        </select>
        <button
          className="btn btn-sm"
          onClick={() => { setPicking(false); setSelected(""); }}
        >
          Cancel
        </button>
      </div>
      {selected && (
        <DriverAddRow
          device={device}
          driverId={selected}
          installedDrivers={installedDrivers}
          driverNameLookup={driverNameLookup}
          sourceLabel="manual selection"
          onDeviceAdded={onDeviceAdded}
          onDeviceUpdated={onDeviceUpdated}
        />
      )}
    </div>
  );
}


// --- Evidence list ("Why?" reveal) ---

function EvidenceList({ evidence }: { evidence: DiscoveryEvidence[] }) {
  if (evidence.length === 0) {
    return (
      <div style={{ marginTop: 4, fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
        No evidence collected.
      </div>
    );
  }
  return (
    <div style={{
      marginTop: 4, padding: "var(--space-sm)",
      background: "var(--bg-input)", borderRadius: "var(--radius)",
      fontSize: "var(--font-size-xs)", color: "var(--text-muted)",
      fontFamily: "monospace",
    }}>
      {evidence.map((e, i) => (
        <div key={i} style={{ marginBottom: 4 }}>
          <span style={{ color: "var(--accent)" }}>[{e.tier}]</span> {e.source}
          {Object.keys(e.data).length > 0 && (
            <span style={{ marginLeft: 8 }}>
              {Object.entries(e.data).slice(0, 4).map(([k, v]) =>
                `${k}=${typeof v === "string" ? v.substring(0, 60) : JSON.stringify(v).substring(0, 60)}`,
              ).join(", ")}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}


function DetailRow({ label, value, copyable }: { label: string; value: string; copyable?: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <span style={{ color: "var(--text-muted)" }}>{label}:</span>
      <span style={{ fontFamily: copyable ? "monospace" : "inherit" }}>{value}</span>
      {copyable && value && value !== "Unknown" && <CopyButton value={value} />}
    </div>
  );
}
