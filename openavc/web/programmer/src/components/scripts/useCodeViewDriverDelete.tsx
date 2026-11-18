import { useCallback } from "react";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import type { PythonDriverInfo } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { showError } from "../../store/toastStore";
import { inUseDevices } from "../shared/driverInUse";
import { useDriverInUse } from "../shared/useDriverInUse";

/** A question the Code view asks in its one confirm dialog. */
export interface CodeViewQuestion {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
}

interface CodeViewDriverDeleteOptions {
  pythonDrivers: PythonDriverInfo[];
  /** The open file has unsaved changes, which opening a device would lose. */
  isDirty: boolean;
  /** Ask in the view's confirm dialog, or close it (null). */
  ask: (question: CodeViewQuestion | null) => void;
  /** After a delete: what the view does to its list and its open file. */
  onDeleted: (driverId: string) => Promise<void>;
}

/**
 * The Code view's delete of a Python driver: a driver devices in the project
 * use answers with the in-use box, which names them; any other is confirmed,
 * then deleted, and a refusal the server sends because the project changed
 * meanwhile opens the in-use box too. Opening a device from the box asks first
 * when the open file has unsaved changes. `request` is the file tree's delete
 * button; `dialog` renders the in-use box.
 */
export function useCodeViewDriverDelete({ pythonDrivers, isDirty, ask, onDeleted }: CodeViewDriverDeleteOptions) {
  const beforeLeaving = useCallback(
    (leave: () => void) => {
      if (!isDirty) return leave();
      ask({
        title: "Unsaved Changes",
        message: "You have unsaved changes. Open the device and discard them?",
        confirmLabel: "Discard & Open",
        onConfirm: () => {
          ask(null);
          leave();
        },
      });
    },
    [isDirty, ask],
  );
  const inUse = useDriverInUse("delete", beforeLeaving);

  const remove = async (id: string) => {
    ask(null);
    try {
      await api.deletePythonDriver(id);
      await onDeleted(id);
    } catch (e) {
      const refused = inUseDevices(e);
      if (refused) inUse.show(refused);
      else showError(`Delete failed: ${parseApiError(e)}`);
    }
  };

  const request = (id: string) => {
    const name = pythonDrivers.find((d) => d.id === id)?.name || id;
    inUse.unlessInUse(useProjectStore.getState().project?.devices, id, () =>
      ask({
        title: "Delete Driver",
        message: `Delete Python driver "${name}"? This will remove the driver file from driver_repo/.`,
        confirmLabel: "Delete",
        onConfirm: () => void remove(id),
      }),
    );
  };

  return { request, dialog: inUse.dialog };
}
