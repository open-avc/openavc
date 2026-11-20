import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A system below the release the cloud needs is told so on its own screens,
// and the notice opens Updates.

const navigateTo = vi.fn();
vi.mock("../../store/navigationStore", () => ({
  useNavigationStore: { getState: () => ({ navigateTo }) },
}));

import { useConnectionStore } from "../../store/connectionStore";
import { CloudUpgradeNotice, cloudUpgradeSentence } from "./CloudUpgradeNotice";

function setNotice(required: unknown, minVersion: unknown) {
  useConnectionStore.setState({
    liveState: {
      "system.cloud_upgrade_required": required,
      "system.cloud_min_version": minVersion,
    },
  });
}

describe("cloudUpgradeSentence", () => {
  it("names the release the cloud needs", () => {
    expect(cloudUpgradeSentence("0.5.14")).toBe(
      "Cloud features need OpenAVC v0.5.14 or later. Update this system.",
    );
  });

  it("never doubles the v", () => {
    expect(cloudUpgradeSentence("v0.42.0")).toBe(
      "Cloud features need OpenAVC v0.42.0 or later. Update this system.",
    );
  });

  it("still says to update when the cloud named no release", () => {
    for (const missing of ["", "  ", undefined, 5]) {
      expect(cloudUpgradeSentence(missing)).toBe(
        "Cloud features need a newer OpenAVC release. Update this system.",
      );
    }
  });
});

describe("CloudUpgradeNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useConnectionStore.setState({ liveState: {} });
  });

  it("shows nothing when the cloud asked for no update", () => {
    setNotice(false, "");
    render(<CloudUpgradeNotice />);
    expect(screen.queryByTestId("cloud-upgrade-notice")).toBeNull();
  });

  it("shows nothing before the cloud has said anything", () => {
    render(<CloudUpgradeNotice />);
    expect(screen.queryByTestId("cloud-upgrade-notice")).toBeNull();
  });

  it("says which release the cloud needs, and opens Updates", async () => {
    setNotice(true, "0.5.14");
    render(<CloudUpgradeNotice />);
    const notice = screen.getByTestId("cloud-upgrade-notice");
    expect(notice.textContent).toContain(
      "Cloud features need OpenAVC v0.5.14 or later. Update this system.",
    );
    await userEvent.click(screen.getByRole("button", { name: /Cloud features need/ }));
    expect(navigateTo).toHaveBeenCalledWith("updates");
  });

  it("goes when the agent clears it", () => {
    setNotice(true, "0.5.14");
    const { rerender } = render(<CloudUpgradeNotice />);
    expect(screen.getByTestId("cloud-upgrade-notice")).toBeTruthy();
    setNotice(false, "");
    rerender(<CloudUpgradeNotice />);
    expect(screen.queryByTestId("cloud-upgrade-notice")).toBeNull();
  });
});
