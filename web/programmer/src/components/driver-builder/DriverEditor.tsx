import { useState, useMemo } from "react";
import { Save, Download, FileCode, Copy, Check } from "lucide-react";
import yaml from "js-yaml";
import type { DriverDefinition } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { TransportPicker } from "./TransportPicker";
import { CommandBuilder } from "./CommandBuilder";
import { ResponseBuilder } from "./ResponseBuilder";
import { PollingConfig } from "./PollingConfig";
import { StateVariableEditor } from "./StateVariableEditor";
import { DiscoveryHintsEditor } from "./DiscoveryHintsEditor";
import { DeviceSettingsEditor } from "./DeviceSettingsEditor";
import { SimulatorEditor } from "./SimulatorEditor";
import { LiveTestPanel } from "./LiveTestPanel";
import { LifecycleEditor } from "./LifecycleEditor";
import { AuthEditor } from "./AuthEditor";
import { FrameParserEditor } from "./FrameParserEditor";

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
  /** The id this driver was loaded under. null for a brand-new draft. */
  originalId: string | null;
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
  originalId,
  onUpdate,
  onSave,
  onExport,
}: DriverEditorProps) {
  const [activeTab, setActiveTab] = useState<TabId>("general");
  const [yamlPaneOpen, setYamlPaneOpen] = useState(false);
  const [yamlCopied, setYamlCopied] = useState(false);
  const project = useProjectStore((s) => s.project);

  const yamlPreview = useMemo(() => {
    try {
      return yaml.dump(draft, {
        lineWidth: 120,
        noCompatMode: true,
        quotingType: '"',
        skipInvalid: true,
      });
    } catch (e) {
      return `# YAML serialization failed: ${e instanceof Error ? e.message : String(e)}`;
    }
  }, [draft]);

  const copyYaml = async () => {
    try {
      await navigator.clipboard.writeText(yamlPreview);
      setYamlCopied(true);
      setTimeout(() => setYamlCopied(false), 1500);
    } catch {
      // ignore — older browsers / no permissions
    }
  };

  // Devices in the current project that reference the loaded driver by its
  // original id. Used to warn the user that renaming will orphan them.
  const devicesUsingDriver = originalId
    ? (project?.devices ?? []).filter((d) => d.driver === originalId)
    : [];
  const idChanged = originalId !== null && draft.id !== originalId;

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

        <button
          onClick={() => setYamlPaneOpen((v) => !v)}
          title={yamlPaneOpen ? "Hide YAML preview" : "Show live YAML preview of the driver"}
          aria-pressed={yamlPaneOpen}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: yamlPaneOpen ? "var(--accent-bg)" : "var(--bg-hover)",
            color: yamlPaneOpen ? "var(--text-on-accent)" : "var(--text-primary)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          <FileCode size={14} /> YAML
        </button>

        {!isNew && (
          <button
            onClick={onExport}
            title="Download this driver as a .avcdriver file"
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
            <Download size={14} /> Export .avcdriver
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
            background: dirty ? "var(--accent-bg)" : "var(--bg-hover)",
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

      {/* Tab content + optional live YAML pane */}
      <div
        style={{
          flex: 1,
          display: "flex",
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            flex: 1,
            overflow: "auto",
            padding: "var(--space-lg)",
            minWidth: 0,
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
                style={{ width: "100%" }}
              />
              {isNew ? (
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    marginTop: "var(--space-xs)",
                  }}
                >
                  Lowercase letters, digits, and underscores only.
                </div>
              ) : idChanged ? (
                <div
                  style={{
                    fontSize: "11px",
                    marginTop: "var(--space-xs)",
                    padding: "var(--space-xs) var(--space-sm)",
                    borderRadius: "var(--border-radius)",
                    background: "rgba(255, 152, 0, 0.15)",
                    color: "var(--color-warning, #d97706)",
                    border: "1px solid rgba(255, 152, 0, 0.4)",
                  }}
                >
                  Renaming from <code>{originalId}</code> to <code>{draft.id || "?"}</code>.
                  {devicesUsingDriver.length > 0
                    ? ` ${devicesUsingDriver.length} device${devicesUsingDriver.length === 1 ? "" : "s"} in the current project reference the old id and will need to be reassigned (${devicesUsingDriver.map((d) => d.name || d.id).join(", ")}).`
                    : " No devices in the current project reference this driver."}
                </div>
              ) : (
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    marginTop: "var(--space-xs)",
                  }}
                >
                  Lowercase letters, digits, and underscores only.
                  {devicesUsingDriver.length > 0
                    ? ` In use by ${devicesUsingDriver.length} device${devicesUsingDriver.length === 1 ? "" : "s"} in the current project — renaming will orphan them.`
                    : ""}
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
                  <option value="video">Video (encoders, decoders, NDI)</option>
                  <option value="audio">Audio</option>
                  <option value="camera">Camera</option>
                  <option value="lighting">Lighting</option>
                  <option value="recorder">Recorder</option>
                  <option value="power">Power (PDU, UPS, sequencer)</option>
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

            <HelpFieldsSection draft={draft} onUpdate={onUpdate} />

            <PublishingSection draft={draft} onUpdate={onUpdate} />
          </div>
        )}

        {activeTab === "transport" && (
          <>
            <TransportPicker draft={draft} onUpdate={onUpdate} />
            <AuthEditor draft={draft} onUpdate={onUpdate} />
            <LifecycleEditor draft={draft} onUpdate={onUpdate} />
            <FrameParserEditor draft={draft} onUpdate={onUpdate} />
          </>
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

        {yamlPaneOpen && (
          <div
            style={{
              width: "42%",
              minWidth: 360,
              maxWidth: 720,
              borderLeft: "1px solid var(--border-color)",
              display: "flex",
              flexDirection: "column",
              background: "var(--bg-surface)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "var(--space-sm) var(--space-md)",
                borderBottom: "1px solid var(--border-color)",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                flexShrink: 0,
              }}
            >
              <FileCode size={14} />
              <span style={{ flex: 1 }}>
                Live YAML preview
                <span style={{ color: "var(--text-muted)", marginLeft: 6 }}>
                  (read-only)
                </span>
              </span>
              <button
                onClick={copyYaml}
                title="Copy YAML to clipboard"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 8px",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "11px",
                }}
              >
                {yamlCopied ? <Check size={12} /> : <Copy size={12} />}
                {yamlCopied ? "Copied" : "Copy"}
              </button>
            </div>
            <pre
              style={{
                flex: 1,
                margin: 0,
                padding: "var(--space-md)",
                overflow: "auto",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
                lineHeight: 1.5,
                color: "var(--text-primary)",
                whiteSpace: "pre",
              }}
            >
              {yamlPreview}
            </pre>
            <div
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderTop: "1px solid var(--border-color)",
                fontSize: "11px",
                color: "var(--text-muted)",
                flexShrink: 0,
              }}
            >
              This is exactly what gets saved as the .avcdriver file.
              Edits flow back through the form.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const sectionLabelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

function HelpFieldsSection({
  draft,
  onUpdate,
}: {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}) {
  const help = draft.help ?? {};

  const update = (partial: Partial<typeof help>) => {
    const next = { ...help, ...partial };
    // Drop empty strings so we don't ship `help: {}` blocks in YAML.
    for (const k of Object.keys(next) as (keyof typeof next)[]) {
      if (!next[k]) delete next[k];
    }
    onUpdate({ help: Object.keys(next).length ? next : undefined });
  };

  return (
    <div style={{ marginTop: "var(--space-xl)" }}>
      <h3 style={{ fontSize: "var(--font-size-md)", marginBottom: "var(--space-xs)" }}>
        Help &amp; Setup
      </h3>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Markdown shown to integrators in the Add Device dialog. Overview is a
        short pitch (what does this device do, who's it for). Setup is the
        step-by-step the user follows to get it talking — IP setup, pairing,
        physical button presses, anything device-specific.
      </p>

      <div style={{ marginBottom: "var(--space-md)" }}>
        <label style={sectionLabelStyle}>Overview (markdown)</label>
        <textarea
          value={help.overview ?? ""}
          onChange={(e) => update({ overview: e.target.value })}
          placeholder="Short pitch — what this device is, where AV integrators use it."
          rows={4}
          style={{
            width: "100%",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            resize: "vertical",
          }}
        />
      </div>

      <div>
        <label style={sectionLabelStyle}>Setup Instructions (markdown)</label>
        <textarea
          value={help.setup ?? ""}
          onChange={(e) => update({ setup: e.target.value })}
          placeholder={
            "1. Set a static IP on the device.\n" +
            "2. Note the admin credentials (or pair via the device's button).\n" +
            "3. Enter host, port, and credentials below..."
          }
          rows={8}
          style={{
            width: "100%",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            resize: "vertical",
          }}
        />
      </div>
    </div>
  );
}

function PublishingSection({
  draft,
  onUpdate,
}: {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}) {
  // Comma-separated text -> string[] helper.
  const parseList = (raw: string): string[] | undefined => {
    const items = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return items.length ? items : undefined;
  };

  const parsePorts = (raw: string): number[] | undefined => {
    const items = raw
      .split(",")
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => Number.isFinite(n));
    return items.length ? items : undefined;
  };

  return (
    <div style={{ marginTop: "var(--space-xl)" }}>
      <h3 style={{ fontSize: "var(--font-size-md)", marginBottom: "var(--space-xs)" }}>
        Publishing
      </h3>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Catalog metadata used by the community driver index, Browse Drivers,
        and the platform-version compatibility check at install time.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-md)",
          marginBottom: "var(--space-md)",
        }}
      >
        <div>
          <label style={sectionLabelStyle}>Minimum Platform Version</label>
          <input
            value={draft.min_platform_version ?? ""}
            onChange={(e) =>
              onUpdate({ min_platform_version: e.target.value || undefined })
            }
            placeholder="e.g. 0.9.0"
            style={{ width: "100%", fontFamily: "var(--font-mono)" }}
          />
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 4 }}>
            Blocks install on older OpenAVC versions that lack required
            features. Leave blank if the driver works on every supported
            version.
          </div>
        </div>
        <div>
          <label style={sectionLabelStyle}>Source URL</label>
          <input
            value={draft.source_url ?? ""}
            onChange={(e) =>
              onUpdate({ source_url: e.target.value || undefined })
            }
            placeholder="https://github.com/..."
            style={{ width: "100%", fontFamily: "var(--font-mono)" }}
          />
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 4 }}>
            Optional. Reference implementation or protocol docs.
          </div>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-md)",
          marginBottom: "var(--space-md)",
        }}
      >
        <div>
          <label style={sectionLabelStyle}>Protocols</label>
          <input
            value={(draft.protocols ?? []).join(", ")}
            onChange={(e) => onUpdate({ protocols: parseList(e.target.value) })}
            placeholder="e.g. sis, telnet"
            style={{ width: "100%", fontFamily: "var(--font-mono)" }}
          />
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 4 }}>
            Protocol identifiers for catalog filtering. Comma-separated.
          </div>
        </div>
        <div>
          <label style={sectionLabelStyle}>Tags</label>
          <input
            value={(draft.tags ?? []).join(", ")}
            onChange={(e) => onUpdate({ tags: parseList(e.target.value) })}
            placeholder="e.g. matrix, 4k, hdmi"
            style={{ width: "100%", fontFamily: "var(--font-mono)" }}
          />
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 4 }}>
            Free-form discovery tags. Comma-separated.
          </div>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 120px 120px",
          gap: "var(--space-md)",
          alignItems: "end",
        }}
      >
        <div>
          <label style={sectionLabelStyle}>Default Ports</label>
          <input
            value={(draft.ports ?? []).join(", ")}
            onChange={(e) => onUpdate({ ports: parsePorts(e.target.value) })}
            placeholder="e.g. 23, 80"
            style={{ width: "100%", fontFamily: "var(--font-mono)" }}
          />
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 4 }}>
            Network ports this driver speaks on. Used by the discovery
            engine. Comma-separated.
          </div>
        </div>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: "var(--font-size-sm)",
            paddingBottom: 6,
          }}
        >
          <input
            type="checkbox"
            checked={!!draft.simulated}
            onChange={(e) =>
              onUpdate({ simulated: e.target.checked || undefined })
            }
          />
          Simulated
        </label>
        <div
          title="Server-controlled — set by the community catalog after testing"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: "var(--font-size-sm)",
            paddingBottom: 6,
            color: draft.verified ? "var(--accent)" : "var(--text-muted)",
          }}
        >
          <input
            type="checkbox"
            checked={!!draft.verified}
            disabled
            readOnly
          />
          Verified
        </div>
      </div>
      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
        <strong>Simulated:</strong> set when this driver has a simulator
        section so users can test without hardware. <strong>Verified:</strong>{" "}
        read-only — the community catalog flips this once a driver is
        validated against real hardware.
      </div>
    </div>
  );
}
