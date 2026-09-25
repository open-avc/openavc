/**
 * "Which driver?": the manufacturer, model and driver lists, built from the
 * community catalog and the drivers this system already has.
 *
 * Built from the Community browser's own helpers (a driver fans out into one
 * entry per manufacturer it lists models for), so the audit offers a driver
 * under exactly the brands the browser shows it under. Drivers the catalog
 * does not list (imported by the person, or built into OpenAVC) join under
 * their own manufacturer.
 */
import { blockedByPlatform, expandDriverToCards } from "../../../components/driver-builder/CommunityBrowser";
import { hasUpdate } from "../../../api/types";
import type { CommunityDriver, DriverInfo, InstalledDriver } from "../../../api/types";

export type DriverSource = "catalog" | "imported" | "built_in";
export type ModelConfidence = "full" | "partial" | "untested";

export interface DriverOption {
  id: string;
  name: string;
  /** The manufacturer this entry is listed under. */
  brand: string;
  /** The models the driver lists for that manufacturer. */
  models: string[];
  confidence: ModelConfidence | null;
  /** A driver named for another manufacturer (a generic protocol driver). */
  isVia: boolean;
  verified: boolean;
  source: DriverSource;
  installed: boolean;
  installedVersion: string;
  catalogVersion: string;
  updateAvailable: boolean;
  /** The OpenAVC version the driver needs, when this system is older. */
  blockedBy: string;
  /** The catalog path an install fetches ("" when not in the catalog). */
  file: string;
  minPlatform?: string;
  deprecated: boolean;
}

const CONFIDENCE_RANK: Record<string, number> = { full: 3, partial: 2, untested: 1 };

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** Every entry the picker can offer: catalog drivers per brand, then the
 *  installed drivers the catalog does not list. */
export function buildDriverOptions(
  catalog: CommunityDriver[],
  registered: DriverInfo[],
  installed: InstalledDriver[],
  runningVersion: string,
): DriverOption[] {
  const registeredById = new Map(registered.map((d) => [d.id, d]));
  const inRepo = new Set(installed.map((d) => d.id));
  const out: DriverOption[] = [];
  const catalogIds = new Set<string>();
  for (const driver of catalog) {
    catalogIds.add(driver.id);
    const reg = registeredById.get(driver.id);
    // A deprecated driver is offered only where it is already installed.
    if (driver.deprecated && !reg) continue;
    for (const card of expandDriverToCards(driver)) {
      out.push({
        id: driver.id,
        name: driver.name,
        brand: card.brand,
        models: card.brandModels,
        confidence: card.brandConfidence,
        isVia: card.isViaCard,
        verified: !!driver.verified,
        source: "catalog",
        installed: !!reg,
        installedVersion: reg?.version ?? "",
        catalogVersion: driver.version,
        updateAvailable: !!reg && hasUpdate(reg.version ?? "", driver.version),
        blockedBy: blockedByPlatform(runningVersion, driver.min_platform_version),
        file: driver.file,
        minPlatform: driver.min_platform_version,
        deprecated: !!driver.deprecated,
      });
    }
  }
  for (const reg of registered) {
    if (catalogIds.has(reg.id)) continue;
    out.push({
      id: reg.id,
      name: reg.name || reg.id,
      brand: reg.manufacturer || "Other",
      models: [],
      confidence: null,
      isVia: false,
      verified: false,
      source: inRepo.has(reg.id) ? "imported" : "built_in",
      installed: true,
      installedVersion: reg.version ?? "",
      catalogVersion: "",
      updateAvailable: false,
      blockedBy: "",
      file: "",
      deprecated: false,
    });
  }
  return out;
}

/** Every manufacturer, once each, alphabetical. */
export function manufacturers(options: DriverOption[]): string[] {
  const seen = new Map<string, string>();
  for (const o of options) {
    const key = o.brand.trim().toLowerCase();
    if (key && !seen.has(key)) seen.set(key, o.brand.trim());
  }
  return [...seen.values()].sort((a, b) => a.localeCompare(b));
}

/** The models the drivers list for one manufacturer, once each. */
export function modelsFor(options: DriverOption[], brand: string): string[] {
  const seen = new Map<string, string>();
  for (const o of options) {
    if (!same(o.brand, brand)) continue;
    for (const m of o.models) {
      const key = m.trim().toLowerCase();
      if (key && !seen.has(key)) seen.set(key, m.trim());
    }
  }
  return [...seen.values()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

/**
 * The drivers for a manufacturer, and for one of its models when ``model`` is
 * given (null: every driver of that manufacturer, for "My model is not
 * listed"). The manufacturer's own drivers come before generic ones, then the
 * strongest confidence, then the name.
 */
export function driversFor(
  options: DriverOption[],
  brand: string,
  model: string | null,
): DriverOption[] {
  const picked = options.filter(
    (o) =>
      same(o.brand, brand) &&
      (model === null || o.models.some((m) => same(m, model))),
  );
  const seen = new Set<string>();
  return picked
    .sort(
      (a, b) =>
        Number(a.isVia) - Number(b.isVia) ||
        (CONFIDENCE_RANK[b.confidence ?? ""] ?? 0) - (CONFIDENCE_RANK[a.confidence ?? ""] ?? 0) ||
        a.name.localeCompare(b.name),
    )
    .filter((o) => (seen.has(o.id) ? false : (seen.add(o.id), true)));
}

export interface Preselection {
  brand: string;
  /** null when no listed model matches what the device reported. */
  model: string | null;
  driverId: string;
}

/**
 * What to select first: the driver the verdict identified (or its first
 * candidate), under the manufacturer the device reported when that driver
 * lists it, and the model the device reported when that driver lists it.
 */
export function preselect(
  options: DriverOption[],
  verdictDriver: string | null,
  candidates: string[],
  reported: { manufacturer?: string | null; model?: string | null },
): Preselection | null {
  const driverId = verdictDriver || candidates[0] || "";
  if (!driverId) return null;
  const entries = options.filter((o) => o.id === driverId);
  if (entries.length === 0) return null;
  const maker = reported.manufacturer ?? "";
  const entry =
    entries.find((o) => maker && same(o.brand, maker)) ??
    entries.find((o) => !o.isVia) ??
    entries[0];
  return { brand: entry.brand, model: matchModel(entry.models, reported.model ?? ""), driverId };
}

/** The listed model a reported model string names, if any. A device often
 *  reports more than the model ("DM75E AllShare1.0"), so a listed model the
 *  report starts with, followed by a space, counts. */
export function matchModel(models: string[], reported: string): string | null {
  const text = reported.trim().toLowerCase();
  if (!text) return null;
  const exact = models.find((m) => m.trim().toLowerCase() === text);
  if (exact) return exact;
  const prefixed = models
    .filter((m) => text.startsWith(`${m.trim().toLowerCase()} `))
    .sort((a, b) => b.length - a.length);
  return prefixed[0] ?? null;
}

/** What the driver row offers: an installed driver runs as it is (an
 *  update this system is too old for is simply not offered); one that is
 *  not installed can be, unless it needs a newer OpenAVC. */
export function installState(
  o: DriverOption,
): "installed" | "install" | "update" | "blocked" | "unavailable" {
  if (o.installed) return o.updateAvailable && !o.blockedBy ? "update" : "installed";
  if (!o.file) return "unavailable";
  return o.blockedBy ? "blocked" : "install";
}

/** The confidence the catalog gives a model, in words. */
export function confidenceText(c: ModelConfidence | null): string {
  if (c === "full") return "Tested on this model";
  if (c === "partial") return "Partly tested";
  if (c === "untested") return "Not tested on hardware yet";
  return "";
}
