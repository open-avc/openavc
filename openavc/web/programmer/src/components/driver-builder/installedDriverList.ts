import type { CommunityDriver, DriverInfo, InstalledDriver } from "../../api/types";

/** The built-in generic devices, which the Installed list leaves out. */
const GENERIC_IDS = new Set(["generic_tcp", "generic_serial", "generic_http"]);

/** A library file that never registered, as a list entry: named, made and
 *  filed as the catalog has it, else by its file. */
function notLoadedEntry(installed: InstalledDriver, entry: CommunityDriver | undefined): DriverInfo {
  const named = entry ?? { name: installed.name, manufacturer: "", category: "" };
  return {
    id: installed.id,
    name: named.name,
    manufacturer: named.manufacturer,
    category: named.category,
    version: installed.version,
    commands: {},
    config_schema: {},
  };
}

/** What the list shows: every registered driver but the generics, then each
 *  library file that never registered (it failed to import, or was written for
 *  an older OpenAVC), so it can be updated or uninstalled from here. Such a
 *  file is named as the catalog names it, else as the file does. */
export function listedDrivers(
  registered: DriverInfo[],
  installed: InstalledDriver[],
  catalog: CommunityDriver[],
): { drivers: DriverInfo[]; notLoaded: Set<string> } {
  const registeredIds = new Set(registered.map((d) => d.id));
  const catalogById = new Map(catalog.map((d) => [d.id, d]));
  const notLoaded = installed
    .filter((d) => !registeredIds.has(d.id) && !GENERIC_IDS.has(d.id))
    .map((d) => notLoadedEntry(d, catalogById.get(d.id)));
  return {
    drivers: [...registered.filter((d) => !GENERIC_IDS.has(d.id)), ...notLoaded],
    notLoaded: new Set(notLoaded.map((d) => d.id)),
  };
}

export const NOT_LOADED_TEXT =
  "This driver did not load, so devices can't use it. Update it, or uninstall it and " +
  "install it again from Browse Community. The Log has the reason.";
