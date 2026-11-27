import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { PRESENCE_WORDS } from "../../components/shared/devicePresence";

/**
 * The device list's row: the Needs login mark shows exactly when the device's
 * page would ask for the login, so a paused, restarting or sleeping device
 * reads the same in the list as on its page. The row reads the device's
 * presence word, as the page does.
 */

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

import { DeviceListItem } from "./DeviceListItem";

function row() {
  return render(
    <DeviceListItem deviceId="amp" name="Rack Amp" driver="acme_amp" selected={false} onClick={() => {}} />,
  );
}

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, {
    "device.amp.offline_reason": "login_missing",
    "device.amp.presence": "offline",
    ...next,
  });
}

beforeEach(() => setLive({}));

describe("the device list's Needs login mark", () => {
  it("shows for an offline device waiting for a login", () => {
    row();
    expect(screen.getByText("Needs login")).toBeTruthy();
  });

  it.each(PRESENCE_WORDS.filter((word) => word !== "offline"))(
    "gives way while the device reads %s, as its page does",
    (word) => {
      setLive({ "device.amp.presence": word });
      row();
      expect(screen.queryByText("Needs login")).toBeNull();
    },
  );
});

describe("the device list's row", () => {
  it("dims a device turned off in the project or waiting for its driver", () => {
    for (const word of PRESENCE_WORDS) {
      setLive({ "device.amp.presence": word, "device.amp.offline_reason": null });
      const { container, unmount } = row();
      const opacity = (container.querySelector("button") as HTMLButtonElement).style.opacity;
      expect([word, opacity]).toEqual([word, word === "disabled" || word === "waiting_for_driver" ? "0.6" : "1"]);
      unmount();
    }
  });

  it("says the driver is not installed only while the device waits for it", () => {
    setLive({ "device.amp.presence": "waiting_for_driver", "device.amp.offline_reason": null });
    row();
    expect(screen.getByText(/\(not installed\)/)).toBeTruthy();
  });
});
