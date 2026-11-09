import { useState, useCallback } from "react";
import { Save, AlertTriangle, Power } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { HostNetworkCard } from "../components/system/HostNetworkCard";
import { envHold, heldSetting } from "../components/system/EnvHeldNote";
import {
  btnStyle,
  cardStyle,
  fieldRow,
  inputStyle,
  sectionTitle,
  selectStyle,
  warningBox,
} from "../components/system/settingsStyles";
import { FieldRow, NumberBox, SwitchRow } from "./settings/settingsControls";
import { AccessCards, NoCredentialWarning, useSshAccess } from "./settings/AccessCards";
import { useAdapters, useCertificateStatus, useInstallFacts } from "./settings/serverReads";
import { saveChecks } from "./settings/saveChecks";
import { DevicesCard, PanelDisplayCard } from "./settings/ProjectSettingsCards";
import { SecurityCard } from "./settings/SecurityCard";
import { TrustedCertificateCard, type TrustedCertificatePage } from "./settings/TrustedCertificateCard";
import type { UpdateTls } from "./settings/YourOwnCertificate";
import { AcceptConnectionsField, ReachDevicesField } from "../components/system/NetworkChoiceFields";
import { adapterName } from "../components/system/adapterChoice";
import { useListenChoice } from "./settings/listenChoice";
import { useRestart } from "./settings/restartFlow";
import { RestartProgress, RestartQuestion } from "./settings/RestartDialogs";
import { useSettingsSave } from "./settings/saveFlow";
import { useSettingsDraft } from "./settings/settingsDraft";
import { CutOffQuestion, RebootQuestion } from "./settings/SaveDialogs";
import { useProjectStore } from "../store/projectStore";
import type { AdapterIdentity } from "../api/restClient";

function getTheme(): "dark" | "light" {
  return (localStorage.getItem("openavc-theme") as "dark" | "light") || "dark";
}

function setTheme(theme: "dark" | "light") {
  localStorage.setItem("openavc-theme", theme);
  document.documentElement.dataset.theme = theme;
}

