import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";

// The two Settings cards that write the PROJECT rather than system.json:
// Panel Display and Devices. Each write goes through the project store with
// both settings groups carried along, so a change to one never drops the
// other; the numbers are corrected when the box loses focus, not per key.

vi.mock("../api/restClient", async () => (await import("../test/settingsHarness")).api);
vi.mock("../store/projectStore", async () => (await import("../test/settingsHarness")).projectStoreModule);
vi.mock("../store/toastStore", async () => (await import("../test/settingsHarness")).toast);
vi.mock("../components/shared/clipboard", async () => (await import("../test/settingsHarness")).clipboard);
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({
  VariableKeyPicker: ({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) => (
    <input aria-label="Stay bright key" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
  ),
}));

import { SystemSettingsView } from "./SystemSettingsView";
import { useConnectionStore } from "../store/connectionStore";
import { projectState, renderLoaded, resetHarness, served } from "../test/settingsHarness";

const display = () => projectState.project!.settings.display;
const devices = () => projectState.project!.settings.devices;

/** The one write the last change made: both groups, one of them changed. */
function lastWrite() {
  const calls = projectState.update.mock.calls;
  return calls[calls.length - 1][0];
}

function rowOf(label: string): HTMLElement {
  return screen.getByText(label, { selector: "label" }).parentElement as HTMLElement;
}

function switchBeside(text: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(text);
  while (el && !el.querySelector("[role=switch]")) el = el.parentElement;
  return el!.querySelector("[role=switch]") as HTMLElement;
}

beforeEach(() => {
  resetHarness();
  served.version = { ...served.version, panel_dim_available: true };
  useConnectionStore.setState({ liveState: {} });
});

describe("Panel Display", () => {
  it("writes the brightness, carrying the device settings along", async () => {
    devices().reconnect_interval_seconds = 30;
    await renderLoaded(<SystemSettingsView />);
    const slider = within(rowOf("Brightness")).getByRole("slider") as HTMLInputElement;
    expect(slider.min).toBe("10");
    expect(slider.value).toBe("100");
    fireEvent.change(slider, { target: { value: "40" } });
    expect(lastWrite()).toEqual({
      settings: {
        display: { ...display(), brightness_percent: 40 },
        devices: { reconnect_interval_seconds: 30 },
      },
    });
  });

  it("says each panel keeps its own brightness until one is set here", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Panel's own")).toBeTruthy();
    expect(screen.getByText(/Each panel is using whatever its own screen is set to\./)).toBeTruthy();
  });

  it("shows a brightness set here", async () => {
    display().brightness_percent = 55;
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("55%")).toBeTruthy();
    expect((within(rowOf("Brightness")).getByRole("slider") as HTMLInputElement).value).toBe("55");
    expect(screen.getByText(/The panel's normal brightness\./)).toBeTruthy();
  });

  it("switches the dim off and on", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(switchBeside("Dim the panel when idle"));
    expect(lastWrite().settings.display.idle_dim_enabled).toBe(false);
  });

  it("writes the minutes as seconds, and corrects them on leaving the box", async () => {
    await renderLoaded(<SystemSettingsView />);
    const minutes = within(rowOf("Dim after")).getByRole("spinbutton") as HTMLInputElement;
    expect(minutes.value).toBe("5");
    fireEvent.change(minutes, { target: { value: "15" } });
    expect(lastWrite().settings.display.idle_dim_timeout_seconds).toBe(900);
    fireEvent.change(minutes, { target: { value: "" } });
    expect(lastWrite().settings.display.idle_dim_timeout_seconds).toBe(0);
    fireEvent.blur(minutes, { target: { value: "500" } });
    expect(lastWrite().settings.display.idle_dim_timeout_seconds).toBe(7200);
    fireEvent.blur(minutes, { target: { value: "0" } });
    expect(lastWrite().settings.display.idle_dim_timeout_seconds).toBe(60);
    fireEvent.blur(minutes, { target: { value: "" } });
    expect(lastWrite().settings.display.idle_dim_timeout_seconds).toBe(300);
  });

  it("writes the dim level, keeps 0 as blackout, and corrects the rest on leaving the box", async () => {
    await renderLoaded(<SystemSettingsView />);
    const level = within(rowOf("Dim to")).getByRole("spinbutton") as HTMLInputElement;
    fireEvent.change(level, { target: { value: "35" } });
    expect(lastWrite().settings.display.idle_dim_level_percent).toBe(35);
    fireEvent.change(level, { target: { value: "" } });
    expect(lastWrite().settings.display.idle_dim_level_percent).toBe(0);
    fireEvent.blur(level, { target: { value: "95" } });
    expect(lastWrite().settings.display.idle_dim_level_percent).toBe(90);
    fireEvent.blur(level, { target: { value: "-4" } });
    expect(lastWrite().settings.display.idle_dim_level_percent).toBe(0);
    fireEvent.blur(level, { target: { value: "0" } });
    expect(lastWrite().settings.display.idle_dim_level_percent).toBe(0);
    fireEvent.blur(level, { target: { value: "" } });
    expect(lastWrite().settings.display.idle_dim_level_percent).toBe(20);
  });

  it("says what 0 means", async () => {
    display().idle_dim_level_percent = 0;
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(/Blacked out: the screen goes as dark as the panel allows/)).toBeTruthy();
  });

  it("writes the key that keeps it bright, and the waking touch", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(screen.getByLabelText("Stay bright key"), { target: { value: "var.in_use" } });
    expect(lastWrite().settings.display.idle_dim_hold_state_key).toBe("var.in_use");
    fireEvent.click(switchBeside("Waking touch also presses the button"));
    expect(lastWrite().settings.display.idle_dim_wake_passes_touch).toBe(true);
  });

  it("says a panel software update waits too, on a panel that takes them", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText(/a panel software update waits to install/)).toBeNull();
    useConnectionStore.setState({ liveState: { "system.panel_software.version": "1.2.0" } });
    expect(await screen.findByText(/a panel software update waits to install/)).toBeTruthy();
  });

  it("dims by default when the project does not say", async () => {
    projectState.project!.settings.display = {};
    await renderLoaded(<SystemSettingsView />);
    expect(switchBeside("Dim the panel when idle").getAttribute("aria-checked")).toBe("true");
    expect((within(rowOf("Dim after")).getByRole("spinbutton") as HTMLInputElement).value).toBe("5");
    expect((within(rowOf("Dim to")).getByRole("spinbutton") as HTMLInputElement).value).toBe("20");
  });

  it("shows only the switch while the dim is off", async () => {
    display().idle_dim_enabled = false;
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("Dim after", { selector: "label" })).toBeNull();
    expect(screen.queryByLabelText("Stay bright key")).toBeNull();
    expect(switchBeside("Dim the panel when idle").getAttribute("aria-checked")).toBe("false");
  });

  it("is not drawn before a project is open", async () => {
    projectState.project = null;
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("Panel Display")).toBeNull();
    expect(screen.queryByText("Devices", { selector: "h3" })).toBeNull();
  });
});

