import { describe, it, expect } from "vitest";
import type { NetworkInterface } from "../../api/systemClient";
import {
  adapterOptions,
  interfaceLabel,
  listenChoiceFor,
  listenHelp,
  listenValue,
  matchInterface,
  ownScreenOpensProgrammer,
  restartPlan,
  sameListenChoice,
  valueForIdentity,
} from "./adapterChoice";

const ETHERNET: NetworkInterface = {
  name: "Ethernet", mac: "50-EB-F6-D2-FC-29",
  addresses: [{ ip: "10.20.30.5", subnet: "10.20.30.0/24" }], link: true, has_address: true,
};
const WIFI: NetworkInterface = {
  name: "Wi-Fi", mac: "F0-57-A6-A8-D4-0F",
  addresses: [{ ip: "192.168.1.40", subnet: "192.168.1.0/24" }], link: true, has_address: true,
};
const UNPLUGGED: NetworkInterface = {
  name: "Ethernet 2", mac: "00-11-22-33-44-55", addresses: [], link: false, has_address: false,
};

describe("matchInterface", () => {
  it("finds an adapter by MAC whatever its separators, before its name", () => {
    // A renamed adapter keeps its MAC.
    expect(matchInterface({ name: "Renamed", mac: "50:eb:f6:d2:fc:29" }, [WIFI, ETHERNET])).toBe(ETHERNET);
  });

  it("then by name", () => {
    // A replaced network card keeps its connection name on Windows.
    expect(matchInterface({ name: "wi-fi", mac: "aa-aa-aa-aa-aa-aa" }, [ETHERNET, WIFI])).toBe(WIFI);
  });

  it("matches an address only to the adapter holding it", () => {
    expect(matchInterface({ address: "192.168.1.40" }, [ETHERNET, WIFI])).toBe(WIFI);
    expect(matchInterface({ address: "192.0.2.77" }, [ETHERNET, WIFI])).toBeUndefined();
  });

  it("tells adapters apart by name when the system reports no MAC", () => {
    // Appliance hardware lists its adapters without one.
    const eth0: NetworkInterface = {
      name: "eth0", mac: "",
      addresses: [{ ip: "192.168.4.61", subnet: "192.168.4.0/22" }], link: true, has_address: true,
    };
    const wlan0: NetworkInterface = {
      name: "wlan0", mac: "",
      addresses: [{ ip: "10.0.0.8", subnet: "10.0.0.0/24" }], link: true, has_address: true,
    };
    const options = adapterOptions([eth0, wlan0], null);
    expect(new Set(options.map((o) => o.value)).size).toBe(2);
    expect(matchInterface({ name: "wlan0", mac: "" }, [eth0, wlan0])).toBe(wlan0);
    expect(valueForIdentity({ name: "wlan0", mac: "" }, [eth0, wlan0])).toBe(options[1].value);
  });
});

describe("the adapter list", () => {
  it("lists each adapter by name and current address", () => {
    expect(interfaceLabel(ETHERNET)).toBe("Ethernet · 10.20.30.5 · 10.20.30.0/24");
  });

  it("lists every address of an adapter that has two", () => {
    const two = { ...ETHERNET, addresses: [...ETHERNET.addresses, { ip: "10.20.31.5", subnet: "10.20.31.0/24" }] };
    expect(interfaceLabel(two)).toBe("Ethernet · 10.20.30.5, 10.20.31.5 · 10.20.30.0/24, 10.20.31.0/24");
  });

  it("keeps an adapter that is unplugged or waiting for an address", () => {
    expect(interfaceLabel(UNPLUGGED)).toBe("Ethernet 2 · not connected");
    expect(interfaceLabel({ ...UNPLUGGED, link: true })).toBe("Ethernet 2 · no address yet");
  });

  it("keeps a chosen adapter this computer no longer has, so the choice stays visible", () => {
    const options = adapterOptions([WIFI], { name: "Ethernet", mac: ETHERNET.mac });
    expect(options.map((o) => o.label)).toEqual([
      "Wi-Fi · 192.168.1.40 · 192.168.1.0/24",
      "Ethernet · not connected",
    ]);
    expect(valueForIdentity({ name: "Ethernet", mac: ETHERNET.mac }, [WIFI])).toBe(options[1].value);
  });

  it("names an environment address no adapter holds as an address", () => {
    const options = adapterOptions([WIFI], { address: "192.0.2.77" });
    expect(options[1].label).toBe("192.0.2.77 · not on this computer");
  });
});

