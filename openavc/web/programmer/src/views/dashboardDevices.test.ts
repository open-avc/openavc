import { describe, it, expect } from "vitest";
import { PRESENCE_WORDS, presenceKey, type PresenceWord } from "../components/shared/devicePresence";
import { asleepHeadline, dashboardDisconnected, summarizeDashboardDevices, tileBorder } from "./dashboardDevices";

/** Where each word lands on the Dashboard: its figure, and its tile's border. */
const LANDS: Record<PresenceWord, { figure: "none" | "connected" | "asleep" | "disconnected" | "enabled only"; border: string }> = {
  disabled: { figure: "none", border: "var(--border-color)" },
  waiting_for_driver: { figure: "disconnected", border: "rgba(239,68,68,0.3)" },
  online: { figure: "connected", border: "rgba(76,175,80,0.3)" },
  restarting: { figure: "enabled only", border: "var(--border-color)" },
  asleep: { figure: "asleep", border: "var(--border-color)" },
  paused: { figure: "enabled only", border: "var(--border-color)" },
  offline: { figure: "disconnected", border: "rgba(239,68,68,0.3)" },
  connecting: { figure: "disconnected", border: "rgba(239,68,68,0.3)" },
};

function figureOf(word: PresenceWord): string {
  const s = summarizeDashboardDevices([{ id: "d", name: "Dev" }], { [presenceKey("d")]: word });
  if (s.enabled === 0) return "none";
  if (s.connected === 1) return "connected";
  if (s.asleep.length === 1) return "asleep";
  if (s.disconnected.length === 1) return "disconnected";
  return "enabled only";
}

describe("summarizeDashboardDevices", () => {
  it.each(PRESENCE_WORDS)("counts a device that reads %s where the word says", (word) => {
    expect(figureOf(word)).toBe(LANDS[word].figure);
  });

  it("lists names in the project's order", () => {
    const s = summarizeDashboardDevices(
      [
        { id: "proj", name: "Projector" },
        { id: "disp", name: "Lobby Display" },
        { id: "pc", name: "vMix PC" },
        { id: "cam", name: "Stage Camera" },
        { id: "amp", name: "Amplifier" },
      ],
      {
        [presenceKey("proj")]: "online",
        [presenceKey("disp")]: "offline",
        [presenceKey("pc")]: "asleep",
        [presenceKey("cam")]: "paused",
        [presenceKey("amp")]: "disabled",
      },
    );
    expect(s).toEqual({ connected: 1, enabled: 4, disconnected: ["Lobby Display"], asleep: ["vMix PC"] });
  });

  it("reads the word, not the parts it is made of", () => {
    const s = summarizeDashboardDevices([{ id: "pc", name: "vMix PC" }], {
      "device.pc.connected": false,
      "device.pc.asleep": true,
      [presenceKey("pc")]: "offline",
    });
    expect(s.disconnected).toEqual(["vMix PC"]);
    expect(s.asleep).toEqual([]);
  });
});

describe("tileBorder", () => {
  it.each(PRESENCE_WORDS)("borders a tile that reads %s as its figure does", (word) => {
    expect(tileBorder(word)).toBe(LANDS[word].border);
    expect(dashboardDisconnected(word)).toBe(LANDS[word].figure === "disconnected");
  });
});

describe("asleepHeadline", () => {
  it("agrees in number", () => {
    expect(asleepHeadline(1)).toBe("1 device asleep");
    expect(asleepHeadline(2)).toBe("2 devices asleep");
  });
});
