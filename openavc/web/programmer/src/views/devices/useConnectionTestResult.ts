import { useEffect, useState } from "react";
import type { DeviceConnectionTest } from "../../api/types";

/** The device page's Test Connection result. It describes the moment it was
 * run, so it is dropped when the device connects or drops, or when another
 * device is shown. */
export function useConnectionTestResult(deviceId: string, connected: boolean) {
  const [result, setResult] = useState<DeviceConnectionTest | null>(null);
  useEffect(() => {
    setResult(null);
  }, [deviceId, connected]);
  return [result, setResult] as const;
}
