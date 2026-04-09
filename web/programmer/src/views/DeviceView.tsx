import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Plus, Send, Pencil, Trash2, Wifi, Power, RefreshCw, Copy, CheckSquare, Settings, Check, X, Loader2, Radar, Layers } from "lucide-react";
import { CopyButton } from "../components/shared/CopyButton";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { DeviceStatusDot } from "../components/shared/DeviceStatusDot";
import { useProjectStore } from "../store/projectStore";
import { useConnectionStore } from "../store/connectionStore";
import { useLogStore } from "../store/logStore";
import { useNavigationStore } from "../store/navigationStore";
import * as api from "../api/restClient";
import type { DeviceConfig, DeviceGroup, DeviceInfo, DeviceSettingValue, DriverInfo } from "../api/types";
import { DevicePanelSlot, ContextActionRenderer } from "../components/plugins/PluginExtensions";
import { DeviceSettingsSetupDialog, hasDriverSetupSettings } from "../components/shared/DeviceSettingsSetupDialog";
import { DiscoveryPanel } from "./DiscoveryView";
import { DriverPanel } from "./DriverBuilderView";

type DeviceSubTab = "devices" | "groups" | "discovery" | "drivers";

export function DeviceView() {
  const project = useProjectStore((s) => s.project);
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

  // When a device is added from Discovery, switch to devices tab and select it
  const handleDeviceAddedFromDiscovery = useCallback((deviceId: string) => {
    reloadProject();
    setSubTab("devices");
    setSelectedId(deviceId);
  }, [reloadProject]);

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

  const deviceConfigs = project?.devices ?? [];

  const deviceGroups = project?.device_groups ?? [];

  // Filter and group devices (memoized)
  const connections = project?.connections ?? {};
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
    const sorted = [...groups.keys()].sort((a, b) => {
      if (!a) return 1;
      if (!b) return -1;
      return a.localeCompare(b);
    });
    // Compute status counts from live state (snapshot read)
    const ls = useConnectionStore.getState().liveState;
    let online = 0, offline = 0, orphanedCount = 0;
    for (const dev of deviceConfigs) {
      if (ls[`device.${dev.id}.orphaned`]) orphanedCount++;
      else if (ls[`device.${dev.id}.connected`]) online++;
      else offline++;
    }

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
      statusCounts: { total: deviceConfigs.length, online, offline, orphaned: orphanedCount },
      deviceGroupNames: deviceToAllGroups,
    };
  }, [deviceConfigs, deviceGroups, search, connections, statusFilter]);

  const handleDeviceDeleted = useCallback(
    (deletedId: string) => {
      if (selectedId === deletedId) setSelectedId(null);
      // Clean up phantom state keys from the frontend store
      useConnectionStore.getState().removeKeysWithPrefix(`device.${deletedId}`);
      reloadProject();
    },
    [selectedId, reloadProject]
  );

  const handleDeviceUpdated = useCallback(() => {
    reloadProject();
  }, [reloadProject]);

  const handleBulkDelete = useCallback(() => {
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
  }, [project, selectedIds]);

  const doBulkDelete = useCallback(async () => {
    setBulkDeleteConfirm(null);
    const { removeKeysWithPrefix } = useConnectionStore.getState();
    for (const id of selectedIds) {
      try {
        await api.deleteDevice(id);
        removeKeysWithPrefix(`device.${id}`);
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
      if (!project || selectedIds.size === 0) return;
      const updatedDevices = project.devices.map((d) =>
        selectedIds.has(d.id) ? { ...d, enabled } : d
      );
      update({ devices: updatedDevices });
      await useProjectStore.getState().save();
      setSelectedIds(new Set());
      setBulkMode(false);
    },
    [project, selectedIds, update]
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
                background: subTab === tab.id ? "var(--accent)" : "var(--bg-hover)",
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
              background: "var(--accent)",
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
                    background: statusFilter === f.key ? "var(--accent)" : "var(--bg-hover)",
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
              deviceId={selectedId}
              onEdit={(config) => setEditDevice(config)}
              onDeleted={handleDeviceDeleted}
              onDuplicate={(config) => setDuplicateSource(config)}
              onBrowseDrivers={() => setSubTab("drivers")}
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

// --- Device List Item ---

function DeviceListItem({
  deviceId,
  name,
  driver,
  selected,
  enabled,
  groupNames,
  onClick,
}: {
  deviceId: string;
  name: string;
  driver: string;
  selected: boolean;
  enabled: boolean;
  groupNames?: string[];
  onClick: () => void;
}) {
  const connected = useConnectionStore(
    (s) => s.liveState[`device.${deviceId}.connected`] as boolean | undefined
  );
  const orphaned = useConnectionStore(
    (s) => s.liveState[`device.${deviceId}.orphaned`] as boolean | undefined
  );

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
        opacity: enabled && !orphaned ? 1 : 0.6,
      }}
    >
      <DeviceStatusDot connected={connected ?? false} orphaned={orphaned ?? false} />
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {name}
        </div>
        <div style={{
          fontSize: "var(--font-size-sm)",
          color: orphaned ? "var(--color-warning, #f59e0b)" : "var(--text-muted)",
          display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap",
        }}>
          <span>{driver}{orphaned ? " (not installed)" : ""}</span>
          {groupNames && groupNames.length > 0 && groupNames.map((gn) => (
            <span key={gn} style={{
              fontSize: 9, padding: "0 4px", borderRadius: 3,
              background: "rgba(33,150,243,0.12)", color: "var(--accent)",
              lineHeight: "16px",
            }}>{gn}</span>
          ))}
        </div>
      </div>
    </button>
  );
}

// --- Device reference finder ---

function findDeviceReferences(project: import("../api/types").ProjectConfig, deviceId: string): string[] {
  const refs: string[] = [];
  const prefix = `device.${deviceId}`;

  // Check macro steps
  for (const macro of project.macros) {
    const stepRefs = macro.steps.filter((s) => s.device === deviceId);
    if (stepRefs.length > 0) {
      refs.push(`Macro "${macro.name}": ${stepRefs.length} step(s)`);
    }
    // Check trigger state_key references
    for (const t of macro.triggers ?? []) {
      if (t.state_key?.startsWith(prefix)) {
        refs.push(`Macro "${macro.name}" trigger: ${t.state_key}`);
      }
      for (const c of t.conditions ?? []) {
        if (c.key.startsWith(prefix)) {
          refs.push(`Macro "${macro.name}" trigger condition: ${c.key}`);
        }
      }
    }
  }

  // Check UI bindings (press/feedback bindings reference device state keys)
  for (const page of project.ui?.pages ?? []) {
    for (const el of page.elements) {
      const bindings = JSON.stringify(el.bindings);
      if (bindings.includes(deviceId)) {
        refs.push(`UI page "${page.name}" element "${el.label || el.id}"`);
      }
    }
  }

  return refs;
}

