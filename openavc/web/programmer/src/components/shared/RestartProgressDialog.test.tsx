import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

// After a change of who can connect, the restart waits where the new choice
// answers: another address of this computer, or, for a browser the change
// cuts off, the new choice's own addresses until it says where to go. That is
// never the 60-second "may have refused the new configuration" failure.

vi.mock("../../api/restClient", () => ({
  restartSystem: vi.fn(async () => ({ status: "restarting", mode: "graceful", delay_seconds: 2 })),
  downloadCertificate: vi.fn(),
}));

import { RestartProgressDialog } from "./RestartProgressDialog";
import * as api from "../../api/restClient";
import { ApiError } from "../../api/errors";

/** A status answer, as the server's run-state record gives it. */
function said(phase: string, more: Record<string, unknown> = {}) {
  const body = {
    phase, ready: phase === "ready", version: "0.41.0", error: null,
    problems: [], held: false, retry_in: null, ...more,
  };
  return new Response(JSON.stringify(body), { status: 200 });
}

const fetchMock = vi.fn();

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function runFor(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const handoff = {
  lines: <span>This page will stop loading here.</span>,
  saved: <span>Saved. Open the Programmer on this computer's own screen.</span>,
};

describe("RestartProgressDialog after a change of who can connect", () => {
  it("hands over at once when the new choice has no address this browser could try", async () => {
    render(
      <RestartProgressDialog
        targetUrl="http://localhost:8080/programmer"
        alsoTry={[]}
        handoff={handoff}
        isProtocolSwitch={false}
        expectsNewCert={false}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("This page will stop loading here.")).toBeTruthy();
    await runFor(3500);
    expect(screen.getByTestId("restart-handoff-saved").textContent).toContain("Saved. Open the Programmer");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/may have refused the new configuration/)).toBeNull();
  });

  it("tries the new choice's address, then hands over instead of failing", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(
      <RestartProgressDialog
        targetUrl="http://10.20.30.5:8080/programmer"
        alsoTry={["http://10.20.30.5:8080/programmer"]}
        handoff={handoff}
        isProtocolSwitch={false}
        expectsNewCert={false}
        onClose={() => {}}
      />,
    );
    await runFor(30_000);
    const probed = fetchMock.mock.calls.map((c) => [String(c[0]), (c[1] as RequestInit).mode]);
    expect(probed.length).toBeGreaterThan(0);
    // Only the new address, asking its status as a read the page can see.
    expect(new Set(probed.map((p) => p.join(" ")))).toEqual(
      new Set(["http://10.20.30.5:8080/api/startup-status cors"]),
    );
    expect(screen.getByTestId("restart-handoff-saved")).toBeTruthy();
    expect(screen.queryByText(/may have refused the new configuration/)).toBeNull();
  });

  it("moves to the address the new choice answers on", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, origin: "http://127.0.0.2:8080", pathname: "/programmer/", assign });
    fetchMock.mockImplementation(async () => said("ready"));
    render(
      <RestartProgressDialog
        targetUrl="http://127.0.0.1:8080/programmer"
        isProtocolSwitch={false}
        expectsNewCert={false}
        onClose={() => {}}
      />,
    );
    await runFor(1000);
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://127.0.0.1:8080/api/startup-status");
    expect(assign).toHaveBeenCalledWith("http://127.0.0.1:8080/programmer");
  });
});

const HERE = "http://192.168.1.20:8080";

describe("RestartProgressDialog waits on what the server says", () => {
  let assign: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, origin: HERE, pathname: "/programmer/", assign });
  });

  function open(targetUrl = `${HERE}/programmer`) {
    render(
      <RestartProgressDialog
        targetUrl={targetUrl}
        isProtocolSwitch={false}
        expectsNewCert={false}
        onClose={() => {}}
      />,
    );
  }

  it("asks at once and never takes the server it asked, on its way down, for the new one", async () => {
    fetchMock
      .mockResolvedValueOnce(said("stopping"))
      .mockResolvedValueOnce(said("stopping"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(said("starting"))
      .mockImplementation(async () => said("ready"));
    open();
    await runFor(0);
    // No fixed delay before the first ask.
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${HERE}/api/startup-status`);
    await runFor(3000);
    expect(screen.getByTestId("restart-wait-line").textContent).toBe("OpenAVC is starting...");
    expect(assign).not.toHaveBeenCalled();
    await runFor(1500);
    expect(assign).toHaveBeenCalledWith(`${HERE}/programmer`);
  });

  it("shows a failed start's reason and keeps waiting, so a retry that starts reconnects", async () => {
    fetchMock
      .mockResolvedValueOnce(said("failed", { error: "Loading the project failed: bad JSON", retry_in: 42 }))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockImplementation(async () => said("ready"));
    open();
    await runFor(0);
    expect(screen.getByTestId("restart-start-failed").textContent).toContain("OpenAVC could not start");
    expect(screen.getByTestId("restart-start-error").textContent).toBe("Loading the project failed: bad JSON");
    expect(screen.getByText("Trying again in 42 seconds.")).toBeTruthy();
    await runFor(1000);
    expect(screen.getByTestId("restart-wait-line").textContent).toBe("Starting again...");
    await runFor(1500);
    expect(assign).toHaveBeenCalledWith(`${HERE}/programmer`);
  });

  it("counts the last second of a failed start's countdown as one second", async () => {
    fetchMock.mockImplementation(async () => said("failed", { error: "No project", retry_in: 1 }));
    open();
    await runFor(0);
    expect(screen.getByText("Trying again in 1 second.")).toBeTruthy();
  });

  it("sends a held start to the server's page for Start again, and never gives up on it", async () => {
    fetchMock.mockImplementation(async () => said("failed", { error: "No project", held: true }));
    open();
    await runFor(120_000);
    expect(screen.getByText("Fix the problem, then open the server's page and press Start again.")).toBeTruthy();
    expect(screen.queryByText(/didn't come back/)).toBeNull();
    act(() => screen.getByRole("button", { name: /Open the server's page/ }).click());
    expect(assign).toHaveBeenCalledWith(`${HERE}/programmer`);
  });

  it("says why a refused restart was refused, and waits for nothing", async () => {
    vi.mocked(api.restartSystem).mockRejectedValueOnce(
      new ApiError(503, JSON.stringify({ detail: "The server is still starting. Try the restart again once it is up." })),
    );
    open();
    await runFor(5000);
    expect(screen.getByText(/The server is still starting/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("says an update is being installed, and waits for nothing", async () => {
    vi.mocked(api.restartSystem).mockRejectedValueOnce(
      new ApiError(503, JSON.stringify({
        detail: "An update to v9.9.9 is being installed. OpenAVC restarts by itself when it is done.",
      })),
    );
    open();
    await runFor(5000);
    expect(screen.getByText(/An update to v9.9.9 is being installed/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("waits through a restart already under way", async () => {
    vi.mocked(api.restartSystem).mockRejectedValueOnce(
      new ApiError(409, JSON.stringify({ detail: "A restart is already under way." })),
    );
    fetchMock.mockImplementation(async () => said("ready"));
    open();
    await runFor(1000);
    expect(assign).toHaveBeenCalledWith(`${HERE}/programmer`);
  });

  it("never takes an answer that is not the server's status for the server", async () => {
    fetchMock.mockResolvedValue({ ok: false, type: "opaque", json: async () => ({}) });
    open("http://192.168.1.20:9090/programmer");
    await runFor(70_000);
    expect(assign).not.toHaveBeenCalled();
    expect(screen.getByText(/didn't come back within 60 seconds/)).toBeTruthy();
  });
});