export function SystemSettingsView() {
  const [theme, setThemeState] = useState<"dark" | "light">(getTheme);
  // The config, the changes not saved yet, and whether one needs a restart.
  const draft = useSettingsDraft();
  const { config, dirty, update, merged, restartNeeded } = draft;
  // Read when Settings opens, in this order after the config.
  const { kioskAvailable, panelDimAvailable, updateGuidance } = useInstallFacts();
  const ssh = useSshAccess();
  const adapters = useAdapters();
  const certificate = useCertificateStatus();
  const projectLoaded = useProjectStore((s) => s.project !== null);

  const updateTls: UpdateTls = useCallback((key, value) => update("tls", key, value), [update]);

  // Fields the service's environment holds: the server runs with that value
  // at every start whatever is saved here, so they are shown locked and the
  // server refuses a change to one.
  const hold = envHold(config);
  // Accept connections from: the choice on screen, the server's answer for
  // it, and where it stands against the saved and the running choice.
  const listen = useListenChoice(config, dirty.network, hold.deploymentType);
  // "Restart to apply changes?" and the restart itself.
  const restart = useRestart(config);

  // Save, once saveChecks lets it: the cut-off question, the project, the
  // system settings, and what follows.
  const save = useSettingsSave({
    dirty,
    restartNeeded,
    kioskAvailable,
    listen,
    restart,
    saved: (fresh) => {
      draft.saved(fresh);
      certificate.load();
    },
  });

  const hasDirty = save.hasChanges;
  const net = merged("network");
  const auth = merged("auth");
  const log = merged("logging");
  const upd = merged("updates");
  const kiosk = merged("kiosk");
  const tls = merged("tls");
  // Read the way the server does: an absent discovery section advertises.
  const advertise = merged("discovery")?.advertise !== false;

  const reachChosen: AdapterIdentity = net.reach_devices_through ?? {};
  const listenAdapterName = adapterName(listen.adapter, adapters.interfaces, listen.running?.adapter_name ?? "");
  const urlFacts = {
    httpPort: net.http_port,
    // Settings cannot know whether port 80 is answering, so it always
    // prints the port.
    port80: false,
    tlsEnabled: !!tls?.enabled,
    tlsPort: tls?.port ?? 8443,
    redirectHttp: tls?.redirect_http !== false,
  };
  const checks = saveChecks({ tls, httpPort: net.http_port, auth, dirtyAuth: dirty.auth });
  const { tlsPortError, tlsProvidedBlank, blocked: saveBlocked } = checks;

  const trustedCertPage: TrustedCertificatePage = {
    savedTlsEnabled: config?.tls?.enabled,
    setConfig: draft.setConfig,
    setTlsStatus: certificate.setStatus,
    loadTlsStatus: certificate.load,
    askForRestart: restart.ask,
  };

  if (!config) {
    return (
      <ViewContainer title="System Settings">
        <div style={{ padding: "var(--space-xl)", color: "var(--text-muted)" }}>Loading...</div>
      </ViewContainer>
    );
  }

  return (
    <>
    <ViewContainer
      title="System Settings"
      actions={
        <button
          style={{ ...btnStyle, opacity: hasDirty && !save.saving && !saveBlocked ? 1 : 0.5 }}
          onClick={() => void save.save()}
          disabled={!hasDirty || save.saving || saveBlocked}
          title={saveBlocked ? "Fix the validation errors below before saving" : undefined}
        >
          <Save size={14} />
          <span>{save.saving ? "Saving..." : "Save"}</span>
        </button>
      }
    >
      <div style={{ maxWidth: 700 }}>
        {/* Restart warning + action.
            - hasDirty: settings unsaved; user has to save first.
            - !hasDirty: settings saved; offer to restart in-app. */}
        {/* Not once a restart has handed this page over: the server it would
            ask can no longer be reached from here. */}
        {(restartNeeded || listen.needsRestart) && !restart.handedOver && (
          <div style={warningBox} data-testid="restart-banner">
            <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)", flexWrap: "wrap", flex: 1 }}>
              <span style={{ flex: 1 }}>
                {hasDirty
                  ? "Network or security changes need a restart. Save first, then restart from here."
                  : "Saved. Restart the server to apply network or security changes."}
              </span>
              {!hasDirty && (
                <button
                  onClick={() => void restart.start()}
                  style={{
                    ...btnStyle,
                    background: "rgb(255, 152, 0)",
                    color: "#fff",
                    border: "none",
                  }}
                >
                  <Power size={14} />
                  <span>Restart now</span>
                </button>
              )}
            </div>
          </div>
        )}

        <NoCredentialWarning auth={auth} reachesNetwork={listen.reachesNetwork} />

        {/* Appearance */}
        <h3 style={sectionTitle}>Appearance</h3>
        <div style={cardStyle}>
          <FieldRow label="Theme">
            <select
              style={selectStyle}
              value={theme}
              onChange={(e) => {
                const t = e.target.value as "dark" | "light";
                setThemeState(t);
                setTheme(t);
              }}
            >
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </FieldRow>
        </div>

        {/* Network */}
        <h3 style={sectionTitle}>Network</h3>
        <div style={cardStyle}>
          <div style={fieldRow}>
            <AcceptConnectionsField
              interfaces={adapters.interfaces}
              mode={listen.mode}
              adapter={listen.adapter}
              runningName={listen.running?.adapter_name ?? ""}
              problem={listen.needsRestart ? null : (listen.running?.problem ?? null)}
              preview={listen.preview}
              changed={listen.changed}
              hold={hold}
              urlFacts={urlFacts}
              onChange={(mode, adapter) => {
                update("network", "accept_connections_from", mode);
                update("network", "accept_connections_adapter", adapter);
              }}
            />
          </div>
          <div style={fieldRow}>
            <ReachDevicesField
              interfaces={adapters.interfaces}
              chosen={reachChosen}
              resolved={config._reach_devices}
              hold={hold}
              loading={adapters.loading}
              onChange={(identity) => update("network", "reach_devices_through", identity)}
              onRefresh={adapters.reload}
            />
          </div>
          <FieldRow label="HTTP port" htmlFor="cfg-http-port" held={heldSetting(hold, "network", "http_port")}>
            <NumberBox
              id="cfg-http-port"
              value={net.http_port}
              fallback={8080}
              onChange={(n) => update("network", "http_port", n)}
            />
          </FieldRow>
          <SwitchRow
            title="Short URLs (port 80 redirect)"
            help={<>
              Lets people type addresses without a port: <code>http://&lt;server&gt;/panel</code> forwards
              to the real port automatically. Needs port 80 free on this machine. On Linux the
              OpenAVC service also needs permission to bind it (fresh installs have this; see
              the deployment guide).
            </>}
            held={heldSetting(hold, "network", "port80_redirect")}
            checked={!!net.port80_redirect}
            onChange={(v) => update("network", "port80_redirect", v)}
          />
          {/* With this computer only, it is shown off and left as saved, so
              choosing a network again brings back what was set. */}
          <SwitchRow
            title="Advertise on the network"
            help={listen.mode === "this_computer"
              ? "Nothing on the network can connect, so there is nothing to announce."
              : listen.mode === "adapter"
                ? `Lets the OpenAVC Panel app on the ${listenAdapterName || "chosen"} network find this system in its list. Turning this off hides the system from the list; devices can still connect by address.`
                : "Lets the OpenAVC Panel app find this system in its list. Turning this off hides the system from the list; devices can still connect by address."}
            helpTestId="advertise-help"
            label="Advertise on the network"
            held={listen.mode === "this_computer"
              ? { ...heldSetting(hold, "discovery", "advertise"), locked: "this_computer" }
              : heldSetting(hold, "discovery", "advertise")}
            checked={listen.mode === "this_computer" ? false : advertise}
            onChange={(v) => update("discovery", "advertise", v)}
          />
        </div>

        {/* Host network configuration (Pi appliance / Linux with NetworkManager).
            Renders nothing when the deployment has no backend. */}
        <HostNetworkCard />

        {/* Security (HTTPS / TLS) */}
        <h3 style={sectionTitle}>Security</h3>
        <SecurityCard
          tls={tls}
          savedTlsEnabled={config?.tls?.enabled}
          httpPort={net.http_port}
          tlsStatus={certificate.status}
          updateTls={updateTls}
          hold={hold}
          tlsPortError={tlsPortError}
          tlsProvidedBlank={tlsProvidedBlank}
        />

        <TrustedCertificateCard
          page={trustedCertPage}
          tlsStatus={certificate.status}
          tlsPort={tls?.port}
          adapters={adapters.adapters}
          hold={hold}
        />

        {/* Access */}
        <h3 style={sectionTitle}>Access</h3>
        <AccessCards draft={draft} hold={hold} ssh={ssh} problems={checks} />

        {/* Logging */}
        <h3 style={sectionTitle}>Logging</h3>
        <div style={cardStyle}>
          <FieldRow label="Log level" htmlFor="cfg-log-level" held={heldSetting(hold, "logging", "level")}>
            <select
              id="cfg-log-level"
              style={selectStyle}
              value={log.level}
              onChange={(e) => update("logging", "level", e.target.value)}
            >
              <option value="debug">Debug</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="error">Error</option>
            </select>
          </FieldRow>
          <SwitchRow
            title="File logging"
            help="Write logs to disk in the data directory."
            checked={log.file_enabled}
            onChange={(v) => update("logging", "file_enabled", v)}
          />
          <FieldRow label="Max file size (MB)">
            <NumberBox value={log.max_size_mb} fallback={50} onChange={(n) => update("logging", "max_size_mb", n)} />
          </FieldRow>
          <FieldRow label="Max log files">
            <NumberBox value={log.max_files} fallback={5} onChange={(n) => update("logging", "max_files", n)} />
          </FieldRow>
        </div>

        {/* Updates */}
        <h3 style={sectionTitle}>Updates</h3>
        <div style={cardStyle}>
          {updateGuidance !== "" ? (
            <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>{updateGuidance ?? "Loading update settings..."}</div>
          ) : <>
          <SwitchRow
            title="Check for updates"
            help="Periodically check GitHub for new releases."
            held={heldSetting(hold, "updates", "check_enabled")}
            checked={upd.check_enabled}
            onChange={(v) => update("updates", "check_enabled", v)}
          />
          <FieldRow label="Channel" held={heldSetting(hold, "updates", "channel")} help="Beta includes pre-release versions.">
            <select
              style={selectStyle}
              value={upd.channel}
              onChange={(e) => update("updates", "channel", e.target.value)}
            >
              <option value="stable">Stable</option>
              <option value="beta">Beta</option>
            </select>
          </FieldRow>
          <FieldRow label="Check interval (hours)" help="How often to check for new versions.">
            <NumberBox
              value={upd.auto_check_interval_hours}
              fallback={24}
              onChange={(n) => update("updates", "auto_check_interval_hours", n)}
            />
          </FieldRow>
          <SwitchRow
            title="Notify only"
            help="Say when an update is available, but never install one on its own. An update scheduled from the cloud is skipped too; you can still install one yourself."
            checked={upd.notify_only}
            onChange={(v) => update("updates", "notify_only", v)}
          />
          </>}
        </div>

        {/* Panel display — only where this instance drives a screen it can dim.
            These live in the PROJECT, not system.json, so one template carries
            them to every panel a customer deploys it to. */}
        {panelDimAvailable && projectLoaded && <>
        <h3 style={sectionTitle}>Panel Display</h3>
        <PanelDisplayCard />
        </>}

        {/* Device behaviour that travels with the project, for the same reason. */}
        {projectLoaded && <>
        <h3 style={sectionTitle}>Devices</h3>
        <DevicesCard />
        </>}

        {/* Kiosk — only shown on Pi/kiosk-capable deployments */}
        {kioskAvailable && <>
        <h3 style={sectionTitle}>Kiosk</h3>
        <div style={cardStyle}>
          <SwitchRow
            title="Kiosk mode"
            help="Launch the Panel UI fullscreen on an attached display (e.g. Raspberry Pi with HDMI touchscreen)."
            checked={kiosk.enabled}
            onChange={(v) => update("kiosk", "enabled", v)}
          />
          <FieldRow label="Target URL" help="The URL loaded in the kiosk browser. Defaults to the local Panel UI.">
            <input
              style={inputStyle}
              value={kiosk.target_url}
              onChange={(e) => update("kiosk", "target_url", e.target.value)}
            />
          </FieldRow>
          <SwitchRow
            title="Show cursor"
            help="Show the mouse cursor on the kiosk display. Disable for touch-only panels."
            checked={kiosk.cursor_visible}
            onChange={(v) => update("kiosk", "cursor_visible", v)}
          />
        </div>
        </>}
      </div>
    </ViewContainer>

    <RebootQuestion save={save} />

    <RestartQuestion restart={restart} />

    <CutOffQuestion save={save} listen={listen} tls={tls} httpPort={net.http_port} />

    <RestartProgress restart={restart} facts={listen.facts} />
    </>
  );
}
