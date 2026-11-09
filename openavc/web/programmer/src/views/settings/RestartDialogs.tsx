/** What Settings draws for the restart: the banner while a change needs one,
 *  "Restart to apply changes?", and the restart itself. Each draws nothing
 *  while it has nothing to say, so the page places all three always. Where
 *  the restart waits is the restart's own (restartFlow.ts). */
import { AlertTriangle, Power } from "lucide-react";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { RestartProgressDialog } from "../../components/shared/RestartProgressDialog";
import { CutOffRestartLines, CutOffSaved } from "../../components/system/ListenCutOff";
import type { ListenChoice } from "./listenChoice";
import { btnStyle, warningBox } from "../../components/system/settingsStyles";
import type { Restart } from "./restartFlow";

/** The banner at the top of Settings while a change needs a restart: save it
 *  first, or restart from here once it is saved. Not once a restart has
 *  handed this page over: the server it would ask can no longer be reached
 *  from here. */
export function RestartBanner({ needed, unsaved, restart }: {
  needed: boolean;
  unsaved: boolean;
  restart: Restart;
}) {
  if (!needed || restart.handedOver) return null;
  return (
    <div style={warningBox} data-testid="restart-banner">
      <AlertTriangle size={16} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)", flexWrap: "wrap", flex: 1 }}>
        <span style={{ flex: 1 }}>
          {unsaved
            ? "Network or security changes need a restart. Save first, then restart from here."
            : "Saved. Restart the server to apply network or security changes."}
        </span>
        {!unsaved && (
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
  );
}

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
export function RestartProgress({ restart, facts }: {
  restart: Restart;
  facts: ListenChoice["facts"];
}) {
  if (!restart.dialogOpen) return null;
  const target = restart.targets();
  const handoff = restart.handedOver && restart.setup?.preview
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
