import { useState, useMemo, useEffect } from "react";
import { Save, Download, FileCode, Copy, Check, ExternalLink, Lock } from "lucide-react";
import yaml from "js-yaml";
import type { DriverDefinition } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import { DRIVER_CATEGORIES } from "./driverCategories";
import { TransportPicker } from "./TransportPicker";
import { BridgePortsEditor } from "./BridgePortsEditor";
import { CommandBuilder } from "./CommandBuilder";
import { ActionsEditor } from "./ActionsEditor";
import { ResponseBuilder } from "./ResponseBuilder";
import { PollingConfig } from "./PollingConfig";
import { StateVariableEditor } from "./StateVariableEditor";
import { ChildEntityTypesEditor } from "./ChildEntityTypesEditor";
import { DiscoveryHintsEditor } from "./DiscoveryHintsEditor";
import { DeviceSettingsEditor } from "./DeviceSettingsEditor";
import { SimulatorEditor } from "./SimulatorEditor";
import { LiveTestPanel } from "./LiveTestPanel";
import { LifecycleEditor } from "./LifecycleEditor";
import { AuthEditor } from "./AuthEditor";
import { PushEditor } from "./PushEditor";
import { LivenessEditor } from "./LivenessEditor";
import { FrameParserEditor } from "./FrameParserEditor";
import { SendFrameEditor } from "./SendFrameEditor";
import { ConfigSchemaEditor } from "./ConfigSchemaEditor";
import { CollapsibleSection } from "./CollapsibleSection";
import { IssueList } from "./IssueList";
import { validateDriver, issuesFor } from "./validateDriver";
import { DOCS } from "./docLinks";
import { copyToClipboard } from "../shared/clipboard";

type TabId =
  | "general"
  | "connection"
  | "behavior"
  | "discovery"
  | "simulation"
  | "test";

interface DriverEditorProps {
  draft: DriverDefinition;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  isNew: boolean;
  /** True when the loaded driver is built-in. Inputs disabled, Save hidden,
   *  banner offers "Customize a copy" to fork into an editable version. */
  readOnly: boolean;
  /** The id this driver was loaded under. null for a brand-new draft. */
  originalId: string | null;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
  onSave: () => void;
  onExport: () => void;
  /** Fork the built-in into an editable copy and switch to it. Used by the
   *  read-only banner's "Customize a copy" button. */
  onDuplicate: () => void;
}

