/**
 * Searchable state key picker with grouped dropdown, live values, and inline variable creation.
 * Used in macro editors, UI Builder binding editors, and anywhere a state key is selected.
 */
import { useState, useRef, useEffect, useMemo } from "react";
import { ChevronDown, Plus } from "lucide-react";
import type { VariableConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { CopyButton } from "./CopyButton";
import { showError } from "../../store/toastStore";

interface VariableKeyPickerProps {
  value: string;
  onChange: (key: string) => void;
  /** Show device state keys in addition to project variables */
  showDeviceState?: boolean;
  /** Placeholder text */
  placeholder?: string;
  /** Style override for the outer container */
  style?: React.CSSProperties;
}

interface KeyEntry {
  key: string;
  label: string;
  type?: string;
  group: string;
  groupDesc: string;
  deviceName?: string;
}

export function VariableKeyPicker({
  value,
  onChange,
  showDeviceState = true,
  placeholder = "Select state key...",
  style,
}: VariableKeyPickerProps) {
  const project = useProjectStore((s) => s.project);
  const storeUpdate = useProjectStore((s) => s.update);
  const liveState = useConnectionStore((s) => s.liveState);

  const variables = project?.variables ?? [];
  const devices = project?.devices ?? [];
  const pages = project?.ui?.pages ?? [];

  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newId, setNewId] = useState("");
  const [newType, setNewType] = useState("string");
  const [newLabel, setNewLabel] = useState("");
  const [newDefault, setNewDefault] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch("");
        setShowCreate(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  // Focus search when opening
  useEffect(() => {
    if (open && searchRef.current) {
      searchRef.current.focus();
    }
  }, [open]);

  // Build grouped entries
  const allEntries = useMemo((): KeyEntry[] => {
    const entries: KeyEntry[] = [];

    // Project Variables
    for (const v of variables) {
      entries.push({
        key: `var.${v.id}`,
        label: v.label || v.id,
        type: v.type,
        group: "variables",
        groupDesc: "Values you define for your program logic",
      });
    }

    // Live state keys — group by prefix
    if (showDeviceState) {
      // Build device name lookup from project
      const deviceNames: Record<string, string> = {};
      for (const d of devices) {
        deviceNames[d.id] = d.name;
      }

      // Build page/element lookup for UI keys
      const uiElements = new Set<string>();
      const pageNames: Record<string, string> = {};
      for (const page of pages) {
        for (const el of page.elements ?? []) {
          uiElements.add(el.id);
          pageNames[el.id] = page.name;
        }
      }

      for (const k of Object.keys(liveState)) {
        if (k.startsWith("device.")) {
          const parts = k.split(".");
          const deviceId = parts[1] ?? "";
          entries.push({
            key: k,
            label: parts.slice(2).join("."),
            group: `device:${deviceId}`,
            groupDesc: "Live hardware state reported by this device",
            deviceName: deviceNames[deviceId] || deviceId,
          });
        } else if (k.startsWith("system.")) {
          entries.push({
            key: k,
            label: k.slice(7),
            group: "system",
            groupDesc: "System-level values (uptime, status)",
          });
        } else if (k.startsWith("plugin.")) {
          const parts = k.split(".");
          const pluginId = parts[1] ?? "";
          entries.push({
            key: k,
            label: parts.slice(2).join("."),
            group: `plugin:${pluginId}`,
            groupDesc: "State from a running plugin",
            deviceName: pluginId,
          });
        } else if (k.startsWith("ui.")) {
          const parts = k.split(".");
          const elId = parts[1] ?? "";
          entries.push({
            key: k,
            label: parts.slice(1).join("."),
            group: uiElements.has(elId) ? `ui:${elId}` : "ui",
            groupDesc: "Override element appearance from macros or scripts",
            deviceName: pageNames[elId] || "",
          });
        }
      }
    }

    return entries;
  }, [variables, devices, pages, liveState, showDeviceState]);

  // Filter entries by search
  const filteredEntries = useMemo(() => {
    if (!search) return allEntries;
    const q = search.toLowerCase();
    return allEntries.filter(
      (e) =>
        e.key.toLowerCase().includes(q) ||
        e.label.toLowerCase().includes(q) ||
        (e.deviceName && e.deviceName.toLowerCase().includes(q)),
    );
  }, [allEntries, search]);

  // Group filtered entries
  const groups = useMemo(() => {
    const map = new Map<string, { label: string; desc: string; entries: KeyEntry[] }>();
    for (const e of filteredEntries) {
      if (!map.has(e.group)) {
        let label = "Project Variables";
        if (e.group.startsWith("device:")) {
          label = `Device: ${e.deviceName}`;
        } else if (e.group === "system") {
          label = "System";
        } else if (e.group.startsWith("ui:")) {
          label = `UI: ${e.deviceName}`;
        }
        map.set(e.group, { label, desc: e.groupDesc, entries: [] });
      }
      map.get(e.group)!.entries.push(e);
    }
    return map;
  }, [filteredEntries]);

  // Display text for collapsed state
  const selectedEntry = allEntries.find((e) => e.key === value);
  const displayText = selectedEntry
    ? selectedEntry.key
    : value || placeholder;
  const liveValue = value ? liveState[value] : undefined;

  const handleSelect = (key: string) => {
    onChange(key);
    setOpen(false);
    setSearch("");
    setShowCreate(false);
  };

  const handleCreateVariable = () => {
    const id = newId.trim().replace(/[^a-zA-Z0-9_]/g, "_");
    if (!id) return;
    if (variables.some((v) => v.id === id)) {
      showError(`Variable "${id}" already exists.`);
      return;
    }
    let defVal: unknown = newDefault;
    if (newType === "boolean") defVal = newDefault === "true";
    else if (newType === "number") defVal = Number(newDefault) || 0;

    const newVar: VariableConfig = {
      id,
      type: newType,
      default: defVal,
      label: newLabel.trim() || id,
    };
    storeUpdate({ variables: [...variables, newVar] });
    onChange(`var.${id}`);
    setNewId("");
    setNewType("string");
    setNewLabel("");
    setNewDefault("");
    setShowCreate(false);
    setOpen(false);
    setSearch("");
    setTimeout(() => useProjectStore.getState().save(), 100);
  };

  const hasLiveData = Object.keys(liveState).length > 0;

  return (
    <div ref={containerRef} style={{ position: "relative", ...style }}>
      {/* Collapsed trigger button */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          ...triggerStyle,
          color: value ? "var(--text-primary)" : "var(--text-muted)",
        }}
      >
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left" }}>
          {displayText}
          {liveValue !== undefined && (
            <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>
              = {String(liveValue)}
            </span>
          )}
        </span>
        <ChevronDown size={14} style={{ flexShrink: 0, opacity: 0.5 }} />
      </button>

      {/* Dropdown panel */}
      {open && (
        <div style={dropdownStyle}>
          {/* Search input */}
          <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border-color)" }}>
            <input
              ref={searchRef}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search state keys..."
              style={searchInputStyle}
            />
          </div>

          {/* Scrollable list */}
          <div style={{ maxHeight: 280, overflowY: "auto" }}>
            {!hasLiveData && showDeviceState && (
              <div style={emptyHintStyle}>
                Start the system to see live device state values.
              </div>
            )}

            {filteredEntries.length === 0 && search && (
              <div style={emptyHintStyle}>
                No keys matching &ldquo;{search}&rdquo;
              </div>
            )}

            {Array.from(groups.entries()).map(([groupId, group]) => (
              <div key={groupId}>
                <div style={groupHeaderStyle}>
                  <span style={{ fontWeight: 600 }}>{group.label}</span>
                  <span style={{ fontWeight: 400, fontStyle: "italic", marginLeft: 6 }}>
                    {group.desc}
                  </span>
                </div>
                {group.entries.map((entry) => (
                  <div
                    key={entry.key}
                    onClick={() => handleSelect(entry.key)}
                    style={{
                      ...rowStyle,
                      background: entry.key === value ? "var(--bg-hover)" : undefined,
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background =
                        entry.key === value ? "var(--bg-hover)" : "transparent")
                    }
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <span
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontSize: 12,
                            color: "var(--text-primary)",
                          }}
                        >
                          {entry.label}
                        </span>
                        {entry.type && (
                          <span style={typeBadgeStyle}>{entry.type}</span>
                        )}
                      </div>
                      {entry.group !== "variables" && (
                        <div
                          style={{
                            fontSize: 10,
                            color: "var(--text-muted)",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {entry.key}
                        </div>
                      )}
                    </div>
                    <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0, marginRight: 4 }}>
                      {liveState[entry.key] !== undefined ? String(liveState[entry.key]) : ""}
                    </span>
                    <CopyButton value={entry.key} title="Copy state key" />
                  </div>
                ))}
              </div>
            ))}

            {/* New Variable option */}
            {!showCreate && (
              <div
                onClick={() => setShowCreate(true)}
                style={{ ...rowStyle, color: "var(--accent)", gap: 4, borderTop: "1px solid var(--border-color)" }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <Plus size={14} />
                <span>New Variable...</span>
              </div>
            )}
          </div>

          {/* Inline create form */}
          {showCreate && (
            <div style={createFormStyle}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)" }}>
                Create New Variable
              </div>
              <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <div style={{ flex: 1 }}>
                  <label style={miniLabel}>ID</label>
                  <input
                    style={fieldStyle}
                    value={newId}
                    onChange={(e) => setNewId(e.target.value)}
                    placeholder="e.g. room_active"
                    autoFocus
                    onKeyDown={(e) => e.key === "Enter" && handleCreateVariable()}
                  />
                </div>
                <div style={{ width: 90 }}>
                  <label style={miniLabel}>Type</label>
                  <select style={fieldStyle} value={newType} onChange={(e) => setNewType(e.target.value)}>
                    <option value="string">String</option>
                    <option value="boolean">Boolean</option>
                    <option value="number">Number</option>
                  </select>
                </div>
              </div>
              <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <div style={{ flex: 1 }}>
                  <label style={miniLabel}>Label</label>
                  <input
                    style={fieldStyle}
                    value={newLabel}
                    onChange={(e) => setNewLabel(e.target.value)}
                    placeholder="Display name"
                    onKeyDown={(e) => e.key === "Enter" && handleCreateVariable()}
                  />
                </div>
                <div style={{ width: 90 }}>
                  <label style={miniLabel}>Default</label>
                  {newType === "boolean" ? (
                    <select style={fieldStyle} value={newDefault} onChange={(e) => setNewDefault(e.target.value)}>
                      <option value="false">false</option>
                      <option value="true">true</option>
                    </select>
                  ) : (
                    <input
                      style={fieldStyle}
                      value={newDefault}
                      onChange={(e) => setNewDefault(e.target.value)}
                      placeholder={newType === "number" ? "0" : ""}
                      onKeyDown={(e) => e.key === "Enter" && handleCreateVariable()}
                    />
                  )}
                </div>
              </div>
              <div style={{ display: "flex", gap: "var(--space-xs)" }}>
                <button type="button" onClick={handleCreateVariable} style={btnPrimary}>Create & Select</button>
                <button type="button" onClick={() => setShowCreate(false)} style={btnSecondary}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Styles ── */

const triggerStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const dropdownStyle: React.CSSProperties = {
  position: "absolute",
  top: "100%",
  left: 0,
  right: 0,
  marginTop: 2,
  background: "var(--bg-elevated)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  boxShadow: "var(--shadow-lg)",
  zIndex: 100,
  minWidth: 320,
};

const searchInputStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const groupHeaderStyle: React.CSSProperties = {
  padding: "6px 8px 2px",
  fontSize: 11,
  color: "var(--text-muted)",
  display: "flex",
  alignItems: "baseline",
  flexWrap: "wrap",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "4px 8px 4px 16px",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
  transition: "background 0.1s",
  position: "relative",
};

const typeBadgeStyle: React.CSSProperties = {
  fontSize: 10,
  padding: "0 4px",
  borderRadius: 3,
  background: "var(--bg-hover)",
  color: "var(--text-muted)",
};

const emptyHintStyle: React.CSSProperties = {
  padding: "12px 8px",
  fontSize: 12,
  color: "var(--text-muted)",
  fontStyle: "italic",
  textAlign: "center",
};

const createFormStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-md)",
  borderTop: "1px solid var(--border-color)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const miniLabel: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const fieldStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const btnPrimary: React.CSSProperties = {
  padding: "4px 14px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

const btnSecondary: React.CSSProperties = {
  padding: "4px 14px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};
