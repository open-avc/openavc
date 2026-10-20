import { describe, it, expect } from "vitest";
import {
  driverUpdate,
  driverUpdates,
  installableCount,
  pluginUpdate,
  updateAvailableText,
  updatesAvailableText,
} from "./catalogUpdates";
import type { CommunityDriver, InstalledDriver } from "../../api/types";
import type { CommunityPlugin, InstalledPlugin } from "../../api/pluginClient";

// One answer to "is there an update, and can this OpenAVC install it?" for
// every screen that offers Update. These pin the answer; the screens pin that
// they ask.

const ENTRY = {
  id: "acme_widget",
  name: "Acme Widget",
  file: "displays/acme_widget.py",
  format: "python",
  category: "display",
  manufacturer: "Acme",
  version: "1.5.0",
  author: "OpenAVC",
  transport: "tcp",
  verified: true,
  description: "",
} as CommunityDriver;

const INSTALLED: InstalledDriver = {
  id: "acme_widget",
  name: "Acme Widget",
  format: "python",
  filename: "acme_widget.py",
  version: "1.4.3",
};

describe("driverUpdate", () => {
  it("offers the catalog version of an older installed driver", () => {
    expect(driverUpdate(INSTALLED, ENTRY, "0.37.0")).toEqual({
      id: "acme_widget",
      name: "Acme Widget",
      installedVersion: "1.4.3",
      version: "1.5.0",
      fileUrl:
        "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/displays/acme_widget.py",
      minPlatformVersion: undefined,
      blockedBy: "",
    });
  });

  it("offers nothing for a current driver, or one newer than the catalog", () => {
    expect(driverUpdate({ ...INSTALLED, version: "1.5.0" }, ENTRY, "0.37.0")).toBeNull();
    expect(driverUpdate({ ...INSTALLED, version: "1.6.0" }, ENTRY, "0.37.0")).toBeNull();
  });

  it("offers nothing for a driver that is not installed or not in the catalog", () => {
    expect(driverUpdate(undefined, ENTRY, "0.37.0")).toBeNull();
    expect(driverUpdate(INSTALLED, undefined, "0.37.0")).toBeNull();
  });

  it("offers the catalog version when the installed copy records none", () => {
    expect(driverUpdate({ ...INSTALLED, version: "" }, ENTRY, "0.37.0")?.version).toBe("1.5.0");
  });

  it("says which OpenAVC a newer version needs, so no screen offers a refused update", () => {
    const needsNewer = { ...ENTRY, min_platform_version: "0.38.0" };
    expect(driverUpdate(INSTALLED, needsNewer, "0.37.0")).toMatchObject({
      blockedBy: "0.38.0",
      minPlatformVersion: "0.38.0",
    });
    expect(driverUpdate(INSTALLED, needsNewer, "0.38.0")?.blockedBy).toBe("");
  });
});

describe("driverUpdates and the count", () => {
  it("counts only the updates this OpenAVC can install", () => {
    const catalog = [
      ENTRY,
      { ...ENTRY, id: "acme_amp", version: "2.0.0", min_platform_version: "9.0.0" },
      { ...ENTRY, id: "acme_cam", version: "1.0.0" },
    ];
    const installed = [
      INSTALLED,
      { ...INSTALLED, id: "acme_amp", version: "1.0.0" },
      { ...INSTALLED, id: "acme_cam", version: "1.0.0" },
    ];
    const updates = driverUpdates(installed, catalog, "0.37.0");
    expect([...updates.keys()]).toEqual(["acme_widget", "acme_amp"]);
    expect(installableCount(updates)).toBe(1);
  });
});

describe("pluginUpdate", () => {
  const plugin = {
    id: "acme_thing",
    name: "Acme Thing",
    file: "utility/acme_thing",
    version: "2.1.0",
    min_openavc_version: "0.38.0",
  } as CommunityPlugin;
  const installed: InstalledPlugin = {
    id: "acme_thing",
    name: "Acme Thing",
    version: "2.0.0",
    source: "community",
  };

  it("reads the plugin catalog's own minimum-version field", () => {
    expect(pluginUpdate(installed, plugin, "0.37.0")).toMatchObject({
      version: "2.1.0",
      fileUrl: "https://raw.githubusercontent.com/open-avc/openavc-plugins/main/utility/acme_thing",
      blockedBy: "0.38.0",
    });
    expect(pluginUpdate(installed, plugin, "0.38.0")?.blockedBy).toBe("");
  });
});

describe("the words", () => {
  it("names both versions, and leaves out an installed version nobody recorded", () => {
    const update = driverUpdate(INSTALLED, ENTRY, "0.37.0")!;
    expect(updateAvailableText(update, "Driver update available")).toBe(
      "Driver update available: v1.5.0 (installed v1.4.3)",
    );
    expect(updateAvailableText({ ...update, installedVersion: "" })).toBe(
      "Update available: v1.5.0",
    );
  });

  it("counts in words", () => {
    expect(updatesAvailableText(1)).toBe("1 update available");
    expect(updatesAvailableText(3)).toBe("3 updates available");
  });
});
