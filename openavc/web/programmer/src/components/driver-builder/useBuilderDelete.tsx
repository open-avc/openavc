import { useState } from "react";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { useDriverInUse } from "../shared/useDriverInUse";

/**
 * The Driver Builder's delete: a driver devices in the project use answers with
 * the in-use box, which names them; any other is confirmed, then deleted, and a
 * refusal the server sends because the project changed meanwhile opens the
 * in-use box too. `request` is the list's delete button; `dialogs` renders both.
 */
export function useBuilderDelete() {
  const deleteDriver = useDriverBuilderStore((s) => s.deleteDriver);
  const devices = useProjectStore((s) => s.project?.devices);
  const [confirming, setConfirming] = useState<string | null>(null);
  const inUse = useDriverInUse("delete");

  const request = (id: string) => inUse.unlessInUse(devices, id, () => setConfirming(id));

  const confirm = async () => {
    if (!confirming) return;
    const refused = await deleteDriver(confirming);
    setConfirming(null);
    inUse.show(refused);
  };

  const dialogs = (
    <>
      {confirming && (
        <ConfirmDialog
          title="Delete Driver"
          message={`Are you sure you want to delete the driver "${confirming}"? This cannot be undone.`}
          confirmLabel="Delete"
          onConfirm={confirm}
          onCancel={() => setConfirming(null)}
        />
      )}
      {inUse.dialog}
    </>
  );

  return { request, dialogs };
}
