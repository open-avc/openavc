/**
 * What the top of a device's page says about its condition, under the header:
 * the one away banner `awayBanner` (deviceAway.ts) chooses, product hardware's
 * own panel, and the line for children in trouble, in this order: the missing
 * driver, paused, restarting or asleep; children in trouble; product
 * hardware's panel; the login box or the Offline banner.
 *
 * `AWAY_BANNERS` holds every banner `awayBanner` can choose, with which side of
 * product hardware's panel it sits on and how it is drawn. It is keyed by the
 * `AwayBanner` type, so a new way of being away does not compile until it says
 * both; the page that places this block does not change.
 */
import type { ReactNode } from "react";
import type { DeviceConfig, DeviceInfo } from "../../api/types";
import type { ProblemReportContext } from "../../api/problemReportClient";
import { awayBanner, type AwayBanner, type DeviceAway } from "./deviceAway";
import { AsleepBanner, OfflineBanner, PausedBanner, RestartingBanner } from "./AwayBanners";
import { OrphanBanner } from "./OrphanBanner";
import { NeedsLoginBanner } from "./NeedsLoginBanner";
import { ChildTroubleBanner } from "./ChildTroubleBanner";
import { InstalledEquipment } from "./InstalledEquipment";

/** What any away banner may need to draw itself. */
interface BannerContext {
  deviceId: string;
  away: DeviceAway;
  info: DeviceInfo | null;
  config: DeviceConfig | undefined;
  installed: boolean;
  /** Opens the device's settings (Reassign Driver). */
  onEdit: (config: DeviceConfig) => void;
  onBrowseDrivers?: () => void;
  /** The missing driver is installed: take the device as the running system has it. */
  onActivated: () => void;
  /** A login was saved here: read the device again. */
  onLoginSaved: () => void;
  onReport: (context: ProblemReportContext) => void;
}

interface PlacedBanner {
  /** Below product hardware's panel rather than above it. */
  below: boolean;
  draw: (c: BannerContext) => ReactNode;
}

const AWAY_BANNERS: Record<AwayBanner, PlacedBanner> = {
  orphaned: {
    below: false,
    draw: (c) => (
      <OrphanBanner
        driverId={c.config?.driver ?? ""}
        onReassign={() => c.config && c.onEdit(c.config)}
        onBrowseDrivers={c.onBrowseDrivers}
        onActivated={c.onActivated}
      />
    ),
  },
  restarting: { below: false, draw: (c) => <RestartingBanner detail={c.away.offlineDetail} /> },
  asleep: { below: false, draw: (c) => <AsleepBanner detail={c.away.offlineDetail} /> },
  paused: { below: false, draw: (c) => <PausedBanner deviceId={c.deviceId} pausedFor={c.away.pausedFor} /> },
  // Saving the login adds the device again, so there is nothing to retry.
  needs_login: {
    below: true,
    draw: (c) => (
      <NeedsLoginBanner
        deviceId={c.deviceId}
        fields={c.info?.needs_login ?? []}
        driverInfo={c.info?.driver_info}
        onSaved={c.onLoginSaved}
      />
    ),
  },
  offline: {
    below: true,
    draw: (c) => (
      <OfflineBanner away={c.away} driverInfo={c.info?.driver_info} installed={c.installed} onReport={c.onReport} />
    ),
  },
};

/** The banner that explains the device, drawn on its side of product
 *  hardware's panel; nothing on either side when none does. */
function placedBanner(context: BannerContext, isEnabled: boolean): { above: ReactNode; below: ReactNode } {
  const chosen = awayBanner(context.away, isEnabled, context.info?.needs_login?.length ?? 0);
  if (!chosen) return { above: null, below: null };
  const { below, draw } = AWAY_BANNERS[chosen];
  const drawn = draw(context);
  return below ? { above: null, below: drawn } : { above: drawn, below: null };
}

export function DeviceBanners({
  isEnabled,
  deviceName,
  liveState,
  ...context
}: BannerContext & {
  /** The project has the device turned on. */
  isEnabled: boolean;
  /** The name product hardware's panel opens with. */
  deviceName: string;
  liveState: Record<string, unknown>;
}) {
  const { deviceId, away, info, installed } = context;
  const banner = placedBanner(context, isEnabled);
  return (
    <>
      {banner.above}
      <ChildTroubleBanner
        deviceId={deviceId}
        connected={away.connected}
        childTypes={info?.driver_info?.child_entity_types}
        liveState={liveState}
      />
      {installed && (
        <InstalledEquipment
          deviceId={deviceId}
          name={deviceName}
          completionWait={info?.completion_wait}
        />
      )}
      {banner.below}
    </>
  );
}
