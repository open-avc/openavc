import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { CommandRun } from "../../api/commandFileClient";
import { CommandFileRunStatus } from "./CommandFileRun";

function running(extra: Partial<CommandRun> = {}): CommandRun {
  return {
    run_id: "r1",
    device_id: "dev1",
    command: "load_firmware",
    label: "Load Firmware",
    status: "running",
    started: 0,
    ended: null,
    files: [{ param: "image", name: "fw.bin", size: 100, read: 40 }],
    sent: 40,
    total: 100,
    result: null,
    error: null,
    returned: null,
    client_id: null,
    ...extra,
  };
}

describe("CommandFileRunStatus", () => {
  it("says a change to the device waits for the run", () => {
    render(
      <CommandFileRunStatus
        view={{ kind: "run", run: running({ change_waiting: true }) }}
        deviceId="dev1"
        deviceName="Acme Widget"
        onStop={() => {}}
      />,
    );
    expect(
      screen.getByText("Your change to Acme Widget is saved. It takes effect when Load Firmware finishes."),
    ).toBeTruthy();
  });

  it("says nothing of a change when none waits", () => {
    render(
      <CommandFileRunStatus
        view={{ kind: "run", run: running() }}
        deviceId="dev1"
        deviceName="Acme Widget"
        onStop={() => {}}
      />,
    );
    expect(screen.queryByText(/takes effect when/)).toBeNull();
  });
});
