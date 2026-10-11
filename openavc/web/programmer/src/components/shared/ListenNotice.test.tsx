import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Every Programmer load confirms a change of who can connect that the safety
// net was watching, and says "Kept" or "Went back" once.

const answer = vi.hoisted(() => ({ kept: null as string | null, went_back: null as string | null }));

vi.mock("../../api/restClient", () => ({
  confirmListen: vi.fn(async () => ({ ...answer })),
}));

const navigateTo = vi.fn();
vi.mock("../../store/navigationStore", () => ({
  useNavigationStore: { getState: () => ({ navigateTo }) },
}));

import * as api from "../../api/restClient";
import { ListenNotice, resetConfirmOnLoad } from "./ListenNotice";

describe("ListenNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetConfirmOnLoad();
    answer.kept = null;
    answer.went_back = null;
  });

  it("confirms on load and says nothing when nothing was waiting", async () => {
    render(<ListenNotice />);
    await waitFor(() => expect(api.confirmListen).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("listen-notice")).toBeNull();
  });

  it("says Kept once the change is confirmed", async () => {
    answer.kept = "Kept: Accept connections from Ethernet only.";
    render(<ListenNotice />);
    expect((await screen.findByTestId("listen-notice")).textContent).toContain(answer.kept);
    expect(screen.queryByRole("button", { name: "Settings" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("listen-notice")).toBeNull();
  });

  it("says Went back, with the way to Settings", async () => {
    answer.went_back =
      "Went back to Any network because nobody opened the Programmer through Ethernet within 10 minutes.";
    render(<ListenNotice />);
    expect((await screen.findByTestId("listen-notice")).textContent).toContain(answer.went_back);
    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(navigateTo).toHaveBeenCalledWith("settings");
  });

  it("asks once per page load, however often it mounts", async () => {
    answer.kept = "Kept: Accept connections from Ethernet only.";
    const first = render(<ListenNotice />);
    await screen.findByTestId("listen-notice");
    first.unmount();
    render(<ListenNotice />);
    await screen.findByTestId("listen-notice");
    expect(api.confirmListen).toHaveBeenCalledTimes(1);
  });

  it("asks again after a call that failed", async () => {
    vi.mocked(api.confirmListen).mockRejectedValueOnce(new Error("401"));
    const first = render(<ListenNotice />);
    await waitFor(() => expect(api.confirmListen).toHaveBeenCalledTimes(1));
    first.unmount();
    answer.kept = "Kept: Accept connections from Ethernet only.";
    render(<ListenNotice />);
    await screen.findByTestId("listen-notice");
    expect(api.confirmListen).toHaveBeenCalledTimes(2);
  });
});
