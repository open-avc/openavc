import { useCallback, useEffect, useState } from "react";
import type { DeviceAction } from "../../api/types";
import { SetupActionWizard } from "./SetupActionWizard";
import { takeReopenedRun } from "./openSetupRun";

export interface SetupWizardState {
  action: DeviceAction | null;
  /** The run it opened on, when the tab reopened one after a sign-in or a reload. */
  runId?: string;
  open: (action: DeviceAction) => void;
  close: () => void;
}

/**
 * The device page's setup wizard: closed, opened by its button, or opened by
 * itself on a setup run this tab had open before a sign-in or a reload
 * (`openSetupRun.ts`), once the device's actions are known and whether or
 * not the button shows now.
 */
export function useSetupWizard(deviceId: string, actions: DeviceAction[]): SetupWizardState {
  const [action, setAction] = useState<DeviceAction | null>(null);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  useEffect(() => {
    const run = takeReopenedRun(deviceId, (id) => actions.some((a) => a.id === id));
    const reopened = run && actions.find((a) => a.id === run.actionId);
    if (!run || !reopened) return;
    setAction(reopened);
    setRunId(run.runId);
  }, [deviceId, actions]);
  const open = useCallback((next: DeviceAction) => {
    setAction(next);
    setRunId(undefined);
  }, []);
  const close = useCallback(() => {
    setAction(null);
    setRunId(undefined);
  }, []);
  return { action, runId, open, close };
}

/** The wizard `useSetupWizard` holds, or nothing while it is closed. */
export function SetupWizardSlot({
  deviceId,
  wizard,
  onComplete,
}: {
  deviceId: string;
  wizard: SetupWizardState;
  onComplete: () => void;
}) {
  if (!wizard.action) return null;
  return (
    <SetupActionWizard
      deviceId={deviceId}
      action={wizard.action}
      runId={wizard.runId}
      onClose={wizard.close}
      onComplete={onComplete}
    />
  );
}