describe("Accept connections from", () => {
  it("round-trips each choice through the list", () => {
    const options = adapterOptions([ETHERNET, WIFI], null);
    expect(listenChoiceFor(listenValue("any", {}, [ETHERNET]), options)).toEqual({ mode: "any", adapter: {} });
    expect(listenChoiceFor(listenValue("this_computer", {}, [ETHERNET]), options))
      .toEqual({ mode: "this_computer", adapter: {} });
    const value = listenValue("adapter", { name: "Wi-Fi", mac: "f0:57:a6:a8:d4:0f" }, [ETHERNET, WIFI]);
    expect(listenChoiceFor(value, options)).toEqual({
      mode: "adapter", adapter: { name: "Wi-Fi", mac: "F0-57-A6-A8-D4-0F" },
    });
  });

  it("says who can connect in one sentence", () => {
    expect(listenHelp("any", "")).toBe(
      "Touch panels, phones and computers on any network this computer is connected to can open the panel and the Programmer.",
    );
    expect(listenHelp("adapter", "Ethernet")).toBe(
      "Only devices on the Ethernet network, and this computer itself, can open the panel and the Programmer.",
    );
    expect(listenHelp("this_computer", "")).toBe(
      "Only this computer's own screen and browser can open the panel and the Programmer.",
    );
  });

  it("on the appliance, never offers its own screen for the Programmer", () => {
    expect(listenHelp("adapter", "eth0", false)).toBe(
      "Only devices on the eth0 network can open the panel and the Programmer, and this computer's own screen shows the panel.",
    );
    expect(listenHelp("this_computer", "", false)).toBe(
      "Only this computer's own screen shows the panel, and only remote access through OpenAVC Cloud can open the Programmer.",
    );
    expect(ownScreenOpensProgrammer("android_appliance")).toBe(false);
    expect(ownScreenOpensProgrammer("windows_installer")).toBe(true);
  });

  it("compares choices by adapter identity, not spelling", () => {
    expect(sameListenChoice(
      { mode: "adapter", adapter: { name: "Ethernet", mac: "50-EB-F6-D2-FC-29" } },
      { mode: "adapter", adapter: { name: "Ethernet", mac: "50:eb:f6:d2:fc:29" } },
    )).toBe(true);
    expect(sameListenChoice({ mode: "any", adapter: {} }, { mode: "any", adapter: { name: "x" } })).toBe(true);
    expect(sameListenChoice({ mode: "any" }, { mode: "this_computer" })).toBe(false);
  });
});

describe("where the restart dialog waits", () => {
  const preview = { serves_this_address: true, keeps_this_connection: true, panel_addresses: ["10.20.30.5"] };

  it("stays on this page's address when the choice keeps it", () => {
    expect(restartPlan(preview)).toEqual({ cutOff: false, hosts: [""] });
    expect(restartPlan(null)).toEqual({ cutOff: false, hosts: [""] });
  });

  it("moves a browser on this computer to 127.0.0.1", () => {
    expect(restartPlan({ ...preview, serves_this_address: false })).toEqual({ cutOff: false, hosts: ["127.0.0.1"] });
  });

  it("tries the new choice's own addresses from a browser it cuts off", () => {
    expect(restartPlan({ ...preview, serves_this_address: false, keeps_this_connection: false }))
      .toEqual({ cutOff: true, hosts: ["10.20.30.5"] });
    expect(restartPlan({
      serves_this_address: false, keeps_this_connection: false, panel_addresses: [],
    })).toEqual({ cutOff: true, hosts: [] });
  });
});