describe("Devices", () => {
  it("writes the retry interval, carrying the display settings along", async () => {
    await renderLoaded(<SystemSettingsView />);
    const box = within(rowOf("Retry every")).getByRole("spinbutton") as HTMLInputElement;
    expect(box.value).toBe("");
    expect(box.placeholder).toBe("5");
    fireEvent.change(box, { target: { value: "12" } });
    expect(lastWrite()).toEqual({
      settings: { display: { ...display() }, devices: { reconnect_interval_seconds: 12 } },
    });
  });

  it("writes an emptied box as the system's own setting, and corrects a number on leaving it", async () => {
    devices().reconnect_interval_seconds = 20;
    await renderLoaded(<SystemSettingsView />);
    const box = within(rowOf("Retry every")).getByRole("spinbutton") as HTMLInputElement;
    expect(box.value).toBe("20");
    fireEvent.change(box, { target: { value: "" } });
    expect(lastWrite().settings.devices.reconnect_interval_seconds).toBeNull();
    fireEvent.change(box, { target: { value: "0" } });
    expect(lastWrite().settings.devices.reconnect_interval_seconds).toBe(0);
    fireEvent.blur(box, { target: { value: "900" } });
    expect(lastWrite().settings.devices.reconnect_interval_seconds).toBe(300);
    fireEvent.blur(box, { target: { value: "0" } });
    expect(lastWrite().settings.devices.reconnect_interval_seconds).toBe(5);
    fireEvent.blur(box, { target: { value: "" } });
    expect(lastWrite().settings.devices.reconnect_interval_seconds).toBeNull();
  });

  it("is drawn where the panel cannot be dimmed too", async () => {
    served.version = { ...served.version, panel_dim_available: false };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("Panel Display")).toBeNull();
    expect(screen.getByText("Devices", { selector: "h3" })).toBeTruthy();
  });

  it("writes into a project that has no settings yet", async () => {
    projectState.project = { openavc_version: "0.13.0", project: { id: "p1", name: "t" } };
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(within(rowOf("Retry every")).getByRole("spinbutton"), { target: { value: "7" } });
    expect(lastWrite()).toEqual({ settings: { display: {}, devices: { reconnect_interval_seconds: 7 } } });
    fireEvent.change(within(rowOf("Brightness")).getByRole("slider"), { target: { value: "70" } });
    expect(lastWrite()).toEqual({ settings: { display: { brightness_percent: 70 }, devices: {} } });
  });
});