// --- Device Detail ---

function DeviceDetail({
  deviceId,
  onEdit,
  onDeleted,
  onDuplicate,
  onBrowseDrivers,
}: {
  deviceId: string;
  onEdit: (config: DeviceConfig) => void;
  onDeleted: (deviceId: string) => void;
  onDuplicate: (config: DeviceConfig) => void;
  onBrowseDrivers?: () => void;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const liveState = useConnectionStore((s) => s.liveState);
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [commandResult, setCommandResult] = useState<string | null>(null);
  const [selectedCommand, setSelectedCommand] = useState("");
  const [commandParams, setCommandParams] = useState<Record<string, string>>({});
  const [sending, setSending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    error: string | null;
    latency_ms: number | null;
    protocol_status?: string | null;
  } | null>(null);
  const [testing, setTesting] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => {
    api.getDevice(deviceId).then(setDeviceInfo).catch(console.error);
  }, [deviceId]);

  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const isEnabled = deviceConfig?.enabled !== false;

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await api.deleteDevice(deviceId);
      onDeleted(deviceId);
    } catch (e) {
      console.error(e);
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [deviceId, onDeleted]);

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testDeviceConnection(deviceId);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, error: String(e), latency_ms: null });
    } finally {
      setTesting(false);
    }
  };

  const handleToggleEnabled = async () => {
    if (!project || !deviceConfig) return;
    const updatedDevices = project.devices.map((d) =>
      d.id === deviceId ? { ...d, enabled: !isEnabled } : d
    );
    update({ devices: updatedDevices });
    setTimeout(() => useProjectStore.getState().save(), 100);
  };

  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      await api.reconnectDevice(deviceId);
    } catch (e) {
      console.error(e);
    } finally {
      setTimeout(() => setReconnecting(false), 2000);
    }
  };

  // Extract device state from flat liveState
  const prefix = `device.${deviceId}.`;
  const stateEntries: [string, string][] = [];
  for (const [key, value] of Object.entries(liveState)) {
    if (key.startsWith(prefix)) {
      stateEntries.push([key.slice(prefix.length), String(value ?? "")]);
    }
  }

  const deviceName = String(liveState[`device.${deviceId}.name`] ?? deviceId);
  const connected = Boolean(liveState[`device.${deviceId}.connected`]);

  const commands = deviceInfo?.commands ?? {};
  const commandNames = Object.keys(commands);

  const handleSendCommand = useCallback(async () => {
    if (!selectedCommand) return;
    setSending(true);
    setCommandResult(null);
    try {
      const params: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(commandParams)) {
        if (v === "") continue;
        params[k] = v;
      }
      const result = await api.sendCommand(deviceId, selectedCommand, params);
      setCommandResult(JSON.stringify(result, null, 2));
    } catch (e) {
      setCommandResult(String(e));
    } finally {
      setSending(false);
    }
  }, [deviceId, selectedCommand, commandParams]);

  // Get param fields for selected command
  const commandDef = commands[selectedCommand] as Record<string, unknown> | undefined;
  const paramKeys = Object.keys((commandDef?.params as Record<string, unknown>) ?? {});

  const sectionStyle: React.CSSProperties = {
    marginBottom: "var(--space-xl)",
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "var(--space-md)",
    fontWeight: 600,
  };

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "var(--space-xl)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-md)",
            flexWrap: "wrap",
          }}
        >
          <DeviceStatusDot connected={connected} orphaned={Boolean(liveState[`device.${deviceId}.orphaned`])} size={12} />
          <h2 style={{ fontSize: "var(--font-size-xl)", flex: 1 }}>{deviceName}</h2>
          <button
            onClick={handleToggleEnabled}
            title={isEnabled ? "Disable device" : "Enable device"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: isEnabled ? "rgba(76,175,80,0.15)" : "var(--bg-hover)",
              color: isEnabled ? "var(--color-success)" : "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Power size={14} /> {isEnabled ? "Enabled" : "Disabled"}
          </button>
          <button
            onClick={handleTestConnection}
            disabled={testing}
            title="Test device connection"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
              opacity: testing ? 0.6 : 1,
            }}
          >
            <Wifi size={14} /> {testing ? "Testing..." : "Test"}
          </button>
          {!connected && isEnabled && (
            <button
              onClick={handleReconnect}
              disabled={reconnecting}
              title="Force reconnect"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                color: "var(--accent)",
                fontSize: "var(--font-size-sm)",
                opacity: reconnecting ? 0.6 : 1,
              }}
            >
              <RefreshCw size={14} /> {reconnecting ? "Reconnecting..." : "Reconnect"}
            </button>
          )}
          <button
            onClick={() => deviceConfig && onEdit(deviceConfig)}
            title="Edit device settings"
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
            <Pencil size={14} /> Edit
          </button>
          <button
            onClick={() => deviceConfig && onDuplicate(deviceConfig)}
            title="Duplicate device"
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
            <Copy size={14} /> Duplicate
          </button>
          {confirmDelete ? (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>
                  Delete this device?
                </span>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--color-error)",
                    color: "#fff",
                    fontSize: "var(--font-size-sm)",
                    opacity: deleting ? 0.6 : 1,
                  }}
                >
                  {deleting ? "Deleting..." : "Yes, Delete"}
                </button>
                <button
                  onClick={() => setConfirmDelete(false)}
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
              {project && (() => {
                const refs = findDeviceReferences(project, deviceId);
                if (refs.length === 0) return null;
                return (
                  <div style={{ marginTop: "var(--space-xs)", padding: "var(--space-sm)", background: "rgba(244,67,54,0.08)", borderRadius: "var(--border-radius)", fontSize: 12, color: "var(--text-secondary)" }}>
                    <strong>Warning:</strong> This device is referenced in {refs.length} place(s):
                    <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                      {refs.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}
                      {refs.length > 5 && <li>...and {refs.length - 5} more</li>}
                    </ul>
                  </div>
                );
              })()}
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              title="Delete device"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                color: "var(--color-error)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <Trash2 size={14} /> Delete
            </button>
          )}
        </div>
        <div style={{ marginLeft: 22, display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            {deviceInfo?.driver ?? ""}
          </span>
          <span style={{ color: "var(--border-color)" }}>&middot;</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {deviceId}
            </code>
            <CopyButton value={deviceId} title="Copy device ID" />
          </span>
        </div>
      </div>

      {/* Orphaned device banner — prominent red warning */}
      {Boolean(liveState[`device.${deviceId}.orphaned`]) && (
        <div
          style={{
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            background: "rgba(239, 68, 68, 0.1)",
            border: "2px solid rgba(239, 68, 68, 0.4)",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: "var(--space-sm)", color: "#ef4444", fontSize: "var(--font-size-md)" }}>
            Driver Not Installed
          </div>
          <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
            This device needs the driver "{deviceConfig?.driver}" which is not installed.
            Install the driver from the community repository or reassign to a different driver.
          </div>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <button
              onClick={() => onBrowseDrivers?.()}
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
            <button
              onClick={() => deviceConfig && onEdit(deviceConfig)}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              Reassign Driver
            </button>
          </div>
        </div>
      )}

      {/* Test connection result */}
      {testResult && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            background: testResult.success
              ? "rgba(76,175,80,0.15)"
              : "var(--color-error-bg)",
            color: testResult.success ? "var(--color-success)" : "var(--color-error)",
          }}
        >
          {testResult.success
            ? `Connected successfully (${testResult.latency_ms}ms)${
                testResult.protocol_status === "verified" ? " — protocol verified"
                : testResult.protocol_status === "not_verified" ? " — protocol not verified"
                : ""
              }`
            : `Connection failed: ${testResult.error}`}
        </div>
      )}

      {/* Live State */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Live State</h3>
        <div
          style={{
            background: "var(--bg-surface)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            overflow: "hidden",
          }}
        >
          {stateEntries.length === 0 ? (
            <div
              style={{
                padding: "var(--space-lg)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              No state values yet
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {stateEntries.map(([key, value]) => (
                  <tr
                    key={key}
                    style={{ borderBottom: "1px solid var(--border-color)" }}
                  >
                    <td
                      style={{
                        padding: "var(--space-sm) var(--space-md)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        color: "var(--text-secondary)",
                        width: "40%",
                      }}
                    >
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                        {key}
                        <CopyButton value={`device.${deviceId}.${key}`} size={11} title="Copy full state key" />
                      </span>
                    </td>
                    <td
                      style={{
                        padding: "var(--space-sm) var(--space-md)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                      }}
                    >
                      {value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Device Settings */}
      <DeviceSettingsSection deviceId={deviceId} connected={connected} />

      {/* Command Testing */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Command Testing</h3>
        <div
          style={{
            background: "var(--bg-surface)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            padding: "var(--space-lg)",
          }}
        >
          {commandNames.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              {!connected
                ? "Device is not connected. Commands will be available once the device connects."
                : "No commands available. The driver may not be loaded or may not define any commands."}
            </div>
          ) : (
            <>
              <div style={{ marginBottom: "var(--space-md)" }}>
                <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <select
                  value={selectedCommand}
                  onChange={(e) => {
                    setSelectedCommand(e.target.value);
                    setCommandParams({});
                    setCommandResult(null);
                  }}
                  style={{ flex: 1 }}
                >
                  <option value="">Select a command...</option>
                  {commandNames.map((cmd) => (
                    <option key={cmd} value={cmd}>
                      {cmd}
                    </option>
                  ))}
                </select>
                <button
                  onClick={handleSendCommand}
                  disabled={!selectedCommand || sending}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-xs)",
                    padding: "var(--space-sm) var(--space-lg)",
                    borderRadius: "var(--border-radius)",
                    background: selectedCommand ? "var(--accent)" : "var(--bg-hover)",
                    color: selectedCommand ? "var(--text-on-accent)" : "var(--text-muted)",
                    opacity: sending ? 0.6 : 1,
                  }}
                >
                  <Send size={14} /> Send
                </button>
                </div>
                {selectedCommand && (() => {
                  const cmdDef = commands[selectedCommand] as Record<string, unknown> | undefined;
                  const cmdHelp = cmdDef?.help as string | undefined;
                  return cmdHelp ? (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                      {cmdHelp}
                    </div>
                  ) : null;
                })()}
              </div>

              {/* Param fields */}
              {paramKeys.length > 0 && (
                <div style={{ marginBottom: "var(--space-md)" }}>
                  {paramKeys.map((paramName) => {
                    const pDef = (commands[selectedCommand] as Record<string, unknown>)?.params as Record<string, Record<string, unknown>> | undefined;
                    const paramHelp = pDef?.[paramName]?.help as string | undefined;
                    return (
                    <div
                      key={paramName}
                      style={{
                        marginBottom: "var(--space-sm)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      <label
                        style={{
                          width: 120,
                          fontSize: "var(--font-size-sm)",
                          color: "var(--text-secondary)",
                        }}
                      >
                        {paramName}
                      </label>
                      <input
                        value={commandParams[paramName] ?? ""}
                        onChange={(e) =>
                          setCommandParams((p) => ({
                            ...p,
                            [paramName]: e.target.value,
                          }))
                        }
                        placeholder={paramName}
                        style={{ flex: 1 }}
                      />
                      </div>
                      {paramHelp && (
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2, marginLeft: 120 }}>
                          {paramHelp}
                        </div>
                      )}
                    </div>
                    );
                  })}
                </div>
              )}

              {/* Result */}
              {commandResult !== null && (
                <pre
                  style={{
                    background: "var(--bg-base)",
                    padding: "var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    fontSize: "var(--font-size-sm)",
                    fontFamily: "var(--font-mono)",
                    overflow: "auto",
                    maxHeight: 200,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {commandResult}
                </pre>
              )}
            </>
          )}
        </div>
      </div>

      {/* Plugin Device Panels */}
      <DevicePanelSlot
        deviceId={deviceId}
        driverId={deviceConfig?.driver ?? ""}
      />

      {/* Plugin Context Actions */}
      <ContextActionRenderer context="device" deviceId={deviceId} driverId={deviceConfig?.driver} />

      {/* Device Log */}
      <DeviceLog deviceId={deviceId} />
    </div>
  );
}

// --- Device Settings Section ---

function DeviceSettingsSection({ deviceId, connected }: { deviceId: string; connected: boolean }) {
  const project = useProjectStore((s) => s.project);
  const pendingSettings = useMemo(() => {
    const dev = project?.devices.find((d) => d.id === deviceId);
    return dev?.pending_settings ?? {};
  }, [project, deviceId]);

  const [settings, setSettings] = useState<Record<string, DeviceSettingValue>>({});
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [saveResult, setSaveResult] = useState<{ key: string; success: boolean; error?: string } | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadSettings = useCallback(() => {
    api.getDeviceSettings(deviceId).then((data) => {
      setSettings(data.settings);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, [deviceId]);

  useEffect(() => {
    loadSettings();
    const interval = setInterval(loadSettings, 5000);
    return () => clearInterval(interval);
  }, [loadSettings]);

  const settingKeys = Object.keys(settings);
  if (!loaded || settingKeys.length === 0) return null;

  const handleStartEdit = (key: string) => {
    const current = settings[key]?.current_value;
    setEditingKey(key);
    setEditValue(current != null ? String(current) : String(settings[key]?.default ?? ""));
    setSaveResult(null);
  };

  const handleSave = async (key: string) => {
    setSaving(key);
    setSaveResult(null);
    try {
      const def = settings[key];
      const fieldType = String(def?.type ?? "string");
      let coerced: unknown = editValue;
      if (fieldType === "integer") coerced = parseInt(editValue, 10) || 0;
      else if (fieldType === "number") coerced = parseFloat(editValue) || 0;
      else if (fieldType === "boolean") coerced = editValue === "true";

      await api.setDeviceSetting(deviceId, key, coerced);
      setSaveResult({ key, success: true });
      setEditingKey(null);
      // Refresh settings to get updated current_value
      setTimeout(loadSettings, 1000);
    } catch (e) {
      setSaveResult({ key, success: false, error: String(e) });
    } finally {
      setSaving(null);
    }
  };

  const handleCancel = () => {
    setEditingKey(null);
    setSaveResult(null);
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "var(--space-md)",
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    gap: "var(--space-sm)",
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3 style={sectionTitleStyle}>
        <Settings size={14} /> Device Settings
      </h3>
      <div
        style={{
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          overflow: "hidden",
        }}
      >
        {settingKeys.map((key) => {
          const def = settings[key];
          const label = String(def?.label ?? key);
          const help = String(def?.help ?? "");
          const fieldType = String(def?.type ?? "string");
          const values = def?.values as string[] | undefined;
          const currentValue = def?.current_value;
          const isPending = key in pendingSettings;
          const isEditing = editingKey === key;
          const isSaving = saving === key;
          const result = saveResult?.key === key ? saveResult : null;

          return (
            <div
              key={key}
              style={{
                padding: "var(--space-md)",
                borderBottom: "1px solid var(--border-color)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>{label}</div>
                  {help && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{help}</div>
                  )}
                </div>
                {isEditing ? (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                    {fieldType === "boolean" ? (
                      <select
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        style={{ fontSize: "var(--font-size-sm)", padding: "2px 6px" }}
                      >
                        <option value="true">Yes</option>
                        <option value="false">No</option>
                      </select>
                    ) : fieldType === "enum" && values ? (
                      <select
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        style={{ fontSize: "var(--font-size-sm)", padding: "2px 6px" }}
                      >
                        {values.map((v) => (
                          <option key={v} value={v}>{v}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        type={fieldType === "integer" || fieldType === "number" ? "number" : "text"}
                        style={{
                          fontSize: "var(--font-size-sm)",
                          padding: "2px 6px",
                          width: 180,
                        }}
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleSave(key);
                          if (e.key === "Escape") handleCancel();
                        }}
                      />
                    )}
                    <button
                      onClick={() => handleSave(key)}
                      disabled={isSaving}
                      title="Save"
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--color-success-bg)",
                        color: "var(--color-success)",
                        fontSize: "var(--font-size-sm)",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      {isSaving ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Check size={14} />}
                    </button>
                    <button
                      onClick={handleCancel}
                      title="Cancel"
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--bg-hover)",
                        fontSize: "var(--font-size-sm)",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ) : (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        color: currentValue != null ? "var(--text-primary)" : "var(--text-muted)",
                      }}
                    >
                      {currentValue != null ? String(currentValue) : "(not set)"}
                    </span>
                    {isPending && (
                      <span
                        style={{
                          fontSize: 10,
                          color: "var(--accent)",
                          padding: "1px 6px",
                          borderRadius: "var(--border-radius)",
                          background: "var(--accent-dim)",
                        }}
                        title={`Pending: ${String(pendingSettings[key])} — will be applied when device connects`}
                      >
                        pending
                      </span>
                    )}
                    <button
                      onClick={() => handleStartEdit(key)}
                      disabled={!connected}
                      title={connected ? "Edit setting" : "Device must be connected to change settings"}
                      style={{
                        padding: "2px 8px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--bg-hover)",
                        fontSize: "var(--font-size-sm)",
                        opacity: connected ? 1 : 0.4,
                      }}
                    >
                      <Pencil size={12} />
                    </button>
                  </div>
                )}
              </div>
              {result && (
                <div
                  style={{
                    marginTop: "var(--space-xs)",
                    fontSize: 11,
                    color: result.success ? "var(--color-success)" : "var(--color-error)",
                  }}
                >
                  {result.success ? "Setting saved successfully" : `Error: ${result.error}`}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- Typed Config Fields ---

function ConfigFieldInputs({
  configKeys,
  driverInfo,
  configValues,
  setConfigValues,
}: {
  configKeys: string[];
  driverInfo: DriverInfo | undefined;
  configValues: Record<string, string>;
  setConfigValues: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}) {
  return (
    <>
      {configKeys.map((key) => {
        const schema =
          (driverInfo?.config_schema as Record<string, Record<string, unknown>>)?.[key] ?? {};
        const label = String(schema.label || key);
        const description = schema.description ? String(schema.description) : "";
        const fieldType = String(schema.type || "string");
        const values = schema.values as string[] | undefined;
        const isRequired = schema.required === true;
        const defaultVal = schema.default;
        // Build helpful placeholder from key name conventions
        const placeholder = key === "host" ? "192.168.1.100"
          : key === "port" ? "1-65535"
          : key === "username" ? "admin"
          : key === "password" ? "password"
          : key === "community" ? "public"
          : key === "baud_rate" || key === "baudrate" ? "9600"
          : defaultVal != null && defaultVal !== "" ? String(defaultVal)
          : label;

        return (
          <div key={key} style={{ marginBottom: "var(--space-sm)" }}>
            <label
              style={{
                display: "block",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-xs)",
              }}
            >
              {label}
              {isRequired && (
                <span style={{ color: "var(--error, #f44336)", marginLeft: 2 }}>*</span>
              )}
            </label>
            {fieldType === "boolean" ? (
              <button
                onClick={() =>
                  setConfigValues((v) => ({
                    ...v,
                    [key]: v[key] === "true" ? "false" : "true",
                  }))
                }
                style={{
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background:
                    configValues[key] === "true"
                      ? "var(--color-success-bg)"
                      : "var(--bg-hover)",
                  color:
                    configValues[key] === "true" ? "var(--color-success)" : "var(--text-secondary)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {configValues[key] === "true" ? "Yes" : "No"}
              </button>
            ) : values && values.length > 0 ? (
              <select
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                style={{ width: "100%" }}
              >
                <option value="">Select...</option>
                {values.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            ) : fieldType === "integer" || fieldType === "number" ? (
              <input
                type="number"
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            ) : fieldType === "password" ? (
              <input
                type="password"
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            ) : (
              <input
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            )}
            {description && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                {description}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

// --- Searchable Driver Dropdown ---

const CATEGORY_ORDER = ["projector", "display", "audio", "switcher", "camera", "lighting", "control", "utility", "other"];

function DriverSearchSelect({
  drivers,
  value,
  onChange,
}: {
  drivers: DriverInfo[];
  value: string;
  onChange: (driverId: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return drivers.filter(
      (d) =>
        !q ||
        (d.name || d.id).toLowerCase().includes(q) ||
        (d.manufacturer || "").toLowerCase().includes(q) ||
        (d.category || "").toLowerCase().includes(q)
    );
  }, [drivers, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, DriverInfo[]>();
    for (const d of filtered) {
      const cat = d.category || "other";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(d);
    }
    const sorted = [...map.entries()].sort(
      (a, b) => (CATEGORY_ORDER.indexOf(a[0]) === -1 ? 99 : CATEGORY_ORDER.indexOf(a[0]))
        - (CATEGORY_ORDER.indexOf(b[0]) === -1 ? 99 : CATEGORY_ORDER.indexOf(b[0]))
    );
    return sorted;
  }, [filtered]);

  const selected = drivers.find((d) => d.id === value);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <input
        value={open ? search : (selected ? (selected.name || selected.id) : "")}
        onChange={(e) => { setSearch(e.target.value); if (!open) setOpen(true); }}
        onFocus={() => { setOpen(true); setSearch(""); }}
        placeholder="Search drivers..."
        style={{ width: "100%" }}
      />
      {open && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            maxHeight: 260,
            overflow: "auto",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            zIndex: 10,
            boxShadow: "var(--shadow-md)",
          }}
        >
          {grouped.length === 0 && (
            <div style={{ padding: "var(--space-sm) var(--space-md)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              No drivers found
            </div>
          )}
          {grouped.map(([cat, items]) => (
            <div key={cat}>
              <div
                style={{
                  padding: "var(--space-xs) var(--space-md)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  background: "var(--bg-surface)",
                  position: "sticky",
                  top: 0,
                }}
              >
                {cat}
              </div>
              {items.map((d) => (
                <div
                  key={d.id}
                  onClick={() => { onChange(d.id); setOpen(false); setSearch(""); }}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                    background: d.id === value ? "var(--accent-dim)" : "transparent",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = d.id === value ? "var(--accent-dim)" : "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = d.id === value ? "var(--accent-dim)" : "transparent")}
                >
                  <span>{d.name || d.id}</span>
                  {d.manufacturer && (
                    <span style={{ color: "var(--text-muted)", fontSize: 11 }}>{d.manufacturer}</span>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Add Device Dialog ---

function AddDeviceDialog({
  onClose,
  prefill,
}: {
  onClose: () => void;
  prefill?: DeviceConfig;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const save = useProjectStore((s) => s.save);

  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [deviceId, setDeviceId] = useState(prefill ? "" : "");
  const [deviceName, setDeviceName] = useState(prefill?.name ? `${prefill.name} (Copy)` : "");
  const [selectedDriver, setSelectedDriver] = useState(prefill?.driver ?? "");
  const [configValues, setConfigValues] = useState<Record<string, string>>(() => {
    if (!prefill) return {};
    // Merge device.config with connection table overrides (host, port, etc.)
    const conn = useProjectStore.getState().project?.connections?.[prefill.id] ?? {};
    const merged = { ...prefill.config, ...conn };
    const vals: Record<string, string> = {};
    for (const [k, v] of Object.entries(merged)) {
      vals[k] = String(v ?? "");
    }
    return vals;
  });
  const [error, setError] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [setupDeviceId, setSetupDeviceId] = useState<string | null>(null);

  useEffect(() => {
    api.listDrivers().then(setDrivers).catch(console.error);
  }, []);

  const driverInfo = drivers.find((d) => d.id === selectedDriver);
  const configKeys = Object.keys((driverInfo?.config_schema ?? {}) as Record<string, unknown>);

  // Check if driver has setup settings
  const hasSetupSettings = useMemo(() => hasDriverSetupSettings(driverInfo), [driverInfo]);

  const handleAdd = async () => {
    if (!deviceId || !selectedDriver) {
      setError("Device ID and driver are required");
      return;
    }
    if (project?.devices.some((d) => d.id === deviceId)) {
      setError("A device with this ID already exists");
      return;
    }

    const config: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(configValues)) {
      if (val === "") continue;
      // Only coerce simple decimal numbers (not hex 0x1A, scientific 1e5, etc.)
      const isSimpleNumber = /^-?\d+(\.\d+)?$/.test(val);
      config[key] = isSimpleNumber ? Number(val) : val;
    }

    const newDevice: DeviceConfig = {
      id: deviceId,
      driver: selectedDriver,
      name: deviceName || deviceId,
      config,
    };

    update({
      devices: [...(project?.devices ?? []), newDevice],
    });

    save();

    // Show setup dialog if driver has setup settings
    if (hasSetupSettings) {
      setIsAdding(true);
      setSetupDeviceId(deviceId);
    } else {
      onClose();
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xl)",
          width: 480,
          maxHeight: "80vh",
          overflow: "auto",
          boxShadow: "var(--shadow-lg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>
          {prefill ? "Duplicate Device" : "Add Device"}
        </h3>

        {error && (
          <div
            style={{
              background: "var(--color-error-bg)",
              color: "var(--color-error)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-md)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {error}
          </div>
        )}

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Driver
          </label>
          <DriverSearchSelect
            drivers={drivers}
            value={selectedDriver}
            onChange={(newDriverId) => {
              setSelectedDriver(newDriverId);
              const newDriver = drivers.find((d) => d.id === newDriverId);
              const defaults = newDriver?.default_config ?? {};
              const prefilled: Record<string, string> = {};
              for (const [k, v] of Object.entries(defaults)) {
                if (v !== "" && v != null) prefilled[k] = String(v);
              }
              setConfigValues(prefilled);
            }}
          />
          {driverInfo?.help?.overview && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              {driverInfo.help.overview}
            </div>
          )}
          {driverInfo?.help?.setup && (
            <div style={{
              fontSize: 11,
              color: "var(--text-secondary)",
              marginTop: 4,
              padding: "var(--space-sm)",
              background: "var(--bg-base)",
              borderRadius: "var(--border-radius)",
              whiteSpace: "pre-line",
            }}>
              {driverInfo.help.setup}
            </div>
          )}
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Device ID
          </label>
          <input
            value={deviceId}
            onChange={(e) =>
              setDeviceId(e.target.value.replace(/[^a-z0-9_]/gi, "").toLowerCase())
            }
            placeholder="e.g., projector_room_1"
            style={{
              width: "100%",
              borderColor: deviceId && !isAdding && project?.devices.some((d) => d.id === deviceId)
                ? "var(--color-error, #ef4444)" : undefined,
            }}
          />
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3 }}>
            Lowercase letters, numbers, and underscores only.
            {deviceId && (
              <span style={{ marginLeft: 6 }}>
                Your ID: <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{deviceId}</code>
                {!isAdding && project?.devices.some((d) => d.id === deviceId) && (
                  <span style={{ color: "var(--color-error, #ef4444)", marginLeft: 6 }}>Already exists</span>
                )}
              </span>
            )}
          </div>
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Display Name
          </label>
          <input
            value={deviceName}
            onChange={(e) => setDeviceName(e.target.value)}
            placeholder="e.g., Main Projector"
            style={{ width: "100%" }}
          />
        </div>


        {configKeys.length > 0 && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-sm)",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              Connection Settings
            </div>
            <ConfigFieldInputs
              configKeys={configKeys}
              driverInfo={driverInfo}
              configValues={configValues}
              setConfigValues={setConfigValues}
            />
          </div>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-sm)",
            marginTop: "var(--space-lg)",
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleAdd}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "var(--text-on-accent)",
            }}
          >
            {prefill ? "Duplicate Device" : "Add Device"}
          </button>
        </div>
      </div>

      {setupDeviceId && driverInfo && (
        <DeviceSettingsSetupDialog
          deviceId={setupDeviceId}
          driverInfo={driverInfo}
          existingDeviceIds={(project?.devices ?? []).map((d) => d.id)}
          onClose={onClose}
        />
      )}
    </div>
  );
}

// --- Edit Device Dialog ---

function EditDeviceDialog({
  device,
  onClose,
  onSaved,
}: {
  device: DeviceConfig;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [deviceName, setDeviceName] = useState(device.name);
  const [selectedDriver, setSelectedDriver] = useState(device.driver);
  const [configValues, setConfigValues] = useState<Record<string, string>>(() => {
    // Merge device.config with connection table overrides (host, port, etc.)
    const conn = useProjectStore.getState().project?.connections?.[device.id] ?? {};
    const merged = { ...device.config, ...conn };
    const vals: Record<string, string> = {};
    for (const [k, v] of Object.entries(merged)) {
      vals[k] = String(v ?? "");
    }
    return vals;
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.listDrivers().then(setDrivers).catch(console.error);
  }, []);

  const driverInfo = drivers.find((d) => d.id === selectedDriver);
  // Show config fields from driver schema if available, otherwise from the device's existing config
  const schemaKeys = Object.keys((driverInfo?.config_schema ?? {}) as Record<string, unknown>);
  const existingKeys = Object.keys(configValues);
  const configKeys = schemaKeys.length > 0 ? schemaKeys : existingKeys;

  // When driver changes, pre-fill config from driver's default_config
  const handleDriverChange = (newDriver: string) => {
    setSelectedDriver(newDriver);
    if (newDriver !== device.driver) {
      const newDriverInfo = drivers.find((d) => d.id === newDriver);
      const defaults = newDriverInfo?.default_config ?? {};
      const prefilled: Record<string, string> = {};
      for (const [k, v] of Object.entries(defaults)) {
        if (v !== "" && v != null) prefilled[k] = String(v);
      }
      setConfigValues(prefilled);
    }
  };

  const handleSave = async () => {
    if (!selectedDriver) {
      setError("Driver is required");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const config: Record<string, unknown> = {};
      for (const [key, val] of Object.entries(configValues)) {
        if (val === "") continue;
        const num = Number(val);
        config[key] = isNaN(num) ? val : num;
      }

      const updateData: Record<string, unknown> = {
        name: deviceName || device.id,
        driver: selectedDriver,
        config,
      };

      await api.updateDevice(device.id, updateData as {
        name?: string;
        driver?: string;
        config?: Record<string, unknown>;
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xl)",
          width: 480,
          maxHeight: "80vh",
          overflow: "auto",
          boxShadow: "var(--shadow-lg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>
          Edit Device
        </h3>

        {error && (
          <div
            style={{
              background: "var(--color-error-bg)",
              color: "var(--color-error)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-md)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {error}
          </div>
        )}

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Device ID
          </label>
          <input value={device.id} disabled style={{ width: "100%", opacity: 0.6 }} />
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginTop: "var(--space-xs)",
            }}
          >
            Device ID cannot be changed after creation
          </div>
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Driver
          </label>
          <DriverSearchSelect
            drivers={
              // Include current driver if not in the loaded list
              selectedDriver && !drivers.some(d => d.id === selectedDriver)
                ? [...drivers, { id: selectedDriver, name: selectedDriver + (drivers.length === 0 ? " (loading...)" : " (not installed)"), manufacturer: "", category: "other", commands: {}, config_schema: {} }]
                : drivers
            }
            value={selectedDriver}
            onChange={handleDriverChange}
          />
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Display Name
          </label>
          <input
            value={deviceName}
            onChange={(e) => setDeviceName(e.target.value)}
            placeholder="e.g., Main Projector"
            style={{ width: "100%" }}
          />
        </div>


        {configKeys.length > 0 && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-sm)",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              Connection Settings
            </div>
            <ConfigFieldInputs
              configKeys={configKeys}
              driverInfo={driverInfo}
              configValues={configValues}
              setConfigValues={setConfigValues}
            />
          </div>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-sm)",
            marginTop: "var(--space-lg)",
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "var(--text-on-accent)",
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Device Log ---

type DeviceLogTab = "protocol" | "state";

function DeviceLog({ deviceId }: { deviceId: string }) {
  const [tab, setTab] = useState<DeviceLogTab>("protocol");

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <h3
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
            margin: 0,
          }}
        >
          Device Log
        </h3>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setTab("protocol")}
          style={{
            padding: "2px 8px",
            borderRadius: "var(--border-radius)",
            background: tab === "protocol" ? "var(--accent)" : "var(--bg-hover)",
            color: tab === "protocol" ? "#fff" : "var(--text-secondary)",
            fontSize: 11,
            fontWeight: tab === "protocol" ? 600 : 400,
            border: "none",
            cursor: "pointer",
          }}
        >
          Protocol
        </button>
        <button
          onClick={() => setTab("state")}
          style={{
            padding: "2px 8px",
            borderRadius: "var(--border-radius)",
            background: tab === "state" ? "var(--accent)" : "var(--bg-hover)",
            color: tab === "state" ? "#fff" : "var(--text-secondary)",
            fontSize: 11,
            fontWeight: tab === "state" ? 600 : 400,
            border: "none",
            cursor: "pointer",
          }}
        >
          State Changes
        </button>
      </div>
      {tab === "protocol" ? (
        <DeviceProtocolLog deviceId={deviceId} />
      ) : (
        <DeviceStateLog deviceId={deviceId} />
      )}
    </div>
  );
}

function DeviceProtocolLog({ deviceId }: { deviceId: string }) {
  const logEntries = useLogStore((s) => s.logEntries);
  const listRef = useRef<HTMLDivElement>(null);

  const deviceLogs = logEntries.filter(
    (e) => e.message.toLowerCase().includes(deviceId.toLowerCase())
      || (e.category === "device" && e.source?.toLowerCase().includes(deviceId.toLowerCase()))
  );
  const recent = deviceLogs.slice(-50);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [recent.length]);

  const LEVEL_COLORS: Record<string, string> = {
    DEBUG: "var(--text-muted)",
    INFO: "var(--accent)",
    WARNING: "#f59e0b",
    ERROR: "#ef4444",
  };

  return (
    <div
      ref={listRef}
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        overflow: "auto",
        maxHeight: 250,
        fontFamily: "var(--font-mono)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {recent.length === 0 ? (
        <div
          style={{
            padding: "var(--space-lg)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            textAlign: "center",
            fontFamily: "var(--font-primary, inherit)",
          }}
        >
          No log entries for this device yet.
        </div>
      ) : (
        recent.map((e, i) => {
          const time = new Date(e.timestamp * 1000);
          return (
            <div
              key={i}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                display: "flex",
                gap: "var(--space-sm)",
                alignItems: "baseline",
              }}
            >
              <span style={{ color: "var(--text-muted)", fontSize: 11, flexShrink: 0 }}>
                {time.toLocaleTimeString(undefined, { hour12: false })}
              </span>
              <span
                style={{
                  color: LEVEL_COLORS[e.level] ?? "var(--text-primary)",
                  fontWeight: e.level === "ERROR" ? 600 : 400,
                  fontSize: 11,
                  flexShrink: 0,
                  width: 40,
                }}
              >
                {e.level}
              </span>
              <span style={{ color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {String(e.message)}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}

function DeviceStateLog({ deviceId }: { deviceId: string }) {
  const liveState = useConnectionStore((s) => s.liveState);
  const prevStateRef = useRef<Record<string, unknown>>({});
  const [entries, setEntries] = useState<
    { key: string; oldValue: unknown; newValue: unknown; timestamp: number }[]
  >([]);
  const listRef = useRef<HTMLDivElement>(null);

  const devicePrefix = `device.${deviceId}.`;

  // Track live state changes for this device
  useEffect(() => {
    const prev = prevStateRef.current;
    const newEntries: typeof entries = [];
    for (const [key, value] of Object.entries(liveState)) {
      if (!key.startsWith(devicePrefix)) continue;
      if (prev[key] !== value && prev[key] !== undefined) {
        newEntries.push({
          key: key.slice(devicePrefix.length),
          oldValue: prev[key],
          newValue: value,
          timestamp: Date.now() / 1000,
        });
      }
    }
    prevStateRef.current = { ...liveState };
    if (newEntries.length > 0) {
      setEntries((prev) => [...prev, ...newEntries].slice(-100));
    }
  }, [liveState, devicePrefix]);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [entries.length]);

  const formatValue = (v: unknown) => {
    if (v === null || v === undefined) return "null";
    return String(v);
  };

  return (
    <div
      ref={listRef}
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        overflow: "auto",
        maxHeight: 250,
        fontFamily: "var(--font-mono)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {entries.length === 0 ? (
        <div
          style={{
            padding: "var(--space-lg)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            textAlign: "center",
            fontFamily: "var(--font-primary, inherit)",
          }}
        >
          No state changes yet. Interact with the device to see live updates.
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-color)", position: "sticky", top: 0, background: "var(--bg-surface)" }}>
              <th style={devLogThStyle}>Time</th>
              <th style={devLogThStyle}>Property</th>
              <th style={devLogThStyle}>Old</th>
              <th style={devLogThStyle}>New</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => {
              const time = new Date(e.timestamp * 1000);
              return (
                <tr key={i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td style={devLogTdStyle}>
                    {time.toLocaleTimeString(undefined, { hour12: false })}
                  </td>
                  <td style={{ ...devLogTdStyle, color: "var(--accent)" }}>{e.key}</td>
                  <td style={{ ...devLogTdStyle, color: "var(--text-muted)" }}>
                    {formatValue(e.oldValue)}
                  </td>
                  <td style={devLogTdStyle}>{formatValue(e.newValue)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ==========================================================================
// Device Groups Panel
// ==========================================================================

function DeviceGroupsPanel() {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const updateWithUndo = useProjectStore((s) => s.updateWithUndo);
  const save = useProjectStore((s) => s.save);

  const groups = project?.device_groups ?? [];
  const devices = project?.devices ?? [];

  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const selectedGroup = groups.find((g) => g.id === selectedGroupId);

  // Auto-generate ID from display name
  const autoGroupId = newGroupName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

  const handleCreate = () => {
    const id = autoGroupId;
    if (!id || groups.some((g) => g.id === id)) return;
    const newGroup: DeviceGroup = {
      id,
      name: newGroupName.trim(),
      device_ids: [],
    };
    update({ device_groups: [...groups, newGroup] });
    setNewGroupName("");
    setShowCreate(false);
    setSelectedGroupId(id);
    setTimeout(() => save(), 100);
  };

  const handleDelete = (groupId: string) => {
    const group = groups.find((g) => g.id === groupId);
    updateWithUndo({ device_groups: groups.filter((g) => g.id !== groupId) }, `Delete group "${group?.name || groupId}"`);
    if (selectedGroupId === groupId) setSelectedGroupId(null);
    setDeleteConfirm(null);
    setTimeout(() => save(), 100);
  };

  const handleUpdateGroup = (groupId: string, patch: Partial<DeviceGroup>) => {
    update({
      device_groups: groups.map((g) =>
        g.id === groupId ? { ...g, ...patch } : g
      ),
    });
    setTimeout(() => save(), 500);
  };

  const toggleDevice = (groupId: string, deviceId: string) => {
    const group = groups.find((g) => g.id === groupId);
    if (!group) return;
    const ids = group.device_ids.includes(deviceId)
      ? group.device_ids.filter((d) => d !== deviceId)
      : [...group.device_ids, deviceId];
    handleUpdateGroup(groupId, { device_ids: ids });
  };

  return (
    <div style={{ display: "flex", height: "100%" }}>
      {/* Left: group list */}
      <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid var(--border-color)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "var(--space-sm) var(--space-md)", borderBottom: "1px solid var(--border-color)" }}>
          <button
            onClick={() => setShowCreate((v) => !v)}
            style={{
              display: "flex", alignItems: "center", gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-sm)",
              background: "var(--accent)", color: "var(--text-on-accent)",
              border: "none", borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-sm)", cursor: "pointer", fontWeight: 500,
            }}
          >
            <Plus size={14} /> New Group
          </button>
        </div>

        {showCreate && (
          <div style={{ padding: "var(--space-sm) var(--space-md)", borderBottom: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
            <input
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              placeholder="Group name (e.g., All Projectors)"
              style={{ fontSize: "var(--font-size-sm)", padding: "var(--space-xs) var(--space-sm)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", background: "var(--bg-input)", color: "var(--text-primary)" }}
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />
            {autoGroupId && (
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                ID: <code style={{ fontFamily: "var(--font-mono)" }}>{autoGroupId}</code>
                {groups.some((g) => g.id === autoGroupId) && (
                  <span style={{ color: "var(--color-error, #ef4444)", marginLeft: 6 }}>Already exists</span>
                )}
              </div>
            )}
            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <button onClick={handleCreate} disabled={!autoGroupId || groups.some((g) => g.id === autoGroupId)} style={{ padding: "var(--space-xs) var(--space-sm)", background: "var(--accent)", color: "var(--text-on-accent)", border: "none", borderRadius: "var(--border-radius)", fontSize: "var(--font-size-sm)", cursor: "pointer", opacity: !autoGroupId || groups.some((g) => g.id === autoGroupId) ? 0.5 : 1 }}>Create</button>
              <button onClick={() => { setShowCreate(false); setNewGroupName(""); }} style={{ padding: "var(--space-xs) var(--space-sm)", background: "var(--bg-hover)", color: "var(--text-secondary)", border: "none", borderRadius: "var(--border-radius)", fontSize: "var(--font-size-sm)", cursor: "pointer" }}>Cancel</button>
            </div>
          </div>
        )}

        <div style={{ flex: 1, overflow: "auto" }}>
          {groups.length === 0 ? (
            <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
              <Layers size={32} style={{ opacity: 0.3, marginBottom: "var(--space-sm)" }} />
              <div style={{ fontWeight: 500, color: "var(--text-secondary)", marginBottom: "var(--space-sm)" }}>No groups yet</div>
              <div>
                Groups let you control multiple devices at once.
                Create a "Projectors" group to power them all on with one macro step.
              </div>
              <button
                onClick={() => setShowCreate(true)}
                style={{
                  marginTop: "var(--space-md)", padding: "var(--space-xs) var(--space-md)",
                  background: "var(--accent)", color: "var(--text-on-accent)",
                  border: "none", borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-sm)", cursor: "pointer", fontWeight: 500,
                }}
              >
                Create your first group
              </button>
            </div>
          ) : (
            groups.map((g) => (
              <div
                key={g.id}
                onClick={() => setSelectedGroupId(g.id)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "var(--space-sm) var(--space-md)",
                  cursor: "pointer",
                  background: selectedGroupId === g.id ? "var(--bg-hover)" : "transparent",
                  borderBottom: "1px solid var(--border-color)",
                }}
                onMouseEnter={(e) => { if (selectedGroupId !== g.id) (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; }}
                onMouseLeave={(e) => { if (selectedGroupId !== g.id) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
              >
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                    <Layers size={14} style={{ color: "var(--accent)" }} />
                    <span style={{ fontWeight: selectedGroupId === g.id ? 600 : 400, color: "var(--text-primary)" }}>{g.name}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 1 }}>
                    {g.device_ids.length} device{g.device_ids.length !== 1 ? "s" : ""}
                  </div>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); setDeleteConfirm(g.id); }}
                  style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 2 }}
                  title="Delete group"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Right: group detail */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-lg)" }}>
        {selectedGroup ? (
          <div>
            <div style={{ marginBottom: "var(--space-lg)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: "var(--space-sm)" }}>
                <Layers size={20} style={{ color: "var(--accent)" }} />
                <input
                  value={selectedGroup.name}
                  onChange={(e) => handleUpdateGroup(selectedGroup.id, { name: e.target.value })}
                  style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, background: "transparent", border: "none", color: "var(--text-primary)", outline: "none", padding: 0 }}
                />
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                ID: <code style={{ background: "var(--bg-hover)", padding: "1px 4px", borderRadius: 3 }}>{selectedGroup.id}</code>
              </div>
            </div>

            <h3 style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "var(--space-sm)" }}>
              Devices ({selectedGroup.device_ids.length})
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {devices.map((dev) => {
                const isMember = selectedGroup.device_ids.includes(dev.id);
                return (
                  <label
                    key={dev.id}
                    style={{
                      display: "flex", alignItems: "center", gap: "var(--space-sm)",
                      padding: "var(--space-xs) var(--space-sm)",
                      borderRadius: "var(--border-radius)",
                      cursor: "pointer",
                      background: isMember ? "rgba(33,150,243,0.08)" : "transparent",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={isMember}
                      onChange={() => toggleDevice(selectedGroup.id, dev.id)}
                    />
                    <span style={{ fontWeight: isMember ? 500 : 400, color: "var(--text-primary)" }}>{dev.name}</span>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>({dev.id})</span>
                  </label>
                );
              })}
              {devices.length === 0 && (
                <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", fontStyle: "italic" }}>
                  No devices in the project yet.
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", gap: "var(--space-sm)", textAlign: "center" }}>
            <Layers size={32} style={{ opacity: 0.3 }} />
            <div style={{ fontSize: "var(--font-size-md)" }}>
              {groups.length === 0 ? "Create your first device group" : "Select a group to manage its devices"}
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, lineHeight: 1.5 }}>
              Device groups let you target multiple devices with a single macro step.
              Create a group, add devices to it, then use "Group Command" in your macros.
            </div>
          </div>
        )}
      </div>

      {deleteConfirm && (
        <ConfirmDialog
          title="Delete Group"
          message={`Delete group "${groups.find((g) => g.id === deleteConfirm)?.name ?? deleteConfirm}"? This will not delete any devices.`}
          confirmLabel="Delete"
          onConfirm={() => handleDelete(deleteConfirm)}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </div>
  );
}

const devLogThStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--text-secondary)",
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};

const devLogTdStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 150,
};
