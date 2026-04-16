import { useState, useEffect, useMemo } from "react";
import { Zap, Cpu } from "lucide-react";
import { CopyButton } from "../../components/shared/CopyButton";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { listDrivers, getScriptReferences } from "../../api/restClient";
import type { DriverInfo, ScriptReference } from "../../api/types";
import { HelpBanner, UsageRow, buildStateUsageMap, typeBadgeStyle, sectionTitle } from "./variablesShared";

export function DeviceStatesSubTab() {
  const project = useProjectStore((s) => s.project);
  const liveState = useConnectionStore((s) => s.liveState);

  const devices = project?.devices ?? [];
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const [selectedProp, setSelectedProp] = useState<string | null>(null);

  // Driver registry (from /api/drivers — includes project-level community drivers)
  const [driverRegistry, setDriverRegistry] = useState<DriverInfo[]>([]);

  // Load driver registry once
  useEffect(() => {
    let cancelled = false;
    listDrivers()
      .then((drivers) => { if (!cancelled) setDriverRegistry(drivers); })
      .catch(console.error);
    return () => { cancelled = true; };
  }, []);

  // Script references (fetched once)
  const [scriptRefs, setScriptRefs] = useState<ScriptReference[]>([]);
  useEffect(() => {
    let cancelled = false;
    getScriptReferences()
      .then((refs) => { if (!cancelled) setScriptRefs(refs); })
      .catch(console.error);
    return () => { cancelled = true; };
  }, []);

  // Cross-reference map for all state keys
  const usageMap = useMemo(() => {
    if (!project) return new Map<string, import("./variablesShared").VariableUsage[]>();
    return buildStateUsageMap(project, scriptRefs);
  }, [project, scriptRefs]);

  // Group devices by device_groups
  const projGroups = project?.device_groups ?? [];
  const deviceGroups = useMemo(() => {
    const deviceToGroup = new Map<string, string>();
    for (const g of projGroups) {
      for (const did of g.device_ids) {
        if (!deviceToGroup.has(did)) deviceToGroup.set(did, g.name);
      }
    }
    const groups = new Map<string, typeof devices>();
    for (const d of devices) {
      const g = deviceToGroup.get(d.id) || "Ungrouped";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(d);
    }
    return groups;
  }, [devices, projGroups]);

  // Build state entries from driver-declared state_variables + live state
  const stateEntries = useMemo(() => {
    if (!selectedDeviceId) return [];
    const selectedDevice = devices.find((d) => d.id === selectedDeviceId);
    if (!selectedDevice) return [];

    const prefix = `device.${selectedDeviceId}.`;
    const seen = new Set<string>();
    const entries: { prop: string; key: string; value: unknown; meta: { type?: string; label?: string; values?: string[] } | null }[] = [];

    // Get state_variables from driver registry
    const driverDef = driverRegistry.find((d) => d.id === selectedDevice.driver);
    const declaredVars = (driverDef?.state_variables ?? {}) as Record<string, { type?: string; label?: string; values?: string[] }>;

    // 1. Start with driver-declared state variables (always available once loaded)
    for (const [prop, meta] of Object.entries(declaredVars)) {
      const key = `${prefix}${prop}`;
      seen.add(key);
      entries.push({ prop, key, value: liveState[key], meta });
    }

    // 2. Add any live state keys not declared by the driver (e.g., connected, enabled, name)
    for (const [k, v] of Object.entries(liveState)) {
      if (k.startsWith(prefix) && !seen.has(k)) {
        const prop = k.slice(prefix.length);
        entries.push({ prop, key: k, value: v, meta: null });
      }
    }

    entries.sort((a, b) => a.prop.localeCompare(b.prop));
    return entries;
  }, [selectedDeviceId, devices, driverRegistry, liveState]);

  const selectedDevice = devices.find((d) => d.id === selectedDeviceId);
  const selectedPropUsages = selectedProp ? usageMap.get(selectedProp) ?? [] : [];

  return (
    <div style={{ display: "flex", height: "100%" }}>
      {/* Left: device list */}
      <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid var(--border-color)", display: "flex", flexDirection: "column" }}>
        <HelpBanner storageKey="openavc-help-device-states">
          Device states are live values reported by your hardware — power status,
          input selection, volume levels, etc. These update automatically. You can
          bind UI elements directly to these values, use them in macro triggers,
          or reference them in scripts. Use the copy button to grab a state key.
        </HelpBanner>

        <div style={{ flex: 1, overflow: "auto" }}>
          {devices.length === 0 ? (
            <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
              No devices configured yet.
              <br /><br />
              Add devices in the <strong>Devices</strong> tab to see their live state properties here.
            </div>
          ) : (
            Array.from(deviceGroups.entries()).map(([group, groupDevices]) => (
              <div key={group}>
                {deviceGroups.size > 1 && (
                  <div style={{ padding: "var(--space-sm) var(--space-md) 2px", fontSize: 11, color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    {group}
                  </div>
                )}
                {groupDevices.map((d) => {
                  const isConnected = liveState[`device.${d.id}.connected`] === true;
                  return (
                    <div
                      key={d.id}
                      onClick={() => { setSelectedDeviceId(d.id); setSelectedProp(null); }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "var(--space-sm)",
                        padding: "var(--space-sm) var(--space-md)",
                        cursor: "pointer",
                        background: selectedDeviceId === d.id ? "var(--bg-hover)" : "transparent",
                        borderBottom: "1px solid var(--border-color)",
                      }}
                      onMouseEnter={(e) => { if (selectedDeviceId !== d.id) e.currentTarget.style.background = "var(--bg-hover)"; }}
                      onMouseLeave={(e) => { if (selectedDeviceId !== d.id) e.currentTarget.style.background = "transparent"; }}
                    >
                      <div style={{
                        width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                        background: isConnected ? "var(--color-success)" : "var(--text-muted)",
                      }} />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500, color: "var(--text-primary)" }}>{d.name}</div>
                        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{d.driver}</div>
                      </div>
                      <Cpu size={14} style={{ color: "var(--text-muted)", flexShrink: 0, opacity: 0.5 }} />
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Right: state properties */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {selectedDevice ? (
          <div style={{ padding: "var(--space-lg)" }}>
            <div style={{ marginBottom: "var(--space-lg)" }}>
              <div style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, color: "var(--text-primary)" }}>
                {selectedDevice.name}
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                {selectedDevice.driver} &middot; device.{selectedDevice.id}.*
              </div>
            </div>

            {stateEntries.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", fontStyle: "italic" }}>
                This driver does not declare any state properties.
              </div>
            ) : (
              <>
              {/* Column header */}
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", padding: "0 var(--space-md) 4px", fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                <div style={{ flex: 1 }}>Property</div>
                <div style={{ width: 16, flexShrink: 0, textAlign: "center" }} title="Used in macros, UI, or scripts" />
                <div style={{ flexShrink: 0, minWidth: 80, textAlign: "right" }}>Live Value</div>
                <div style={{ width: 22, flexShrink: 0 }} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {stateEntries.map((entry) => {
                  const meta = entry.meta;
                  const isSelected = selectedProp === entry.key;
                  const usageCount = usageMap.get(entry.key)?.length ?? 0;
                  return (
                    <div key={entry.key}>
                      <div
                        onClick={() => setSelectedProp(isSelected ? null : entry.key)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "var(--space-sm)",
                          padding: "var(--space-sm) var(--space-md)",
                          borderRadius: "var(--border-radius)",
                          background: isSelected ? "var(--bg-hover)" : "var(--bg-surface)",
                          border: "1px solid " + (isSelected ? "var(--accent)" : "var(--border-color)"),
                          cursor: "pointer",
                          transition: "background 0.1s",
                        }}
                        onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)"; }}
                        onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-surface)"; }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                            <code style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)", fontWeight: 500, color: "var(--text-primary)" }}>
                              {entry.prop}
                            </code>
                            {meta?.type && <span style={typeBadgeStyle}>{meta.type}</span>}
                            {meta?.label && (
                              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{meta.label}</span>
                            )}
                          </div>
                          <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                            {entry.key}
                          </div>
                        </div>
                        {/* Usage indicator */}
                        <div style={{ width: 16, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }} title={usageCount > 0 ? `Used in ${usageCount} place(s)` : "Not used yet"}>
                          {usageCount > 0 ? (
                            <Zap size={12} style={{ color: "#f59e0b" }} />
                          ) : (
                            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--border-color)" }} />
                          )}
                        </div>
                        {/* Live value */}
                        <span style={{ fontSize: "var(--font-size-sm)", color: entry.value !== undefined ? "var(--text-secondary)" : "var(--text-muted)", fontFamily: "var(--font-mono)", flexShrink: 0, fontStyle: entry.value !== undefined ? "normal" : "italic", minWidth: 80, textAlign: "right" }}>
                          {entry.value !== undefined ? String(entry.value) : "—"}
                        </span>
                        <CopyButton value={entry.key} size={14} title="Copy state key" />
                      </div>

                      {/* Expanded detail: metadata + where used */}
                      {isSelected && (
                        <div style={{ padding: "var(--space-sm) var(--space-md) var(--space-md) var(--space-lg)", borderLeft: "2px solid var(--accent)", marginLeft: "var(--space-md)" }}>
                          {meta?.label && (
                            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                              {meta.label}
                            </div>
                          )}
                          {meta?.values && (
                            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
                              Possible values: {meta.values.join(", ")}
                            </div>
                          )}

                          <h4 style={{ ...sectionTitle, fontSize: 11, marginBottom: "var(--space-sm)" }}>
                            Where Used ({selectedPropUsages.length})
                          </h4>
                          {selectedPropUsages.length === 0 ? (
                            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", fontStyle: "italic" }}>
                              This property isn&apos;t used anywhere yet. You can reference it in
                              macro triggers, UI bindings, or scripts.
                            </div>
                          ) : (
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              {selectedPropUsages.map((u, i) => (
                                <UsageRow key={i} usage={u} />
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              </>
            )}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", gap: "var(--space-sm)", padding: "var(--space-xl)", textAlign: "center" }}>
            <Cpu size={32} style={{ opacity: 0.3 }} />
            <div style={{ fontSize: "var(--font-size-md)" }}>Select a device</div>
            <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 360, lineHeight: 1.5 }}>
              Choose a device from the list to browse its live state properties.
              You can copy state keys for use in macros, UI bindings, and scripts.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
