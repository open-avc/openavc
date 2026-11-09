/**
 * The two checks a device page's header starts, Test and Audit this device,
 * and what each leaves behind. The header starts them; the page draws what
 * they found below the banners (`DeviceCheckResults.tsx`), so the state lives
 * here, between the two.
 *
 * Test asks the server to reach the device where it really connects and keeps
 * the answer until the device connects or drops (`useConnectionTestResult`).
 * Audit this device opens the Device Audit wizard on the device's address,
 * driver and saved settings, or says why it cannot (a device on a serial
 * port, say).
 */
import { useState } from "react";
import * as api from "../../api/restClient";
import { getAuditDeviceTarget } from "../../api/auditClient";
import { parseApiError } from "../../api/errors";
import { useAuditStore } from "../../store/auditStore";
import { useConnectionTestResult } from "./useConnectionTestResult";

export function useDeviceChecks(deviceId: string, connected: boolean) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useConnectionTestResult(deviceId, connected);
  const [auditRefusal, setAuditRefusal] = useState("");
  const [openingAudit, setOpeningAudit] = useState(false);

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testDeviceConnection(deviceId);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, error: parseApiError(e), latency_ms: null });
    } finally {
      setTesting(false);
    }
  };

  const openAudit = async () => {
    setAuditRefusal("");
    setOpeningAudit(true);
    try {
      const target = await getAuditDeviceTarget(deviceId);
      if (!target.auditable) {
        setAuditRefusal(target.reason);
        return;
      }
      useAuditStore.getState().openWizard({ address: target.address, deviceId });
    } catch (e) {
      setAuditRefusal(parseApiError(e));
    } finally {
      setOpeningAudit(false);
    }
  };

  return {
    testing,
    testResult,
    runTest,
    openingAudit,
    auditRefusal,
    openAudit,
    dismissAuditRefusal: () => setAuditRefusal(""),
  };
}

export type DeviceChecks = ReturnType<typeof useDeviceChecks>;
