/**
 * The banners a device's page shows while the device is away on purpose or
 * offline: restarting, asleep, paused, and the Offline banner. Which one shows
 * is decided in deviceAway.ts; the missing-driver banner (OrphanBanner.tsx)
 * and the login box (NeedsLoginBanner.tsx) are the other two.
 *
 * Restarting and asleep are not the Offline banner in another colour: that one
 * names a fault and tells somebody to go and check something, and here the
 * honest instruction is to wait. `offline_detail` carries their sentence, so
 * the wording lives in core/connection_fault.py with everything else that key
 * can say.
 */
import { Moon, Pause, Play, RefreshCw, WifiOff } from "lucide-react";
import type { CSSProperties } from "react";
import * as api from "../../api/restClient";
import type { ProblemReportContext } from "../../api/problemReportClient";
import { pausedBannerText } from "../../components/shared/pausedText";
import {
  type DeviceAway,
  driverConnectionHint,
  hasSignInFields,
  offlineNextStep,
  reportableOffline,
} from "./deviceAway";

const lineStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  marginBottom: "var(--space-md)",
  fontSize: "var(--font-size-sm)",
};

const INFO = "var(--color-info, #6aa3d6)";
const WARNING = "var(--color-warning, #f59e0b)";

/** A command took the device away on purpose and it is expected back. */
export function RestartingBanner({ detail }: { detail: string }) {
  return (
    <div
      style={{
        ...lineStyle,
        background: "rgba(245, 158, 11, 0.1)",
        border: `1px solid ${WARNING}`,
        color: WARNING,
      }}
    >
      <RefreshCw size={14} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1 }}>{detail || "Restarting."}</span>
    </div>
  );
}

/** A command put the device to sleep; it stays off the network until its wake
 *  command. Nothing is wrong, so there are no troubleshooting steps. */
export function AsleepBanner({ detail }: { detail: string }) {
  return (
    <div role="status" style={{ ...lineStyle, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
      <Moon size={14} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1 }}>{detail || "Asleep."}</span>
    </div>
  );
}

/** Live Test or a Device Audit holds the device. Resume is offered so a pause
 *  left behind by a closed test can be cleared here. */
export function PausedBanner({ deviceId, pausedFor }: { deviceId: string; pausedFor: unknown }) {
  const resume = async () => {
    try {
      await api.resumeDevice(deviceId);
    } catch (e) {
      console.error(e);
    }
  };
  return (
    <div style={{ ...lineStyle, background: "var(--bg-info, #1a2a3a)", border: `1px solid ${INFO}`, color: INFO }}>
      <Pause size={14} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1 }}>{pausedBannerText(pausedFor)}</span>
      <button
        onClick={resume}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "2px 10px",
          borderRadius: 12,
          border: `1px solid ${INFO}`,
          background: "transparent",
          color: INFO,
          fontSize: 11,
          cursor: "pointer",
        }}
        title="Clear the pause and reconnect now"
      >
        <Play size={12} /> Resume
      </button>
    </div>
  );
}

/** The Offline banner's last line. Product hardware that stopped retrying
 *  points at its own status rather than at a network or a driver. */
function offlineLastLine(installed: boolean, reason: string | undefined, failed: boolean, signInFields: boolean) {
  const productStopped = installed && failed;
  if (productStopped && reason !== "no_simulator") {
    return "Automatic reconnect is paused. Check this product's OpenAVC status, then press Reconnect.";
  }
  return offlineNextStep(reason, failed, signInFields);
}

/** The server's reason a device is offline (device.<id>.offline_detail), the
 *  driver's own troubleshooting line (DRIVER_INFO.help.connection), and what
 *  happens next. Everything it says is read here, from the device's away state
 *  and its driver's declaration, so a new line on it is a change to this file
 *  and deviceAway.ts, never to the page that places it. */
export function OfflineBanner({
  away,
  driverInfo,
  installed = false,
  onReport,
}: {
  away: DeviceAway;
  /** The running driver's declaration (DeviceInfo.driver_info). */
  driverInfo?: Record<string, unknown>;
  installed?: boolean;
  /** Opens Report a problem, offered when the driver could have caused the fault. */
  onReport: (context: ProblemReportContext) => void;
}) {
  const detail = away.offlineDetail;
  const hint = driverConnectionHint(driverInfo);
  const signInFields = hasSignInFields(driverInfo?.config_schema as Record<string, unknown> | undefined);
  const report = reportableOffline(away.offlineReason)
    ? () => onReport({ source: "offline_banner", error: `Offline. ${detail}` })
    : undefined;
  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.1)",
        border: "2px solid rgba(245, 158, 11, 0.4)",
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
      }}
    >
      <WifiOff size={18} style={{ color: WARNING, flexShrink: 0, marginTop: 2 }} />
      <div>
        <div style={{ fontWeight: 600, color: WARNING, fontSize: "var(--font-size-md)" }}>
          Offline
        </div>
        <div style={{ fontSize: "var(--font-size-sm)", marginTop: 2 }}>{detail}</div>
        {hint && (
          <div style={{ fontSize: "var(--font-size-sm)", marginTop: "var(--space-xs)", whiteSpace: "pre-line" }}>
            {hint}
          </div>
        )}
        <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
          {offlineLastLine(installed, away.offlineReason, away.reconnectFailed, signInFields)}
          {report && <OfflineReportButton onClick={report} />}
        </div>
      </div>
    </div>
  );
}

function OfflineReportButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        marginLeft: "var(--space-sm)",
        padding: "2px 10px",
        borderRadius: 12,
        border: `1px solid ${WARNING}`,
        background: "transparent",
        color: WARNING,
        fontSize: 11,
        cursor: "pointer",
      }}
    >
      Report a problem
    </button>
  );
}
