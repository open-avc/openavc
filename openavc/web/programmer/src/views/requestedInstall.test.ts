import { describe, it, expect } from "vitest";
import type { UpdateCheckResult, UpdateStatus } from "../api/restClient";
import { requestedInstall } from "./requestedInstall";

// Another screen (the assistant, on a system behind the release it needs)
// asks the Updates page for a release. The page installs only what the button
// promised: that release or a newer one, on a system that updates itself, with
// nothing else under way. Otherwise it is left showing what the check found.

const CHECKED: UpdateCheckResult = { update_available: true } as UpdateCheckResult;

function status(extra: Partial<UpdateStatus>): UpdateStatus {
  return {
    current_version: "0.41.0",
    deployment_type: "linux_package",
    can_self_update: true,
    update_available: "0.42.0",
    update_channel: "stable",
    update_status: "idle",
    update_progress: 0,
    update_error: "",
    rollback_available: false,
    rollback_version: "",
    install_version: "0.42.0",
    ...extra,
  };
}

describe("requestedInstall", () => {
  it("installs the release asked for when the updater would install it", () => {
    expect(requestedInstall("0.42.0", CHECKED, status({}))).toEqual({ install: "0.42.0" });
  });

  it("installs a newer release than the one asked for", () => {
    expect(requestedInstall("0.42.0", CHECKED, status({ install_version: "0.43.0" }))).toEqual({ install: "0.43.0" });
  });

  it("reads a tag spelling as the release", () => {
    expect(requestedInstall("v0.42.0", CHECKED, status({}))).toEqual({ install: "0.42.0" });
  });

  it("installs again after a failed attempt", () => {
    expect(requestedInstall("0.42.0", CHECKED, status({ update_status: "error" }))).toEqual({ install: "0.42.0" });
  });

  it.each([
    ["an older release", { install_version: "0.41.1" }],
    ["a release candidate of it", { install_version: "0.42.0-rc1" }],
    ["nothing", { install_version: "" }],
    ["nothing it can read", { install_version: undefined }],
  ])("says the release was not found when the updater would install %s", (_what, extra) => {
    expect(requestedInstall("0.42.0", CHECKED, status(extra))).toBe("not_found");
  });

  it("installs nothing on a system that does not update itself", () => {
    expect(requestedInstall("0.42.0", CHECKED, status({ can_self_update: false }))).toBe("show");
  });

  it("installs nothing where the product firmware carries updates", () => {
    expect(requestedInstall("0.42.0", CHECKED, status({ update_owner: "firmware" }))).toBe("show");
  });

  it("installs nothing while an update is already under way", () => {
    expect(requestedInstall("0.42.0", CHECKED, status({ update_status: "downloading" }))).toBe("show");
  });

  it("installs nothing when the check could not be made", () => {
    const failed = { update_available: false, error: "GitHub could not be reached." } as UpdateCheckResult;
    expect(requestedInstall("0.42.0", failed, status({}))).toBe("show");
  });
});
