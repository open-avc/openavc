import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { followedAudit, rememberFollowedAudit, useAuditStore } from "../../../store/auditStore";
import { DeviceAuditHost } from "./DeviceAuditHost";

const getCurrentAudit = vi.fn();

vi.mock("../../../api/auditClient", () => ({
  getCurrentAudit: () => getCurrentAudit(),
}));
vi.mock("./DeviceAuditWizard", () => ({
  DeviceAuditWizard: () => <div>wizard</div>,
}));

function running(id: string, status = "active") {
  return { session: { session_id: id, status } };
}

describe("DeviceAuditHost after a reload", () => {
  beforeEach(() => {
    useAuditStore.setState({ open: false, session: null });
    getCurrentAudit.mockReset();
  });
  afterEach(() => sessionStorage.clear());

  it("reopens the wizard on the audit this tab was following", async () => {
    rememberFollowedAudit("abc123");
    getCurrentAudit.mockResolvedValue(running("abc123"));
    render(<DeviceAuditHost />);
    await waitFor(() => expect(useAuditStore.getState().open).toBe(true));
    expect(followedAudit()).toBe("abc123");
  });

  it("forgets an audit that has ended or is another one", async () => {
    rememberFollowedAudit("abc123");
    getCurrentAudit.mockResolvedValue(running("other1"));
    render(<DeviceAuditHost />);
    await waitFor(() => expect(followedAudit()).toBeNull());
    expect(useAuditStore.getState().open).toBe(false);

    rememberFollowedAudit("abc123");
    getCurrentAudit.mockResolvedValue({ session: null });
    render(<DeviceAuditHost />);
    await waitFor(() => expect(followedAudit()).toBeNull());
    expect(useAuditStore.getState().open).toBe(false);
  });

  it("asks nothing in a tab that was following no audit", async () => {
    render(<DeviceAuditHost />);
    await new Promise((r) => setTimeout(r, 20));
    expect(getCurrentAudit).not.toHaveBeenCalled();
    expect(useAuditStore.getState().open).toBe(false);
  });

  it("closing the wizard forgets the audit", () => {
    rememberFollowedAudit("abc123");
    useAuditStore.getState().closeWizard();
    expect(followedAudit()).toBeNull();
  });
});
