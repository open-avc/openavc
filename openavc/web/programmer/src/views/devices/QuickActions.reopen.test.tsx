import { it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// After a sign-in, the setup run this tab had open comes back in its wizard,
// even when the device is online again and the setup button is not shown.

vi.mock("./SetupActionWizard", () => ({
  SetupActionWizard: ({ action, runId }: { action: { label: string }; runId?: string }) => (
    <div>{`wizard ${action.label} on ${runId}`}</div>
  ),
}));

import type { DeviceAction } from "../../api/types";
import { QuickActions } from "./QuickActions";
import { rememberOpenSetupRun, reopenSetupRun, takeReopenedRun } from "./openSetupRun";

const provision: DeviceAction = {
  id: "provision", kind: "setup", label: "Provision", availability: "offline", params: {},
};

beforeEach(() => {
  sessionStorage.clear();
  takeReopenedRun("dev1");
});

it("opens the wizard on the reopened run once the device's actions are known", () => {
  rememberOpenSetupRun({ deviceId: "dev1", actionId: "provision", runId: "r1" });
  reopenSetupRun();

  const { rerender } = render(
    <QuickActions deviceId="dev1" actions={[]} connected={true} liveState={{}} />,
  );
  expect(screen.queryByText(/wizard/)).toBeNull();

  rerender(<QuickActions deviceId="dev1" actions={[provision]} connected={true} liveState={{}} />);
  expect(screen.getByText("wizard Provision on r1")).toBeTruthy();
});
