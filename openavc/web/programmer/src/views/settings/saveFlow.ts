/** What Settings' Save does once its checks pass (saveChecks.ts), in order: a
 *  change of who can connect that would cut this browser off is asked about
 *  first ("Change who can connect?") and nothing is saved until the person
 *  agrees; the project's own settings (Panel Display, Devices) are saved
 *  next, because the project store is the one with a conflict path and its
 *  failure must not hide behind a saved system.json; then the system
 *  settings, the config read again, and what follows: the kiosk's reboot
 *  question, the restart already agreed to, or the restart prompt. */
import { useState } from "react";
import * as api from "../../api/restClient";
import type { ListenPreview, SystemConfig } from "../../api/restClient";
import { ApiError, parseApiError } from "../../api/errors";
import { useProjectStore } from "../../store/projectStore";
import { showError, showSuccess } from "../../store/toastStore";
import { REDACTED } from "./settingsControls";
import type { ListenChoice } from "./listenChoice";
import type { Restart } from "./restartFlow";

/** The unsaved changes as the server takes them. A secret sent back as the
 *  server gave it means "leave it as it is", so it is left out, and a section
 *  left with nothing is not sent. */
export function changesToSend(dirty: Partial<SystemConfig>): Record<string, Record<string, unknown>> {
  const payload: Record<string, Record<string, unknown>> = {};
  for (const [section, fields] of Object.entries(dirty)) {
    const clean = Object.fromEntries(
      Object.entries(fields as Record<string, unknown>).filter(([, value]) => value !== REDACTED),
    );
    if (Object.keys(clean).length > 0) payload[section] = clean;
  }
  return payload;
}

/** What follows a save that went through. The kiosk's reboot question
 *  handles its own flow, so nothing is stacked on it; "Restart and apply" was
 *  already the answer to the cut-off question. */
export function afterSave(f: {
  kioskChanged: boolean;
  kioskAvailable: boolean;
  cutOffConfirmed: boolean;
  needsRestart: boolean;
}): "reboot" | "restart" | "ask-restart" | null {
  if (f.kioskChanged && f.kioskAvailable) return "reboot";
  if (f.cutOffConfirmed) return "restart";
  if (f.needsRestart) return "ask-restart";
  return null;
}

/** What a save that failed says, or null for nothing. A 401 after the save
 *  went through is the save itself: a new password or username ends every
 *  session, this one included, and the sign-in screen has already taken
 *  over. "Failed to save" there told the person, once signed back in, that a
 *  password change which worked had not. */
export function saveFailure(e: unknown, wentThrough: boolean): string | null {
  if (!wentThrough) return "Failed to save: " + parseApiError(e);
  if (e instanceof ApiError && e.status === 401) return null;
  return "Settings saved, but this page could not refresh (" + parseApiError(e) +
    "). Reload the page to see the current values.";
}

/** The Programmer's addresses the cut-off question names: what this save will
 *  run with, so the ports on screen. */
export function questionUrls(
  tls: Partial<SystemConfig["tls"]> | undefined,
  httpPort: number,
  addresses: string[],
): { programmerUrls: string[]; localUrl: string } {
  const scheme = tls?.enabled ? "https" : "http";
  const port = tls?.enabled ? (tls.port ?? 8443) : httpPort;
  return {
    programmerUrls: addresses.map((ip) => `${scheme}://${ip}:${port}/programmer`),
    localUrl: `${scheme}://localhost:${port}/programmer`,
  };
}

/** Save, and the two questions it can ask: the cut-off question before it,
 *  the kiosk's reboot after it. */
export function useSettingsSave(page: {
  /** The system settings changed and not saved. */
  dirty: Partial<SystemConfig>;
  /** A change that needs a restart was made since Settings opened. */
  restartNeeded: boolean;
  kioskAvailable: boolean;
  listen: ListenChoice;
  restart: Restart;
  /** The save went through: the fresh config replaces the saved one, with
   *  nothing left unsaved. */
  saved: (fresh: SystemConfig) => void;
}) {
  const { dirty, restartNeeded, kioskAvailable, listen, restart } = page;
  // The Panel Display and Devices settings live in the PROJECT, so the one
  // Save button saves the project too.
  const projectDirty = useProjectStore((s) => s.dirty);
  const [saving, setSaving] = useState(false);
  const [cutOffCheck, setCutOffCheck] = useState<ListenPreview | null>(null);
  const [rebootAsked, setRebootAsked] = useState(false);

  /** "Change who can connect?" is asked first, from the server's own answer,
   *  for a change that would cut this browser off. True when Save goes on. */
  const cutOffAnswered = async (cutOffConfirmed: boolean): Promise<boolean> => {
    if (!listen.saving || cutOffConfirmed) return true;
    let check: ListenPreview;
    try {
      check = await listen.ask();
    } catch (e) {
      showError("Could not check the change to Accept connections from: " + parseApiError(e));
      return false;
    }
    if (!check.safety_net) return true;
    setCutOffCheck(check);
    return false;
  };

  /** The project's own settings, before system.json. True when Save goes on. */
  const projectSaved = async (): Promise<boolean> => {
    if (!projectDirty) return true;
    try {
      await useProjectStore.getState().save();
      return true;
    } catch (e) {
      showError("Could not save project settings: " + parseApiError(e));
      return false;
    }
  };

  /** Send the system settings, read the config again, and do what follows. */
  const send = async (payload: Record<string, Record<string, unknown>>, needsRestart: boolean, cutOffConfirmed: boolean) => {
    setSaving(true);
    let wentThrough = false;
    try {
      await api.updateSystemConfig(payload as Partial<SystemConfig>);
      wentThrough = true;
      showSuccess("Settings saved" + (needsRestart ? ". Restart required for some changes to take effect." : "."));
      const fresh = await api.getSystemConfig();
      page.saved(fresh);
      const next = afterSave({ kioskChanged: "kiosk" in payload, kioskAvailable, cutOffConfirmed, needsRestart });
      if (next === "reboot") setRebootAsked(true);
      else if (next === "restart") void restart.start(fresh);
      else if (next === "ask-restart") restart.ask();
    } catch (e) {
      const sentence = saveFailure(e, wentThrough);
      if (sentence) showError(sentence);
    } finally {
      setSaving(false);
    }
  };

  const save = async (cutOffConfirmed = false) => {
    if (!(await cutOffAnswered(cutOffConfirmed))) return;
    if (!(await projectSaved())) return;
    if (Object.keys(dirty).length === 0) {
      if (projectDirty) showSuccess("Settings saved.");
      return;
    }
    const payload = changesToSend(dirty);
    if (Object.keys(payload).length === 0) return;
    await send(payload, restartNeeded || listen.saving, cutOffConfirmed);
  };

  return {
    save,
    saving,
    /** Something changed in either store: the panel display and device
     *  settings count, or a change to only those would never save. */
    hasChanges: Object.keys(dirty).length > 0 || projectDirty,
    /** The server's answer while "Change who can connect?" is open. */
    cutOffCheck,
    closeCutOffCheck: () => setCutOffCheck(null),
    rebootAsked,
    closeRebootQuestion: () => setRebootAsked(false),
  };
}

export type SettingsSave = ReturnType<typeof useSettingsSave>;
