import { useCallback } from "react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { HostNetworkCard } from "../components/system/HostNetworkCard";
import { envHold } from "../components/system/EnvHeldNote";
import { sectionTitle } from "../components/system/settingsStyles";
import { saveChecks } from "./settings/saveChecks";
import { DevicesCard, PanelDisplayCard } from "./settings/ProjectSettingsCards";
import { SecurityCard } from "./settings/SecurityCard";
import { TrustedCertificateCard, type TrustedCertificatePage } from "./settings/TrustedCertificateCard";
import type { UpdateTls } from "./settings/YourOwnCertificate";
import { AccessCards, NoCredentialWarning, useSshAccess } from "./settings/AccessCards";
import { NetworkCard } from "./settings/NetworkCard";
import { AppearanceCard, KioskCard, LoggingCard, UpdatesCard } from "./settings/SystemCards";
import { useAdapters, useCertificateStatus, useInstallFacts } from "./settings/serverReads";
import { useListenChoice } from "./settings/listenChoice";
import { useRestart } from "./settings/restartFlow";
import { RestartBanner, RestartProgress, RestartQuestion } from "./settings/RestartDialogs";
import { useSettingsSave } from "./settings/saveFlow";
import { useSettingsDraft } from "./settings/settingsDraft";
import { CutOffQuestion, RebootQuestion, SaveButton } from "./settings/SaveDialogs";
import { useProjectStore } from "../store/projectStore";

export function SystemSettingsView() {
  // The config, the changes not saved yet, and whether one needs a restart.
  const draft = useSettingsDraft();
  const { config, dirty, update, merged, restartNeeded } = draft;
  // Read when Settings opens, in this order after the config.
  const install = useInstallFacts();
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
    kioskAvailable: install.kioskAvailable,
    listen,
    restart,
    saved: (fresh) => {
      draft.saved(fresh);
      certificate.load();
    },
  });

  const net = merged("network");
  const auth = merged("auth");
  const tls = merged("tls");
  const checks = saveChecks({ tls, httpPort: net.http_port, auth, dirtyAuth: dirty.auth });
  // What a restart applies: HTTPS as saved, not as on screen.
  const savedTlsEnabled = config?.tls?.enabled;

  const trustedCertPage: TrustedCertificatePage = {
    savedTlsEnabled,
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
    <ViewContainer title="System Settings" actions={<SaveButton save={save} blocked={checks.blocked} />}>
      <div style={{ maxWidth: 700 }}>
        <RestartBanner needed={restartNeeded || listen.needsRestart} unsaved={save.hasChanges} restart={restart} />
        <NoCredentialWarning auth={auth} reachesNetwork={listen.reachesNetwork} />

        <h3 style={sectionTitle}>Appearance</h3>
        <AppearanceCard />

        <h3 style={sectionTitle}>Network</h3>
        <NetworkCard draft={draft} listen={listen} adapters={adapters} hold={hold} />

        {/* Host network configuration (Pi appliance / Linux with NetworkManager).
            Renders nothing when the deployment has no backend. */}
        <HostNetworkCard />

        <h3 style={sectionTitle}>Security</h3>
        <SecurityCard
          tls={tls}
          savedTlsEnabled={savedTlsEnabled}
          httpPort={net.http_port}
          tlsStatus={certificate.status}
          updateTls={updateTls}
          hold={hold}
          tlsPortError={checks.tlsPortError}
          tlsProvidedBlank={checks.tlsProvidedBlank}
        />

        <TrustedCertificateCard
          page={trustedCertPage}
          tlsStatus={certificate.status}
          tlsPort={tls?.port}
          adapters={adapters.adapters}
          hold={hold}
        />

        <h3 style={sectionTitle}>Access</h3>
        <AccessCards draft={draft} hold={hold} ssh={ssh} problems={checks} />

        <h3 style={sectionTitle}>Logging</h3>
        <LoggingCard draft={draft} hold={hold} />

        <h3 style={sectionTitle}>Updates</h3>
        <UpdatesCard draft={draft} hold={hold} guidance={install.updateGuidance} />

        {/* Panel display — only where this instance drives a screen it can dim.
            These live in the PROJECT, not system.json, so one template carries
            them to every panel a customer deploys it to. */}
        {install.panelDimAvailable && projectLoaded && <>
        <h3 style={sectionTitle}>Panel Display</h3>
        <PanelDisplayCard />
        </>}

        {/* Device behaviour that travels with the project, for the same reason. */}
        {projectLoaded && <>
        <h3 style={sectionTitle}>Devices</h3>
        <DevicesCard />
        </>}

        {/* Kiosk — only shown on Pi/kiosk-capable deployments */}
        {install.kioskAvailable && <>
        <h3 style={sectionTitle}>Kiosk</h3>
        <KioskCard draft={draft} />
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
