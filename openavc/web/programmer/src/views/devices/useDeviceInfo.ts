/**
 * What the running system says about the device a page shows (GET
 * /api/devices/{id}), read again whenever something changes what the device
 * offers.
 *
 * Read again when the connection flips: a role-adaptive driver narrows its
 * commands and actions to what the hardware reports at connect, so what was
 * read while the device was offline goes stale the moment it comes up (and
 * the other way round). When its web page is found: the platform looks for it
 * in the background after connect, and Open Web UI comes with the answer.
 * When the running system starts or stops running it. And when it starts or
 * stops waiting for a login: which settings wait is in this answer, and a
 * login saved anywhere changes it.
 *
 * A 404 means the running system has no such device: its driver failed while
 * loading (the device log says why). The device is still in the project, so
 * the page can still edit, duplicate or delete it (`notRunning`).
 *
 * `answered` turns true once the first read settles, whatever it came to:
 * until then the page does not know the device's child types, so it lists no
 * live keys rather than drawing every child key as a row.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../api/restClient";
import { ApiError } from "../../api/errors";
import type { DeviceInfo } from "../../api/types";

/** The live values whose change sends the page back for the device's info. */
export interface DeviceInfoTriggers {
  connected: boolean;
  webUiUrl: unknown;
  runtimeEnabled: boolean;
  awaitingLogin: boolean;
}

export function useDeviceInfo(deviceId: string, triggers: DeviceInfoTriggers) {
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [notRunning, setNotRunning] = useState(false);
  const [answered, setAnswered] = useState(false);

  // The id the page is showing, as of the last render. An answer that
  // arrives after the page has moved to another device would otherwise draw
  // that device's commands, settings and driver under this one's name.
  const shownDeviceId = useRef(deviceId);
  shownDeviceId.current = deviceId;

  const refetch = useCallback(() => {
    const forDevice = deviceId;
    api.getDevice(deviceId)
      .then((info) => {
        if (shownDeviceId.current !== forDevice) return;
        setDeviceInfo(info);
        setNotRunning(false);
        setAnswered(true);
      })
      .catch((e) => {
        if (shownDeviceId.current === forDevice) {
          if (isMissingDevice(e)) {
            setDeviceInfo(null);
            setNotRunning(true);
          }
          setAnswered(true);
        }
        console.error(e);
      });
  }, [deviceId]);

  const { connected, webUiUrl, runtimeEnabled, awaitingLogin } = triggers;
  useEffect(() => {
    refetch();
  }, [refetch, connected, webUiUrl, runtimeEnabled, awaitingLogin]);

  /** Once the device's missing driver is installed: the device as the
   *  running system now has it. */
  const takeActivated = useCallback(() => {
    api.getDevice(deviceId).then(setDeviceInfo).catch(console.error);
  }, [deviceId]);

  return { deviceInfo, notRunning, answered, refetch, takeActivated };
}

function isMissingDevice(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}
