/** The two restart dialogs Settings draws: "Restart to apply changes?" and the
 *  restart itself. Each draws nothing while closed, so the page places both
 *  always. Where the restart waits is `restartTargets` (restartFlow.ts). */
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { RestartProgressDialog } from "../../components/shared/RestartProgressDialog";
import { CutOffRestartLines, CutOffSaved } from "../../components/system/ListenCutOff";
import type { SystemConfig } from "../../api/restClient";
import type { ListenChoice } from "./listenChoice";
import { restartTargets, type Restart } from "./restartFlow";

/** Asked after a save that needs a restart, and by the trusted certificate. */
export function RestartQuestion({ restart }: { restart: Restart }) {
  if (!restart.asking) return null;
  return (
    <ConfirmDialog
      title="Restart to apply changes?"
      message={
        <>
          Your network or security changes need a server restart to take effect.
          The Programmer will reconnect automatically once the server is back,
          usually about ten seconds.
          <br /><br />
          You can also keep working and restart later from the banner at the top of this page.
        </>
      }
      confirmLabel="Restart now"
      cancelLabel="Later"
      onConfirm={() => {
        restart.stopAsking();
        void restart.start();
      }}
      onCancel={restart.stopAsking}
    />
  );
}

/** The restart, waiting where `restartTargets` says; when the saved change of
 *  who can connect cuts this browser off, it says where to go instead. */
export function RestartProgress({ restart, config, facts }: {
  restart: Restart;
  config: SystemConfig;
  facts: ListenChoice["facts"];
}) {
  if (!restart.dialogOpen) return null;
  const plan = restart.setup?.plan ?? { cutOff: false, hosts: [""] };
  const target = restartTargets(window.location, restart.openedAt.current, config, plan);
  const handoff = plan.cutOff && restart.setup?.preview
    ? facts(restart.setup.preview, target.urls, target.localUrl)
    : null;
  return (
    <RestartProgressDialog
      targetUrl={target.targetUrl}
      alsoTry={target.alsoTry}
      handoff={handoff ? {
        lines: <CutOffRestartLines facts={handoff} />,
        saved: <CutOffSaved facts={handoff} />,
      } : undefined}
      isProtocolSwitch={target.isProtocolSwitch}
      expectsNewCert={target.expectsNewCert}
      onClose={restart.closeDialog}
    />
  );
}
