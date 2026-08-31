import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";

// The regression this file exists for: on a CLAIMED instance a successful
// update left the progress dialog open under "Restarting server" until a
// two-minute watchdog called it slow. Session tokens live in server memory, so
// the restart being waited on invalidates this browser's token and the
// Programmer WebSocket comes back refused -- and that socket's snapshot was
// the dialog's only completion signal. The fix polls /api/health, which needs
// no credential.
//
// Note what these tests deliberately do NOT do: they never move the mocked
// live state off "restarting"/0.24.1. That keeps the WebSocket completion path
// permanently silent, so anything that closes the dialog here can only be the
// health poll. A test that also updated the store would pass with the poll
// deleted.

const liveState: Record<string, unknown> = {};

vi.mock("../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: { liveState: Record<string, unknown> }) => unknown) =>
    selector({ liveState }),
}));

const showSuccess = vi.fn();
const showError = vi.fn();
vi.mock("../store/toastStore", () => ({
  showSuccess: (m: string) => showSuccess(m),
  showError: (m: string) => showError(m),
}));

vi.mock("../api/restClient", () => ({
  getUpdateStatus: vi.fn(async () => ({
    current_version: "0.24.1",
    deployment_type: "macos_app",
    can_self_update: true,
    rollback_available: true,
  })),
  getUpdateHistory: vi.fn(async () => []),
  checkForUpdates: vi.fn(async () => ({ update_available: false })),
  applyUpdate: vi.fn(async () => ({ success: true })),
  rollbackUpdate: vi.fn(async () => ({ success: true })),
}));

import { UpdatesView } from "./UpdatesView";
import * as api from "../api/restClient";

/** Serve /api/health with a given version; everything else 404s. */
function stubHealth(version: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/health")) {
        return new Response(JSON.stringify({ status: "healthy", version }), { status: 200 });
      }
      return new Response("", { status: 404 });
    }),
  );
}

beforeEach(() => {
  for (const k of Object.keys(liveState)) delete liveState[k];
  liveState["system.update_status"] = "restarting";
  liveState["system.version"] = "0.24.1";
  showSuccess.mockClear();
  showError.mockClear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("UpdatesView progress dialog", () => {
  it("opens while the server is restarting", async () => {
    stubHealth("0.24.1");
    render(<UpdatesView />);
    expect(await screen.findByText("Restarting server")).toBeInTheDocument();
  });

  it("closes itself once /api/health reports the new version", async () => {
    // Server is back, on the new version, but the WebSocket never reconnects
    // (the live state below stays "restarting"/0.24.1 for the whole test).
    stubHealth("0.25.0");
    render(<UpdatesView />);
    expect(await screen.findByText("Restarting server")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

    await waitFor(() => {
      expect(screen.queryByText("Restarting server")).not.toBeInTheDocument();
    });
    expect(showSuccess).toHaveBeenCalledWith("Updated to v0.25.0");
  });

  it("stays open while health still reports the old version", async () => {
    // The server answers /api/health before it restarts too. Treating that as
    // completion would close the dialog with a false success.
    stubHealth("0.24.1");
    render(<UpdatesView />);
    expect(await screen.findByText("Restarting server")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });

    expect(screen.getByText("Restarting server")).toBeInTheDocument();
    expect(showSuccess).not.toHaveBeenCalled();
  });

  it("stays open while the server is not answering at all", async () => {
    // Mid-swap the daemon is down and every probe throws. That is the normal
    // middle of an update, not a failure.
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("connection refused"); }));
    render(<UpdatesView />);
    expect(await screen.findByText("Restarting server")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });

    expect(screen.getByText("Restarting server")).toBeInTheDocument();
    expect(showSuccess).not.toHaveBeenCalled();
  });

  it("reports a rollback as a rollback, not an update", async () => {
    // Direction comes from the semver fall-back here, since no action was
    // started through this view (the cloud-initiated case).
    stubHealth("0.24.0");
    liveState["system.version"] = "0.25.0";
    render(<UpdatesView />);
    expect(await screen.findByText("Restarting server")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

    await waitFor(() => {
      expect(showSuccess).toHaveBeenCalledWith("Rolled back to v0.24.0");
    });
  });

  it("announces completion once, not once per poll", async () => {
    stubHealth("0.25.0");
    render(<UpdatesView />);
    expect(await screen.findByText("Restarting server")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

    expect(showSuccess).toHaveBeenCalledTimes(1);
  });
});


// The other thing this page has to say: update-helper.sh could not apply a
// staged update and set it aside to retry. Everything else here reads healthy
// on such a box -- the install works, the server answers, the version is a real
// version -- so without this card an appliance stuck on its golden baseline
// looks exactly like one that is up to date.
describe("UpdatesView deferred update", () => {
  function statusWith(extra: Record<string, unknown>) {
    vi.mocked(api.getUpdateStatus).mockResolvedValue({
      current_version: "0.24.1",
      deployment_type: "linux_package",
      can_self_update: true,
      update_available: "",
      update_channel: "stable",
      update_status: "idle",
      update_progress: 0,
      update_error: "",
      rollback_available: false,
      rollback_version: "",
      ...extra,
    } as Awaited<ReturnType<typeof api.getUpdateStatus>>);
  }

  beforeEach(() => {
    liveState["system.update_status"] = "idle";
    stubHealth("0.24.1");
  });

  it("says which version has not been installed, and why", async () => {
    statusWith({
      deferred_version: "0.25.0",
      deferred_attempts: 1,
      deferred_reason: "could not download the release's dependencies",
      deferred_final: false,
    });

    render(<UpdatesView />);

    expect(
      await screen.findByText("Update to v0.25.0 has not been installed"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/could not download the release's dependencies/),
    ).toBeInTheDocument();
    expect(screen.getByText(/try again the next time/)).toBeInTheDocument();
  });

  it("says so plainly once nothing will retry it", async () => {
    statusWith({
      deferred_version: "0.25.0",
      deferred_attempts: 3,
      deferred_reason: "could not download the release's dependencies",
      deferred_final: true,
    });

    render(<UpdatesView />);

    expect(
      await screen.findByText("Update to v0.25.0 has not been installed"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Nothing will try again on its own/)).toBeInTheDocument();
    expect(screen.getByText(/stopped 3 times/)).toBeInTheDocument();
  });

  it("does not call the box up to date while an update is sitting undone", async () => {
    statusWith({
      deferred_version: "0.25.0",
      deferred_attempts: 1,
      deferred_reason: "could not rebuild the Python environment",
      deferred_final: false,
    });

    render(<UpdatesView />);

    await screen.findByText("Update to v0.25.0 has not been installed");
    expect(screen.queryByText("You're up to date")).not.toBeInTheDocument();
  });

  it("says nothing when there is nothing deferred", async () => {
    statusWith({});

    render(<UpdatesView />);

    expect(await screen.findByText("You're up to date")).toBeInTheDocument();
    expect(screen.queryByText(/has not been installed/)).not.toBeInTheDocument();
  });
});
