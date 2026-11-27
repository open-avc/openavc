import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

// Where the assistant opens on a system behind the release it needs: the
// release to install and the install one click away, instead of an error.

const api = vi.hoisted(() => ({ getUpdateStatus: vi.fn() }));
vi.mock("../../api/restClient", () => api);

import { AssistantUnavailable } from "./AssistantUnavailable";
import { useConnectionStore } from "../../store/connectionStore";
import { useNavigationStore } from "../../store/navigationStore";

function updatesItself(yes: boolean, extra: Record<string, unknown> = {}) {
  api.getUpdateStatus.mockResolvedValue({ can_self_update: yes, ...extra });
}

beforeEach(() => {
  api.getUpdateStatus.mockReset();
  useNavigationStore.setState({ activeView: "ai", pendingFocus: null });
  useConnectionStore.setState({ liveState: { "system.version": "0.41.0" } } as never);
});

describe("the assistant on a system behind the release it needs", () => {
  it("says which release to install and offers to install it", async () => {
    updatesItself(true);
    render(<AssistantUnavailable state="outdated" reason="cloud sentence" neededRelease="0.42.0" />);

    expect(screen.getByText("Update to v0.42.0 to use the assistant.")).toBeInTheDocument();
    expect(screen.getByText("This system runs v0.41.0.")).toBeInTheDocument();
    const install = await screen.findByRole("button", { name: "Install v0.42.0" });
    expect(screen.getByText("Installing restarts the server.")).toBeInTheDocument();

    await act(async () => { fireEvent.click(install); });
    expect(useNavigationStore.getState().activeView).toBe("updates");
    expect(useNavigationStore.getState().pendingFocus).toEqual({ type: "install", id: "0.42.0" });
  });

  it("opens the Updates page on a system that does not update itself", async () => {
    updatesItself(false);
    render(<AssistantUnavailable state="outdated" reason="cloud sentence" neededRelease="0.42.0" />);

    const open = await screen.findByRole("button", { name: "Open System Updates" });
    expect(screen.queryByRole("button", { name: "Install v0.42.0" })).not.toBeInTheDocument();
    expect(screen.queryByText("Installing restarts the server.")).not.toBeInTheDocument();

    await act(async () => { fireEvent.click(open); });
    expect(useNavigationStore.getState().activeView).toBe("updates");
    expect(useNavigationStore.getState().pendingFocus).toEqual({ type: "install", id: "0.42.0" });
  });

  it("opens the Updates page where the product firmware carries updates", async () => {
    updatesItself(true, { update_owner: "firmware" });
    render(<AssistantUnavailable state="outdated" reason="cloud sentence" neededRelease="0.42.0" />);
    expect(await screen.findByRole("button", { name: "Open System Updates" })).toBeInTheDocument();
  });

  it("opens the Updates page when the update settings cannot be read", async () => {
    api.getUpdateStatus.mockRejectedValue(new Error("401"));
    render(<AssistantUnavailable state="outdated" reason="cloud sentence" neededRelease="0.42.0" />);
    expect(await screen.findByRole("button", { name: "Open System Updates" })).toBeInTheDocument();
  });

  it("never offers Open OpenAVC Cloud or Cloud Connection for it", async () => {
    updatesItself(true);
    render(<AssistantUnavailable state="outdated" reason="cloud sentence" neededRelease="0.42.0" />);
    await screen.findByRole("button", { name: "Install v0.42.0" });
    expect(screen.queryByText("Open OpenAVC Cloud")).not.toBeInTheDocument();
    expect(screen.queryByText("Open Cloud Connection")).not.toBeInTheDocument();
    expect(screen.queryByText("cloud sentence")).not.toBeInTheDocument();
  });
});

describe("the assistant refused for any other reason", () => {
  it("shows the cloud's sentence, as before", () => {
    render(<AssistantUnavailable state="unavailable" reason="The AI assistant is paused on this account." neededRelease="" />);
    expect(screen.getByText("The AI assistant is paused on this account.")).toBeInTheDocument();
    expect(screen.getByText("Open OpenAVC Cloud")).toBeInTheDocument();
    expect(api.getUpdateStatus).not.toHaveBeenCalled();
  });

  it("tells an unpaired system how to pair, as before", () => {
    render(<AssistantUnavailable state="unpaired" reason="Cloud not paired" neededRelease="" />);
    expect(screen.getByText("Sign up at cloud.openavc.com")).toBeInTheDocument();
  });
});
