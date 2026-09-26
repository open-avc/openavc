import { describe, expect, it } from "vitest";
import type { CommunityDriver, DriverInfo, InstalledDriver } from "../../../api/types";
import {
  buildDriverOptions,
  driversFor,
  installState,
  manufacturers,
  matchModel,
  modelsFor,
  optionConfidence,
  preselect,
} from "./driverPicker";

function catalogDriver(partial: Partial<CommunityDriver>): CommunityDriver {
  return {
    id: "acme_x",
    name: "Acme X",
    file: "utility/acme_x.avcdriver",
    format: "avcdriver",
    category: "utility",
    manufacturer: "Acme",
    version: "1.0.0",
    author: "",
    transport: "tcp",
    verified: false,
    description: "",
    ...partial,
  };
}

const CATALOG: CommunityDriver[] = [
  catalogDriver({
    id: "acme_display",
    name: "Acme Display",
    version: "1.2.0",
    verified: true,
    compatible_models: [
      { manufacturer: "Acme", models: ["W-100", "W-200"], confidence: "full" },
    ],
  }),
  catalogDriver({
    id: "generic_visca",
    name: "Generic VISCA",
    manufacturer: "Generic",
    compatible_models: [
      { manufacturer: "Generic", models: ["Any VISCA"], confidence: "partial" },
      { manufacturer: "Acme", models: ["W-100"], confidence: "untested" },
    ],
  }),
  catalogDriver({
    id: "acme_new",
    name: "Acme New",
    min_platform_version: "9.0.0",
    compatible_models: [{ manufacturer: "Acme", models: ["W-300"], confidence: "untested" }],
  }),
  catalogDriver({ id: "acme_old", name: "Acme Old", deprecated: true }),
];

const REGISTERED = [
  { id: "acme_display", name: "Acme Display", manufacturer: "Acme", category: "utility", version: "1.1.0" },
  { id: "bench_widget", name: "Bench Widget", manufacturer: "Workbench", category: "utility", version: "0.1.0" },
  { id: "generic_tcp", name: "Generic TCP", manufacturer: "Generic", category: "utility", version: "1.0.0" },
] as DriverInfo[];

const INSTALLED = [
  { id: "acme_display", name: "Acme Display", format: "avcdriver", filename: "x", version: "1.1.0" },
  { id: "bench_widget", name: "Bench Widget", format: "python", filename: "y", version: "0.1.0" },
] as InstalledDriver[];

const options = buildDriverOptions(CATALOG, REGISTERED, INSTALLED, "0.33.0");

describe("building the lists", () => {
  it("lists a driver under each manufacturer it names, and the ones the catalog lacks", () => {
    expect(manufacturers(options)).toEqual(["Acme", "Generic", "Workbench"]);
    const bench = options.find((o) => o.id === "bench_widget");
    expect(bench?.source).toBe("imported");
    expect(options.find((o) => o.id === "generic_tcp")?.source).toBe("built_in");
    // A deprecated driver is offered only when it is installed.
    expect(options.some((o) => o.id === "acme_old")).toBe(false);
  });

  it("lists each manufacturer's models once", () => {
    expect(modelsFor(options, "acme")).toEqual(["W-100", "W-200", "W-300"]);
  });

  it("puts the manufacturer's own driver before a general one", () => {
    expect(driversFor(options, "Acme", "W-100").map((o) => o.id)).toEqual([
      "acme_display",
      "generic_visca",
    ]);
    expect(driversFor(options, "Acme", null).map((o) => o.id)).toEqual([
      "acme_display",
      "acme_new",
      "generic_visca",
    ]);
  });

  it("says what installing each would take", () => {
    const byId = (id: string) => options.find((o) => o.id === id && !o.isVia)!;
    expect(installState(byId("acme_display"))).toBe("update");
    expect(byId("acme_display").updateAvailable).toBe(true);
    expect(installState(byId("acme_new"))).toBe("blocked");
    expect(byId("acme_new").blockedBy).toBe("9.0.0");
    expect(installState(byId("generic_visca"))).toBe("install");
    expect(installState(byId("bench_widget"))).toBe("installed");
  });
});

describe("the first selection", () => {
  it("starts from the verdict's driver under the manufacturer the device reported", () => {
    expect(
      preselect(options, "generic_visca", [], { manufacturer: "ACME", model: "W-100 v2.1" }),
    ).toEqual({ brand: "Acme", model: "W-100", driverId: "generic_visca" });
    expect(preselect(options, null, ["acme_display"], {})).toEqual({
      brand: "Acme",
      model: null,
      driverId: "acme_display",
    });
    expect(preselect(options, null, [], { manufacturer: "Acme" })).toBeNull();
  });

  it("matches a reported model that carries more than the model", () => {
    expect(matchModel(["DM75E", "DM75"], "DM75E AllShare1.0")).toBe("DM75E");
    expect(matchModel(["W-100"], "w-100")).toBe("W-100");
    expect(matchModel(["W-100"], "W-1000")).toBeNull();
    expect(matchModel(["W-100"], "")).toBeNull();
  });
});

describe("the confidence a driver row shows", () => {
  const split = catalogDriver({
    id: "acme_amp",
    name: "Acme Amp",
    compatible_models: [
      { manufacturer: "Acme", models: ["A-84", "A-352D"], confidence: "untested" },
      { manufacturer: "Acme", models: ["A-352D"], confidence: "full" },
    ],
  });
  const [option] = buildDriverOptions([split], [], [], "1.0.0");

  it("is the chosen model's, not the first group's", () => {
    expect(optionConfidence(option, "A-352D")).toBe("full");
    expect(optionConfidence(option, "a-84")).toBe("untested");
  });
  it("is nothing for a model the driver does not list, and the brand's with no model", () => {
    expect(optionConfidence(option, "A-999")).toBeNull();
    expect(optionConfidence(option, null)).toBe(option.confidence);
  });
});
