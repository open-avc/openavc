import { useState, useEffect, useCallback, useMemo } from "react";
import { Plus, CheckSquare, Radar, ChevronRight, ChevronDown } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { useProjectStore } from "../store/projectStore";
import { useConnectionStore } from "../store/connectionStore";
import { useNavigationStore } from "../store/navigationStore";
import * as api from "../api/restClient";
import type { DeviceConfig, DriverInfo } from "../api/types";
import { DiscoveryPanel } from "./DiscoveryView";
import { DriverPanel } from "./DriverBuilderView";
import { DeviceDetail } from "./devices/DeviceDetail";
import { DeviceGroupsPanel } from "./devices/DeviceGroupsPanel";
import { DeviceListItem } from "./devices/DeviceListItem";
import { AddDeviceDialog, EditDeviceDialog } from "./devices/DeviceDialogs";
import { findDeviceReferences } from "./devices/deviceUtils";
import { computeStatusCounts } from "./deviceViewHelpers";

type DeviceSubTab = "devices" | "groups" | "discovery" | "drivers";

export function DeviceView() {
  const devices = useProjectStore((s) => s.project?.devices);
  const projectConnections = useProjectStore((s) => s.project?.connections);
  const projectDeviceGroups = useProjectStore((s) => s.project?.device_groups);
  const update = useProjectStore((s) => s.update);
  const reloadProject = useProjectStore((s) => s.load);

  // Sub-tab: "devices" (device list+detail) or "discovery" (network scan)
  const [subTab, setSubTab] = useState<DeviceSubTab>(() => {
    // If navigated here via old routes, start on the appropriate tab
    const hash = window.location.hash;
    if (hash === "#discovery") return "discovery";
    if (hash === "#drivers") return "drivers";
    return "devices";
  });

  const [selectedId, setSelectedId] = useState<string | null>(() => {
    const focus = useNavigationStore.getState().consumeFocus();
    return focus?.type === "device" ? focus.id : null;
  });
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [editDevice, setEditDevice] = useState<DeviceConfig | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "online" | "offline" | "orphaned">("all");
  const [duplicateSource, setDuplicateSource] = useState<DeviceConfig | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkMode, setBulkMode] = useState(false);
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState<{ message: React.ReactNode } | null>(null);
  const [showTopology, setShowTopology] = useState(false);
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);

  // Listen for focus changes (e.g., "Go to Device" from discovery)
  useEffect(() => {
    return useNavigationStore.subscribe((state) => {
      if (state.pendingFocus?.type === "device") {
        setSubTab("devices");
        const focus = useNavigationStore.getState().consumeFocus();
        if (focus) setSelectedId(focus.id);
      }
    });
  }, []);

  // Driver registry — needed to know which devices are bridges (their driver
  // advertises bridge ports) for the read-only topology panel below.
  useEffect(() => {
    api.listDrivers().then(setDrivers).catch(() => {});
  }, []);

  const stateVersion = useConnectionStore((s) => s.stateVersion);
  const deviceConfigs = devices ?? [];

  // Bridge topology: each bridge device, its advertised ports, and which
  // devices are bound to each port (from the connections table). Drives the
  // read-only tree panel in the device list column.
  const bridges = useMemo(() => {
    const byId = new Map(drivers.map((d) => [d.id, d]));
    const conns = projectConnections ?? {};
    return deviceConfigs
      .map((dev) => ({ dev, ports: byId.get(dev.driver)?.bridge?.ports ?? [] }))
      .filter((b) => b.ports.length > 0)
      .map((b) => ({
        dev: b.dev,
        ports: b.ports.map((port) => ({
          port,
          bound: deviceConfigs.filter((d) => {
            const c = conns[d.id];
            return c?.bridge === b.dev.id && c?.bridge_port === port.id;
          }),
        })),
      }));
  }, [devices, drivers, projectConnections]);

  const deviceGroups = projectDeviceGroups ?? [];

  // Filter and group devices (memoized)
  const connections = projectConnections ?? {};
  const { filteredDevices, grouped, sortedGroups, hasGroups, statusCounts, deviceGroupNames } = useMemo(() => {
    const q = search.toLowerCase();
    const filtered = deviceConfigs.filter(
      (dev) => {
        if (!q) return true;
        if (dev.name.toLowerCase().includes(q)) return true;
        if (dev.id.toLowerCase().includes(q)) return true;
        if (dev.driver.toLowerCase().includes(q)) return true;
        // Search by IP/host from connections table or config
        const conn = connections[dev.id] ?? {};
        const host = String(conn.host ?? dev.config?.host ?? "").toLowerCase();
        if (host && host.includes(q)) return true;
        return false;
      }
    );
    // Build device -> first group name mapping from device_groups
    const deviceToGroup = new Map<string, string>();
    const deviceToAllGroups = new Map<string, string[]>();
    for (const g of deviceGroups) {
      for (const did of g.device_ids) {
        if (!deviceToGroup.has(did)) {
          deviceToGroup.set(did, g.name);
        }
        if (!deviceToAllGroups.has(did)) deviceToAllGroups.set(did, []);
        deviceToAllGroups.get(did)!.push(g.name);
      }
    }
    const groups = new Map<string, typeof filtered>();
    for (const dev of filtered) {
      const g = deviceToGroup.get(dev.id) || "";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(dev);
    }
    // Status counts from live state (snapshot read). Count from the
    // search-`filtered` list, not all deviceConfigs, so the chip counts match
    // the visible (search-narrowed) device list.
    const ls = useConnectionStore.getState().liveState;
    const statusCounts = computeStatusCounts(filtered, ls);

    // Apply status filter
    let statusFiltered = filtered;
    if (statusFilter !== "all") {
      statusFiltered = filtered.filter((dev) => {
        const isOrphaned = !!ls[`device.${dev.id}.orphaned`];
        const isConnected = !!ls[`device.${dev.id}.connected`];
        if (statusFilter === "orphaned") return isOrphaned;
        if (statusFilter === "online") return !isOrphaned && isConnected;
        if (statusFilter === "offline") return !isOrphaned && !isConnected;
        return true;
      });
    }

    // Re-group with status-filtered devices
    const filteredGroups = new Map<string, typeof statusFiltered>();
    for (const dev of statusFiltered) {
      const g = deviceToGroup.get(dev.id) || "";
      if (!filteredGroups.has(g)) filteredGroups.set(g, []);
      filteredGroups.get(g)!.push(dev);
    }
    const filteredSorted = [...filteredGroups.keys()].sort((a, b) => {
      if (!a) return 1;
      if (!b) return -1;
      return a.localeCompare(b);
    });

    return {
      filteredDevices: statusFiltered,
      grouped: filteredGroups,
      sortedGroups: filteredSorted,
      hasGroups: filteredSorted.some((g) => g !== ""),
      statusCounts,
      deviceGroupNames: deviceToAllGroups,
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devices, projectDeviceGroups, search, projectConnections, statusFilter, stateVersion]);

  const handleDeviceDeleted = useCallback(
    (deletedId: string) => {
      if (selectedId === deletedId) setSelectedId(null);
      reloadProject();
    },
    [selectedId, reloadProject]
  );

  const handleDeviceUpdated = useCallback(() => {
    reloadProject();
  }, [reloadProject]);

  const handleBulkDelete = useCallback(() => {
    const project = useProjectStore.getState().project;
    if (!project || selectedIds.size === 0) return;
    const allRefs: string[] = [];
    for (const id of selectedIds) {
      allRefs.push(...findDeviceReferences(project, id));
    }
    const message = (
      <>
        <div>Delete {selectedIds.size} device(s)? This cannot be undone.</div>
        {allRefs.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-secondary)" }}>
            Warning: These devices are referenced in {allRefs.length} place(s) (macros, triggers, UI bindings).
          </div>
        )}
      </>
    );
    setBulkDeleteConfirm({ message });
  }, [selectedIds]);

  const doBulkDelete = useCallback(async () => {
    setBulkDeleteConfirm(null);
    for (const id of selectedIds) {
      try {
        await api.deleteDevice(id);
      } catch (err) {
        console.error(`Failed to delete device ${id}:`, err);
      }
    }
    if (selectedId && selectedIds.has(selectedId)) setSelectedId(null);
    setSelectedIds(new Set());
    setBulkMode(false);
    reloadProject();
  }, [selectedIds, selectedId, reloadProject]);

  const handleBulkToggle = useCallback(
    async (enabled: boolean) => {
      if (!devices || selectedIds.size === 0) return;
      const updatedDevices = devices.map((d) =>
        selectedIds.has(d.id) ? { ...d, enabled } : d
      );
      update({ devices: updatedDevices });
      await useProjectStore.getState().save();
      setSelectedIds(new Set());
      setBulkMode(false);
    },
    [devices, selectedIds, update]
  );

  const toggleSelection = useCallback(
    (id: string) => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return next;
      });
    },
    []
  );

  return (
    <ViewContainer
      title={
        <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }} role="tablist">
          {([
            { id: "devices" as const, label: "Devices" },
            { id: "groups" as const, label: "Groups" },
            { id: "discovery" as const, label: "Discovery" },
            { id: "drivers" as const, label: "Drivers" },
          ]).map((tab) => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={subTab === tab.id}
              onClick={() => setSubTab(tab.id)}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: subTab === tab.id ? "var(--accent-bg)" : "var(--bg-hover)",
                color: subTab === tab.id ? "var(--text-on-accent)" : "var(--text-secondary)",
                fontSize: "var(--font-size-sm)",
                fontWeight: subTab === tab.id ? 600 : 400,
                border: "none",
                cursor: "pointer",
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      }
      actions={
        subTab === "devices" ? (
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button
            onClick={() => {
              setBulkMode((v) => !v);
              if (bulkMode) setSelectedIds(new Set());
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: bulkMode ? "var(--accent-dim)" : "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <CheckSquare size={14} /> Select
          </button>
          <button
            onClick={() => setSubTab("discovery")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-elevated)",
              color: "var(--text-primary)",
              fontSize: "var(--font-size-sm)",
              border: "1px solid var(--border-color)",
            }}
            title="Discover devices on the network"
          >
            <Radar size={14} /> Scan Network
          </button>
          <button
            onClick={() => setShowAddDialog(true)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Plus size={14} /> Add Device
          </button>
        </div>
        ) : undefined
      }
    >
      {subTab === "discovery" ? (
        <DiscoveryPanel />
      ) : subTab === "drivers" ? (
        <DriverPanel />
      ) : subTab === "groups" ? (
        <DeviceGroupsPanel />
      ) : (
      <div style={{ display: "flex", gap: "var(--space-lg)", flex: 1, minHeight: 0 }}>
        {/* Device list */}
        <div
          style={{
            width: 280,
            flexShrink: 0,
            borderRight: "1px solid var(--border-color)",
            paddingRight: "var(--space-lg)",
            overflow: "auto",
          }}
        >
          {/* Bridge topology — read-only overview (bridge > port > device).
              Only shown when the project has bridge devices. Names are
              clickable to jump to that device's detail. */}
          {bridges.length > 0 && (
            <div style={{ marginBottom: "var(--space-sm)" }}>
              <button
                onClick={() => setShowTopology((v) => !v)}
                style={{
                  display: "flex", alignItems: "center", gap: 4, width: "100%",
                  padding: "var(--space-xs) var(--space-sm)", background: "var(--bg-surface)",
                  border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)",
                  color: "var(--text-secondary)", fontSize: 11, cursor: "pointer",
                  textTransform: "uppercase", letterSpacing: "0.5px",
                }}
              >
                {showTopology ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                Bridge topology
              </button>
              {showTopology && (
                <div
                  style={{
                    marginTop: "var(--space-xs)", padding: "var(--space-sm)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "var(--border-radius)", fontSize: 11,
                  }}
                >
                  {bridges.map(({ dev, ports }) => (
                    <div key={dev.id} style={{ marginBottom: "var(--space-sm)" }}>
                      <button
                        onClick={() => setSelectedId(dev.id)}
                        style={{
                          background: "none", border: "none", padding: 0, cursor: "pointer",
                          color: "var(--text-primary)", fontWeight: 600, textAlign: "left",
                        }}
                      >
                        {dev.name || dev.id}
                      </button>
                      {ports.map(({ port, bound }) => (
                        <div key={port.id} style={{ marginLeft: 10, marginTop: 2, color: "var(--text-secondary)" }}>
                          {port.label || port.id}
                          {bound.length === 0 ? (
                            <div style={{ marginLeft: 10, color: "var(--text-muted)" }}>&mdash; unbound</div>
                          ) : (
                            bound.map((b) => (
                              <div key={b.id} style={{ marginLeft: 10 }}>
                                <button
                                  onClick={() => setSelectedId(b.id)}
                                  style={{
                                    background: "none", border: "none", padding: 0,
                                    cursor: "pointer", color: "var(--accent-bg)", textAlign: "left",
                                  }}
                                >
                                  {b.name || b.id}
                                </button>
                              </div>
                            ))
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Search input */}
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search devices..."
            style={{
              width: "100%",
              marginBottom: "var(--space-sm)",
              padding: "var(--space-xs) var(--space-sm)",
              fontSize: "var(--font-size-sm)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-surface)",
              color: "var(--text-primary)",
            }}
          />

          {/* Device count summary + filter chips */}
          {deviceConfigs.length > 0 && (
            <div style={{
              display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center",
              marginBottom: "var(--space-sm)", fontSize: 11, color: "var(--text-muted)",
            }}>
              <span>{statusCounts.total} device{statusCounts.total !== 1 ? "s" : ""}:</span>
              {([
                { key: "all" as const, label: "All", count: statusCounts.total },
                { key: "online" as const, label: "Online", count: statusCounts.online },
                { key: "offline" as const, label: "Offline", count: statusCounts.offline },
                { key: "orphaned" as const, label: "Orphaned", count: statusCounts.orphaned },
              ] as const).filter((f) => f.key === "all" || f.count > 0).map((f) => (
                <button
                  key={f.key}
                  onClick={() => setStatusFilter(f.key)}
                  style={{
                    padding: "1px 6px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                    background: statusFilter === f.key ? "var(--accent-bg)" : "var(--bg-hover)",
                    color: statusFilter === f.key ? "#fff" : "var(--text-secondary)",
                    border: "none",
                  }}
                >
                  {f.label} {f.key !== "all" ? f.count : ""}
                </button>
              ))}
            </div>
          )}

          {/* Bulk action bar */}
          {selectedIds.size > 0 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "var(--space-sm) var(--space-md)",
                background: "var(--bg-surface)",
                borderRadius: "var(--border-radius)",
                border: "1px solid var(--border-color)",
                marginBottom: "var(--space-sm)",
                fontSize: "var(--font-size-sm)",
                flexWrap: "wrap",
              }}
            >
              <span style={{ color: "var(--text-secondary)" }}>
                {selectedIds.size} selected
              </span>
              <button
                onClick={() => setSelectedIds(new Set(filteredDevices.map((d) => d.id)))}
                style={{ padding: "2px var(--space-sm)", borderRadius: "var(--border-radius)", background: "var(--bg-hover)", fontSize: "var(--font-size-sm)" }}
              >
                All
              </button>
              <button
                onClick={() => setSelectedIds(new Set())}
                style={{ padding: "2px var(--space-sm)", borderRadius: "var(--border-radius)", background: "var(--bg-hover)", fontSize: "var(--font-size-sm)" }}
              >
                None
              </button>
              <button
                onClick={() => {
                  const ls = useConnectionStore.getState().liveState;
                  setSelectedIds(new Set(filteredDevices.filter((d) => ls[`device.${d.id}.connected`]).map((d) => d.id)));
                }}
                style={{ padding: "2px var(--space-sm)", borderRadius: "var(--border-radius)", background: "rgba(76,175,80,0.15)", color: "var(--color-success)", fontSize: "var(--font-size-sm)" }}
              >
                Online
              </button>
              <span style={{ color: "var(--border-color)" }}>|</span>
              <button
                onClick={handleBulkDelete}
                style={{
                  padding: "2px var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--color-error-bg)",
                  color: "var(--color-error)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                Delete
              </button>
              <button
                onClick={() => handleBulkToggle(true)}
                style={{
                  padding: "2px var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "rgba(76,175,80,0.15)",
                  color: "var(--color-success)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                Enable
              </button>
              <button
                onClick={() => handleBulkToggle(false)}
                style={{
                  padding: "2px var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  color: "var(--text-secondary)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                Disable
              </button>
              <button
                onClick={() => {
                  setSelectedIds(new Set());
                  setBulkMode(false);
                }}
                style={{
                  padding: "2px var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                Cancel
              </button>
            </div>
          )}

          {deviceConfigs.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
              No devices configured. Click &quot;Add Device&quot; to get started.
              <br />
              <a href="https://docs.openavc.com/devices-and-drivers" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", fontSize: 12 }}>
                Learn about devices and drivers
              </a>
            </p>
          ) : filteredDevices.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              No devices match your search.
            </p>
          ) : (
            sortedGroups.map((group) => (
              <div key={group || "__ungrouped"}>
                {hasGroups && (
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: "0.5px",
                      padding: "var(--space-sm) var(--space-md)",
                      marginTop: "var(--space-sm)",
                      fontWeight: 600,
                    }}
                  >
                    {group || "Ungrouped"}
                  </div>
                )}
                {grouped.get(group)!.map((dev) => {
                  const isChecked = selectedIds.has(dev.id);
                  return (
                  <div
                    key={dev.id}
                    style={{ display: "flex", alignItems: "center" }}
                    onMouseEnter={(e) => {
                      const cb = e.currentTarget.querySelector<HTMLElement>("[data-bulk-cb]");
                      if (cb) cb.style.opacity = "1";
                    }}
                    onMouseLeave={(e) => {
                      const cb = e.currentTarget.querySelector<HTMLElement>("[data-bulk-cb]");
                      if (cb && !isChecked) cb.style.opacity = "0";
                    }}
                  >
                    <input
                      type="checkbox"
                      data-bulk-cb=""
                      checked={isChecked}
                      onChange={() => toggleSelection(dev.id)}
                      style={{
                        marginRight: "var(--space-xs)", flexShrink: 0,
                        opacity: isChecked ? 1 : 0,
                        transition: "opacity 0.15s",
                        cursor: "pointer",
                      }}
                    />
                    <DeviceListItem
                      deviceId={dev.id}
                      name={dev.name}
                      driver={dev.driver}
                      selected={selectedId === dev.id}
                      enabled={dev.enabled !== false}
                      groupNames={deviceGroupNames.get(dev.id)}
                      onClick={() => {
                        if (bulkMode) {
                          toggleSelection(dev.id);
                        } else {
                          setSelectedId(dev.id);
                        }
                      }}
                    />
                  </div>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {/* Device detail */}
        <div style={{ flex: 1, overflow: "auto" }}>
          {selectedId ? (
            <DeviceDetail
              // Remount per device: detail state (state-change log, command
              // selection, in-progress setting edits) must never carry over
              // from a previously-viewed device.
              key={selectedId}
              deviceId={selectedId}
              onEdit={(config) => setEditDevice(config)}
              onDeleted={handleDeviceDeleted}
              onDuplicate={(config) => setDuplicateSource(config)}
              onBrowseDrivers={() => setSubTab("drivers")}
              onOpenDevice={(id) => setSelectedId(id)}
            />
          ) : (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                color: "var(--text-muted)",
              }}
            >
              Select a device to view details
            </div>
          )}
        </div>
      </div>
      )}

      {(showAddDialog || duplicateSource) && (
        <AddDeviceDialog
          onClose={() => {
            setShowAddDialog(false);
            setDuplicateSource(null);
          }}
          prefill={duplicateSource ?? undefined}
        />
      )}

      {editDevice && (
        <EditDeviceDialog
          device={editDevice}
          onClose={() => setEditDevice(null)}
          onSaved={handleDeviceUpdated}
        />
      )}
      {bulkDeleteConfirm && (
        <ConfirmDialog
          title="Delete Devices"
          message={bulkDeleteConfirm.message}
          confirmLabel="Delete"
          onConfirm={doBulkDelete}
          onCancel={() => setBulkDeleteConfirm(null)}
        />
      )}
    </ViewContainer>
  );
}
