import { useCallback, useState } from "react";
import { DriverInUseDialog } from "./DriverInUseNotice";
import { devicesOnDriver, openDevice, type DeviceOnDriver } from "./driverInUse";

interface ProjectDevice {
  id: string;
  name?: string;
  driver: string;
}

/**
 * The in-use box a delete button in a list answers with, for the Driver
 * Builder's delete and the Code view's delete. `unlessInUse` opens it instead
 * of going on when project devices use the driver; `show` opens it with the
 * devices a server refusal named; `dialog` is what to render. `beforeLeaving`
 * is asked before a device is opened, for a view that would lose unsaved work.
 */
export function useDriverInUse(action: string, beforeLeaving?: (leave: () => void) => void) {
  const [devices, setDevices] = useState<DeviceOnDriver[] | null>(null);

  const show = useCallback((refused: DeviceOnDriver[] | null) => {
    if (refused && refused.length > 0) setDevices(refused);
  }, []);

  const unlessInUse = useCallback(
    (project: readonly ProjectDevice[] | null | undefined, driverId: string, goOn: () => void) => {
      const using = devicesOnDriver(project, driverId);
      if (using.length > 0) setDevices(using);
      else goOn();
    },
    [],
  );

  const onOpenDevice = useCallback(
    (deviceId: string) => {
      const leave = () => {
        setDevices(null);
        openDevice(deviceId);
      };
      if (beforeLeaving) beforeLeaving(leave);
      else leave();
    },
    [beforeLeaving],
  );

  const dialog = devices ? (
    <DriverInUseDialog
      devices={devices}
      action={action}
      onOpenDevice={onOpenDevice}
      onClose={() => setDevices(null)}
    />
  ) : null;

  return { show, unlessInUse, dialog };
}
