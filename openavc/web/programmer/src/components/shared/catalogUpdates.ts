/**
 * Is there a newer catalog version of an installed community driver or plugin,
 * and can this OpenAVC install it?
 *
 * One answer for every screen that offers Update: Browse Community, Installed
 * Drivers, the device page, the Plugins list and a plugin's own page. Each of
 * them reads the same two lists (what is installed, what the catalog
 * publishes), so none of them can offer an update another one hides, or an
 * update the server would refuse for needing a newer OpenAVC.
 */
import { compareSemver, hasUpdate } from "../../api/types";
import type { CommunityDriver, InstalledDriver } from "../../api/types";
import type { CommunityPlugin, InstalledPlugin } from "../../api/pluginClient";

export const DRIVER_CATALOG_BASE_URL =
  "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/";
export const PLUGIN_CATALOG_BASE_URL =
  "https://raw.githubusercontent.com/open-avc/openavc-plugins/main/";

/** The platform release a driver or plugin needs, when this system is older
 * than it.
 *
 * The catalog has always carried the minimum and the server has always
 * enforced it, but nothing showed it: a card rendered a live button and the
 * refusal only arrived as an error after the click. That was a rare case until
 * the `server` -> `openavc` rename, which gated every Python driver in the
 * catalog at once -- so on a 0.24.x box the whole Python half of the library
 * looked installable and none of it was.
 *
 * Returns "" when it is installable, so the caller can treat it as a plain
 * boolean. An unparseable or absent version never blocks: the server is the
 * authority, and this is only here to say so earlier.
 */
export function blockedByPlatform(
  running: string,
  minRequired: string | null | undefined,
): string {
  if (!minRequired || !running) return "";
  try {
    return compareSemver(minRequired, running) > 0 ? minRequired : "";
  } catch {
    return "";
  }
}

/** A newer catalog version of something installed here. */
export interface CatalogUpdate {
  id: string;
  name: string;
  /** "" when the installed copy records no version. */
  installedVersion: string;
  /** The catalog's version. */
  version: string;
  fileUrl: string;
  minPlatformVersion?: string;
  /** The OpenAVC release this version needs, when this system is older. "" = installable. */
  blockedBy: string;
}

export function driverUpdate(
  installed: InstalledDriver | undefined,
  entry: CommunityDriver | undefined,
  runningVersion: string,
): CatalogUpdate | null {
  if (!installed || !entry) return null;
  const installedVersion = installed.version ?? "";
  if (!hasUpdate(installedVersion, entry.version)) return null;
  return {
    id: entry.id,
    name: entry.name,
    installedVersion,
    version: entry.version,
    fileUrl: `${DRIVER_CATALOG_BASE_URL}${entry.file}`,
    minPlatformVersion: entry.min_platform_version || undefined,
    blockedBy: blockedByPlatform(runningVersion, entry.min_platform_version),
  };
}

/** Every installed driver with a newer catalog version, by driver id. */
export function driverUpdates(
  installed: InstalledDriver[],
  catalog: CommunityDriver[],
  runningVersion: string,
): Map<string, CatalogUpdate> {
  const byId = new Map(catalog.map((d) => [d.id, d]));
  const updates = new Map<string, CatalogUpdate>();
  for (const d of installed) {
    const update = driverUpdate(d, byId.get(d.id), runningVersion);
    if (update) updates.set(d.id, update);
  }
  return updates;
}

export function pluginUpdate(
  installed: InstalledPlugin | undefined,
  entry: CommunityPlugin | undefined,
  runningVersion: string,
): CatalogUpdate | null {
  if (!installed || !entry) return null;
  const installedVersion = installed.version ?? "";
  if (!hasUpdate(installedVersion, entry.version)) return null;
  return {
    id: entry.id,
    name: entry.name,
    installedVersion,
    version: entry.version,
    fileUrl: `${PLUGIN_CATALOG_BASE_URL}${entry.file}`,
    minPlatformVersion: entry.min_openavc_version || undefined,
    blockedBy: blockedByPlatform(runningVersion, entry.min_openavc_version),
  };
}

/** Every installed plugin with a newer catalog version, by plugin id. */
export function pluginUpdates(
  installed: InstalledPlugin[],
  catalog: CommunityPlugin[],
  runningVersion: string,
): Map<string, CatalogUpdate> {
  const byId = new Map(catalog.map((p) => [p.id, p]));
  const updates = new Map<string, CatalogUpdate>();
  for (const p of installed) {
    const update = pluginUpdate(p, byId.get(p.id), runningVersion);
    if (update) updates.set(p.id, update);
  }
  return updates;
}

/** How many of these updates this OpenAVC can install. */
export function installableCount(updates: Map<string, CatalogUpdate>): number {
  let count = 0;
  for (const u of updates.values()) if (!u.blockedBy) count++;
  return count;
}

/** "Update available: v1.5.0 (installed v1.4.3)", without the parenthesis
 * when the installed copy records no version. */
export function updateAvailableText(update: CatalogUpdate, prefix = "Update available"): string {
  const installed = update.installedVersion ? ` (installed v${update.installedVersion})` : "";
  return `${prefix}: v${update.version}${installed}`;
}

/** The count at the top of an installed list: "2 updates available". */
export function updatesAvailableText(count: number): string {
  return `${count} ${count === 1 ? "update" : "updates"} available`;
}
