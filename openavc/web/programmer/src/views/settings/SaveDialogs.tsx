/** Settings' Save button and the two questions Save asks (saveFlow.ts):
 *  "Change who can connect?" before a save that would cut this browser off,
 *  and the kiosk's reboot after a save that changed it. Each question draws
 *  nothing while closed, so the page places both always. */
import { Save } from "lucide-react";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { CutOffWarning } from "../../components/system/ListenCutOff";
import type { SystemConfig } from "../../api/restClient";
import * as api from "../../api/restClient";
import { showError, showSuccess } from "../../store/toastStore";
import type { ListenChoice } from "./listenChoice";
import { btnStyle } from "../../components/system/settingsStyles";
import { questionUrls, type SettingsSave } from "./saveFlow";

/** Save, off while there is nothing to save, while a save is in flight, and
 *  while saveChecks refuses one. */
export function SaveButton({ save, blocked }: { save: SettingsSave; blocked: boolean }) {
  const ready = save.hasChanges && !save.saving && !blocked;
  return (
    <button
      style={{ ...btnStyle, opacity: ready ? 1 : 0.5 }}
      onClick={() => void save.save()}
      disabled={!ready}
      title={blocked ? "Fix the validation errors below before saving" : undefined}
    >
      <Save size={14} />
      <span>{save.saving ? "Saving..." : "Save"}</span>
    </button>
  );
}

export function RebootQuestion({ save }: { save: SettingsSave }) {
  if (!save.rebootAsked) return null;
  return (
    <ConfirmDialog
      title="Reboot Required"
      message="Kiosk display settings require a reboot to take effect. Reboot now?"
      confirmLabel="Reboot Now"
      cancelLabel="Later"
      onConfirm={async () => {
        save.closeRebootQuestion();
        try {
          await api.rebootSystem();
          showSuccess("Device is rebooting...");
        } catch {
          showError("Reboot not available. Restart the device manually.");
        }
      }}
      onCancel={() => {
        save.closeRebootQuestion();
        showSuccess("Settings saved. Reboot the device for changes to take effect.");
      }}
    />
  );
}

/** Asked from the server's own answer for the change; "Restart and apply"
 *  saves and restarts at once. */
export function CutOffQuestion({ save, listen, tls, httpPort }: {
  save: SettingsSave;
  listen: ListenChoice;
  /** HTTPS as on screen. */
  tls: Partial<SystemConfig["tls"]> | undefined;
  httpPort: number;
}) {
  const check = save.cutOffCheck;
  if (!check) return null;
  const where = questionUrls(tls, httpPort, check.panel_addresses);
  return (
    <ConfirmDialog
      title="Change who can connect?"
      message={<CutOffWarning facts={listen.facts(check, where.programmerUrls, where.localUrl)} />}
      confirmLabel="Restart and apply"
      destructive
      onConfirm={() => {
        save.closeCutOffCheck();
        void save.save(true);
      }}
      onCancel={save.closeCutOffCheck}
    />
  );
}
