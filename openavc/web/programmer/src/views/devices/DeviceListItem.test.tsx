import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * The device list's row: the Needs login mark shows exactly when the device's
 * page would ask for the login, so a paused, restarting or sleeping device
 * reads the same in the list as on its page.
 */

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

import { DeviceListItem } from "./DeviceListItem";

function row(enabled = true) {
  return render(
    <DeviceListItem deviceId="amp" name="Rack Amp" driver="acme_amp" selected={false} enabled={enabled} onClick={() => {}} />,
  );
}

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.amp.offline_reason": "login_missing", ...next });
}

beforeEach(() => setLive({}));

describe("the device list's Needs login mark", () => {
  it("shows for a disconnected, enabled device waiting for a login", () => {
    row();
    expect(screen.getByText("Needs login")).toBeTruthy();
  });

  it.each([
    ["paused", { "device.amp.paused": true }],
    ["restarting", { "device.amp.restarting": true }],
    ["asleep", { "device.amp.asleep": true }],
    ["missing its driver", { "device.amp.orphaned": true }],
    ["connected", { "device.amp.connected": true }],
  ])("gives way while the device is %s, as its page does", (_label, state) => {
    setLive(state);
    row();
    expect(screen.queryByText("Needs login")).toBeNull();
  });

  it("does not show for a device turned off in the project", () => {
    row(false);
    expect(screen.queryByText("Needs login")).toBeNull();
  });
});
