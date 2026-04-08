import { useState } from "react";
import { Save, Download } from "lucide-react";
import type { DriverDefinition } from "../../api/types";
import { TransportPicker } from "./TransportPicker";
import { CommandBuilder } from "./CommandBuilder";
import { ResponseBuilder } from "./ResponseBuilder";
import { PollingConfig } from "./PollingConfig";
import { StateVariableEditor } from "./StateVariableEditor";
import { DiscoveryHintsEditor } from "./DiscoveryHintsEditor";
import { DeviceSettingsEditor } from "./DeviceSettingsEditor";
import { SimulatorEditor } from "./SimulatorEditor";
import { LiveTestPanel } from "./LiveTestPanel";

type TabId =
  | "general"
  | "transport"
  | "states"
  | "commands"
  | "responses"
  | "polling"
  | "discovery"
  | "settings"
  | "simulator"
  | "test";

interface DriverEditorProps {
  draft: DriverDefinition;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  isNew: boolean;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
  onSave: () => void;
  onExport: () => void;
}

export function DriverEditor({
  draft,
  dirty,
  saving,
  error,
  isNew,
  onUpdate,
  onSave,
  onExport,
}: DriverEditorProps) {
  const [activeTab, setActiveTab] = useState<TabId>("general");

  const tabs: { id: TabId; label: string }[] = [
    { id: "general", label: "General" },
    { id: "transport", label: "Transport" },
    { id: "states", label: "State Variables" },
    { id: "commands", label: "Commands" },
    { id: "responses", label: "Responses" },
    { id: "polling", label: "Polling" },
    { id: "discovery", label: "Discovery" },
    { id: "settings", label: "Device Settings" },
    { id: "simulator", label: "Simulator" },
    { id: "test", label: "Live Test" },
  ];

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  const rowStyle: React.CSSProperties = {
    marginBottom: "var(--space-md)",
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "var(--space-md) var(--space-lg)",
          borderBottom: "1px solid var(--border-color)",
          gap: "var(--space-md)",
          flexShrink: 0,
        }}
      >
        <h2
          style={{
            fontSize: "var(--font-size-lg)",
            flex: 1,
          }}
        >
          {isNew ? "New Driver" : draft.name || "Untitled Driver"}
        </h2>

        {error && (
          <span
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-error)",
            }}
          >
            {error}
          </span>
        )}

        {!isNew && (
          <button
            onClick={onExport}
            title="Export as .json file"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Download size={14} /> Export
          </button>
        )}

        <button
          onClick={onSave}
          disabled={!dirty || saving}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: dirty ? "var(--accent)" : "var(--bg-hover)",
            color: dirty ? "var(--text-on-accent)" : "var(--text-muted)",
            opacity: saving ? 0.6 : 1,
          }}
        >
          <Save size={14} /> {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {/* Tabs */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--border-color)",
          flexShrink: 0,
          overflowX: "auto",
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              fontSize: "var(--font-size-sm)",
              borderBottom:
                activeTab === tab.id
                  ? "2px solid var(--accent)"
                  : "2px solid transparent",
              color:
                activeTab === tab.id
                  ? "var(--text-primary)"
                  : "var(--text-muted)",
              fontWeight: activeTab === tab.id ? 600 : 400,
              whiteSpace: "nowrap",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "var(--space-lg)",
        }}
      >
        {activeTab === "general" && (
          <div>
            <div style={rowStyle}>
              <label style={labelStyle}>Driver ID</label>
              <input
                value={draft.id}
                onChange={(e) =>
                  onUpdate({
                    id: e.target.value
                      .replace(/[^a-z0-9_]/gi, "")
                      .toLowerCase(),
                  })
                }
                placeholder="e.g., extron_sw4"
                disabled={!isNew}
                style={{ width: "100%", opacity: isNew ? 1 : 0.6 }}
              />
              {!isNew && (
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    marginTop: "var(--space-xs)",
                  }}
                >
                  ID cannot be changed after creation
                </div>
              )}
            </div>

            <div style={rowStyle}>
              <label style={labelStyle}>Driver Name</label>
              <input
                value={draft.name}
                onChange={(e) => onUpdate({ name: e.target.value })}
                placeholder="e.g., Extron SW4 HD 4K"
                style={{ width: "100%" }}
              />
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "var(--space-md)",
              }}
            >
              <div style={rowStyle}>
                <label style={labelStyle}>Manufacturer</label>
                <input
                  value={draft.manufacturer}
                  onChange={(e) => onUpdate({ manufacturer: e.target.value })}
                  placeholder="Generic"
                  style={{ width: "100%" }}
                />
              </div>

              <div style={rowStyle}>
                <label style={labelStyle}>Category</label>
                <select
                  value={draft.category}
                  onChange={(e) => onUpdate({ category: e.target.value })}
                  style={{ width: "100%" }}
                >
                  <option value="projector">Projector</option>
                  <option value="display">Display</option>
                  <option value="switcher">Switcher</option>
                  <option value="scaler">Scaler</option>
                  <option value="audio">Audio</option>
                  <option value="camera">Camera</option>
                  <option value="lighting">Lighting</option>
                  <option value="relay">Relay / GPIO</option>
                  <option value="utility">Utility</option>
                  <option value="other">Other</option>
                </select>
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "var(--space-md)",
              }}
            >
              <div style={rowStyle}>
                <label style={labelStyle}>Version</label>
                <input
                  value={draft.version}
                  onChange={(e) => onUpdate({ version: e.target.value })}
                  placeholder="1.0.0"
                  style={{ width: "100%" }}
                />
              </div>

              <div style={rowStyle}>
                <label style={labelStyle}>Author</label>
                <input
                  value={draft.author}
                  onChange={(e) => onUpdate({ author: e.target.value })}
                  placeholder="Your name"
                  style={{ width: "100%" }}
                />
              </div>
            </div>

            <div style={rowStyle}>
              <label style={labelStyle}>Description</label>
              <textarea
                value={draft.description}
                onChange={(e) => onUpdate({ description: e.target.value })}
                placeholder="Brief description of this driver..."
                rows={3}
                style={{
                  width: "100%",
                  resize: "vertical",
                  fontFamily: "inherit",
                }}
              />
            </div>
          </div>
        )}

        {activeTab === "transport" && (
          <TransportPicker draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "states" && (
          <StateVariableEditor draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "commands" && (
          <CommandBuilder draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "responses" && (
          <ResponseBuilder draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "polling" && (
          <PollingConfig draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "discovery" && (
          <DiscoveryHintsEditor draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "settings" && (
          <DeviceSettingsEditor draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "simulator" && (
          <SimulatorEditor draft={draft} onUpdate={onUpdate} />
        )}

        {activeTab === "test" && <LiveTestPanel draft={draft} />}
      </div>
    </div>
  );
}