export function DriverEditor({
  draft,
  dirty,
  saving,
  error,
  isNew,
  readOnly,
  originalId,
  onUpdate,
  onSave,
  onExport,
  onDuplicate,
}: DriverEditorProps) {
  const [activeTab, setActiveTab] = useState<TabId>("general");
  const [yamlPaneOpen, setYamlPaneOpen] = useState(false);
  const [yamlCopied, setYamlCopied] = useState(false);
  // Gate validation surfacing on freshly-created drafts. A user who just
  // clicked "Create New Driver" hasn't authored anything yet — showing
  // red/orange warnings before they've typed a character is jarring and
  // out of step with the rest of the app. We hold validation back until
  // the first Save attempt; for existing drivers (where the user has
  // chosen what to edit), validation is live as before.
  const [attemptedSave, setAttemptedSave] = useState(false);
  const devices = useProjectStore((s) => s.project?.devices);
  const allDefinitions = useDriverBuilderStore((s) => s.definitions);

  // When the loaded driver changes (or a new draft starts), reset the
  // save-attempted flag so a previous editor's state doesn't bleed in.
  useEffect(() => {
    setAttemptedSave(false);
  }, [originalId, isNew]);

  const issues = useMemo(
    () => validateDriver(draft, allDefinitions, originalId),
    [draft, allDefinitions, originalId],
  );

  // For brand-new drafts we suppress issues until the user attempts to
  // save. Existing drivers always show their issues so problems with
  // already-authored content are visible as soon as the editor opens.
  const showValidation = !isNew || attemptedSave;
  const effectiveIssues = showValidation ? issues : [];

  const handleSave = () => {
    setAttemptedSave(true);
    onSave();
  };

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
    if (!(await copyToClipboard(yamlPreview))) return;
    setYamlCopied(true);
    setTimeout(() => setYamlCopied(false), 1500);
  };

  // Devices in the current project that reference the loaded driver by its
  // original id. Used to warn the user that renaming will orphan them.
  const devicesUsingDriver = originalId
    ? (devices ?? []).filter((d) => d.driver === originalId)
    : [];
  const idChanged = originalId !== null && draft.id !== originalId;

  const tabs: { id: TabId; label: string }[] = [
    { id: "general", label: "General" },
    { id: "connection", label: "Connection" },
    { id: "behavior", label: "Behavior" },
    { id: "discovery", label: "Discovery" },
    { id: "simulation", label: "Simulation" },
    { id: "test", label: "Test" },
  ];

  // Counts surfaced in collapsible headers so users can scan a tab and see
  // which sections are populated without expanding every panel.
  const stateCount = Object.keys(draft.state_variables ?? {}).length;
  const childTypeCount = Object.keys(draft.child_entity_types ?? {}).length;
  const commandCount = Object.keys(draft.commands ?? {}).length;
  const responseCount = (draft.responses ?? []).length;
  // Actions header meta: explicit actions plus legacy quick_actions count as
  // buttons; a web_ui flag alone still deserves a non-"none" hint since it
  // adds the Open Web UI button.
  const actionCount = (draft.actions ?? []).length + (draft.quick_actions ?? []).length;
  const webUiEnabled = draft.web_ui !== undefined && draft.web_ui !== false;
  const pollingQueryCount = (draft.polling?.queries ?? []).length;
  const settingCount = Object.keys(draft.device_settings ?? {}).length;
  const configFieldCount = Object.keys(draft.config_schema ?? {}).filter(
    (k) =>
      ![
        "host",
        "port",
        "baudrate",
        "parity",
        "poll_interval",
        "inter_command_delay",
      ].includes(k),
  ).length;
  const derivedFieldCount = Object.keys(draft.config_derived ?? {}).length;
  const bridgeEnabled = draft.bridge !== undefined;
  const bridgePortCount = (draft.bridge?.ports ?? []).length;
  const onConnectCount = (draft.on_connect ?? []).length;
  const authEnabled = !!draft.auth;
  const pushEnabled = !!draft.push;
  const livenessEnabled = !!draft.liveness;
  const frameParserEnabled = !!draft.frame_parser;
  const sendFrameEnabled = !!draft.send_frame;
  // Command framing wraps byte-stream sends only — OSC uses an address, HTTP a
  // path/body, so neither is line-framed and the section is hidden for them.
  const isByteStream = ["tcp", "serial", "udp"].includes(draft.transport);
  const framingEnabled = !!(draft.command_prefix || draft.command_suffix);

  const countMeta = (n: number, singular: string) =>
    n === 0 ? "none" : `${n} ${n === 1 ? singular : `${singular}s`}`;

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

        {!readOnly && (
          <button
            onClick={handleSave}
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
        )}
      </div>

      {/* Read-only banner — built-in drivers ship with the platform and
          can't be edited in place. The "Customize a copy" button forks
          the driver into an editable version (see store.duplicateDriver,
          which auto-selects the new copy). */}
      {readOnly && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-md)",
            padding: "var(--space-sm) var(--space-lg)",
            background: "var(--bg-hover)",
            borderBottom: "1px solid var(--border-color)",
            flexShrink: 0,
          }}
        >
          <Lock size={14} style={{ color: "var(--text-muted)" }} />
          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", flex: 1 }}>
            Built-in driver — read only. Customize a copy to edit.
          </span>
          <button
            onClick={onDuplicate}
            disabled={saving}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              fontSize: "var(--font-size-sm)",
              opacity: saving ? 0.6 : 1,
            }}
          >
            <Copy size={14} /> {saving ? "Copying..." : "Customize a copy"}
          </button>
        </div>
      )}

      {/* Tabs */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--border-color)",
          flexShrink: 0,
          overflowX: "auto",
        }}
      >
        {tabs.map((tab) => {
          const tabIssues = issuesFor(effectiveIssues, tab.id);
          const errorCount = tabIssues.filter((i) => i.severity === "error").length;
          const warningCount = tabIssues.filter((i) => i.severity === "warning").length;
          const badgeColor =
            errorCount > 0
              ? "var(--color-error)"
              : warningCount > 0
                ? "var(--color-warning, #d97706)"
                : null;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              title={
                badgeColor
                  ? `${errorCount} error${errorCount === 1 ? "" : "s"}, ${warningCount} warning${warningCount === 1 ? "" : "s"}`
                  : undefined
              }
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
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
              {badgeColor && (
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: "50%",
                    background: badgeColor,
                    display: "inline-block",
                    flexShrink: 0,
                  }}
                />
              )}
            </button>
          );
        })}
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
        {/* fieldset[disabled] cascades to every native input/select/textarea/
            button inside the tab content — gives us a single read-only switch
            for built-in drivers without plumbing readOnly into a dozen
            sub-editors. The default fieldset border/padding are stripped so
            layout is unchanged. */}
        <fieldset
          disabled={readOnly}
          style={{ border: "none", margin: 0, padding: 0, minWidth: 0 }}
        >
        {activeTab === "general" && (
          <div>
            <LearnMore href={DOCS.general} label="Driver definition reference" />
            <IssueList issues={issuesFor(effectiveIssues, "general")} />
            {/* Friendly coach mark on a brand-new draft before the user
                tries to save — replaces the validation-error wall they
                used to see on a freshly-created driver. */}
            {isNew && !attemptedSave && (
              <div
                style={{
                  padding: "var(--space-sm) var(--space-md)",
                  marginBottom: "var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                }}
              >
                Enter a Driver ID and Name to save your driver.
              </div>
            )}
            {/* Informational only — inline_protocol is reserved for the
                built-in generic drivers and has no authoring control. The
                flag survives edits via the draft spread. */}
            {!!draft.inline_protocol && (
              <div
                style={{
                  padding: "var(--space-sm) var(--space-md)",
                  marginBottom: "var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                }}
              >
                This driver uses the no-code device-page protocol editor
                (built-in generic drivers only). Commands, responses, and
                state variables authored on the device page merge into it at
                runtime — community drivers ship theirs in the driver file
                instead.
              </div>
            )}
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
                  {DRIVER_CATEGORIES.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
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

            <div style={rowStyle}>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <input
                  type="checkbox"
                  checked={draft.ir_codes === true}
                  onChange={(e) =>
                    onUpdate({ ir_codes: e.target.checked || undefined })
                  }
                />
                IR code-set device
              </label>
              <div
                style={{
                  fontSize: "11px",
                  color: "var(--text-muted)",
                  marginTop: "var(--space-xs)",
                }}
              >
                A device controlled by an infrared remote through an IR
                bridge. Codes are authored on the device page (learn, paste
                Pronto hex, type a sendir string, or search a code database)
                and sent through an IR bridge port — each code becomes a
                device command. A community IR driver ships its code-set in
                default config. Use the Bridge transport with this.
              </div>
            </div>

            <HelpFieldsSection draft={draft} onUpdate={onUpdate} />

            <PublishingSection draft={draft} onUpdate={onUpdate} />
          </div>
        )}

        {activeTab === "connection" && (
          <>
            <IssueList issues={issuesFor(effectiveIssues, "connection")} />
            <CollapsibleSection
              title="Transport"
              subtitle="How the driver talks to the device — TCP, serial, UDP, OSC, HTTP."
              meta={draft.transport || "not set"}
              helpHref={DOCS.transport}
            >
              <TransportPicker draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Bridge Ports"
              subtitle="Optional — declares this device as a bridge others connect through (a serial-to-Ethernet or IR bridge) and the typed ports it advertises."
              meta={bridgeEnabled ? countMeta(bridgePortCount, "port") : "disabled"}
              defaultOpen={bridgeEnabled}
              helpHref={DOCS.bridge}
            >
              <BridgePortsEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            {isByteStream && (
              <CollapsibleSection
                title="Command Framing"
                subtitle="Optional — a constant prefix and suffix that wrap every command, so a fixed packet header and line terminator are set once instead of on each command."
                meta={framingEnabled ? "enabled" : "none"}
                defaultOpen={framingEnabled}
                helpHref={DOCS.commands}
              >
                <CommandFramingEditor draft={draft} onUpdate={onUpdate} />
              </CollapsibleSection>
            )}

            <CollapsibleSection
              title="Authentication"
              subtitle="Optional login handshake — for devices that present a login: / password: prompt after connect."
              meta={authEnabled ? "enabled" : "disabled"}
              defaultOpen={authEnabled}
              helpHref={DOCS.auth}
            >
              <AuthEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Push Notifications"
              subtitle="Optional — device-initiated updates on a separate multicast channel. Frames feed the same response rules as the control connection."
              meta={pushEnabled ? "enabled" : "disabled"}
              defaultOpen={pushEnabled}
              helpHref={DOCS.push}
            >
              <PushEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Connection Watchdog"
              subtitle="Optional — probes the device on an interval and reconnects after consecutive misses, for links that die without closing the connection."
              meta={livenessEnabled ? "enabled" : "disabled"}
              defaultOpen={livenessEnabled}
              helpHref={DOCS.liveness}
            >
              <LivenessEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Connect Sequence"
              subtitle="Commands sent automatically on every connect — verbose-mode toggles, GET ALL requests, push subscriptions."
              meta={countMeta(onConnectCount, "command")}
              defaultOpen={onConnectCount > 0}
              helpHref={DOCS.onConnect}
            >
              <LifecycleEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Frame Parser"
              subtitle="Advanced — only for binary protocols framed by length prefix or fixed length. Most drivers leave this off."
              meta={frameParserEnabled ? "enabled" : "disabled"}
              defaultOpen={frameParserEnabled}
              helpHref={DOCS.frameParser}
            >
              <FrameParserEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            {isByteStream && (
              <CollapsibleSection
                title="Send Frame"
                subtitle="Advanced — wraps every command in a binary packet header whose data-length is computed per message (e.g. eISCP). The send twin of Frame Parser. Most drivers leave this off."
                meta={sendFrameEnabled ? "enabled" : "disabled"}
                defaultOpen={sendFrameEnabled}
                helpHref={DOCS.frameParser}
              >
                <SendFrameEditor draft={draft} onUpdate={onUpdate} />
              </CollapsibleSection>
            )}

            <CollapsibleSection
              title="Configuration Fields"
              subtitle="Per-device settings users fill in (display IDs, instance tags, custom passwords). Become {placeholders} in commands."
              meta={
                derivedFieldCount > 0
                  ? `${countMeta(configFieldCount, "field")} + ${derivedFieldCount} computed`
                  : countMeta(configFieldCount, "field")
              }
              defaultOpen={configFieldCount > 0 || derivedFieldCount > 0}
              helpHref={DOCS.configSchema}
            >
              <ConfigSchemaEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>
          </>
        )}

        {activeTab === "behavior" && (
          <>
            <IssueList issues={issuesFor(effectiveIssues, "behavior")} />
            <CollapsibleSection
              title="State Variables"
              subtitle="Read-only values the driver reports — power, input, mute, volume. Use these in command parameters and panel bindings."
              meta={countMeta(stateCount, "variable")}
              helpHref={DOCS.stateVariables}
            >
              <StateVariableEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Child Entity Types"
              subtitle="Sub-units this device manages — encoders, decoders, zones, presets. Each declared type gets a per-instance row in the device's Child Entities tab."
              meta={countMeta(childTypeCount, "type")}
              defaultOpen={childTypeCount > 0}
              helpHref={DOCS.childEntityTypes}
            >
              <ChildEntityTypesEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Commands"
              subtitle="Actions the driver can perform — power on, switch input, set volume. Reference state variables and config fields with {placeholders}."
              meta={countMeta(commandCount, "command")}
              helpHref={DOCS.commands}
            >
              <CommandBuilder draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Actions"
              subtitle="Commands promoted to one-click buttons at the top of the device view — plus an Open Web UI link for devices with a browser interface."
              meta={
                actionCount > 0
                  ? `${countMeta(actionCount, "action")}${webUiEnabled ? " + web UI" : ""}`
                  : webUiEnabled
                    ? "web UI"
                    : "none"
              }
              defaultOpen={actionCount > 0 || webUiEnabled}
              helpHref={DOCS.actions}
            >
              <ActionsEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Responses"
              subtitle="Patterns matched against incoming data — capture groups update state variables."
              meta={countMeta(responseCount, "pattern")}
              helpHref={DOCS.responses}
            >
              <ResponseBuilder draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Polling"
              subtitle="Periodic queries that keep state variables fresh on devices that don't push updates."
              meta={countMeta(pollingQueryCount, "query")}
              defaultOpen={pollingQueryCount > 0}
              helpHref={DOCS.polling}
            >
              <PollingConfig draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>

            <CollapsibleSection
              title="Device Settings"
              subtitle="Writable values stored on the device hardware — labels, IDs, lock codes. Pending writes queue while offline."
              meta={countMeta(settingCount, "setting")}
              defaultOpen={settingCount > 0}
              helpHref={DOCS.deviceSettings}
            >
              <DeviceSettingsEditor draft={draft} onUpdate={onUpdate} />
            </CollapsibleSection>
          </>
        )}

        {activeTab === "discovery" && (
          <>
            <LearnMore href={DOCS.discovery} label="Discovery reference" />
            <IssueList issues={issuesFor(effectiveIssues, "discovery")} />
            <DiscoveryHintsEditor draft={draft} onUpdate={onUpdate} />
          </>
        )}

        {activeTab === "simulation" && (
          <>
            <LearnMore href={DOCS.simulation} label="Writing simulators guide" />
            <IssueList issues={issuesFor(effectiveIssues, "simulation")} />
            <SimulatorEditor draft={draft} onUpdate={onUpdate} />
          </>
        )}

        {activeTab === "test" && <LiveTestPanel draft={draft} />}
        </fieldset>
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
        physical button presses, anything device-specific. Connection
        troubleshooting appears on the device's offline banner when it can't
        connect.
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

      <div style={{ marginBottom: "var(--space-md)" }}>
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

      <div>
        <label style={sectionLabelStyle}>Connection Troubleshooting</label>
        <textarea
          value={help.connection ?? ""}
          onChange={(e) => update({ connection: e.target.value })}
          placeholder={
            "Enable Telnet control in the device's network settings, then power-cycle it..."
          }
          rows={3}
          style={{
            width: "100%",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            resize: "vertical",
          }}
        />
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          Optional short hint shown on the device&apos;s offline banner when
          it can&apos;t connect — e.g. a remote-access setting that must be
          enabled on the device first.
        </div>
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

function CommandFramingEditor({
  draft,
  onUpdate,
}: {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}) {
  const helpStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: "var(--space-xs)",
  };

  // Store the literal-escape text the author types (`\r`, `\x02`, ...) — the
  // runtime decodes escapes at send time, exactly like a command's send
  // string. Blank clears the key so we never ship `command_prefix: ""`.
  const field = (
    key: "command_prefix" | "command_suffix",
    label: string,
    placeholder: string,
    help: React.ReactNode,
  ) => (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <label style={sectionLabelStyle}>{label}</label>
      <input
        value={draft[key] ?? ""}
        onChange={(e) => onUpdate({ [key]: e.target.value || undefined })}
        placeholder={placeholder}
        style={{ width: "100%", fontFamily: "var(--font-mono)" }}
      />
      <div style={helpStyle}>{help}</div>
    </div>
  );

  return (
    <div>
      <p style={{ ...helpStyle, marginTop: 0, marginBottom: "var(--space-md)" }}>
        Both are optional and off by default. When set, every command this
        driver sends goes on the wire as{" "}
        <code>prefix + command + suffix</code> — so a command whose string is{" "}
        <code>PWR01</code>, with prefix <code>!1</code> and suffix{" "}
        <code>\r</code>, is sent as <code>!1PWR01\r</code>. Author your command
        strings bare and let the frame wrap them. Use <code>\r</code>,{" "}
        <code>\n</code>, <code>\xHH</code> for control bytes;{" "}
        <code>{"{config_key}"}</code> is substituted from device config.
      </p>
      {field(
        "command_prefix",
        "Command Prefix",
        "e.g. !1",
        <>Prepended to every command — a fixed packet header the protocol shares.</>,
      )}
      {field(
        "command_suffix",
        "Command Suffix",
        "e.g. \\r",
        <>
          Appended to every command — its line terminator. Set this instead of
          typing <code>\r</code> on each command string.
        </>,
      )}
      <p style={{ ...helpStyle, marginTop: 0 }}>
        A single command can opt out of the frame with its <strong>Send raw</strong>{" "}
        toggle (in the Commands section) — for the odd command that already
        carries its own framing.
      </p>
    </div>
  );
}

/** Top-of-tab "Learn more" link. Used for tabs whose content isn't wrapped
 *  in a CollapsibleSection (which carries its own helpHref). */
function LearnMore({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: "11px",
        color: "var(--text-muted)",
        textDecoration: "none",
        marginBottom: "var(--space-md)",
      }}
    >
      <ExternalLink size={11} /> {label}
    </a>
  );
}
